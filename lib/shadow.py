"""Shadowing: banco de frases, tomas y curvas de melodía.

El análisis NO vive aquí: se llama por subproceso a un analizador externo con su propio
venv (torch + librosa), una línea JSON de entrada y una de salida. Si ese entorno no está,
``ShadowUnavailable`` -> 503 y la app sigue funcionando; la toma queda guardada con error.

Las curvas son 120 puntos en semitonos sobre la mediana de cada hablante (``None`` donde no
hay voz). No se calcula ni se devuelve ningún rango: el mismo audio da cifras distintas según
los parámetros del estimador y solo la forma es comparable (ver SHADOWING-BRIEF).
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import wave
from datetime import datetime
from pathlib import Path

from . import config
from .shadow_ingest import forget_phrase as _forget_phrase

__all__ = [
    "ShadowInvalid",
    "ShadowNotFound",
    "ShadowUnavailable",
    "phrases",
    "phrase",
    "ref_path",
    "bank_write_path",
    "refs_write_dir",
    "forget",
    "save_take",
    "retry",
    "take",
    "take_detail",
    "take_wav",
    "word_path",
    "worker_problem",
]

POINTS = 120
MAX_UPLOAD = 4 * 1024 * 1024
WORKER_TIMEOUT = 60
ATTEMPTS = 2
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_LOCK = threading.Lock()


class ShadowInvalid(ValueError):
    """Petición o audio que no sirve. ``code`` decide el código de la API."""

    def __init__(self, detail: str, code: str = "audio_invalid"):
        super().__init__(detail)
        self.code = code


class ShadowNotFound(LookupError):
    """No existe la toma pedida (-> 404)."""


class ShadowUnavailable(RuntimeError):
    """Falta el intérprete o el worker del analizador (-> 503). La toma ya está guardada."""

    def __init__(self, detail: str, take_id: str | None = None):
        super().__init__(detail)
        self.take_id = take_id


class _WorkerUnavailable(RuntimeError):
    """El binario desapareció entre la comprobación y la llamada."""


class _WorkerTimeout(RuntimeError):
    """El worker agotó el tiempo. No se cae a ``curvas``: serían otros 60 s."""


# --------------------------------------------------------------------------- #
# rutas
# --------------------------------------------------------------------------- #

def _shadow_dir() -> Path:
    p = config.data_dir() / "shadow"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _audio_dir() -> Path:
    p = _shadow_dir() / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _takes_path() -> Path:
    return _shadow_dir() / "takes.json"


def _static_root() -> Path:
    """Copia de los datos estáticos en el proyecto, por si el data/ de ejecución es otro.

    ``REFIRE_DATA`` puede apuntar a un directorio temporal (tests) o a otro sitio; el
    banco y las referencias son activos del proyecto, no datos que escriba la app.
    """
    return config.APP / "data" / "shadow"


def _bank_path() -> Path:
    live = _shadow_dir() / "phrases.json"
    if live.is_file():
        return live
    return _static_root() / "phrases.json"


def bank_write_path() -> Path:
    """``phrases.json`` que se puede escribir; si no existe, se siembra del banco estático.

    La app de ejecución puede tener un ``data/`` distinto del proyecto (tests). Sin esta
    copia, la primera ingesta escribiría un banco nuevo y se perderían p1–p4.
    """
    live = _shadow_dir() / "phrases.json"
    if not live.is_file():
        seed = _static_root() / "phrases.json"
        if seed.is_file() and seed.resolve() != live.resolve():
            shutil.copyfile(seed, live)
    return live


def refs_write_dir() -> Path:
    """``refs/`` vivo donde se copian los WAV nuevos."""
    path = _shadow_dir() / "refs"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# banco de frases
# --------------------------------------------------------------------------- #

def phrases() -> list[dict]:
    path = _bank_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = doc.get("phrases") if isinstance(doc, dict) else doc
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        if not pid or not text or not ID_RE.match(pid):
            continue
        item_out = {
            "id": pid,
            "text": text,
            "ref": "/media/shadow/ref/%s" % pid,
            "has_ref": ref_path(pid) is not None,
        }
        # Procedencia de los clips ingeridos. El banco antiguo no la trae y sigue igual.
        for key in ("source", "source_url", "license", "speaker"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                item_out[key] = value.strip()
        for key in ("start", "end"):
            value = item.get(key)
            if _finite(value):
                item_out[key] = round(float(value), 3)
        if "start" in item_out and "end" in item_out and item_out["end"] > item_out["start"]:
            item_out["duration"] = round(item_out["end"] - item_out["start"], 2)
        out.append(item_out)
    return out


def phrase(pid: str) -> dict:
    pid = str(pid or "").strip()
    for item in phrases():
        if item["id"] == pid:
            return item
    raise ShadowInvalid("no existe la frase %r" % pid, code="bad_request")


def ref_path(pid: str) -> Path | None:
    """WAV de la frase: primero el ``refs/`` vivo, luego el banco estático del proyecto.

    Se mira fichero a fichero (no "si hay algún WAV vivo, usa solo ese directorio"): tras
    una ingesta conviven referencias nuevas en ``data/`` y las p1–p4 del proyecto.
    """
    if not ID_RE.match(str(pid or "")):
        return None
    name = "%s.wav" % pid
    live = _shadow_dir() / "refs" / name
    if live.is_file():
        return live
    static = _static_root() / "refs" / name
    return static if static.is_file() else None


def forget(pid: str) -> dict | None:
    """Quita una frase del banco vivo y borra su WAV. ``None`` si no existía."""
    return _forget_phrase(pid, refs_write_dir(), bank_write_path())


# --------------------------------------------------------------------------- #
# tomas
# --------------------------------------------------------------------------- #

def _load() -> dict:
    path = _takes_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"takes": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("takes"), dict):
        return {"takes": {}}
    return doc


def _store(doc: dict) -> None:
    path = _takes_path()
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


def _update(take_id: str, **fields) -> dict | None:
    with _LOCK:
        doc = _load()
        item = doc["takes"].get(take_id)
        if item is None:
            return None
        item.update(fields)
        _store(doc)
        return dict(item)


def take(take_id: str) -> dict:
    if not ID_RE.match(str(take_id or "")):
        raise ShadowNotFound(take_id)
    with _LOCK:
        item = _load()["takes"].get(take_id)
    if item is None:
        raise ShadowNotFound(take_id)
    return dict(item)


def take_detail(take_id: str) -> dict:
    """La toma tal cual se guardó, más la comparación calculada con la anterior.

    La comparación no se persiste: se recalcula en cada lectura para que una toma nueva no
    obligue a reescribir el histórico. ``compare`` es solo una pista para la UI; los números
    siguen siendo los que devolvió el worker en cada toma.
    """
    if not ID_RE.match(str(take_id or "")):
        raise ShadowNotFound(take_id)
    with _LOCK:
        doc = _load()
        item = doc["takes"].get(take_id)
        if item is None:
            raise ShadowNotFound(take_id)
        out = dict(item)
        out["compare"] = _comparison(doc, item)
    return out


def _comparison(doc: dict, item: dict) -> dict:
    """Compara con la última toma *analizada* de la misma frase.

    - ``first``: no hay ninguna anterior de esa frase.
    - ``same_mic``: la anterior es del mismo micro; trae ``before``.
    - ``other_mic``: la anterior es de otro micro (no se comparan porcentajes).
    - ``unknown_mic``: la anterior no guardó micro (tomas antiguas).
    - ``no_score``: la anterior es del mismo micro pero no trajo número (worker viejo).
    - ``no_dev``: esta toma no guardó micro, así que no se puede afirmar que sea el mismo.
    """
    previous = None
    for candidate in doc.get("takes", {}).values():
        if candidate.get("id") == item.get("id"):
            continue
        if candidate.get("pid") != item.get("pid"):
            continue
        if candidate.get("status") != "listo":
            continue
        if previous is None or (candidate.get("created") or "") >= (previous.get("created") or ""):
            previous = candidate

    if previous is None:
        return {"kind": "first"}

    mine = item.get("dev")
    theirs = previous.get("dev")
    if not mine:
        return {"kind": "no_dev"}
    if not theirs:
        return {"kind": "unknown_mic"}
    if mine != theirs:
        return {"kind": "other_mic", "dev": theirs}

    before = (previous.get("result") or {}).get("sonidos_bien")
    if isinstance(before, bool) or not isinstance(before, int):
        return {"kind": "no_score", "dev": theirs}
    return {"kind": "same_mic", "before": before, "dev": theirs}


def take_wav(take_id: str) -> Path | None:
    path = _audio_dir() / ("%s.wav" % take_id)
    return path if path.is_file() else None


def _clean_dev(dev) -> str | None:
    """Nombre de micro normalizado. ``track.label`` puede traer espacios o venir vacío."""
    if dev is None:
        return None
    clean = " ".join(str(dev).split())
    return clean[:120] or None


def _new_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for _ in range(20):
        candidate = "%s-%s" % (stamp, secrets.token_hex(3))
        if candidate not in _load()["takes"]:
            return candidate
    raise ShadowInvalid("no se pudo generar un id de toma", code="internal")


def save_take(pid: str, filename: str, data: bytes, dev: str | None = None) -> dict:
    """Valida, convierte a WAV 16 kHz mono y lanza el análisis en segundo plano.

    ``dev`` es el nombre del micro tal cual lo da ``track.label`` en el navegador; se guarda
    con la toma porque la comparación solo vale si la toma anterior es del mismo micro. Si no
    llega, la toma se guarda igual y la UI dirá que no se puede comparar.

    Si falta el worker, guarda la toma con estado ``error`` y lanza ``ShadowUnavailable``
    (la API responde 503 pero el audio queda en disco para poder reintentar).
    """
    phrase(pid)  # valida el id contra el banco
    if not data:
        raise ShadowInvalid("la grabación llegó vacía: pulsa grabar y vuelve a intentarlo",
                            code="bad_request")
    if len(data) > MAX_UPLOAD:
        raise ShadowInvalid("la grabación supera los %d bytes" % MAX_UPLOAD, code="too_large")

    take_id = _new_id()
    suffix = Path(filename or "toma.webm").suffix.lower()
    if suffix not in (".webm", ".wav", ".ogg", ".m4a", ".mp3", ".opus", ".mp4"):
        suffix = ".webm"
    tmp_dir = _shadow_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload = tmp_dir / ("%s%s" % (take_id, suffix))
    wav = _audio_dir() / ("%s.wav" % take_id)
    upload.write_bytes(data)
    try:
        _to_wav(upload, wav)
    except ShadowInvalid:
        try:
            wav.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            upload.unlink()
        except OSError:
            pass

    item = {
        "id": take_id,
        "pid": pid,
        "text": phrase(pid)["text"],
        "created": datetime.now().replace(microsecond=0).isoformat(),
        "duration_s": _wav_duration(wav),
        "dev": _clean_dev(dev),
        "status": "pendiente",
        "result": None,
        "error": None,
    }
    with _LOCK:
        doc = _load()
        doc["takes"][take_id] = item
        _store(doc)

    problem = worker_problem()
    if problem:
        _update(take_id, status="error",
                error={"code": "shadow_unavailable", "detail": problem})
        raise ShadowUnavailable(problem, take_id)

    threading.Thread(target=_analyze, args=(take_id,), daemon=True).start()
    return dict(item)


def retry(take_id: str) -> dict:
    """Relanza el análisis de una toma ya guardada (botón reintentar de la UI)."""
    item = take(take_id)
    if item.get("status") == "pendiente":
        return item
    wav = take_wav(take_id)
    if wav is None:
        _update(take_id, status="error",
                error={"code": "take_missing", "detail": "falta el WAV de la toma"})
        raise ShadowInvalid("falta el WAV de la toma", code="take_missing")
    problem = worker_problem()
    if problem:
        _update(take_id, status="error",
                error={"code": "shadow_unavailable", "detail": problem})
        raise ShadowUnavailable(problem, take_id)
    item = _update(take_id, status="pendiente", result=None, error=None) or item
    threading.Thread(target=_analyze, args=(take_id,), daemon=True).start()
    return item


# --------------------------------------------------------------------------- #
# conversión a WAV
# --------------------------------------------------------------------------- #

def _to_wav(src: Path, dst: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        if src.suffix.lower() == ".wav" and _is_wav_16k_mono(src):
            shutil.copyfile(src, dst)
            return
        raise ShadowInvalid("ffmpeg no está instalado y la toma no es WAV 16 kHz mono")
    cmd = [
        ffmpeg, "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src), "-ac", "1", "-ar", "16000", "-f", "wav", str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=WORKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShadowInvalid("ffmpeg no pudo convertir la toma: %s" % str(exc)[:160]) from exc
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 44:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
        raise ShadowInvalid("el audio no se pudo decodificar: %s" % (detail or "vacío"))


def _is_wav_16k_mono(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wf:
            return (wf.getframerate() == 16000 and wf.getnchannels() == 1
                    and wf.getsampwidth() == 2 and wf.getnframes() > 0)
    except (wave.Error, OSError, EOFError):
        return False


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            return round(wf.getnframes() / rate, 2) if rate else 0.0
    except (wave.Error, OSError, EOFError):
        return 0.0


# --------------------------------------------------------------------------- #
# worker
# --------------------------------------------------------------------------- #

def worker_problem() -> str | None:
    """Motivo legible si el analizador no está configurado o no existe, o None si vale."""
    python = config.shadow_python()
    worker = config.shadow_worker()
    if python is None or worker is None:
        return ("el análisis de shadowing no está configurado: define REFIRE_SHADOW_PYTHON "
                "y REFIRE_SHADOW_WORKER")
    if not python.is_file() or not os.access(str(python), os.X_OK):
        return "no está el intérprete del analizador de shadowing: %s" % python
    if not worker.is_file():
        return "no está el analizador de shadowing: %s" % worker
    return None


def _run_worker(entry: dict) -> dict:
    """Una línea JSON al analizador, una línea de vuelta. Único camino al análisis."""
    python = config.shadow_python()
    worker = config.shadow_worker()
    if python is None or worker is None:
        raise _WorkerUnavailable("el análisis de shadowing no está configurado")
    request = json.dumps(entry) + "\n"
    # numba (dentro de librosa) cachea funciones compiladas. Si el venv del analizador está en un
    # sitio de solo lectura, sus localizadores fallan al importar librosa; apuntando su caché
    # de usuario a data/tmp el worker funciona igual y no escribe fuera de APP/data (BRIEF 2).
    cache = config.data_dir() / "tmp" / "numba-cache"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        cache = None
    env = os.environ.copy()
    if cache is not None:
        env["XDG_CACHE_HOME"] = str(cache)
    try:
        proc = subprocess.run(
            [str(python), str(worker)],
            input=request, capture_output=True, text=True, timeout=WORKER_TIMEOUT, env=env,
        )
    except FileNotFoundError as exc:
        raise _WorkerUnavailable(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise _WorkerTimeout("el worker tardó más de %d s" % WORKER_TIMEOUT) from exc
    except OSError as exc:
        raise _WorkerUnavailable(str(exc)) from exc

    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if payload is None:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError("respuesta del worker ilegible%s" % (
            ": " + detail[-1][:200] if detail else ""))
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "el worker devolvió error")[:300])
    return payload


def _call_worker(wav: Path, ref: Path, text: str | None = None,
                 job: str = "shadow") -> dict:
    entry = {"job": job, "wav": str(wav), "ref": str(ref)}
    if job == "shadow":
        entry["text"] = str(text or "")
    return _run_worker(entry)


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019-]{0,40}")


def word_path(word: str) -> Path:
    """Audio de una sola palabra con voz nativa, cacheado: sirve para oír «cómo debería sonar»."""
    clean = (word or "").strip().strip(".,!?;:\u00ab\u00bb\"'()")
    if not WORD_RE.fullmatch(clean):
        raise ShadowInvalid("palabra no válida: %r" % (word or ""), "bad_word")
    slug = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-") or "palabra"
    folder = _shadow_dir() / "words"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / ("%s.wav" % slug)
    if out.exists() and out.stat().st_size > 2000:
        return out
    _run_worker({"job": "palabra", "word": clean, "out": str(out)})
    if not out.exists() or out.stat().st_size <= 44:
        raise RuntimeError("el worker no generó el audio de %r" % clean)
    return out


def _analyze(take_id: str) -> None:
    item = None
    try:
        item = take(take_id)
    except ShadowNotFound:
        return
    wav = take_wav(take_id)
    ref = ref_path(item.get("pid") or "")
    if wav is None:
        _update(take_id, status="error",
                error={"code": "take_missing", "detail": "falta el WAV de la toma"})
        return
    if ref is None:
        _update(take_id, status="error", error={
            "code": "reference_missing",
            "detail": "no hay referencia de audio para esta frase",
        })
        return

    problem = worker_problem()
    if problem:
        _update(take_id, status="error",
                error={"code": "shadow_unavailable", "detail": problem})
        return

    text = item.get("text") or ""
    last = "error desconocido"
    for _attempt in range(ATTEMPTS):
        try:
            payload = _call_worker(wav, ref, text, job="shadow")
            result = _normalize_result(payload)
        except _WorkerUnavailable as exc:
            _update(take_id, status="error",
                    error={"code": "shadow_unavailable", "detail": str(exc)[:300]})
            return
        except _WorkerTimeout as exc:
            last = str(exc)[:300]
            continue
        except Exception as exc:  # noqa: BLE001 - el análisis nunca tumba el servidor
            # Worker viejo: no conoce ``shadow``. Se vuelve al contrato de ``curvas``; la toma
            # se guarda con las curvas y sin número (la UI esconde el bloque de sonidos).
            first = str(exc)[:300] or exc.__class__.__name__
            try:
                payload = _call_worker(wav, ref, text, job="curvas")
                result = _normalize_result(payload)
                result["fallback"] = True
            except _WorkerUnavailable as exc2:
                _update(take_id, status="error",
                        error={"code": "shadow_unavailable", "detail": str(exc2)[:300]})
                return
            except _WorkerTimeout as exc2:
                last = str(exc2)[:300]
                continue
            except Exception as exc2:  # noqa: BLE001
                last = "%s; curvas: %s" % (first, str(exc2)[:200])
                continue

        mine = result["mio"]["f0"]
        if not any(v is not None for v in mine):
            _update(take_id, status="error", error={
                "code": "no_voice",
                "detail": "no se ha detectado voz en tu toma",
            })
            return
        if not any(v is not None for v in result["ref"]["f0"]):
            _update(take_id, status="error", error={
                "code": "reference_silent",
                "detail": "la referencia no tiene voz detectable",
            })
            return
        _update(take_id, status="listo", result=result, error=None)
        return

    _update(take_id, status="error",
            error={"code": "analysis_failed", "detail": last})


def _normalize_result(payload: dict) -> dict:
    result = {"mio": _normalize_curve(payload.get("mio"), "mio"),
              "ref": _normalize_curve(payload.get("ref"), "ref")}

    sonidos = _as_number(payload.get("sonidos_bien"))
    if sonidos is not None:
        result["sonidos_bien"] = max(0, min(100, int(round(sonidos))))
    score = _as_number(payload.get("score"))
    if score is not None:
        result["score"] = round(score, 2)
    if isinstance(payload.get("transcrito"), str):
        result["transcrito"] = payload["transcrito"].strip()
    fonemas = payload.get("fonemas")
    if isinstance(fonemas, list):
        result["fonemas"] = _normalize_phonemes(fonemas)
    return result


def _as_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_phonemes(items: list) -> list[dict]:
    """Máximo 20 fallos guardados; la UI enseña como mucho 4."""
    out = []
    for entry in items[:20]:
        if not isinstance(entry, dict):
            continue
        word = " ".join(str(entry.get("palabra") or "").split())
        expected = " ".join(str(entry.get("de") or "").split())
        heard = " ".join(str(entry.get("a") or "").split())
        if not word and not expected and not heard:
            continue
        out.append({"palabra": word, "de": expected, "a": heard})
    return out


def _normalize_curve(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError("falta la curva %s" % name)
    f0 = value.get("f0")
    if not isinstance(f0, list) or len(f0) != POINTS:
        raise ValueError("la curva %s no trae %d puntos" % (name, POINTS))
    clean = []
    for point in f0:
        if point is None or isinstance(point, bool):
            clean.append(None)
            continue
        try:
            number = float(point)
        except (TypeError, ValueError):
            clean.append(None)
            continue
        clean.append(round(number, 2) if math.isfinite(number) else None)
    duration = value.get("dur")
    median = value.get("med")
    return {
        "f0": clean,
        "dur": round(float(duration), 2) if _finite(duration) else None,
        "med": round(float(median), 1) if _finite(median) else None,
    }


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
