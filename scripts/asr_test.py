#!/usr/bin/env python3
"""Test de fidelidad del ASR: ¿Whisper conserva los errores gramaticales que cometes?

Uso:
    python3 scripts/asr_test.py record     # graba 20 frases, una por una
    python3 scripts/asr_test.py analyse    # transcribe y compara dos configuraciones

Guarda los WAV en data/asr_test/ y el resultado en data/asr_test/resultado.md.
Necesita: pw-record (o arecord), ffmpeg y faster-whisper en el venv de la app.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

OUT = Path(os.environ.get("ASR_TEST_DIR")
           or (Path(__file__).resolve().parent.parent / "data" / "asr_test"))

# 12 frases con error plantado (sus patrones reales) y 8 controles correctos.
# `span` es el trozo que delata que el error sobrevivió a la transcripción.
ITEMS = [
    {"id": "e1",  "kind": "error", "span": "even it have",
     "text": "Even it have side effects, we should ship it."},
    {"id": "e2",  "kind": "error", "span": "if she say no",
     "text": "If she say no, we should wait."},
    {"id": "e3",  "kind": "error", "span": "if i would have known",
     "text": "If I would have known, I would have told you."},
    {"id": "e4",  "kind": "error", "span": "we finish",
     "text": "We finish the spike yesterday."},
    {"id": "e5",  "kind": "error", "span": "didn't had",
     "text": "We didn't had time to test the rollback."},
    {"id": "e6",  "kind": "error", "span": "would told",
     "text": "I would told her next week."},
    {"id": "e7",  "kind": "error", "span": "i am agree",
     "text": "I am agree with the idea."},
    {"id": "e8",  "kind": "error", "span": "depends of",
     "text": "It depends of the team."},
    {"id": "e9",  "kind": "error", "span": "focus in",
     "text": "We need to focus in the migration."},
    {"id": "e10", "kind": "error", "span": "to new cluster",
     "text": "We are moving the data to new cluster."},
    {"id": "e11", "kind": "error", "span": "take a decision",
     "text": "We should take a decision next week."},
    {"id": "e12", "kind": "error", "span": "do a meeting",
     "text": "I will do a meeting with the platform team."},
    {"id": "c1", "kind": "control", "text": "Even if it has side effects, we should ship it."},
    {"id": "c2", "kind": "control", "text": "If she says no, we should wait."},
    {"id": "c3", "kind": "control", "text": "We finished the spike yesterday."},
    {"id": "c4", "kind": "control", "text": "We didn't have time to test the rollback."},
    {"id": "c5", "kind": "control", "text": "It depends on the team."},
    {"id": "c6", "kind": "control", "text": "We should make a decision next week."},
    {"id": "c7", "kind": "control", "text": "I will have a meeting with the platform team."},
    {"id": "c8", "kind": "control", "text": "We are moving the data to the new cluster."},
]

# Ejemplo con disfluencias y errores: sesga el decodificador hacia transcribir literal.
INITIAL_PROMPT = ("Um, so, like, we finish the spike yesterday and it depends of the team, "
                  "so I would told her next week, even it have side effects.")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def recorder() -> list[str]:
    if Path("/usr/sbin/pw-record").exists() or Path("/usr/bin/pw-record").exists():
        return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
    return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav"]


def record() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = recorder()
    print("Grabando %d frases en %s" % (len(ITEMS), OUT))
    print("Enter para empezar a hablar, Enter para cortar. Ctrl-C para salir a mitad.\n")
    for i, item in enumerate(ITEMS, 1):
        target = OUT / ("%s.wav" % item["id"])
        if target.exists():
            print("[%d/%d] %s ya existe, salto" % (i, len(ITEMS), item["id"]))
            continue
        print("[%d/%d] %s" % (i, len(ITEMS), item["text"]))
        input("       Enter para grabar…")
        proc = subprocess.Popen(cmd + [str(target)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        input("       habla ahora — Enter para cortar…")
        time.sleep(0.15)
        # SIGINT y no SIGTERM: pw-record cierra el WAV (y su cabecera) de forma ordenada así.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        size = target.stat().st_size if target.exists() else 0
        print("       %s  (%.1f s)\n" % ("ok" if size > 8000 else "¡VACÍO, repite!", size / 32000))
        if size <= 8000:
            target.unlink(missing_ok=True)
    print("Listo. Ahora:  python3 scripts/asr_test.py analyse")
    return 0


def audio_array(path: Path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "quiet", "-i", str(path),
                          "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def analyse() -> int:
    from faster_whisper import WhisperModel

    wavs = {item["id"]: OUT / ("%s.wav" % item["id"]) for item in ITEMS}
    missing = [k for k, p in wavs.items() if not p.exists()]
    if missing:
        print("Faltan grabaciones: %s" % ", ".join(missing))
        print("Corre antes:  python3 scripts/asr_test.py record")
        return 2

    t0 = time.monotonic()
    model = WhisperModel("small", device="cpu", compute_type="int8")
    load = time.monotonic() - t0

    configs = {
        "por defecto": {},
        "initial_prompt": {"initial_prompt": INITIAL_PROMPT},
    }
    results = {name: {} for name in configs}
    timings = {name: [] for name in configs}

    for name, extra in configs.items():
        for item in ITEMS:
            audio = audio_array(wavs[item["id"]])
            t = time.monotonic()
            segs, _info = model.transcribe(
                audio, language="en", beam_size=5, temperature=0.0,
                condition_on_previous_text=False, word_timestamps=False, **extra)
            hyp = " ".join(s.text.strip() for s in segs)
            timings[name].append(time.monotonic() - t)
            results[name][item["id"]] = hyp

    lines = ["# Test de fidelidad del ASR", "",
             "Carga del modelo: %.2f s  ·  %d grabaciones  ·  %s" %
             (load, len(ITEMS), time.strftime("%Y-%m-%d %H:%M")), ""]
    summary = {}
    for name in configs:
        kept = repaired = altered = 0
        controls_ok = 0
        for item in ITEMS:
            hyp = results[name][item["id"]]
            if item["kind"] == "error":
                if item["span"] in norm(hyp):
                    kept += 1
                elif norm(item["text"]) in norm(hyp) or SequenceMatcher(
                        None, norm(item["text"]), norm(hyp)).ratio() > 0.9:
                    repaired += 1
                else:
                    altered += 1
            else:
                if norm(item["text"]) == norm(hyp):
                    controls_ok += 1
        summary[name] = (kept, repaired, altered, controls_ok)

    for name, (kept, repaired, altered, controls_ok) in summary.items():
        lines += ["## Configuración: %s" % name, "",
                  "- **Errores conservados: %d/12**" % kept,
                  "- Reparados (el error desaparece): %d/12" % repaired,
                  "- Transcritos de otra forma (ni error ni frase correcta): %d/12" % altered,
                  "- Controles intactos: %d/8" % controls_ok,
                  "- Latencia media por frase: %.2f s" % (sum(timings[name]) / len(timings[name])),
                  "", "| id | esperado | transcrito | resultado |", "|---|---|---|---|"]
        for item in ITEMS:
            hyp = results[name][item["id"]]
            if item["kind"] == "error":
                verdict = "conserva" if item["span"] in norm(hyp) else "REPARA/PIERDE"
            else:
                verdict = "intacto" if norm(item["text"]) == norm(hyp) else "ALTERA"
            lines.append("| %s | %s | %s | %s |" % (item["id"], item["text"], hyp or "(vacío)", verdict))
        lines.append("")

    best = max(summary, key=lambda n: (summary[n][0], summary[n][3]))
    lines += ["## Veredicto", "",
              "Mejor configuración: **%s** (conserva %d/12 errores y %d/8 controles)." %
              (best, summary[best][0], summary[best][3]), "",
              "Umbral acordado: si conserva **≥ 8/12**, el informe por transcripción sirve.", ""]

    (OUT / "resultado.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4]))
    for name, (kept, repaired, altered, controls_ok) in summary.items():
        print("  %-16s errores %d/12 · reparados %d · otros %d · controles %d/8"
              % (name, kept, repaired, altered, controls_ok))
    print("\nMejor: %s. Detalle en %s" % (best, OUT / "resultado.md"))
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "record":
        sys.exit(record())
    if action == "analyse":
        sys.exit(analyse())
    print(__doc__)
    sys.exit(1)
