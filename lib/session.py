"""Pipeline del informe: YAML -> informe guardado -> fila en el vault -> drills.

Orden y atomicidad según BRIEF 5 (POST /api/report): parsear, idempotencia,
guardar respaldo, escribir la fila, generar drills. Los pasos 1-4 son todo o
nada; el 5 puede fallar y se reintenta con POST /api/report/{date}/drills.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from . import agent, config, patterns, prompts, srs, text, vault, yamlmini

__all__ = [
    "ReportInvalid",
    "DuplicateReport",
    "parse_report",
    "process_report",
    "regenerate_drills",
    "state",
    "scenario",
    "export_note",
    "build_row",
]

SCORE_KEYS = ("fluency", "grammar", "lexis", "organisation", "interaction")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORT_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(-\d{6})?$")


class ReportInvalid(ValueError):
    """YAML ilegible o informe sin los datos mínimos (-> 422)."""

    def __init__(self, detail: str, raw_path: Path | None = None):
        super().__init__(detail)
        self.detail = detail
        self.raw_path = raw_path


class DuplicateReport(RuntimeError):
    """Mismo informe (sha256) para la misma fecha ya procesado (-> 409)."""


# --------------------------------------------------------------------------- #
# parseo
# --------------------------------------------------------------------------- #

def _candidates(raw: str):
    yield raw
    for m in re.finditer(r"```[ \t]*[A-Za-z]*[ \t]*\r?\n(.*?)```", raw, re.S):
        yield m.group(1)
    for m in re.finditer(r"^[ \t]*session[ \t]*:", raw, re.M):
        yield raw[m.start():]


def parse_report(raw: str):
    """Devuelve (session, warnings). Lanza ReportInvalid si no hay YAML usable."""
    if not isinstance(raw, str) or not raw.strip():
        raise ReportInvalid("informe vacío")
    data = None
    last_error = "sin bloque 'session:'"
    for cand in _candidates(raw):
        try:
            parsed = yamlmini.parse(cand)
        except yamlmini.YamlError as exc:
            last_error = str(exc)
            continue
        except Exception as exc:  # parser defensivo: nunca revienta la request
            last_error = str(exc)
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("session"), dict):
            data = parsed["session"]
            break
        if isinstance(parsed, dict) and "date" in parsed and "corrections" in parsed:
            data = parsed
            break
    if data is None:
        raise ReportInvalid(last_error)
    return _normalize_session(data)


def _normalize_session(data: dict):
    warnings = []

    raw_date = data.get("date")
    if raw_date is None:
        raise ReportInvalid("session.date ausente")
    date_str = str(raw_date).strip()
    if not DATE_RE.match(date_str):
        raise ReportInvalid("session.date no es YYYY-MM-DD: %r" % date_str)
    try:
        date.fromisoformat(date_str)
    except ValueError as exc:
        raise ReportInvalid("session.date inválida: %s" % exc) from exc

    minutes = data.get("minutes", data.get("duration_minutes"))
    if minutes is not None:
        try:
            minutes = int(minutes)
            if minutes < 0 or minutes > 600:
                raise ValueError
        except (TypeError, ValueError):
            warnings.append("minutes no numérico: %r" % (minutes,))
            minutes = None

    difficulty = data.get("difficulty") or data.get("cefr")
    difficulty = str(difficulty).strip().upper() if difficulty else None
    if difficulty and difficulty not in ("A2", "B1", "B2", "C1", "C2"):
        warnings.append("difficulty desconocida: %r" % difficulty)

    scenario = data.get("scenario") or data.get("topics")
    if isinstance(scenario, list):
        scenario = "; ".join(str(s) for s in scenario)
    scenario = str(scenario).strip() if scenario else None

    scores = {}
    raw_scores = data.get("scores") or {}
    if isinstance(raw_scores, dict):
        for key in SCORE_KEYS:
            if key not in raw_scores:
                continue
            try:
                value = int(float(raw_scores[key]))
            except (TypeError, ValueError):
                warnings.append("score %s no numérico" % key)
                continue
            if value < 1 or value > 5:
                warnings.append("score %s fuera de 1-5: %s" % (key, value))
                value = min(5, max(1, value))
            scores[key] = value
    elif raw_scores:
        warnings.append("scores no es un mapa")

    corrections = []
    raw_corrections = data.get("corrections") or []
    if not isinstance(raw_corrections, list):
        warnings.append("corrections no es una lista")
        raw_corrections = []
    for entry in raw_corrections:
        if not isinstance(entry, dict):
            continue
        said = str(entry.get("said") or "").strip()
        better = str(entry.get("better") or "").strip()
        if not said or not better:
            warnings.append("corrección sin said/better ignorada")
            continue
        pattern, warn = patterns.normalize(entry.get("pattern"))
        if warn:
            warnings.append(warn)
        corrections.append({"said": said, "better": better, "pattern": pattern})

    vocab = data.get("new_vocab") or []
    if isinstance(vocab, str):
        vocab = [vocab]
    if not isinstance(vocab, list):
        warnings.append("new_vocab no es una lista")
        vocab = []
    vocab = [str(v).strip() for v in vocab if str(v).strip()]

    fillers = data.get("fillers_per_minute")
    if fillers is not None and not isinstance(fillers, (int, float)):
        warnings.append("fillers_per_minute no numérico")
        fillers = None

    used_right = data.get("previously_corrected_used_right") or []
    if not isinstance(used_right, list):
        used_right = [used_right]

    session = {
        "date": date_str,
        "minutes": minutes,
        "difficulty": difficulty,
        "scenario": scenario,
        "scores": scores,
        "corrections": corrections,
        "new_vocab": vocab,
        "fillers_per_minute": fillers,
        "next_goal": (str(data["next_goal"]).strip() if data.get("next_goal") else None),
        "previously_corrected_used_right": [str(x) for x in used_right],
    }
    return session, warnings


# --------------------------------------------------------------------------- #
# respaldos
# --------------------------------------------------------------------------- #

def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _report_files(date_str: str, sha: str):
    base = config.reports_dir()
    doc = base / ("%s.json" % date_str)
    raw = base / ("%s-raw.txt" % date_str)
    if not doc.exists():
        return doc, raw
    try:
        existing = json.loads(doc.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    if existing.get("raw_sha256") == sha:
        return doc, raw
    stamp = datetime.now().strftime("%H%M%S")
    return (
        base / ("%s-%s.json" % (date_str, stamp)),
        base / ("%s-%s-raw.txt" % (date_str, stamp)),
    )


def _load_reports():
    docs = []
    for path in sorted(config.reports_dir().glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("session"), dict):
            doc["_path"] = str(path)
            docs.append(doc)
    return docs


def _duplicate(date_str: str, sha: str):
    for doc in _load_reports():
        sess = doc.get("session") or {}
        if sess.get("date") == date_str and doc.get("raw_sha256") == sha:
            return doc
    return None


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# fila del vault
# --------------------------------------------------------------------------- #

def _truncate(value, limit=220):
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def build_row(session: dict) -> dict:
    """Fila determinista desde el informe ya validado (el modelo no manda aquí)."""
    row = {
        "date": session["date"],
        "minutes": session.get("minutes") if session.get("minutes") is not None else "—",
        "topics": _truncate(session.get("scenario") or "—"),
        "primary_correction": "—",
        "new_vocab": _truncate(", ".join(session.get("new_vocab") or [])) or "—",
        "next_goal": _truncate(session.get("next_goal") or "—"),
    }
    corrections = session.get("corrections") or []
    if corrections:
        counts = {}
        for c in corrections:
            counts[c["pattern"]] = counts.get(c["pattern"], 0) + 1
        top = max(counts, key=lambda k: counts[k])
        example = next(c for c in corrections if c["pattern"] == top)
        row["primary_correction"] = _truncate(
            "%s (%d) — «%s» → «%s»" % (top, counts[top], example["said"], example["better"]),
            260,
        )
    return row


# --------------------------------------------------------------------------- #
# drills
# --------------------------------------------------------------------------- #

def _analyze_payload(session: dict, state: dict) -> str:
    deck = [
        {"kind": srs.kind_of(i), "pattern": i.get("pattern"), "prompt": i.get("prompt")}
        for i in state.get("items", [])
        if not i.get("retired")
    ]
    payload = {
        "session": session,
        "patterns": [
            {"name": r["name"], "attempts": r["attempts"], "pass_rate": r["pass_rate"]}
            for r in srs.pattern_rows(state)
            if r["attempts"]
        ],
        "deck": deck,
    }
    template = prompts.load("analyze.md")
    return prompts.fill(
        template,
        {
            "SESSION_JSON": json.dumps(payload, ensure_ascii=False, indent=2),
            "TODAY": date.today().isoformat(),
        },
    )


def _match_correction(drill: dict, corrections):
    source = drill.get("source") or {}
    if not isinstance(source, dict):
        return None
    said = str(source.get("said") or "").strip()
    better = str(source.get("better") or "").strip()
    if not said and not better:
        return None
    for c in corrections:
        if said and text.equal(said, c["said"]):
            return c
        if better and text.equal(better, c["better"]):
            return c
    for c in corrections:
        ns, nc = text.normalize(said), text.normalize(c["said"])
        if ns and nc and (ns in nc or nc in ns):
            return c
    return None


def _validate_drills(raw_drills, session: dict, warnings):
    corrections = session.get("corrections") or []
    if not corrections:
        return []
    by_pattern = {}
    order = []
    dropped = 0
    not_gap = 0
    for drill in raw_drills or []:
        if not isinstance(drill, dict):
            continue
        kind = srs.kind_of(drill)
        prompt = str(drill.get("prompt") or "").strip()
        answer = str(drill.get("answer") or "").strip()
        if not prompt or not answer:
            dropped += 1
            continue
        match = _match_correction(drill, corrections)
        if match is None:
            dropped += 1
            continue
        if kind == srs.KIND_MODEL:
            # Una frase modelo no se inventa: prompt y answer son la corrección tal cual
            # (el borrador que dijo y la versión natural). Nada de huecos ni de reescrituras.
            if not (text.equal(prompt, match["said"]) and text.equal(answer, match["better"])):
                dropped += 1
                continue
        # Un drill de hueco es una frase con UN hueco. "Say it the natural way / Rewrite" tiene
        # decenas de respuestas válidas: con una sola esperada se marca en rojo al que acierta.
        elif not is_gap_drill(prompt, answer):
            not_gap += 1
            continue
        pattern = match["pattern"]
        alts = [] if kind == srs.KIND_MODEL else (drill.get("alt_answers") or [])
        if isinstance(alts, str):
            alts = [alts]
        item = {
            "kind": kind,
            "pattern": pattern,
            "prompt": prompt,
            "answer": answer,
            "alt_answers": [str(a).strip() for a in alts if str(a).strip()],
            "source": {"said": match["said"], "better": match["better"]},
        }
        if pattern not in by_pattern:
            by_pattern[pattern] = []
            order.append(pattern)
        if len(by_pattern[pattern]) < 5:  # 3-5 por patrón; si el modelo da más, se recorta
            by_pattern[pattern].append(item)
    if dropped:
        warnings.append("drills_descartados_%d_fuera_de_corrections" % dropped)
    if not_gap:
        warnings.append("drills_rechazados_%d_no_son_hueco" % not_gap)

    out = []
    for pattern in order:
        out.extend(by_pattern[pattern])
    if len(out) > srs.MAX_PER_SESSION:
        warnings.append("drills_truncados_%d" % srs.MAX_PER_SESSION)
        out = out[: srs.MAX_PER_SESSION]
    return out


MAX_ANSWER_WORDS = 4
GAP_RE = re.compile(r"_{2,}")


def is_gap_drill(prompt: str, answer: str) -> bool:
    """Un drill válido = una frase con hueco `___` y una respuesta de 4 palabras o menos."""
    if not GAP_RE.search(prompt or ""):
        return False
    words = str(answer or "").split()
    return 0 < len(words) <= MAX_ANSWER_WORDS


def _gap_from_correction(correction: dict):
    """Convierte una corrección («said» → «better») en un drill de hueco, o None.

    Toma la frase correcta y tapa el tramo que cambió respecto a la incorrecta: así el hueco
    tiene una respuesta acotada y el resto de la frase da contexto. Si el cambio abarca más de
    cuatro palabras, no se puede pedir como hueco y se descarta (mejor sin drill que un drill
    imposible).
    """
    said = text.tokens(correction.get("said") or "")
    better = str(correction.get("better") or "").split()
    if not better:
        return None
    # Comparar en minúsculas pero quedarse con las palabras originales: si se alinea "i" con "I"
    # el matcher cree que el sujeto cambió y el hueco acaba en "___ was really lucky" en vez de
    # "I was really ___".
    better_norm = [text.normalize(w) for w in better]
    ops = difflib.SequenceMatcher(a=said, b=better_norm, autojunk=False).get_opcodes()
    # Un 'replace' es la corrección en sí (have -> has, big -> serious);
    # un 'insert' suele ser lo que faltaba (if, else, the). Se prefiere el replace porque es el
    # que enseña la estructura; el insert es el plan B.
    replaces = [(j1, j2) for tag, i1, i2, j1, j2 in ops if tag == "replace" and i2 > i1]
    inserts = [(j1, j2) for tag, _i1, _i2, j1, j2 in ops if tag == "insert"]
    candidates = [
        (j1, j2) for j1, j2 in replaces if 0 < j2 - j1 <= MAX_ANSWER_WORDS
    ] or [(j1, j2) for j1, j2 in inserts if 0 < j2 - j1 <= MAX_ANSWER_WORDS]
    if not candidates:
        return None
    j1, j2 = candidates[0]
    answer = " ".join(better[j1:j2]).strip(" ,.;:")
    prompt = " ".join(better[:j1] + ["___"] + better[j2:]).strip()
    if not answer or not is_gap_drill(prompt, answer):
        return None
    return {"prompt": prompt, "answer": answer}


def _model_item(correction: dict) -> dict:
    """Frase modelo: lo que dijo, tal cual, y la versión natural. Sin hueco y sin examen."""
    return {
        "kind": srs.KIND_MODEL,
        "pattern": correction["pattern"],
        "prompt": correction["said"],
        "answer": correction["better"],
        "alt_answers": [],
        "source": {"said": correction["said"], "better": correction["better"]},
    }


def _local_drills(session: dict):
    """Reserva determinista: cada corrección acaba en hueco o en frase modelo.

    Una corrección que no admite hueco (p. ej. sobra una preposición: «I was in the project
    manager») no se tira ni se convierte en un hueco imposible: se emite como `kind=model`.
    """
    out = []
    for c in session.get("corrections") or []:
        gap = _gap_from_correction(c)
        if gap is None:
            out.append(_model_item(c))
            continue
        out.append(
            {
                "kind": srs.KIND_GAP,
                "pattern": c["pattern"],
                "prompt": gap["prompt"],
                "answer": gap["answer"],
                "alt_answers": [],
                "source": {"said": c["said"], "better": c["better"]},
            }
        )
    return out[: srs.MAX_PER_SESSION]


def _ensure_coverage(drills, session: dict, warnings):
    """Ninguna corrección se queda sin tarjeta: si no tiene hueco, se le pone una frase modelo.

    Se comprueba sobre lo que ya hay (huecos del modelo incluidos) para no duplicar tarjeta
    cuando la misma corrección ya está cubierta.
    """
    corrections = session.get("corrections") or []
    out = list(drills)
    for correction in corrections:
        if len(out) >= srs.MAX_PER_SESSION:
            warnings.append("drills_truncados_%d" % srs.MAX_PER_SESSION)
            break
        if any(_match_correction(item, [correction]) for item in out):
            continue
        out.append(_model_item(correction))
    return out


def _generate_drills(session: dict, warnings):
    state = srs.load()
    prompt = _analyze_payload(session, state)
    parsed = agent.extract_json(agent.call_agent(prompt))
    if isinstance(parsed, list):
        parsed = {"drills": parsed}
    if not isinstance(parsed, dict):
        raise agent.AgentError("respuesta de análisis sin objeto JSON")
    drills = _validate_drills(parsed.get("drills") or [], session, warnings)
    if not drills and session.get("corrections"):
        warnings.append("drills_local_fallback")
        drills = _validate_drills(_local_drills(session), session, warnings)
    drills = _ensure_coverage(drills, session, warnings)
    extra = parsed.get("warnings")
    if isinstance(extra, list):
        warnings.extend("model: %s" % w for w in extra if w)
    return drills


# --------------------------------------------------------------------------- #
# API: informe
# --------------------------------------------------------------------------- #

def process_report(raw: str) -> dict:
    raw = raw if isinstance(raw, str) else str(raw)
    sha = _sha(raw)

    try:
        session, warnings = parse_report(raw)
    except ReportInvalid as exc:
        exc.raw_path = _save_raw_on_error(raw)
        raise

    if _duplicate(session["date"], sha):
        raise DuplicateReport(session["date"])

    doc_path, raw_path = _report_files(session["date"], sha)
    row = build_row(session)
    doc = {
        "session": session,
        "row": row,
        "raw_sha256": sha,
        "received_at": datetime.now().replace(microsecond=0).isoformat(),
        "warnings": list(warnings),
        "drills": [],
    }

    # 3 + 4: respaldo y fila, todo o nada. Sin nota del vault el informe se guarda igual
    # en data/reports y se avisa; nunca se pierde lo pegado por no tener la nota.
    has_vault = config.vault_path().exists()
    try:
        _write_atomic(raw_path, raw.encode("utf-8"))
        _write_atomic(doc_path, _dump(doc))
        if has_vault:
            vault.append_row(row)
        else:
            warnings.append("vault_no_encontrado")
    except Exception:
        for path in (doc_path, raw_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise

    # 5: drills (si falla, la fila ya está escrita)
    added = []
    try:
        drills = _generate_drills(session, warnings)
        state = srs.load()
        added, merge_warnings = srs.merge_drills(state, drills, created=session["date"])
        srs.save(state)
        warnings.extend(merge_warnings)
        doc["drills"] = drills
    except Exception as exc:  # noqa: BLE001 - paso opcional, nunca rompe el informe
        warnings.append("drills_pending")
        warnings.append("drills_error: %s" % str(exc)[:200])

    doc["warnings"] = list(warnings)
    try:
        _write_atomic(doc_path, _dump(doc))
    except OSError:
        pass

    return {
        "ok": True,
        "session": session,
        "row": row,
        "drills_added": len(added),
        "row_written": has_vault,
        "warnings": warnings,
    }


def _dump(doc: dict) -> bytes:
    clean = {k: v for k, v in doc.items() if k != "_path"}
    return (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _save_raw_on_error(raw: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    path = config.reports_dir() / ("%s-raw.txt" % stamp)
    n = 2
    while path.exists():
        path = config.reports_dir() / ("%s-%d-raw.txt" % (stamp, n))
        n += 1
    _write_atomic(path, raw.encode("utf-8"))
    return path


def regenerate_drills(date_str: str) -> dict:
    """Regenera los drills de un informe concreto.

    Acepta el id del informe (nombre del fichero sin extensión, p. ej. `2026-11-02-145120`) o una
    fecha si ese día tiene un solo informe. Con varios informes el mismo día, la fecha es ambigua:
    se devuelve error con los ids disponibles en vez de adivinar cuál quiso decir.
    """
    key = (date_str or "").strip()
    if not DATE_RE.match(key) and not REPORT_ID_RE.match(key):
        raise ReportInvalid("fecha o id de informe no válido")
    if DATE_RE.match(key):
        # Una fecha es ambigua en cuanto hay más de una sesión ese día: no se adivina.
        docs = [d for d in _load_reports()
                if (d.get("session") or {}).get("date") == key]
    else:
        docs = [d for d in _load_reports()
                if Path(d.get("_path") or "").stem == key]
    if not docs:
        raise ReportInvalid("no hay informe guardado para %s" % key)
    if len(docs) > 1:
        ids = ", ".join(sorted(Path(d["_path"]).stem for d in docs))
        raise ReportInvalid(
            "ese día tiene %d informes; indica cuál con su id: %s" % (len(docs), ids))
    doc = docs[0]

    session = doc["session"]
    warnings = []
    try:
        drills = _generate_drills(session, warnings)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "drills_failed",
            "detail": str(exc)[:300],
            "drills_added": 0,
        }

    state = srs.load()
    added, merge_warnings = srs.merge_drills(state, drills, created=session["date"])
    srs.save(state)
    warnings.extend(merge_warnings)
    doc["drills"] = drills
    doc["warnings"] = warnings
    try:
        _write_atomic(Path(doc["_path"]), _dump(doc))
    except OSError:
        pass
    return {"ok": True, "date": date_str, "drills_added": len(added), "warnings": warnings}


# --------------------------------------------------------------------------- #
# API: estado
# --------------------------------------------------------------------------- #

def _sessions_view():
    rows = vault.parse_sessions()
    # Varias sesiones el MISMO día son normales: la tabla del vault tiene una fila por sesión y
    # `data/reports/` un informe por sesión. Emparejar por fecha hacía que la segunda sesión
    # pisara a la primera y las dos filas enseñaran el mismo escenario.
    by_date = {}
    for doc in _load_reports():
        sess = doc.get("session") or {}
        d = sess.get("date")
        if not d:
            continue
        by_date.setdefault(d, []).append(doc)
    for docs in by_date.values():
        docs.sort(key=lambda doc: (doc.get("received_at") or "", doc.get("_path") or ""))
    seen = {}
    out = [None] * len(rows)
    # Emparejar del más reciente al más antiguo: la fila más nueva del vault corresponde al informe
    # más nuevo de ese día. Una fila escrita a mano sin informe no desplaza a las demás.
    for pos in range(len(rows) - 1, -1, -1):
        row = rows[pos]
        day = row["date"]
        docs = by_date.get(day) or []
        idx = seen.get(day, 0)
        doc = docs[len(docs) - 1 - idx] if idx < len(docs) else None
        if doc:
            seen[day] = idx + 1
        if doc:
            sess = dict(doc["session"])
            out[pos] = {
                "date": sess["date"],
                "report_id": Path(doc.get("_path") or "").stem or sess["date"],
                "received_at": doc.get("received_at"),
                "minutes": sess.get("minutes") if sess.get("minutes") is not None else row["minutes"],
                "difficulty": sess.get("difficulty"),
                "scenario": sess.get("scenario") or row["topics"],
                "scores": sess.get("scores") or {},
                "corrections": sess.get("corrections") or [],
                "next_goal": sess.get("next_goal") or row["next_goal"],
                "estimated": True,
            }
        else:
            out[pos] = {
                "date": row["date"],
                "report_id": None,
                "received_at": None,
                "minutes": row["minutes"],
                "difficulty": None,
                "scenario": row["topics"],
                "scores": {},
                "corrections": [],
                "next_goal": row["next_goal"],
                "estimated": True,
            }
    return out


def drills_list(pattern: str | None = None, only_due: bool = False,
                include_retired: bool = False) -> dict:
    """Extensión aditiva: items del mazo para la pestaña Drills.

    El contrato solo publica contadores; el frontend pide /api/drills y cae a
    modo contador si no existe. Devolver los items no rompe ningún campo.
    """
    deck = srs.load()
    today = date.today().isoformat()
    items = []
    for item in deck.get("items", []):
        if item.get("retired") and not include_retired:
            continue
        if pattern and item.get("pattern") != pattern:
            continue
        if only_due and item.get("retired"):
            continue
        if only_due and (item.get("due") or today) > today:
            continue
        items.append(item)
    items.sort(key=lambda i: (i.get("due") or "9999", i.get("id") or ""))
    counts = srs.counts(deck, today)
    models = srs.model_stats(deck, today)
    return {
        "ok": True,
        "items": items,
        "due_now": counts["due_now"],
        "total": counts["total"],
        "retired": counts["retired"],
        "models": models,
        "today": today,
    }


def state() -> dict:
    sessions = _sessions_view()
    deck = srs.load()
    models = srs.model_stats(deck)
    reports = _load_reports()
    warnings = []
    if not config.vault_path().exists():
        warnings.append("vault_no_encontrado")

    fillers = [
        (doc.get("session") or {}).get("fillers_per_minute") for doc in reports
    ]
    fillers = [f for f in fillers if isinstance(f, (int, float))]
    corrections_pm = []
    for doc in reports:
        sess = doc.get("session") or {}
        minutes = sess.get("minutes") or 0
        count = len(sess.get("corrections") or [])
        if minutes and count:
            corrections_pm.append(count / minutes)

    result = {
        "sessions": sessions,
        "patterns": srs.pattern_rows(deck),
        "drills": srs.counts(deck),
        "stats": {
            "sessions": len(sessions),
            "enough_data": len(sessions) >= 4,
            "measured": {"fillers_per_minute": None, "errors_per_minute": None},
            "estimated": {
                "fillers_per_minute": round(sum(fillers) / len(fillers), 2) if fillers else None,
                "corrections_per_minute": (
                    round(sum(corrections_pm) / len(corrections_pm), 2) if corrections_pm else None
                ),
            },
            # Aditivo: las frases modelo no son examen y se marcan a mano. Su tasa no entra
            # en `patterns`; aquí solo se ve cuántas se han mirado y cuántas vuelven.
            "models": {
                "seen": models["seen"],
                "to_review": models["to_review"],
            },
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result


# --------------------------------------------------------------------------- #
# API: escenario
# --------------------------------------------------------------------------- #

FALLBACK_SCENARIOS = [
    ("architecture review", "the tech lead defending a design", "a sceptical staff engineer"),
    ("incident postmortem", "the engineer who owned the failing service", "the engineering manager"),
    ("technical hiring", "the interviewer", "a senior candidate with strong opinions"),
    ("sprint planning", "a backend developer pushing back on scope", "the delivery manager"),
    ("code review", "the reviewer who found a subtle bug", "the author of the change"),
    ("platform migration", "the person proposing the move to a new runtime", "a colleague worried about cost"),
]


def _fallback_brief(scenario_name, you, me):
    """Brief mínimo para que el escenario de reserva tampoco deje al alumno sin pie."""
    return {
        "brief": {
            "situation": "A routine decision about %s that has to be settled in this call." % scenario_name,
            "my_position": "You are %s and you have a position you can defend." % me,
            "stake": "Your team's next two weeks depend on what you agree today.",
        },
        "ammo": [
            "It has worked well enough so far, but the last two changes were painful.",
            "Doing nothing is also a decision, and it has a cost.",
        ],
        "objections": [
            "Why should we do this now and not next quarter?",
            "What happens if we are wrong?",
        ],
        "opener": "Let's get to it — walk me through what you are proposing and why now?",
        "useful": [
            "The way I see it, ...",
            "That's fair, but ...",
            "What I'm worried about is ...",
        ],
    }


def _fallback_scenario(difficulty: str, focus, family: str = ""):
    key = "%s|%s|%s" % (difficulty, ",".join(focus or []), family)
    idx = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(FALLBACK_SCENARIOS)
    scenario, you, me = FALLBACK_SCENARIOS[idx]
    data = {"scenario": scenario, "you_play": you, "i_play": me}
    data.update(_fallback_brief(scenario, you, me))
    return data


# --------------------------------------------------------------------------- #
# variedad de escenarios: sin esto el modelo repite "patch o rewrite" cada vez
# --------------------------------------------------------------------------- #

SCENARIO_FAMILIES = [
    "an incident that broke overnight (on-call, what you tell people in the morning)",
    "a deadline that moves, or scope that has to be cut",
    "money: a cost, a licence renewal or a budget someone has to defend",
    "two teams disagreeing about who owns something",
    "a security or compliance finding that nobody wants to fix",
    "handing a legacy system over to someone new",
    "an API or contract change that breaks a client",
    "hiring: interviewing, or rejecting a candidate you like",
    "a junior's mistake and how you deal with it",
    "buy versus build: a vendor or tool decision",
    "a performance problem nobody wants to own",
    "explaining a technical decision to a non-technical manager",
]


def _history_path():
    return config.data_dir() / "scenario_history.json"


def _recent_scenarios(limit: int = 6):
    try:
        raw = json.loads(_history_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)][-limit:]


def _remember_scenario(family: str, data: dict):
    hist = _recent_scenarios(limit=20)
    hist.append({
        "at": datetime.now().replace(microsecond=0).isoformat(),
        "family": family,
        "scenario": str(data.get("scenario") or "")[:200],
        "you_play": str(data.get("you_play") or "")[:120],
    })
    try:
        payload = json.dumps(hist[-20:], ensure_ascii=False, indent=2) + "\n"
        _write_atomic(_history_path(), payload.encode("utf-8"))
    except OSError:
        pass


def pick_family(recent=None):
    """Elige una familia de situación que no se haya usado en las últimas tres."""
    recent = _recent_scenarios() if recent is None else recent
    used = [r.get("family") for r in recent[-3:] if r.get("family")]
    fresh = [f for f in SCENARIO_FAMILIES if f not in used]
    pool = fresh or SCENARIO_FAMILIES
    return pool[int(hashlib.sha256(str(datetime.now().timestamp()).encode()).hexdigest()[:8], 16) % len(pool)]



def _as_list(value, limit=4):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    return out[:limit]


def _as_bullets(value, empty="—"):
    items = _as_list(value)
    return "\n".join("- " + i for i in items) if items else empty


def scenario(difficulty="B2", focus=None, minutes=10) -> dict:
    focus = [str(f) for f in (focus or []) if f]
    difficulty = (difficulty or "B2").upper()
    warnings = []
    recent = _recent_scenarios()
    family = pick_family(recent)
    avoid = "\n".join("- " + r.get("scenario", "") for r in recent[-5:] if r.get("scenario"))
    try:
        prompt = prompts.fill(
            prompts.load("scenario.md"),
            {
                "DIFFICULTY": difficulty,
                "FOCUS": ", ".join(focus) or "any",
                "MINUTES": minutes,
                "FAMILY": family,
                "AVOID": avoid or "(ninguno todavía)",
            },
        )
        parsed = agent.extract_json(agent.call_agent(prompt))
        if not isinstance(parsed, dict):
            raise agent.AgentError("sin objeto JSON")
        data = {
            "scenario": str(parsed.get("scenario") or "").strip(),
            "you_play": str(parsed.get("you_play") or "").strip(),
            "i_play": str(parsed.get("i_play") or "").strip(),
        }
        if not all(data.values()):
            raise agent.AgentError("faltan claves del escenario")
        brief = parsed.get("brief") if isinstance(parsed.get("brief"), dict) else {}
        data["brief"] = {
            "situation": str(brief.get("situation") or "").strip(),
            "my_position": str(brief.get("my_position") or "").strip(),
            "stake": str(brief.get("stake") or "").strip(),
        }
        data["ammo"] = _as_list(parsed.get("ammo"), 4)
        data["objections"] = _as_list(parsed.get("objections"), 3)
        data["useful"] = _as_list(parsed.get("useful"), 3)
        data["opener"] = str(parsed.get("opener") or "").strip()
        # Sin brief ni apertura el escenario vuelve a ser demasiado abierto: se completa con la
        # reserva determinista en vez de dejar huecos.
        sin_pie = not (data["brief"]["my_position"] and data["ammo"] and data["opener"])
        if sin_pie:
            data.update(_fallback_brief(data["scenario"], data["you_play"], data["i_play"]))
            warnings.append("scenario_sin_brief")
    except Exception as exc:  # noqa: BLE001 - la sesión nunca se bloquea
        data = _fallback_scenario(difficulty, focus, family)
        warnings.append("scenario_fallback")
        warnings.append(str(exc)[:200])

    _remember_scenario(family, data)

    block_b = prompts.fill(
        prompts.load("blockB.md"),
        {
            "SCENARIO": data["scenario"],
            "YOU_PLAY": data["you_play"],
            "I_PLAY": data["i_play"],
            "SITUATION": data["brief"]["situation"] or "—",
            "MY_POSITION": data["brief"]["my_position"] or "—",
            "STAKE": data["brief"]["stake"] or "—",
            "AMMO": _as_bullets(data["ammo"]),
            "OBJECTIONS": _as_bullets(data["objections"]),
            "USEFUL": _as_bullets(data["useful"]),
            "OPENER": data["opener"],
            "DIFFICULTY": difficulty,
            "FOCUS": ", ".join(focus) or "any",
            "MINUTES": minutes,
        },
    )
    out = {
        "ok": True,
        "family": family,
        "scenario": data["scenario"],
        "you_play": data["you_play"],
        "i_play": data["i_play"],
        "brief": data["brief"],
        "ammo": data["ammo"],
        "objections": data["objections"],
        "useful": data["useful"],
        "opener": data["opener"],
        "block_b": block_b,
        "chatgpt_url": "https://chatgpt.com/",
    }
    if warnings:
        out["warnings"] = warnings
    return out


# --------------------------------------------------------------------------- #
# API: export
# --------------------------------------------------------------------------- #

def export_note():
    note = vault.read_note()
    return "vault-note.md", note.encode("utf-8")
