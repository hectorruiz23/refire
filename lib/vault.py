"""Lectura de la tabla del registro y escritura de una sola fila.

Regla dura (BRIEF 3 y criterio 9): el resto de la nota queda byte a byte igual.
Se trabaja sobre bytes, se localiza la sección `## Registro de sesiones`, dentro
de ella el bloque de tabla y se inserta la línea tras la última fila.
"""
from __future__ import annotations

import hashlib
import os
import re

from . import config

__all__ = [
    "VaultError",
    "read_note",
    "append_row",
    "parse_sessions",
    "has_date",
    "columns",
]

SECTION = "## Registro de sesiones"
COLUMNS = ("date", "minutes", "topics", "primary_correction", "new_vocab", "next_goal")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class VaultError(RuntimeError):
    pass


def columns():
    return list(COLUMNS)


def read_note() -> str:
    path = config.vault_path()
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise VaultError("nota del vault no encontrada: %s" % path) from exc
    except OSError as exc:
        raise VaultError("no se pudo leer la nota: %s" % exc) from exc


# --------------------------------------------------------------------------- #
# localización de la tabla
# --------------------------------------------------------------------------- #

def _split_lines(text: str):
    """Devuelve (líneas_con_salto, salto_de_línea_dominante)."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if text.count("\r\n") > text.count("\n") - text.count("\r\n") else "\n"
    return lines, newline


def _section_index(lines):
    for idx, line in enumerate(lines):
        if line.strip() == SECTION:
            return idx
    return None


def _table_bounds(lines):
    """(inicio, fin_exclusivo) del bloque de tabla dentro de la sección, o None."""
    start = _section_index(lines)
    if start is None:
        raise VaultError("la nota no tiene la sección '%s'" % SECTION)
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        if lines[i].startswith("## "):
            raise VaultError("la sección '%s' no contiene tabla" % SECTION)
        i += 1
    if i >= len(lines):
        raise VaultError("la sección '%s' no contiene tabla" % SECTION)
    first = i
    last = i
    while last + 1 < len(lines) and lines[last + 1].lstrip().startswith("|"):
        last += 1
    return first, last + 1


def _cells(line: str):
    body = line.strip()
    if not body.startswith("|"):
        return []
    if body.endswith("|"):
        body = body[1:-1]
    else:
        body = body[1:]
    return [c.strip() for c in body.split("|")]


def _clean_cell(value) -> str:
    if value is None:
        return "—"
    s = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    s = s.replace("|", "/")
    return s if s else "—"


def parse_sessions():
    """Filas de la tabla del registro, en orden, como dicts."""
    try:
        text = read_note()
    except VaultError:
        return []
    lines, _nl = _split_lines(text)
    try:
        first, end = _table_bounds(lines)
    except VaultError:
        return []
    rows = []
    for line in lines[first:end]:
        cells = _cells(line)
        if len(cells) < 6:
            continue
        if not DATE_RE.match(cells[0]):
            continue  # cabecera y separador
        minutes = cells[1]
        minutes_val = int(minutes) if minutes.isdigit() else None
        rows.append(
            {
                "date": cells[0],
                "minutes": minutes_val,
                "topics": cells[2],
                "primary_correction": cells[3],
                "new_vocab": cells[4],
                "next_goal": cells[5],
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def has_date(date: str) -> bool:
    return any(row["date"] == date for row in parse_sessions())


# --------------------------------------------------------------------------- #
# escritura
# --------------------------------------------------------------------------- #

def render_row(row: dict) -> str:
    values = [row.get(name) for name in COLUMNS]
    return "| " + " | ".join(_clean_cell(v) for v in values) + " |"


def append_row(row: dict) -> str:
    """Inserta una fila al final de la tabla. Devuelve la línea escrita."""
    path = config.vault_path()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VaultError("no se pudo leer la nota: %s" % exc) from exc
    text = raw.decode("utf-8")
    lines, newline = _split_lines(text)
    first, end = _table_bounds(lines)

    last_line = lines[end - 1]
    if not last_line.endswith(("\n", "\r")):
        # la tabla acaba el fichero: cerramos la última fila antes de añadir
        lines[end - 1] = last_line + newline

    line = render_row(row) + newline
    new_text = "".join(lines[:end]) + line + "".join(lines[end:])
    payload = new_text.encode("utf-8")
    # El temporal vive en data/ (nunca junto a la nota): criterio 11.
    tmp = config.data_dir() / (".vault-%s.tmp" % hashlib.sha1(str(path).encode()).hexdigest()[:10])
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        # último recurso (p. ej. otro sistema de ficheros): reescritura in situ
        try:
            with open(path, "r+b") as fh:
                fh.seek(0)
                fh.write(payload)
                fh.truncate()
        except OSError as exc:
            raise VaultError("no se pudo escribir la nota: %s" % exc) from exc
    return line.rstrip("\r\n")
