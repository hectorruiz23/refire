"""Comparación tolerante de respuestas (BRIEF 6.2).

Minúsculas, sin puntuación, espacios colapsados y contracciones equivalentes:
``I'd`` == ``I would``, ``don't`` == ``do not``, ``it's`` == ``it is``.
"""
from __future__ import annotations

import difflib
import re

__all__ = ["normalize", "tokens", "equal", "compare_words"]

_CONTRACTIONS = [
    (r"\bwon't\b", "will not"),
    (r"\bshan't\b", "shall not"),
    (r"\bcan't\b", "can not"),
    (r"\bcannot\b", "can not"),
    (r"\bain't\b", "is not"),
    (r"\blet's\b", "let us"),
    (r"n't\b", " not"),
    (r"'re\b", " are"),
    (r"'ll\b", " will"),
    (r"'ve\b", " have"),
    (r"'d\b", " would"),
    (r"'m\b", " am"),
    (r"'s\b", " is"),
]

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'", "\u00b4": "'"})


def normalize(text: str) -> str:
    if text is None:
        return ""
    s = str(text).translate(_APOSTROPHES).lower()
    s = s.replace("&", " and ")
    for pattern, repl in _CONTRACTIONS:
        s = re.sub(pattern, repl, s)
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokens(text: str):
    return normalize(text).split()


def equal(expected: str, given: str) -> bool:
    return normalize(expected) == normalize(given)


def contains(expected: str, heard: str) -> bool:
    """True si las palabras esperadas aparecen en lo oído, seguidas y en orden.

    Un drill de hueco se contesta muchas veces repitiendo la frase entera
    ("Even if it has side effects"): la respuesta es "has" y la transcripción trae toda la
    frase. Exigir igualdad exacta marcaba en rojo a quien había acertado.
    """
    needle = tokens(expected)
    hay = tokens(heard)
    if not needle or len(needle) > len(hay):
        return False
    for i in range(len(hay) - len(needle) + 1):
        if hay[i : i + len(needle)] == needle:
            return True
    return False


CLOSE_RATIO = 0.72


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=normalize(a), b=normalize(b), autojunk=False).ratio()


def near_miss(expected: str, heard: str, alts=()) -> bool:
    """True si lo oído se parece a la respuesta sin llegar a serlo.

    Sirve para no marcar en rojo a quien ha dicho algo casi correcto (o a quien el
    reconocedor no le ha entendido): "share" por "shared" es un 0,91 de parecido; "have"
    por "has" se queda en 0,57 y sigue contando como fallo. Un falso negativo repetido
    destruye la confianza en el drill más rápido que un fallo real.
    """
    candidates = (expected, *[a for a in (alts or ()) if a])
    if any(equal(c, heard) or contains(c, heard) for c in candidates if c):
        return False
    return any(similarity(c, heard) >= CLOSE_RATIO for c in candidates if c)


def compare_words(expected: str, heard: str):
    """Alinea palabra a palabra la respuesta esperada contra la oída.

    Devuelve ``[{"expected": w, "heard": w|None, "ok": bool}, ...]`` con una
    entrada por palabra esperada (las extra oídas no se inventan).
    """
    exp = tokens(expected)
    got = tokens(heard)
    sm = difflib.SequenceMatcher(a=exp, b=got, autojunk=False)
    mapping = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = got[j1 + k]
        else:
            for k in range(i1, i2):
                mapping[k] = None
    out = []
    for idx, word in enumerate(exp):
        heard_word = mapping.get(idx)
        out.append(
            {"expected": word, "heard": heard_word, "ok": heard_word == word}
        )
    return out
