"""Repetición espaciada en data/srs.json (sin base de datos).

Reglas (BRIEF 6.2): fallo -> vuelve al día siguiente; acierto -> streak+1;
streak>=3 -> retired (fuera del mazo, histórico conservado). Un retirado no
vuelve nunca al mazo.

Dos tipos de tarjeta (`kind`):
  - `gap`: una frase con un hueco `___` y una respuesta corta. Es la que se
    puntúa y la que alimenta `pattern_stats` (tasa de acierto por patrón).
  - `model`: una corrección que no admite hueco (p. ej. sobra una preposición).
    `prompt` es la frase que dijo el usuario y `answer` la versión natural.
    No es un examen: se mira, se escucha y se marca a mano. NUNCA entra en
    `pattern_stats`; su contador propio es `model_marks`.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from . import config, patterns, text

__all__ = [
    "load",
    "save",
    "merge_drills",
    "answer",
    "due_now",
    "counts",
    "model_stats",
    "pattern_rows",
    "find",
    "kind_of",
    "KIND_GAP",
    "KIND_MODEL",
    "MAX_PER_SESSION",
]

MAX_PER_SESSION = 20
RETIRE_AT = 3
INTERVALS = {1: 1, 2: 3}  # días hasta la siguiente vez

KIND_GAP = "gap"
KIND_MODEL = "model"


def kind_of(item) -> str:
    """Tipo de tarjeta. Sin campo (mazos viejos) siempre es `gap`."""
    if isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == KIND_MODEL:
        return KIND_MODEL
    return KIND_GAP


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _empty():
    return {
        "version": 1,
        "items": [],
        "pattern_stats": {},
        "model_marks": {"seen": 0, "to_review": 0},
        "updated": None,
    }



def _today() -> str:
    return date.today().isoformat()


def load() -> dict:
    path = config.srs_path()
    if not path.exists():
        return _empty()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(state, dict):
        return _empty()
    state.setdefault("version", 1)
    state.setdefault("items", [])
    state.setdefault("pattern_stats", {})
    marks = state.get("model_marks")
    if not isinstance(marks, dict):
        marks = {}
    state["model_marks"] = {
        "seen": _int(marks.get("seen"), 0),
        "to_review": _int(marks.get("to_review"), 0),
    }
    # Mazos anteriores a las tarjetas `model`: todo lo que no diga otra cosa es un hueco.
    for item in state["items"]:
        if isinstance(item, dict):
            item.setdefault("kind", KIND_GAP)
    return state


def save(state: dict) -> None:
    path = config.srs_path()
    state["updated"] = _now_iso()
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# mazo
# --------------------------------------------------------------------------- #

def _next_id(items) -> int:
    top = 0
    for item in items:
        raw = str(item.get("id", ""))
        if raw.startswith("d-") and raw[2:].isdigit():
            top = max(top, int(raw[2:]))
    return top + 1


def find(state: dict, item_id: str):
    for item in state.get("items", []):
        if item.get("id") == item_id:
            return item
    return None


def _signature(kind: str, pattern: str, prompt: str) -> str:
    return "%s|%s|%s" % (kind, pattern, text.normalize(prompt))


def merge_drills(state: dict, drills, created: str | None = None):
    """Fusiona drills nuevos en el estado. Devuelve (añadidos, warnings)."""
    created = created or _today()
    warnings = []
    items = state.setdefault("items", [])
    existing = {
        _signature(kind_of(i), i.get("pattern", ""), i.get("prompt", "")) for i in items
    }
    next_num = _next_id(items)

    added = []
    for drill in drills:
        if len(added) >= MAX_PER_SESSION:
            warnings.append("drills_truncated_at_%d" % MAX_PER_SESSION)
            break
        if not isinstance(drill, dict):
            continue
        kind = kind_of(drill)
        prompt = (drill.get("prompt") or "").strip()
        answer = (drill.get("answer") or "").strip()
        if not prompt or not answer:
            warnings.append("drill_ignorado: sin prompt/answer")
            continue
        pattern, warn = patterns.normalize(drill.get("pattern"))
        if warn:
            warnings.append(warn)
        sig = _signature(kind, pattern, prompt)
        if sig in existing:
            warnings.append("drill_duplicado: %s" % prompt[:60])
            continue
        alts = drill.get("alt_answers") or []
        if isinstance(alts, str):
            alts = [alts]
        alts = [str(a).strip() for a in alts if str(a).strip()]
        source = drill.get("source") or {}
        if not isinstance(source, dict):
            source = {}
        item = {
            "id": "d-%04d" % next_num,
            "kind": kind,
            "pattern": pattern,
            "prompt": prompt,
            "answer": answer,
            "alt_answers": alts,
            "source": {
                "said": str(source.get("said") or "").strip(),
                "better": str(source.get("better") or "").strip(),
            },
            "created": created,
            "streak": 0,
            "wrong": 0,
            "last_seen": None,
            "retired": False,
            "due": created,
        }
        next_num += 1
        items.append(item)
        existing.add(sig)
        added.append(item)
    return added, warnings


# --------------------------------------------------------------------------- #
# respuestas
# --------------------------------------------------------------------------- #

def answer(state: dict, item_id: str, correct: bool, today: str | None = None) -> dict:
    """Aplica una respuesta. Devuelve el item actualizado. LookupError si no existe.

    Una tarjeta `model` se marca a mano ("lo tenía" / "para repasar"): mueve su propio
    `streak`/`due` y el contador `model_marks`, pero NO toca `pattern_stats`, porque la tasa
    de acierto por patrón solo mide huecos y con esto dejaría de significar nada.
    """
    item = find(state, item_id)
    if item is None:
        raise LookupError(item_id)
    today = today or _today()
    item["last_seen"] = today

    if kind_of(item) == KIND_MODEL:
        marks = state.setdefault("model_marks", {"seen": 0, "to_review": 0})
        marks["seen"] = _int(marks.get("seen"), 0) + 1
        if correct:
            item["streak"] = _int(item.get("streak"), 0) + 1
            if item["streak"] >= RETIRE_AT:
                item["retired"] = True
                item["due"] = None
            else:
                item["due"] = _plus(today, INTERVALS.get(item["streak"], 1))
        else:
            item["streak"] = 0
            item["wrong"] = _int(item.get("wrong"), 0) + 1
            item["due"] = _plus(today, 1)
            item["retired"] = bool(item.get("retired"))  # un retirado no revive
            marks["to_review"] = _int(marks.get("to_review"), 0) + 1
        return item

    stats = state.setdefault("pattern_stats", {}).setdefault(
        item.get("pattern", patterns.FALLBACK),
        {"attempts": 0, "correct": 0, "streak": 0},
    )
    stats["attempts"] = int(stats.get("attempts", 0)) + 1

    if correct:
        item["streak"] = int(item.get("streak", 0)) + 1
        stats["correct"] = int(stats.get("correct", 0)) + 1
        if item["streak"] >= RETIRE_AT:
            item["retired"] = True
            item["due"] = None
        else:
            days = INTERVALS.get(item["streak"], 1)
            item["due"] = _plus(today, days)
    else:
        item["streak"] = 0
        item["wrong"] = int(item.get("wrong", 0)) + 1
        item["due"] = _plus(today, 1)
        item["retired"] = bool(item.get("retired"))  # un retirado no revive

    stats["streak"] = max(int(stats.get("streak", 0)), int(item.get("streak", 0)))
    return item


def _plus(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def due_now(state: dict, today: str | None = None):
    """Huecos que vencen hoy. Las `model` no son drills: se cuentan en `model_stats`."""
    today = today or _today()
    out = []
    for item in state.get("items", []):
        if item.get("retired") or kind_of(item) == KIND_MODEL:
            continue
        due = item.get("due") or today
        if due <= today:
            out.append(item)
    return out


def counts(state: dict, today: str | None = None) -> dict:
    """Contadores del mazo de drills (huecos). Las `model` viven en `model_stats`."""
    items = [i for i in state.get("items", []) if kind_of(i) != KIND_MODEL]
    retired = [i for i in items if i.get("retired")]
    return {
        "due_now": len(due_now(state, today)),
        "total": len(items) - len(retired),
        "retired": len(retired),
    }


def model_stats(state: dict, today: str | None = None) -> dict:
    """Frases modelo: cuántas vencen, cuántas quedan y cómo van las marcas a mano."""
    today = today or _today()
    models = [i for i in state.get("items", []) if kind_of(i) == KIND_MODEL]
    due = [
        i for i in models
        if not i.get("retired") and (i.get("due") or today) <= today
    ]
    marks = state.get("model_marks")
    if not isinstance(marks, dict):
        marks = {}
    return {
        "due_now": len(due),
        "total": len([i for i in models if not i.get("retired")]),
        "retired": len([i for i in models if i.get("retired")]),
        "seen": _int(marks.get("seen"), 0),
        "to_review": _int(marks.get("to_review"), 0),
    }


def pattern_rows(state: dict, today: str | None = None):
    today = today or _today()
    stats = state.get("pattern_stats") or {}
    names = list(patterns.PATTERNS)
    for name in stats:
        if name not in names:
            names.append(name)
    due_by_pattern = {}
    for item in due_now(state, today):
        due_by_pattern[item.get("pattern")] = due_by_pattern.get(item.get("pattern"), 0) + 1
    rows = []
    for name in names:
        data = stats.get(name) or {}
        attempts = int(data.get("attempts", 0))
        correct = int(data.get("correct", 0))
        if not attempts:
            # sin respuestas registradas: cuenta el mazo para no mentir con 0.
            # Solo huecos: una frase modelo no acierta ni falla.
            items = [
                i for i in state.get("items", [])
                if i.get("pattern") == name and kind_of(i) != KIND_MODEL
            ]
            attempts = sum(int(i.get("streak", 0)) + int(i.get("wrong", 0)) for i in items)
            correct = sum(int(i.get("streak", 0)) for i in items)
        rows.append(
            {
                "name": name,
                "attempts": attempts,
                "correct": correct,
                "pass_rate": round(correct / attempts, 3) if attempts else None,
                "streak": int((stats.get(name) or {}).get("streak", 0)),
                "due": due_by_pattern.get(name, 0),
            }
        )
    return rows
