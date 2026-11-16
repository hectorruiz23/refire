"""Enum cerrado de patrones del BRIEF (sección 4)."""
from __future__ import annotations

PATTERNS = (
    "conditionals",
    "articles",
    "prepositions",
    "collocations",
    "verb-form",
    "word-choice",
    "discourse",
)

FALLBACK = "word-choice"

_ALIASES = {
    "conditional": "conditionals",
    "conditionals": "conditionals",
    "if-clauses": "conditionals",
    "article": "articles",
    "articles": "articles",
    "determiners": "articles",
    "preposition": "prepositions",
    "prepositions": "prepositions",
    "collocation": "collocations",
    "collocations": "collocations",
    "verb form": "verb-form",
    "verb-form": "verb-form",
    "verbforms": "verb-form",
    "verb-forms": "verb-form",
    "tense": "verb-form",
    "verb tense": "verb-form",
    "word choice": "word-choice",
    "word-choice": "word-choice",
    "vocabulary": "word-choice",
    "lexis": "word-choice",
    "discourse": "discourse",
    "discourse markers": "discourse",
    "connectors": "discourse",
    "linking": "discourse",
}


def normalize(value) -> tuple[str, str | None]:
    """Devuelve (patrón, warning o None). Nunca lanza: lo desconocido cae a word-choice."""
    raw = "" if value is None else str(value).strip().lower()
    key = raw.replace("_", " ").replace("-", "-")
    key = " ".join(key.split())
    if key in _ALIASES:
        return _ALIASES[key], None
    candidate = key.replace(" ", "-")
    if candidate in PATTERNS:
        return candidate, None
    warning = "patrón desconocido %r → word-choice" % (raw or "(vacío)")
    return FALLBACK, warning
