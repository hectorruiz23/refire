"""Parser YAML mínimo (solo stdlib) para el contrato de informe de Refire.

No pretende ser YAML completo: cubre el subconjunto que usa el BRIEF (mapas
anidados, secuencias de mapas, secuencias en línea, escalares con comillas,
escalares de bloque, escalares planos partidos en varias líneas, comentarios,
null, ints) y acepta lo que ChatGPT devuelve de verdad cuando se enrolla
formateando. Fuera de eso, lanza YamlError para que la API pueda responder 422
sin inventar datos.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["YamlError", "parse", "load"]


class YamlError(ValueError):
    pass


# Centinela: "esta entrada no trae escalar de bloque ya resuelto".
_NO_BLOCK = object()

# Cabecera de escalar de bloque: |, >, |-, >+, |2, |2-, >-  (indicador de estilo,
# chomping opcional a cada lado y sangría explícita opcional).
_BLOCK_HEAD = re.compile(r"^([|>])([+-]?)([0-9]?)([+-]?)$")

# (indentación, texto, es_item, columna_del_texto, escalar_de_bloque_ya_resuelto)
Entry = tuple[int, str, bool, int, object]


def load(text: str) -> Any:
    return parse(text)


def parse(text: str) -> Any:
    entries = _tokenize(text)
    if not entries:
        return None
    value, i = _parse_block(entries, 0, entries[0][0])
    if i < len(entries):
        raise YamlError(
            "indentación inconsistente cerca de: %r" % (entries[i][1][:60],)
        )
    return value


# --------------------------------------------------------------------------- #
# tokenización
# --------------------------------------------------------------------------- #

def _tokenize(text: str) -> list[Entry]:
    """Devuelve [(indent, texto, es_item_de_secuencia, columna_texto, bloque), ...].

    El quinto campo es `_NO_BLOCK` salvo en las entradas con escalar de bloque
    (`clave: >-`, `- |`, `clave: |2-`...), que llegan ya convertidas a str y con
    las líneas de contenido consumidas.
    """
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    entries = []
    i = 0
    total = len(lines)
    while i < total:
        line = lines[i].replace("\t", "    ")
        i += 1
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("---", "..."):
            continue
        if stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "-" or stripped.startswith("- "):
            is_item = True
            body = stripped[1:].lstrip()
        else:
            is_item = False
            body = stripped
        col = indent + 2 if is_item else indent

        block = _NO_BLOCK
        head = None
        if body in ("|", ">") or _BLOCK_HEAD.match(body):
            head = body  # item de secuencia sin clave: "- |"
            body = ""
        else:
            key, rest = _split_kv(body)
            if rest and _BLOCK_HEAD.match(rest):
                head = rest  # "clave: >-"
                body = body[: len(body) - len(rest)].rstrip()
        if head is not None:
            block, i = _block_scalar(lines, i, indent, head)

        entries.append((indent, body, is_item, col, block))
    return entries


def _split_kv(s: str):
    """Separa 'clave: valor' respetando comillas. Devuelve (None, None) si no hay clave."""
    quote = None
    for idx, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":":
            rest = s[idx + 1:]
            if rest == "" or rest[0] in " \t":
                key = s[:idx].strip()
                if not key:
                    return None, None
                return key, rest.strip()
    return None, None


# --------------------------------------------------------------------------- #
# escalares de bloque
# --------------------------------------------------------------------------- #

def _block_scalar(
    lines: list[str], i: int, parent_indent: int, head: str
) -> tuple[str, int]:
    """Consume las líneas de un escalar de bloque y devuelve (texto, nuevo_i)."""
    m = _BLOCK_HEAD.match(head)
    if m is None:  # el tokenizador solo llama aquí con una cabecera válida
        raise YamlError("escalar de bloque inválido: %r" % (head,))
    style = m.group(1)
    chomp = m.group(2) or m.group(4)
    explicit = int(m.group(3)) if m.group(3) else 0

    block_indent = parent_indent + explicit if explicit else None
    collected = []
    total = len(lines)
    while i < total:
        line = lines[i].replace("\t", "    ")
        if line.strip() == "":
            collected.append("")
            i += 1
            continue
        ind = len(line) - len(line.lstrip(" "))
        if block_indent is None:
            if ind <= parent_indent:
                break
            block_indent = ind
        if ind < block_indent:
            break
        collected.append(line[block_indent:])
        i += 1

    trailing = 0
    while collected and collected[-1] == "":
        collected.pop()
        trailing += 1

    if not collected:
        return "\n" * trailing, i

    body = "\n".join(collected) if style == "|" else _fold(collected)
    if chomp == "-":
        return body, i
    if chomp == "+":
        return body + "\n" * (1 + trailing), i
    return body + "\n", i


def _fold(lines: list[str]) -> str:
    """Plegado de `>`: líneas seguidas se unen con espacio, las vacías son saltos."""
    out = []
    prev_empty = False
    for line in lines:
        if line == "":
            out.append("\n")
            prev_empty = True
            continue
        if out and not prev_empty:
            out.append(" ")
        out.append(line)
        prev_empty = False
    return "".join(out)


def _plain_continuation(
    entries: list[Entry], i: int, indent: int, value: object
) -> tuple[object, int]:
    """Une las líneas de continuación de un escalar plano partido a mano.

    En YAML `clave: texto largo` puede seguir en las líneas de debajo mientras
    estén más indentadas y no sean `clave: valor`. ChatGPT lo hace a menudo.
    """
    while isinstance(value, str) and i < len(entries):
        nind, ntext, nmarker, _ncol, nblock = entries[i]
        if nmarker or nblock is not _NO_BLOCK or nind <= indent:
            break
        if _split_kv(ntext)[0] is not None:
            break
        extra = _scalar(ntext)
        if extra is None:
            break
        value = value + " " + str(extra)
        i += 1
    return value, i


# --------------------------------------------------------------------------- #
# bloques
# --------------------------------------------------------------------------- #

def _parse_block(entries: list[Entry], i: int, indent: int) -> tuple[object, int]:
    if i >= len(entries):
        return None, i
    if entries[i][2]:  # es un item de secuencia
        return _parse_seq(entries, i, indent)
    return _parse_map(entries, i, indent)


def _parse_map(entries: list[Entry], i: int, indent: int) -> tuple[object, int]:
    out = {}
    while i < len(entries):
        ind, text, marker, _col, block = entries[i]
        if marker or ind != indent:
            break
        key, rest = _split_kv(text)
        if key is None:
            raise YamlError("se esperaba 'clave: valor' y llegó: %r" % (text[:60],))
        i += 1
        if block is not _NO_BLOCK:
            out[key] = block
            continue
        if rest != "":
            value = _scalar(rest)
            out[key], i = _plain_continuation(entries, i, indent, value)
            continue
        out[key] = None
        if i < len(entries):
            nind, _ntext, nmarker, _ncol, _nblock = entries[i]
            if nmarker and nind == indent:
                out[key], i = _parse_seq(entries, i, indent)
            elif nmarker and nind > indent:
                out[key], i = _parse_seq(entries, i, nind)
            elif nind > indent:
                out[key], i = _parse_map(entries, i, nind)
    return out, i


def _parse_seq(entries: list[Entry], i: int, indent: int) -> tuple[object, int]:
    out = []
    while i < len(entries):
        ind, text, marker, _col, block = entries[i]
        if not marker or ind != indent:
            break
        i += 1
        if block is not _NO_BLOCK:
            out.append(block)
            continue
        if text == "":
            if i < len(entries) and entries[i][0] > indent:
                value, i = _parse_block(entries, i, entries[i][0])
            else:
                value = None
            out.append(value)
            continue
        key, rest = _split_kv(text)
        if key is None:
            value = _scalar(text)
            value, i = _plain_continuation(entries, i, indent, value)
            out.append(value)
            continue
        item = {}
        item_col = indent + 2
        if block is not _NO_BLOCK:
            item[key] = block
        elif rest != "":
            item[key] = _scalar(rest)
            item[key], i = _plain_continuation(entries, i, item_col, item[key])
        else:
            item[key] = None
            if i < len(entries) and entries[i][0] > item_col:
                item[key], i = _parse_block(entries, i, entries[i][0])
        # claves de continuación del mismo item
        while i < len(entries) and not entries[i][2] and entries[i][0] == item_col:
            sub, j = _parse_map(entries, i, item_col)
            if j == i or not isinstance(sub, dict):
                break
            item.update(sub)
            i = j
        out.append(item)
    return out, i


# --------------------------------------------------------------------------- #
# escalares
# --------------------------------------------------------------------------- #

def _strip_comment(s: str) -> str:
    if s.startswith(("'", '"')):
        return s
    m = re.search(r"(^|\s)#", s)
    return s[: m.start()].strip() if m else s


def _scalar(s: str):
    s = _strip_comment(s.strip())
    if s == "":
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return _unescape(s[1:-1])
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s.startswith("["):
        return _flow_seq(s)
    low = s.lower()
    if low in ("null", "~", "none"):
        return None
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _unescape(s: str) -> str:
    return (
        s.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\x00", "\\")
    )


def _flow_seq(s: str):
    inner = s.strip()
    if not inner.endswith("]"):
        raise YamlError("secuencia en línea sin cerrar: %r" % (s[:60],))
    inner = inner[1:-1].strip()
    if not inner:
        return []
    parts, buf, quote = [], [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quote:
        raise YamlError("comilla sin cerrar en: %r" % (s[:60],))
    parts.append("".join(buf))
    return [_scalar(p.strip()) for p in parts if p.strip() != ""]
