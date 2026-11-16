"""Rutas y configuración del proyecto Refire.

Ninguna ruta lleva dentro una máquina concreta: lo que cambia de un equipo a otro se
pide por variable de entorno y, si no está, se resuelve dentro del directorio de datos.
"""
from __future__ import annotations

import os
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
DATA_DEFAULT = APP / "data"
PROMPTS = APP / "prompts"
STATIC = APP / "static"

# Nota del registro cuando no se configura ninguna: vive con los datos y puede no existir.
VAULT_NAME = "vault.md"


# Claves del LLM: la app no las guarda. Las toma de la variable de entorno o de un
# fichero con pares CLAVE=VALOR, en este orden y sin pisar lo ya exportado.
ENV_FILES = (
    Path("~/.config/refire/env").expanduser(),
    APP / ".env",
)


def load_env(files=None) -> list:
    """Carga claves de los ficheros de entorno y devuelve las que ha añadido.

    No sobreescribe lo que ya esté en el entorno: un `export` del shell manda.
    """
    añadidas = []
    for path in (files if files is not None else ENV_FILES):
        try:
            texto = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        for línea in texto.splitlines():
            línea = línea.strip()
            if not línea or línea.startswith("#") or "=" not in línea:
                continue
            nombre, valor = línea.split("=", 1)
            nombre = nombre.strip()
            valor = valor.strip().strip('"').strip("'")
            if not nombre or nombre in os.environ:
                continue
            os.environ[nombre] = valor
            añadidas.append(nombre)
    return añadidas


def data_dir() -> Path:
    p = Path(os.environ.get("REFIRE_DATA") or DATA_DEFAULT).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def reports_dir() -> Path:
    p = data_dir() / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def srs_path() -> Path:
    return data_dir() / "srs.json"


def vault_path() -> Path:
    """Nota del registro: `REFIRE_VAULT` o, si no, `<datos>/vault.md` (puede no existir)."""
    raw = os.environ.get("REFIRE_VAULT")
    return Path(raw).expanduser() if raw else data_dir() / VAULT_NAME


def scenario_prompt_path() -> Path:
    """El prompt del alumno vive en el equipo: lleva el perfil de cada uno.

    `REFIRE_SCENARIO_PROMPT` o `~/.config/refire/scenario.md`; el repo solo trae
    `prompts/scenario.example.md` como plantilla.
    """
    raw = os.environ.get("REFIRE_SCENARIO_PROMPT")
    return Path(raw).expanduser() if raw else Path("~/.config/refire/scenario.md").expanduser()


def port() -> int:
    return int(os.environ.get("REFIRE_PORT") or 8770)


def host() -> str:
    return os.environ.get("REFIRE_HOST") or "127.0.0.1"


def shadow_python() -> Path | None:
    """Intérprete del venv del analizador de shadowing, si se ha configurado."""
    raw = os.environ.get("REFIRE_SHADOW_PYTHON")
    return Path(raw).expanduser() if raw else None


def shadow_worker() -> Path | None:
    """Script del analizador de shadowing, si se ha configurado."""
    raw = os.environ.get("REFIRE_SHADOW_WORKER")
    return Path(raw).expanduser() if raw else None
