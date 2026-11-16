"""Ingesta automática desde YouTube para el banco de shadowing.

Pegar una URL en la pestaña Shadowing lanza un hilo que:

1. baja **solo un tramo** de audio con ``yt-dlp`` (por defecto del minuto 1 al 6),
2. lo transcribe con ``lib/audio.transcribe_words`` (faster-whisper, como el CLI),
3. segmenta con ``lib/shadow_ingest.segment_words`` y corta los WAV con ffmpeg,
4. añade solas las que pasan el filtro de calidad y no están ya en el banco.

El estado vive en memoria (``_JOBS``): no hay base de datos ni trabajo que sobreviva a un
reinicio. La UI hace ``GET /api/shadow/ingest/<id>`` y va contando el paso.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from . import audio, config, shadow
from .shadow_ingest import (
    AUTO_MAX_NEW,
    AUTO_MAX_S,
    AUTO_MIN_S,
    add_auto_candidates,
    bank_texts,
    cut_clip,
    select_new_candidates,
    segment_words,
)

__all__ = [
    "DEFAULT_INICIO",
    "DEFAULT_FIN",
    "DOWNLOAD_TIMEOUT",
    "IngestError",
    "IngestUnavailable",
    "find_ytdlp",
    "start",
    "get",
]

DEFAULT_INICIO = 60.0
DEFAULT_FIN = 360.0
DOWNLOAD_TIMEOUT = 180
# Candidatas que se miran antes de quedarse con las 20 mejores. Así el tope de 20 no se
# gasta en frases que luego caen por duración, palabras o confianza.
MAX_SCAN = AUTO_MAX_NEW * 4

_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class IngestUnavailable(RuntimeError):
    """Falta yt-dlp o faster-whisper en el servidor (-> 503 / error legible)."""


class IngestError(RuntimeError):
    """La descarga o la transcripción falló. ``code`` lo enseña la UI."""

    def __init__(self, detail: str, code: str = "ingest_failed"):
        super().__init__(detail)
        self.code = code


# --------------------------------------------------------------------------- #
# rutas
# --------------------------------------------------------------------------- #

def _shadow_dir() -> Path:
    path = config.data_dir() / "shadow"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _downloads_dir() -> Path:
    path = _shadow_dir() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidates_dir() -> Path:
    path = _shadow_dir() / "candidates"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #

def find_ytdlp() -> str | None:
    """Ruta de ``yt-dlp``: PATH y, si falla, ``~/.local/bin``.

    El servidor no hereda el PATH del shell del dueño, así que el binario instalado con
    ``pipx``/``uv`` en ``~/.local/bin`` no se ve con ``shutil.which``. El override por
    ``REFIRE_YTDLP`` existe solo para tests y para mover la instalación de sitio.
    """
    override = (os.environ.get("REFIRE_YTDLP") or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(str(path), os.X_OK):
            return str(path)
        return None
    found = shutil.which("yt-dlp")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "yt-dlp"
    if fallback.is_file() and os.access(str(fallback), os.X_OK):
        return str(fallback)
    return None


def _seconds(value: float) -> str:
    return ("%d" % value) if float(value).is_integer() else ("%g" % value)


def _last_line(raw: str) -> str:
    lines = [" ".join(line.split()) for line in (raw or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _print_value(stdout: str, prefix: str) -> str:
    value = ""
    for line in (stdout or "").splitlines():
        if line.startswith(prefix):
            found = line[len(prefix):].strip()
            if found:
                value = found
    return "" if value in ("NA", "None", "null") else value


def download(url: str, inicio: float, fin: float, folder: Path) -> tuple[Path, dict]:
    """Baja el tramo de audio en WAV con yt-dlp. Devuelve (wav, metadatos del canal)."""
    ytdlp = find_ytdlp()
    if ytdlp is None:
        raise IngestUnavailable(
            "no se encuentra yt-dlp: instálalo en ~/.local/bin/yt-dlp o ponlo en el PATH"
        )
    if shutil.which("ffmpeg") is None:
        raise IngestUnavailable("no se encuentra ffmpeg, y yt-dlp lo necesita para sacar WAV")

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    section = "*%s-%s" % (_seconds(float(inicio)), _seconds(float(fin)))
    cmd = [
        ytdlp,
        "--no-playlist",
        "--no-progress",
        "--newline",
        "-f", "bestaudio/best",
        "-x", "--audio-format", "wav",
        "--download-sections", section,
        "-o", str(folder / "audio.%(ext)s"),
        "--no-simulate",
        "--print", "CHANNEL:%(channel,uploader|)s",
        "--print", "TITLE:%(title|)s",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise IngestUnavailable("yt-dlp desapareció antes de llamarlo: %s" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise IngestError(
            "yt-dlp tardó más de %d s en bajar el audio; prueba un tramo más corto"
            % DOWNLOAD_TIMEOUT,
            code="download_timeout",
        ) from exc
    except OSError as exc:
        raise IngestUnavailable("no se pudo ejecutar yt-dlp: %s" % str(exc)[:200]) from exc

    if proc.returncode != 0:
        detail = _last_line(proc.stderr) or _last_line(proc.stdout) or (
            "código %d" % proc.returncode)
        raise IngestError("yt-dlp no pudo bajar el vídeo: %s" % detail,
                          code="download_failed")

    wavs = sorted(folder.glob("*.wav"))
    if not wavs:
        raise IngestError(
            "yt-dlp terminó pero no dejó ningún WAV (¿vídeo no disponible o sección vacía?)",
            code="download_failed",
        )
    channel = _print_value(proc.stdout, "CHANNEL:")
    return wavs[0], {
        "speaker": channel or "YouTube",
        "title": _print_value(proc.stdout, "TITLE:"),
    }


# --------------------------------------------------------------------------- #
# trabajos en segundo plano
# --------------------------------------------------------------------------- #

def _update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _fail(job_id: str, code: str, detail: str) -> None:
    _update(job_id, status="error", paso="error",
            error={"code": code, "detail": detail[:400]})


def get(job_id: str) -> dict:
    """Copia del trabajo. ``KeyError`` si no existe (la API lo convierte en 404)."""
    with _LOCK:
        job = _JOBS.get(str(job_id))
        if job is None:
            raise KeyError(job_id)
        return json.loads(json.dumps(job, ensure_ascii=False))


def _new_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for _ in range(20):
        candidate = "ing-%s-%s" % (stamp, uuid.uuid4().hex[:6])
        with _LOCK:
            if candidate not in _JOBS:
                return candidate
    raise IngestError("no se pudo generar un id de ingesta")


def _write_json(path: Path, doc: dict) -> None:
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


def _work(job_id: str) -> None:
    job = get(job_id)
    url = job["url"]
    inicio = float(job["inicio"])
    fin = float(job["fin"])
    download_dir = _downloads_dir() / job_id
    try:
        if not audio.voice_ready():
            raise IngestUnavailable(
                "no está faster-whisper en este servidor: arráncalo con .venv/bin/python app.py"
            )

        wav, meta = download(url, inicio, fin, download_dir)

        _update(job_id, paso="transcribiendo")
        try:
            result = audio.transcribe_words(wav)
        except audio.AudioUnavailable as exc:
            raise IngestUnavailable(
                "no está faster-whisper en este servidor: arráncalo con .venv/bin/python app.py"
            ) from exc
        except audio.AudioInvalid as exc:
            raise IngestError("el audio descargado no se pudo leer: %s" % exc,
                              code="audio_invalid") from exc

        segments = segment_words(
            result.get("words") or [], min_s=AUTO_MIN_S, max_s=AUTO_MAX_S,
            max_phrases=MAX_SCAN,
        )
        bank = shadow.bank_write_path()
        chosen = select_new_candidates(segments, bank_texts(bank), max_new=AUTO_MAX_NEW)

        _update(job_id, paso="cortando", total_candidatas=len(segments))
        work = _candidates_dir() / job_id
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        candidates = []
        for number, segment in enumerate(chosen, 1):
            name = "%d.wav" % number
            cut_clip(wav, segment["start"], segment["end"], work / name)
            candidates.append({
                "n": number,
                "file": name,
                "start": segment["start"],
                "end": segment["end"],
                "duration": segment["duration"],
                "confidence": segment["confidence"],
                "text": segment["text"],
            })

        index_path = work / "index.json"
        _write_json(index_path, {
            "source": url,
            "source_url": url,
            "slug": job_id,
            "license": "youtube",
            "speaker": meta.get("speaker") or "YouTube",
            "title": meta.get("title") or "",
            "offset_s": inicio,
            "min_s": AUTO_MIN_S,
            "max_s": AUTO_MAX_S,
            "max_frases": AUTO_MAX_NEW,
            "created": datetime.now().replace(microsecond=0).isoformat(),
            "engine": result.get("engine"),
            "duration_s": result.get("duration_s"),
            "candidates": candidates,
        })

        added = add_auto_candidates(
            index_path, shadow.refs_write_dir(), bank, max_new=AUTO_MAX_NEW,
        )
        payload = [{
            "pid": phrase["id"],
            "text": phrase["text"],
            "start": phrase["start"],
            "end": phrase["end"],
            "duration": phrase["duration"],
        } for phrase in added]
        _update(job_id, status="listo", paso="listo", añadidas=len(added),
                candidatas=payload, total_candidatas=len(segments))
    except IngestUnavailable as exc:
        _fail(job_id, "ingest_unavailable", str(exc))
    except IngestError as exc:
        _fail(job_id, exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001 - una ingesta nunca tumba el servidor
        _fail(job_id, "ingest_failed", "la ingesta falló: %s" % str(exc)[:300])
    finally:
        # El vídeo descargado no se versiona ni se queda: solo viven los cortes aceptados.
        shutil.rmtree(download_dir, ignore_errors=True)


def start(url: str, inicio: float = DEFAULT_INICIO, fin: float = DEFAULT_FIN) -> dict:
    """Valida y arranca una ingesta en un hilo. Devuelve el trabajo recién creado."""
    url = str(url or "").strip()
    if not _URL_RE.match(url):
        raise ValueError("el enlace tiene que empezar por http:// o https://")

    try:
        inicio = float(inicio)
        fin = float(fin)
    except (TypeError, ValueError) as exc:
        raise ValueError("inicio y fin tienen que ser números de segundos") from exc
    if not math.isfinite(inicio) or not math.isfinite(fin):
        raise ValueError("inicio y fin tienen que ser números de segundos")
    if inicio < 0:
        raise ValueError("inicio no puede ser negativo")
    if fin <= inicio:
        raise ValueError("fin tiene que ser mayor que inicio")

    if find_ytdlp() is None:
        raise IngestUnavailable(
            "no se encuentra yt-dlp: instálalo en ~/.local/bin/yt-dlp o ponlo en el PATH"
        )

    job_id = _new_id()
    job = {
        "id": job_id,
        "url": url,
        "inicio": inicio,
        "fin": fin,
        "status": "running",
        "paso": "descargando",
        "añadidas": 0,
        "candidatas": [],
        "total_candidatas": 0,
        "error": None,
    }
    with _LOCK:
        _JOBS[job_id] = job
    threading.Thread(target=_work, args=(job_id,), daemon=True).start()
    return dict(job)
