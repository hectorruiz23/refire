"""Ingesta de clips de habla real para el banco de shadowing.

Aquí vive lo que se puede probar sin whisper: convertir palabras con tiempos en frases
(``segment_words``), decidir qué candidatas entran solas (``passes_quality`` y
``select_new_candidates``), volcarlas al banco (``accept_candidates`` /
``add_auto_candidates``) y quitarlas (``forget_phrase``). El corte con ffmpeg también vive
aquí (``cut_clip``) para que el CLI y la ingesta de YouTube usen el mismo código.

La transcripción es ``lib/audio.transcribe_words``; no hay dependencias nuevas.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path

__all__ = [
    "PAUSE_CUT_S",
    "MARGIN_S",
    "MIN_WORDS",
    "MIN_CONFIDENCE",
    "AUTO_MIN_WORDS",
    "AUTO_MIN_CONFIDENCE",
    "AUTO_MAX_NEW",
    "AUTO_MIN_S",
    "AUTO_MAX_S",
    "segment_words",
    "passes_quality",
    "select_new_candidates",
    "normalize_text",
    "word_count",
    "bank_texts",
    "cut_clip",
    "accept_candidates",
    "add_auto_candidates",
    "forget_phrase",
]

# Cortar cuando el silencio entre dos palabras supera este umbral.
PAUSE_CUT_S = 0.5
# Margen de audio a cada lado del corte (lo aplica ffmpeg).
MARGIN_S = 0.04
MIN_WORDS = 3
# Probabilidad media mínima por palabra según whisper: por debajo no hay voz clara.
MIN_CONFIDENCE = 0.35

# Filtro de calidad para añadir frases solas desde un vídeo (encargo 5).
AUTO_MIN_WORDS = 4
AUTO_MIN_CONFIDENCE = 0.80
AUTO_MAX_NEW = 20
AUTO_MIN_S = 2.0
AUTO_MAX_S = 8.0

FFMPEG_TIMEOUT = 180

_END_RE = re.compile(r"[.?!][\"'\u2019\u201d)\]]*$")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")
# Palabras con apóstrofo o guion dentro (don't, side-effects); puntuación fuera.
_WORD_RE = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*", re.UNICODE)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Dos ingestas a la vez (dos pestañas) no pueden pisarse el JSON del banco.
_BANK_LOCK = threading.Lock()


def _word_text(word) -> str:
    if isinstance(word, dict):
        raw = word.get("word", word.get("text", ""))
    else:
        raw = getattr(word, "word", getattr(word, "text", ""))
    return " ".join(str(raw or "").split())


def _number(word, key: str, default: float = 0.0) -> float:
    if isinstance(word, dict):
        raw = word.get(key, default)
    else:
        raw = getattr(word, key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _probability(word) -> float:
    value = _number(word, "probability", 1.0)
    return min(1.0, max(0.0, value))


def _join(words: list[dict]) -> str:
    text = " ".join(word["text"] for word in words)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return " ".join(text.split()).strip()


def _ends_sentence(text: str) -> bool:
    return bool(_END_RE.search(text))


def _cut_words(words) -> list[dict]:
    """Normaliza y descarta palabras sin tiempos utilizables."""
    out = []
    for word in words:
        text = _word_text(word)
        start = _number(word, "start", -1.0)
        end = _number(word, "end", -1.0)
        if not text or start < 0 or end <= start:
            continue
        out.append({
            "text": text,
            "start": start,
            "end": end,
            "probability": _probability(word),
        })
    return out


def segment_words(words, min_s: float = 2.0, max_s: float = 8.0,
                  max_phrases: int = 40) -> list[dict]:
    """Lista de palabras con tiempos -> frases cortas listas para cortar.

    Corta por pausa mayor de ``PAUSE_CUT_S`` entre palabras y por puntuación final
    (``.``, ``?``, ``!``). Los límites siempre caen en bordes de palabra. Descarta
    fragmentos fuera de ``[min_s, max_s]``, con menos de ``MIN_WORDS`` palabras o con
    confianza media baja (sin voz clara). Devuelve como mucho ``max_phrases``.
    """
    if min_s <= 0 or max_s <= 0 or min_s > max_s or max_phrases <= 0:
        return []

    clean = _cut_words(words)
    groups = []
    current = []
    for index, word in enumerate(clean):
        current.append(word)
        following = clean[index + 1] if index + 1 < len(clean) else None
        pause = following["start"] - word["end"] if following else 0.0
        if _ends_sentence(word["text"]) or pause > PAUSE_CUT_S or following is None:
            groups.append(current)
            current = []

    out = []
    for group in groups:
        start = group[0]["start"]
        end = group[-1]["end"]
        duration = end - start
        if len(group) < MIN_WORDS or duration < min_s or duration > max_s:
            continue
        confidence = sum(word["probability"] for word in group) / len(group)
        if confidence < MIN_CONFIDENCE:
            continue
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "confidence": round(confidence, 3),
            "text": _join(group),
        })
        if len(out) >= max_phrases:
            break
    return out


def cut_clip(source: Path, start: float, end: float, out: Path) -> None:
    """Corta ``[start, end)`` con ffmpeg en WAV mono 16 kHz.

    Lo usan el CLI y la ingesta de YouTube: un único sitio para el margen y los parámetros.
    """
    start = max(0.0, float(start) - MARGIN_S)
    end = max(start + 0.01, float(end) + MARGIN_S)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(source), "-ss", "%.3f" % start, "-t", "%.3f" % (end - start),
        "-ac", "1", "-ar", "16000", "-f", "wav", str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("ffmpeg no pudo cortar el audio: %s" % str(exc)[:200]) from exc
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:240]
        raise RuntimeError("ffmpeg no generó %s: %s" % (out.name, detail or "vacío"))


# --------------------------------------------------------------------------- #
# filtro de calidad para la ingesta automática
# --------------------------------------------------------------------------- #

def word_count(text) -> int:
    """Palabras de una frase, sin contar puntuación suelta."""
    return len(_WORD_RE.findall(str(text or "")))


def normalize_text(text) -> str:
    """Texto comparable: minúsculas, sin puntuación y con espacios normalizados.

    Se usa solo para detectar repetidas; nunca se guarda ni se muestra.
    """
    plain = unicodedata.normalize("NFKC", str(text or "")).casefold()
    words = [match.group(0).replace("\u2019", "'") for match in _WORD_RE.finditer(plain)]
    return " ".join(words)


def _duration(item: dict) -> float:
    raw = item.get("duration")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if math.isfinite(value) and value > 0:
        return value
    try:
        value = float(item.get("end")) - float(item.get("start"))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _confidence(item: dict) -> float:
    try:
        value = float(item.get("confidence"))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def passes_quality(candidate, *, min_s: float = AUTO_MIN_S, max_s: float = AUTO_MAX_S,
                   min_words: int = AUTO_MIN_WORDS,
                   min_confidence: float = AUTO_MIN_CONFIDENCE) -> bool:
    """¿Merece entrar sola en el banco?

    Duración 2–8 s, al menos 4 palabras, confianza media de whisper ≥ 0,80 y texto con
    letras. No mira duplicados: de eso se encarga ``select_new_candidates``.
    """
    if not isinstance(candidate, dict):
        return False
    text = str(candidate.get("text") or "").strip()
    if not text or word_count(text) < max(1, int(min_words)):
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    duration = _duration(candidate)
    if duration < min_s or duration > max_s:
        return False
    return _confidence(candidate) >= min_confidence


def select_new_candidates(candidates, existing_texts=(), *, max_new: int = AUTO_MAX_NEW,
                          min_s: float = AUTO_MIN_S, max_s: float = AUTO_MAX_S,
                          min_words: int = AUTO_MIN_WORDS,
                          min_confidence: float = AUTO_MIN_CONFIDENCE) -> list[dict]:
    """Filtra por calidad y quita repetidas, contra el banco y dentro del mismo vídeo.

    Devuelve como mucho ``max_new`` candidatas, en el orden en que llegan.
    """
    limit = int(max_new)
    if limit <= 0:
        return []
    seen = {normalize_text(text) for text in existing_texts}
    seen.discard("")
    out = []
    for candidate in candidates or []:
        if not passes_quality(candidate, min_s=min_s, max_s=max_s,
                              min_words=min_words, min_confidence=min_confidence):
            continue
        key = normalize_text(candidate.get("text"))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# banco de frases
# --------------------------------------------------------------------------- #

def _read_bank(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"phrases": []}
    if isinstance(doc, list):
        doc = {"phrases": doc}
    if not isinstance(doc, dict) or not isinstance(doc.get("phrases"), list):
        doc = {"phrases": []}
    return doc


def _write_bank(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _next_id(taken: set[str], prefix: str = "c") -> str:
    pattern = re.compile(r"%s(\d+)" % re.escape(prefix))
    highest = 0
    for pid in taken:
        match = pattern.fullmatch(str(pid))
        if match:
            highest = max(highest, int(match.group(1)))
    number = highest + 1
    while "%s%03d" % (prefix, number) in taken:
        number += 1
    return "%s%03d" % (prefix, number)


def bank_texts(bank_path: Path) -> list[str]:
    """Textos del banco, para el deduplicado de la ingesta."""
    bank = _read_bank(Path(bank_path))
    return [str(item.get("text") or "") for item in bank["phrases"]
            if isinstance(item, dict)]


def _base_phrase(doc: dict, item: dict, index_path: Path, offset: float = 0.0) -> dict:
    """Metadatos comunes a los dos caminos de ingesta (CLI y YouTube)."""
    start = float(item.get("start") or 0.0) + offset
    end = float(item.get("end") or 0.0) + offset
    if end <= start:
        end = start + max(0.0, _duration(item))
    source = str(doc.get("source_url") or doc.get("source") or index_path.parent.name)
    return {
        "text": str(item.get("text") or "").strip(),
        "source": source,
        "license": str(doc.get("license") or "desconocida"),
        "speaker": str(doc.get("speaker") or source or "desconocido"),
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
    }


def _copy_and_store(bank: dict, new: list[dict], refs_dir: Path, bank_path: Path) -> list[dict]:
    """Copia los WAV y escribe el banco. Si algo falla, no deja el JSON a medias."""
    refs_dir = Path(refs_dir)
    refs_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    stored = []
    try:
        for phrase in new:
            wav = phrase.pop("_wav")
            target = refs_dir / ("%s.wav" % phrase["id"])
            shutil.copyfile(wav, target)
            copied.append(target)
            bank["phrases"].append(phrase)
            stored.append(dict(phrase))
        _write_bank(Path(bank_path), bank)
    except Exception:
        # El banco no se ha escrito (o el fallo fue antes): los WAV copiados sobran.
        bank["phrases"] = bank["phrases"][:len(bank["phrases"]) - len(stored)]
        for target in copied:
            try:
                target.unlink()
            except OSError:
                pass
        raise
    return stored


def accept_candidates(index_path: Path, numbers, refs_dir: Path,
                      bank_path: Path) -> list[dict]:
    """Copia los cortes elegidos a ``refs/`` y los añade al banco de frases.

    ``numbers`` son los ``n`` de la tabla (1-based). Valida todo antes de copiar nada y
    devuelve las frases nuevas con su ``id``.
    """
    index_path = Path(index_path)
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    raw = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        raise ValueError("el índice de candidatos no trae 'candidates'")

    by_number = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            by_number[int(item.get("n"))] = item
        except (TypeError, ValueError):
            continue

    wanted = []
    for value in numbers:
        number = int(value)
        item = by_number.get(number)
        if item is None:
            raise ValueError("no existe el candidato %s" % number)
        wav = index_path.parent / str(item.get("file") or ("%d.wav" % number))
        if not wav.is_file():
            raise ValueError("falta el WAV del candidato %s: %s" % (number, wav))
        wanted.append((number, item, wav))
    if not wanted:
        raise ValueError("no has elegido ningún candidato")

    with _BANK_LOCK:
        bank = _read_bank(bank_path)
        taken = {str(item.get("id")) for item in bank["phrases"] if isinstance(item, dict)}
        new = []
        for _number, item, wav in wanted:
            pid = _next_id(taken)
            taken.add(pid)
            phrase = {"id": pid, **_base_phrase(doc, item, index_path)}
            new.append({**phrase, "_wav": wav})
        return _copy_and_store(bank, new, refs_dir, bank_path)


def add_auto_candidates(index_path: Path, refs_dir: Path, bank_path: Path, *,
                        max_new: int = AUTO_MAX_NEW, prefix: str = "y",
                        min_s: float = AUTO_MIN_S, max_s: float = AUTO_MAX_S,
                        min_words: int = AUTO_MIN_WORDS,
                        min_confidence: float = AUTO_MIN_CONFIDENCE) -> list[dict]:
    """Añade solas las candidatas que pasan el filtro y no están ya en el banco.

    ``index_path`` puede traer ``offset_s`` (la ingesta de YouTube baja un tramo): se suma a
    ``start``/``end`` para que los tiempos apunten al vídeo original. Devuelve las frases
    añadidas, con ``id``; el filtro y el deduplicado son funciones puras aparte.
    """
    index_path = Path(index_path)
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    raw = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        raise ValueError("el índice de candidatos no trae 'candidates'")
    try:
        offset = float(doc.get("offset_s") or 0.0)
    except (TypeError, ValueError):
        offset = 0.0
    if not math.isfinite(offset):
        offset = 0.0

    with _BANK_LOCK:
        bank = _read_bank(bank_path)
        chosen = select_new_candidates(
            [item for item in raw if isinstance(item, dict)],
            bank_texts(bank_path),
            max_new=max_new, min_s=min_s, max_s=max_s,
            min_words=min_words, min_confidence=min_confidence,
        )
        if not chosen:
            return []
        taken = {str(item.get("id")) for item in bank["phrases"] if isinstance(item, dict)}
        new = []
        for item in chosen:
            number = item.get("n")
            wav = index_path.parent / str(item.get("file") or ("%s.wav" % number))
            if not wav.is_file():
                raise ValueError("falta el WAV del candidato %s: %s" % (number, wav))
            pid = _next_id(taken, prefix=prefix)
            taken.add(pid)
            phrase = {"id": pid, **_base_phrase(doc, item, index_path, offset=offset)}
            new.append({**phrase, "_wav": wav})
        return _copy_and_store(bank, new, refs_dir, bank_path)


def forget_phrase(pid, refs_dir: Path, bank_path: Path) -> dict | None:
    """Quita una frase del banco y borra su WAV. ``None`` si no existía.

    Nunca borra referencias estáticas del proyecto (solo el WAV vivo que haya en ``refs_dir``).
    """
    pid = str(pid or "").strip()
    if not _ID_RE.match(pid):
        raise ValueError("id de frase no válido: %r" % pid)
    with _BANK_LOCK:
        bank = _read_bank(Path(bank_path))
        kept = []
        removed = None
        for item in bank["phrases"]:
            if removed is None and isinstance(item, dict) and str(item.get("id")) == pid:
                removed = dict(item)
                continue
            kept.append(item)
        if removed is None:
            return None
        bank["phrases"] = kept
        _write_bank(Path(bank_path), bank)
    wav = Path(refs_dir) / ("%s.wav" % pid)
    if wav.is_file():
        try:
            wav.unlink()
        except OSError:
            pass
    return removed
