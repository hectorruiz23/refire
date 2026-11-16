"""Única puerta al modelo: call_agent(prompt) -> str.

El modelo se enchufa con un comando, sin integración propia:
  REFIRE_AGENT_FIXTURE=/ruta/fichero  -> devuelve el contenido de ese fichero.
  REFIRE_AGENT_CMD="comando --flag"   -> ejecuta ese comando; si contiene
                                               {prompt} se sustituye, si no, el
                                               prompt se pasa como último argumento
                                               (timeout 120 s).
Sin ninguna de las dos no hay modelo: AgentError, y quien llama decide (el escenario
cae a la reserva local y el informe guarda el aviso).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

TIMEOUT_S = 120

__all__ = ["AgentError", "call_agent", "extract_json", "agent_mode"]


class AgentError(RuntimeError):
    pass


def agent_mode() -> str:
    if os.environ.get("REFIRE_AGENT_FIXTURE"):
        return "fixture"
    if os.environ.get("REFIRE_AGENT_CMD"):
        return "cmd"
    return "none"


def call_agent(prompt: str) -> str:
    mode = agent_mode()
    if mode == "fixture":
        path = Path(os.environ["REFIRE_AGENT_FIXTURE"]).expanduser()
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentError("fixture ilegible: %s" % exc) from exc
    if mode == "cmd":
        argv = _cmd_argv(os.environ["REFIRE_AGENT_CMD"], prompt)
        last = None
        for _attempt in range(2):  # un reintento como mucho, igual que antes
            try:
                return _run(argv)
            except AgentError as exc:
                last = exc
        raise last or AgentError("el comando del modelo falló")
    raise AgentError(
        "no hay modelo configurado: define REFIRE_AGENT_CMD con el comando que consulta al modelo"
    )


def _cmd_argv(template: str, prompt: str):
    if "{prompt}" in template:
        parts = shlex.split(template)
        return [p.replace("{prompt}", prompt) for p in parts]
    return shlex.split(template) + [prompt]


def _run(argv) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise AgentError("comando no encontrado: %s" % argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentError("timeout de %ss" % TIMEOUT_S) from exc
    except OSError as exc:
        raise AgentError("no se pudo ejecutar: %s" % exc) from exc
    if proc.returncode != 0:
        raise AgentError(
            "salida %s: %s" % (proc.returncode, (proc.stderr or "").strip()[:400])
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise AgentError("respuesta vacía")
    return out


def extract_json(text: str):
    """Extrae el primer objeto JSON válido de una respuesta de texto libre."""
    if text is None:
        raise AgentError("respuesta vacía")
    s = text.strip()
    fence = s.find("```")
    candidates = [s]
    if fence != -1:
        # quita vallas ```json ... ``` por si acaso
        body = s[fence + 3:]
        body = body.split("\n", 1)[1] if "\n" in body else body
        candidates.append(body.rsplit("```", 1)[0])
    decoder = json.JSONDecoder()
    for cand in candidates:
        idx = cand.find("{")
        while idx != -1:
            try:
                value, _end = decoder.raw_decode(cand[idx:])
                if isinstance(value, (dict, list)):
                    return value
            except ValueError:
                pass
            idx = cand.find("{", idx + 1)
    raise AgentError("no hay JSON válido en la respuesta")
