"""Audio de drills: multipart manual + transcripción local si existe faster-whisper.

Si faster-whisper no está instalado NO se instala: la API responde 501
``audio_unavailable`` y la UI cae al modo escrito (BRIEF 5).

Decodificación: NO se usa PyAV. faster-whisper 1.2.1 llama a ``av.open(...,
metadata_errors=...)``, un parámetro que PyAV 19 quitó, y PyAV 13 (que sí lo tenía) no tiene
rueda para Python 3.14. Así que el audio se normaliza con **ffmpeg** a WAV mono 16 kHz y se le
pasa a whisper como array float32: sin PyAV, y de paso el conteo de pausas funciona con
cualquier formato que grabe el navegador (webm/opus), no solo con WAV.
"""
from __future__ import annotations

import array
import re
import shutil
import subprocess
import wave
from pathlib import Path

from . import config

__all__ = [
    "AudioUnavailable",
    "AudioInvalid",
    "parse_multipart",
    "transcribe",
    "transcribe_words",
    "voice_ready",
    "count_pauses_wav",
    "extract",
]

MODEL_SIZE = "small"
PAUSE_MS = 700
SAMPLE_RATE = 16000
FFMPEG_TIMEOUT = 180
MIN_SECONDS = 0.35


class AudioUnavailable(RuntimeError):
    """El motor no está instalado (501): no es culpa del audio."""


class AudioInvalid(RuntimeError):
    """El audio llegó vacío, no se pudo decodificar o no tiene voz (422)."""


def _whisper():
    try:
        from faster_whisper import WhisperModel  # noqa: WPS433 (import perezoso)
    except Exception as exc:  # ImportError y fallos de librerías nativas
        raise AudioUnavailable("faster-whisper no instalado") from exc
    return WhisperModel


_MODEL = None


def _decode16k(path: Path):
    """Normaliza a WAV mono 16 kHz. Devuelve (ruta_wav, es_temporal)."""
    path = Path(path)
    if shutil.which("ffmpeg") is None:
        if path.suffix.lower() == ".wav":
            return path, False
        raise AudioUnavailable("ffmpeg no instalado y el audio no es WAV")
    out = path.with_name(path.name + ".16k.wav")
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "wav", str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioInvalid("ffmpeg falló: %s" % str(exc)[:200]) from exc
    if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
        detail = proc.stderr.decode("utf-8", "replace")[:200]
        raise AudioInvalid("el audio no se pudo decodificar: %s" % detail)
    return out, True


def _float32_samples(wav_path: Path):
    """Lee el WAV normalizado y devuelve float32 mono en [-1, 1] para whisper."""
    import numpy as np  # dependencia de faster-whisper, ya presente

    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise AudioInvalid("se esperaba PCM 16 bits, llegó %d bytes/muestra" % width)
    data = np.frombuffer(frames[: len(frames) - (len(frames) % 2)], dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels)[:, 0]
    return (data.astype(np.float32) / 32768.0)


def _model(size: str | None = None, local_files_only: bool = False):
    """Carga perezosa del modelo. La comparten ``transcribe`` y ``transcribe_words``."""
    global _MODEL
    if _MODEL is None:
        WhisperModel = _whisper()
        _MODEL = WhisperModel(
            size or MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            local_files_only=local_files_only,
        )
    return _MODEL


def voice_ready() -> bool:
    """¿Está faster-whisper en este intérprete? Lo usa la ingesta de YouTube antes de bajar."""
    try:
        _whisper()
        return True
    except AudioUnavailable:
        return False


def transcribe(path: Path) -> dict:
    """Transcribe cualquier audio decodificable por ffmpeg. Nunca devuelve texto inventado."""
    wav, temp = _decode16k(Path(path))
    try:
        samples = _float32_samples(wav)
        segments, info = _model().transcribe(
            samples,
            language="en",
            vad_filter=True,
            beam_size=1,                     # frase corta: greedy es ~2x más rápido
            condition_on_previous_text=False,  # evita bucles de alucinación en clips sueltos
        )
        parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        return {
            "transcript": " ".join(parts).strip(),
            "duration_s": round(duration, 2) if duration else 0.0,
            "engine": "faster-whisper:%s" % MODEL_SIZE,
        }
    finally:
        if temp:
            try:
                wav.unlink()
            except OSError:
                pass


