#!/usr/bin/env python3
"""Ingesta de clips de habla real para el banco de shadowing.

Uso:
    python3 tools/ingest_clip.py <fichero> [--min 2.0] [--max 8.0] [--max-frases 40]
    python3 tools/ingest_clip.py <fichero> --accept 3,5,7

No usa red ni dependencias nuevas: faster-whisper (caché local) y ffmpeg.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import audio  # noqa: E402
from lib.shadow_ingest import (  # noqa: E402
    accept_candidates,
    cut_clip,
    segment_words,
)


def ensure_whisper() -> None:
    """Si el ``python3`` de fuera no tiene faster-whisper, reintenta con el ``.venv``.

    El encargo se lanza como ``python3 tools/ingest_clip.py``; en este repo las dependencias
    de voz viven en ``.venv``. Sin esto el comando fallaría con ModuleNotFoundError.
    """
    try:
        import faster_whisper  # noqa: F401  (solo comprueba que está)
        return
    except ImportError:
        pass
    venv_python = ROOT / ".venv" / "bin" / "python"
    if (os.environ.get("SHADOW_INGEST_REEXEC") != "1" and venv_python.is_file()
            and Path(sys.executable).resolve() != venv_python.resolve()):
        os.environ["SHADOW_INGEST_REEXEC"] = "1"
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()),
                                    *sys.argv[1:]])


def shadow_root() -> Path:
    """``data/shadow`` del proyecto, o el que marque REFIRE_DATA."""
    raw = os.environ.get("REFIRE_DATA")
    base = Path(raw).expanduser() if raw else ROOT / "data"
    return base / "shadow"


def slug_for(path: Path) -> str:
    plain = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    return slug or "clip"


def parse_accept(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    numbers = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            number = int(chunk)
        except ValueError as exc:
            raise ValueError("--accept espera números separados por comas: %r" % raw) from exc
        if number <= 0:
            raise ValueError("--accept usa números de la tabla (empiezan en 1)")
        if number not in numbers:
            numbers.append(number)
    if not numbers:
        raise ValueError("--accept no ha recibido ningún número")
    return numbers


def print_table(candidates: list[dict]) -> None:
    print()
    print("%3s  %8s  %8s  %9s  %s" % ("n", "inicio", "fin", "duración", "texto"))
    print("%s" % ("-" * 72))
    for item in candidates:
        print("%3d  %8.2f  %8.2f  %8.2fs  %s" % (
            item["n"], item["start"], item["end"], item["duration"], item["text"],
        ))
    print()


def load_index(index_path: Path) -> dict | None:
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def index_is_current(index: dict | None, source: Path, args) -> bool:
    if not index or not isinstance(index.get("candidates"), list):
        return False
    stat = source.stat()
    return (
        index.get("source_path") == str(source.resolve())
        and index.get("input_size") == stat.st_size
        and index.get("input_mtime_ns") == stat.st_mtime_ns
        and index.get("min_s") == args.min
        and index.get("max_s") == args.max
        and index.get("max_frases") == args.max_frases
        and index.get("license") == args.license
        and index.get("speaker") == (args.speaker or source.stem)
    )


def ingest(source: Path, args) -> tuple[Path, dict]:
    """Transcribe, segmenta y escribe los WAV de trabajo. Devuelve (índice, doc)."""
    slug = slug_for(source)
    folder = shadow_root() / "candidates" / slug
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.wav"):
        try:
            old.unlink()
        except OSError:
            pass

    try:
        result = audio.transcribe_words(source)
    except audio.AudioUnavailable as exc:
        raise RuntimeError("falta faster-whisper o ffmpeg: %s" % exc) from exc
    except audio.AudioInvalid as exc:
        raise RuntimeError("el audio no se pudo leer: %s" % exc) from exc

    segments = segment_words(
        result["words"], min_s=args.min, max_s=args.max, max_phrases=args.max_frases,
    )
    if not segments:
        raise RuntimeError(
            "no ha salido ninguna frase entre %.1f y %.1fs; prueba otros límites"
            % (args.min, args.max)
        )

    candidates = []
    for number, segment in enumerate(segments, 1):
        name = "%d.wav" % number
        out = folder / name
        cut_clip(source, segment["start"], segment["end"], out)
        candidates.append({
            "n": number,
            "file": name,
            "start": segment["start"],
            "end": segment["end"],
            "duration": segment["duration"],
            "confidence": segment["confidence"],
            "text": segment["text"],
        })

    stat = source.stat()
    doc = {
        "source": source.name,
        "source_path": str(source.resolve()),
        "input_size": stat.st_size,
        "input_mtime_ns": stat.st_mtime_ns,
        "slug": slug,
        "license": args.license,
        "speaker": args.speaker or source.stem,
        "min_s": args.min,
        "max_s": args.max,
        "max_frases": args.max_frases,
        "created": datetime.now().replace(microsecond=0).isoformat(),
        "engine": result.get("engine"),
        "duration_s": result.get("duration_s"),
        "candidates": candidates,
    }
    index_path = folder / "index.json"
    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, index_path)
    return index_path, doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Saca frases de un audio o vídeo para el banco de shadowing.",
    )
    parser.add_argument("file", help="audio o vídeo de entrada")
    parser.add_argument("--min", type=float, default=2.0, help="duración mínima (s)")
    parser.add_argument("--max", type=float, default=8.0, help="duración máxima (s)")
    parser.add_argument("--max-frases", type=int, default=40, help="máximo de candidatos")
    parser.add_argument("--accept", metavar="N,M,...",
                        help="acepta esos números de la tabla y añádelos al banco")
    parser.add_argument("--license", default="desconocida", help="licencia del audio")
    parser.add_argument("--speaker", default=None, help="hablante del audio")
    args = parser.parse_args(argv)
    ensure_whisper()

    if args.min <= 0 or args.max <= 0 or args.min > args.max:
        print("error: hace falta 0 < --min <= --max", file=sys.stderr)
        return 2
    if args.max_frases <= 0:
        print("error: --max-frases tiene que ser mayor que 0", file=sys.stderr)
        return 2
    try:
        selected = parse_accept(args.accept)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    source = Path(args.file).expanduser()
    if not source.is_file():
        print("error: no existe el fichero %s" % source, file=sys.stderr)
        return 2
    if shutil.which("ffmpeg") is None:
        print("error: hace falta ffmpeg en el PATH", file=sys.stderr)
        return 2

    slug = slug_for(source)
    folder = shadow_root() / "candidates" / slug
    index_path = folder / "index.json"
    index = load_index(index_path)
    if index_is_current(index, source, args):
        doc = index
    else:
        try:
            index_path, doc = ingest(source, args)
        except (RuntimeError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1

    print_table(doc["candidates"])
    if selected is None:
        print("candidatos en %s" % folder)
        return 0

    try:
        new = accept_candidates(
            index_path, selected,
            shadow_root() / "refs",
            shadow_root() / "phrases.json",
        )
    except (OSError, ValueError, KeyError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    print("aceptadas %d frases: %s" % (len(new), ", ".join(p["id"] for p in new)))
    print("referencias en %s" % (shadow_root() / "refs"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
