#!/usr/bin/env python3
"""Genera los 20 WAV sintéticos del test ASR (solo para probar el script, no sustituye a tu voz).

Necesita `piper` (`pip install piper-tts`, no es dependencia del proyecto) y el modelo que
indique `REFIRE_VOICE_MODEL` (o `data/voice/en_GB-alba-medium.onnx`).
"""
import importlib.util
import os
import sys
import wave
from pathlib import Path

# Modelo de voz para el WAV sintético: se pasa por entorno, nunca una ruta de una máquina.
VOICE = Path(
    os.environ.get("REFIRE_VOICE_MODEL")
    or (Path(__file__).resolve().parent.parent / "data" / "voice" / "en_GB-alba-medium.onnx")
)

spec = importlib.util.spec_from_file_location(
    "at", Path(__file__).resolve().parent / "asr_test.py")
mod = importlib.util.module_from_spec(spec)
sys.argv = ["asr_test.py"]
spec.loader.exec_module(mod)

from piper import PiperVoice  # noqa: E402

out = Path(__file__).resolve().parent.parent / "data" / "asr_test" / "synth"
out.mkdir(parents=True, exist_ok=True)
voice = PiperVoice.load(str(VOICE))
for item in mod.ITEMS:
    with wave.open(str(out / (item["id"] + ".wav")), "wb") as w:
        voice.synthesize_wav(item["text"], w, set_wav_format=True)
print("sintetizadas %d frases en %s" % (len(mod.ITEMS), out))
