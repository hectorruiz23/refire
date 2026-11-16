#!/usr/bin/env python3
"""Regenera static/mock/mock-data.js a partir de los fixtures .json.

Solo hace falta si editas un fixture: el navegador usa mock-data.js para
?mock=1 sobre file:// (donde fetch de JSON está bloqueado) y los .json
cuando se sirve por http.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ["state", "state-long", "scenario", "report", "drills-new", "audio", "audio-501", "samples"]

header = (
    "/* GENERADO desde static/mock/*.json — no editar a mano.\n"
    "   Copia inline de los fixtures para que ?mock=1 funcione también abriendo\n"
    "   index.html con file://, donde el navegador bloquea fetch() de JSON.\n"
    "   Si cambias un fixture, regenera este fichero con:\n"
    "     python3 static/mock/build.py\n"
    "   (build.py no se ejecuta en el navegador; es solo para desarrollo). */\n"
)

data = {name: json.loads((HERE / f"{name}.json").read_text(encoding="utf-8")) for name in NAMES}
out = header + "window.__MOCK__ = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
(HERE / "mock-data.js").write_text(out, encoding="utf-8")
print(f"mock-data.js escrito: {len(out)} bytes ({len(data)} fixtures)")