def transcribe_words(path: Path, size: str | None = None,
                     local_files_only: bool = True) -> dict:
    """Igual que ``transcribe`` pero con los tiempos de cada palabra.

    Misma normalización a WAV 16 kHz y mismo modelo que ``transcribe``. Devuelve las
    palabras en el orden del audio con ``word``, ``start``, ``end`` y ``probability``.
    """
    wav, temp = _decode16k(Path(path))
    try:
        samples = _float32_samples(wav)
        segments, info = _model(size, local_files_only=local_files_only).transcribe(
            samples,
            language="en",
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        words = []
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                text = str(getattr(word, "word", "") or "").strip()
                if not text:
                    continue
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if start is None or end is None or float(end) <= float(start):
                    continue
                probability = getattr(word, "probability", 1.0)
                words.append({
                    "word": text,
                    "start": round(float(start), 3),
                    "end": round(float(end), 3),
                    "probability": round(float(probability or 0.0), 3),
                })
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        return {
            "words": words,
            "duration_s": round(duration, 2) if duration else 0.0,
            "engine": "faster-whisper:%s" % (size or MODEL_SIZE),
        }
    finally:
        if temp:
            try:
                wav.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# multipart (sin cgi, que está deprecado y fuera en 3.13+)
# --------------------------------------------------------------------------- #

_BOUNDARY_RE = re.compile(r'boundary="?([^";]+)"?', re.I)


def parse_multipart(content_type: str, body: bytes):
    m = _BOUNDARY_RE.search(content_type or "")
    if not m:
        raise ValueError("sin boundary en Content-Type")
    boundary = ("--" + m.group(1)).encode("latin-1")
    fields = {}
    files = {}
    for chunk in body.split(boundary):
        if not chunk or chunk in (b"--\r\n", b"--", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        if chunk.startswith(b"--"):
            continue
        head, _, payload = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        payload = payload.rstrip(b"\r\n")
        text = head.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]*)"', text)
        file_m = re.search(r'filename="([^"]*)"', text)
        if not name_m:
            continue
        name = name_m.group(1)
        if file_m:
            files[name] = {"filename": file_m.group(1), "data": payload}
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def extract(item_id: str, filename: str, data: bytes) -> dict:
    """Guarda el audio bajo data/tmp, lo normaliza y transcribe.

    AudioUnavailable = falta el motor; AudioInvalid = el audio no sirve (vacío, sin voz, corto).
    """
    _whisper()  # si el motor no está, 501 manda sobre cualquier queja del audio
    if not data:
        raise AudioInvalid("la grabación llegó vacía: pulsa el micro, habla y vuelve a pulsar")
    if len(data) < 1200:
        raise AudioInvalid("la grabación es demasiado corta (%d bytes)" % len(data))
    suffix = Path(filename or "audio.webm").suffix or ".webm"
    if suffix.lower() not in (".webm", ".wav", ".ogg", ".m4a", ".mp3", ".opus"):
        suffix = ".webm"
    tmp_dir = config.data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", item_id or "drill")
    path = tmp_dir / ("%s%s" % (safe_id, suffix))
    path.write_bytes(data)
    wav = None
    try:
        result = transcribe(path)
        wav, temp = _decode16k(path)
        result["pauses"] = count_pauses_wav(wav)
        if temp:
            try:
                wav.unlink()
            except OSError:
                pass
        return result
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def count_pauses_wav(path: Path, pause_ms: int = PAUSE_MS) -> int:
    """Cuenta silencios > pause_ms. Con la normalización a WAV vale cualquier formato."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            width = wf.getsampwidth()
            channels = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, OSError, EOFError):
        return 0
    if not rate or width != 2 or not frames:
        return 0
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return 0
    threshold = 500  # amplitud mínima para considerar habla
    window = max(1, int(rate * 0.02))  # 20 ms
    silent_run = 0
    pauses = 0
    for start in range(0, len(samples), window):
        block = samples[start:start + window]
        peak = max((abs(v) for v in block), default=0)
        if peak < threshold:
            silent_run += len(block)
            continue
        if silent_run * 1000.0 / rate >= pause_ms:
            pauses += 1
        silent_run = 0
    return pauses  # el silencio final no es una pausa de turno
