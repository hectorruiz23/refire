"""Carga y relleno de plantillas de prompts (prompts/*.md)."""
from __future__ import annotations

from . import config

__all__ = ["load", "fill"]

# El prompt del escenario es del alumno, no del repo: se busca primero en su equipo.
PER_LEARNER = "scenario.md"


def load(name: str) -> str:
    if name in (PER_LEARNER, PER_LEARNER[:-3]) and not config.scenario_prompt_path().is_file():
        return (config.PROMPTS / "scenario.example.md").read_text(encoding="utf-8")
    if name in (PER_LEARNER, PER_LEARNER[:-3]):
        return config.scenario_prompt_path().read_text(encoding="utf-8")
    path = config.PROMPTS / (name if name.endswith(".md") else name + ".md")
    return path.read_text(encoding="utf-8")


def fill(template: str, values: dict) -> str:
    out = template
    for key, value in values.items():
        token = "{{%s}}" % key.upper()
        out = out.replace(token, "" if value is None else str(value))
    return out
