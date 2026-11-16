#!/usr/bin/env python3
"""Refire — API local en 127.0.0.1:8770 (solo librería estándar).

Uso:
    python3 app.py            # sirve la API y static/ si existe
    REFIRE_PORT=8771 python3 app.py
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import sys
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import (  # noqa: E402
    agent, audio, config, session, shadow, shadow_youtube, srs, talk, text, vault,
)

MAX_BODY = 4 * 1024 * 1024


def _log(message: str) -> None:
    """Traza de diagnóstico de audio/agente. Va a stderr con la hora; el servidor ya loguea
    cada request, esto añade el motivo real de un 422/501 que antes se perdía."""
    sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), message))
    sys.stderr.flush()


def _audio_log(entry: dict) -> None:
    """Una línea JSON por respuesta de voz en data/audio-log.jsonl.

    Sin esto, cuando el reconocedor no te entiende solo queda la sensación de "me ha dicho que
    me he equivocado" y no hay forma de saber qué oyó. Es también el histórico para medir.
    """
    try:
        path = config.data_dir() / "audio-log.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".md": "text/markdown; charset=utf-8",
}

RE_DRILLS = re.compile(r"^/api/report/(\d{4}-\d{2}-\d{2}(?:-\d{6})?)/drills$")
RE_SHADOW_TAKE = re.compile(r"^/api/shadow/([A-Za-z0-9_-]{1,80})$")
RE_SHADOW_RETRY = re.compile(r"^/api/shadow/([A-Za-z0-9_-]{1,80})/retry$")
RE_SHADOW_INGEST = re.compile(r"^/api/shadow/ingest/([A-Za-z0-9_-]{1,80})$")
RE_TALK_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
RE_TALK_WAV = re.compile(r"^[A-Za-z0-9._-]+\.wav$")


def _ingest_seconds(raw, name: str, default: float) -> float:
    """Segundos de ``inicio``/``fin`` en la petición, con el defecto del encargo."""
    if raw is None or raw == "":
        return float(default)
    if isinstance(raw, bool):
        raise ValueError("%s tiene que ser un número de segundos" % name)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s tiene que ser un número de segundos" % name) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("%s tiene que ser un número de segundos" % name)
    return value


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str = "", **extra):
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra


class Handler(BaseHTTPRequestHandler):
    server_version = "refire/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ utils
    def log_message(self, fmt, *args):  # silencio ordenado
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, detail: str = "", **extra):
        payload = {"ok": False, "error": code}
        if detail:
            payload["detail"] = detail
        payload.update(extra)
        self._json(status, payload)

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            n = int(length)
        except ValueError as exc:
            raise ApiError(400, "bad_request", "Content-Length inválido") from exc
        if n > MAX_BODY:
            # El cliente puede estar enviando el cuerpo: drenar (hasta un tope) evita que se
            # quede con un broken pipe en vez de leer el 413, y cerrar la conexión después.
            self.close_connection = True
            try:
                self.rfile.read(min(n, MAX_BODY + 1))
            except OSError:
                pass
            raise ApiError(413, "too_large", "cuerpo mayor de %d bytes" % MAX_BODY)
        return self.rfile.read(n)

    def _json_body(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "bad_request", "JSON inválido: %s" % exc) from exc
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "se esperaba un objeto JSON")
        return data

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/state":
                return self._json(200, session.state())
            if path == "/api/drills":
                query = parse_qs(urlparse(self.path).query)
                pattern = (query.get("pattern") or [None])[0]
                only_due = (query.get("due") or [""])[0] in ("1", "true", "yes")
                include_retired = (query.get("all") or [""])[0] in ("1", "true", "yes")
                return self._json(200, session.drills_list(pattern, only_due, include_retired))
            if path == "/api/shadow/phrases":
                return self._json(200, {"ok": True, "phrases": shadow.phrases()})
            if path == "/api/shadow/word":
                word = ((parse_qs(urlparse(self.path).query).get("w") or [""])[0] or "").strip()
                try:
                    audio_path = shadow.word_path(word)
                except shadow.ShadowInvalid as exc:
                    raise ApiError(400, exc.code, str(exc)) from exc
                except shadow.ShadowUnavailable as exc:
                    raise ApiError(503, "shadow_unavailable",
                                   "no se puede generar el audio de la palabra") from exc
                return self._json(200, {"ok": True, "word": word,
                                        "url": "/media/shadow/word/%s" % audio_path.stem})
            m = RE_SHADOW_INGEST.match(path)
            if m:
                try:
                    job = shadow_youtube.get(m.group(1))
                except KeyError as exc:
                    raise ApiError(404, "ingest_not_found",
                                   "no existe la ingesta %s" % exc.args[0]) from exc
                return self._json(200, dict(job, ok=True))
            m = RE_SHADOW_TAKE.match(path)
            if m:
                try:
                    item = shadow.take_detail(m.group(1))
                except shadow.ShadowNotFound as exc:
                    raise ApiError(404, "shadow_not_found", "no existe la toma %s" % exc) from exc
                return self._json(200, dict(item, ok=True))
            if path.startswith("/media/shadow/ref/"):
                pid = path[len("/media/shadow/ref/"):].strip("/")
                return self._media(shadow.ref_path(pid), cache="private, max-age=86400")
            if path.startswith("/media/shadow/word/"):
                slug = path[len("/media/shadow/word/"):].strip("/")
                return self._media(shadow.word_path(slug), cache="private, max-age=86400")
            if path.startswith("/media/shadow/"):
                take_id = path[len("/media/shadow/"):].strip("/")
                if not RE_SHADOW_TAKE.match("/api/shadow/" + take_id):
                    raise ApiError(404, "not_found", path)
                return self._media(shadow.take_wav(take_id))
            if path.startswith("/media/talk/"):
                return self._talk_media(path[len("/media/talk/"):])
            if path == "/api/export":
                if not config.vault_path().exists():
                    return self._error(
                        409, "vault_missing",
                        "no hay nota del registro: define REFIRE_VAULT con la ruta de tu nota")
                name, data = session.export_note()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if path == "/api/tts":
                query = parse_qs(urlparse(self.path).query)
                text = (query.get("text") or [""])[0]
                return self._tts(text)
            if path in ("/api/health", "/healthz"):
                return self._json(200, {"ok": True, "port": config.port(),
                                        "agent": agent.agent_mode()})
            if path == "/api/talk/events":
                return self._talk_events()
            if path.startswith("/api/"):
                return self._error(404, "not_found", path)
            return self._static(path)
        except ApiError as exc:
            return self._error(exc.status, exc.code, exc.detail, **exc.extra)
        except talk.TalkBusy as exc:
            return self._error(409, "talk_busy", str(exc))
        except talk.TalkUnavailable as exc:
            return self._error(503, "talk_unavailable", str(exc))
        except vault.VaultError as exc:
            return self._error(500, "vault_error", str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, "internal", str(exc))

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/talk/start":
                return self._talk_start()
            if path == "/api/talk/turn":
                return self._talk_turn()
            if path == "/api/talk/end":
                return self._talk_end()

            if path == "/api/scenario":
                data = self._json_body()
                focus = data.get("focus") or []
                if isinstance(focus, str):
                    focus = [focus]
                out = session.scenario(
                    difficulty=data.get("difficulty") or "B2",
                    focus=focus,
                    minutes=data.get("minutes") or 10,
                )
                return self._json(200, out)

            if path == "/api/report":
                data = self._json_body()
                raw = data.get("raw")
                if not isinstance(raw, str) or not raw.strip():
                    raise ApiError(400, "bad_request", "falta 'raw' con el informe pegado")
                try:
                    out = session.process_report(raw)
                except session.DuplicateReport as exc:
                    raise ApiError(409, "duplicate", "informe ya procesado para %s" % exc) from exc
                except session.ReportInvalid as exc:
                    raise ApiError(
                        422, "yaml_invalid", exc.detail,
                        raw_saved=str(exc.raw_path) if exc.raw_path else None,
                        retry=True,
                    ) from exc
                return self._json(200, out)

            m = RE_DRILLS.match(path)
            if m:
                try:
                    out = session.regenerate_drills(m.group(1))
                except session.ReportInvalid as exc:
                    raise ApiError(404, "no_report", exc.detail) from exc
                return self._json(200 if out.get("ok") else 502, out)

            if path == "/api/drill/answer":
                return self._drill_answer()

            if path == "/api/drill/audio":
                return self._drill_audio()

            if path == "/api/shadow":
                return self._shadow_upload()

            if path == "/api/shadow/ingest":
                return self._shadow_ingest()

            if path == "/api/shadow/forget":
                return self._shadow_forget()

            m = RE_SHADOW_RETRY.match(path)
            if m:
                return self._shadow_retry(m.group(1))

            return self._error(404, "not_found", path)
        except ApiError as exc:
            return self._error(exc.status, exc.code, exc.detail, **exc.extra)
        except talk.TalkBusy as exc:
            return self._error(409, "talk_busy", str(exc))
        except talk.TalkReportFailed as exc:
            return self._error(502, "report_failed", str(exc))
        except talk.TalkUnavailable as exc:
            return self._error(503, "talk_unavailable", str(exc))
        except vault.VaultError as exc:
            return self._error(500, "vault_error", str(exc))
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, "internal", str(exc))

    # ------------------------------------------------------------------ talk
    def _talk_session(self):
        """Sesión activa o error HTTP: 503 si falta el motor, 409 si no hay sesión."""
        if not talk.available():
            raise ApiError(503, "talk_unavailable",
                           "voicekit no está importable en este intérprete")
        current = talk.current()
        if current is None:
            raise ApiError(409, "talk_busy", "no hay ninguna sesión activa")
        return current

    def _talk_start(self):
        data = self._json_body()
        if not talk.available():
            raise ApiError(503, "talk_unavailable",
                           "voicekit no está importable en este intérprete")
        if talk.current() is not None:
            raise ApiError(409, "talk_busy", "ya hay una sesión activa")

        focus = data.get("focus") or []
        if isinstance(focus, str):
            focus = [focus]
        scenario = None
        try:
            scenario = session.scenario(
                difficulty=data.get("difficulty") or "B2",
                focus=focus,
                minutes=data.get("minutes") or 10,
            )
        except Exception as exc:  # noqa: BLE001 - sin escenario se arranca igual
            _log("talk_start_scenario_failed: %s" % str(exc)[:200])

        system_prompt = talk.build_system_prompt(scenario)
        current = talk.TalkSession.start(
            system_prompt=system_prompt,
            scenario=scenario,
            difficulty=data.get("difficulty") or "B2",
        )
        return self._json(200, {"session": current.session, "scenario": scenario})

    def _talk_turn(self):
        current = self._talk_session()
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype:
            raise ApiError(400, "bad_request", "se esperaba multipart/form-data")
        body = self._read_body()
        try:
            _fields, files = audio.parse_multipart(ctype, body)
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from exc
        upload = files.get("file")
        if not upload:
            raise ApiError(400, "bad_request", "falta el fichero 'file'")
        turn = current.turn(upload["data"])
        return self._json(202, {"turn": turn})

    def _talk_end(self):
        current = self._talk_session()
        data = self._json_body()
        report = data.get("report", False)
        if isinstance(report, str):
            report = report.strip().lower() in ("1", "true", "yes", "si", "sí")
        else:
            report = bool(report)
        return self._json(200, current.end(report=report))

    def _talk_events(self):
        current = self._talk_session()
        query = parse_qs(urlparse(self.path).query)
        raw = (query.get("since") or ["0"])[0] or "0"
        try:
            since = int(raw)
        except ValueError as exc:
            raise ApiError(400, "bad_request", "since tiene que ser un entero") from exc
        if since < 0:
            raise ApiError(400, "bad_request", "since tiene que ser >= 0")

        events = current.events(since)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for event in events:
                # Pista para la interfaz: «sin voz» se puede reintentar; el motor no
                # necesita saber nada de reintentos.
                if (
                    event["type"] == "error"
                    and (event.get("data") or {}).get("code") == "stt_empty_audio"
                ):
                    event.setdefault("data", {})["retry"] = True
                line = "data: %s\n\n" % json.dumps(event, ensure_ascii=False)
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            return

    # ------------------------------------------------------------------ drills
    def _drill_answer(self):
        data = self._json_body()
        item_id = data.get("id")
        if not item_id:
            raise ApiError(400, "bad_request", "falta 'id'")
        correct = data.get("correct")
        if isinstance(correct, str):
            correct = correct.strip().lower() in ("1", "true", "yes", "si", "sí")
        correct = bool(correct)
        state = srs.load()
        try:
            item = srs.answer(state, str(item_id), correct)
        except LookupError as exc:
            raise ApiError(404, "drill_not_found", "no existe el drill %s" % exc) from exc
        srs.save(state)
        return self._json(200, {"ok": True, "item": item, "drills": srs.counts(state)})

    def _drill_audio(self):
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype:
            raise ApiError(400, "bad_request", "se esperaba multipart/form-data")
        body = self._read_body()
        try:
            fields, files = audio.parse_multipart(ctype, body)
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from exc
        item_id = fields.get("id") or ""
        upload = files.get("file")
        if not item_id:
            raise ApiError(400, "bad_request", "falta el campo 'id'")
        if not upload:
            raise ApiError(400, "bad_request", "falta el fichero 'file'")

        state = srs.load()
        item = srs.find(state, item_id)
        if item is None:
            raise ApiError(404, "drill_not_found", "no existe el drill %s" % item_id)

        try:
            result = audio.extract(item_id, upload["filename"], upload["data"])
        except audio.AudioUnavailable as exc:
            _log("audio_unavailable: %s" % exc)
            raise ApiError(
                501, "audio_unavailable",
                "el motor de voz (faster-whisper) no está cargado en este servidor: "
                "arráncalo con .venv/bin/python app.py",
            ) from exc
        except audio.AudioInvalid as exc:
            _log("audio_invalid (%d bytes, %s): %s" % (
                len(upload["data"]), upload["filename"], exc))
            raise ApiError(422, "audio_invalid", str(exc)) from exc

        expected = item.get("answer") or ""
        transcript = result.get("transcript") or ""
        if not transcript.strip():
            _log("audio_sin_voz (%d bytes, %.2fs)" % (
                len(upload["data"]), result.get("duration_s") or 0.0))
            raise ApiError(
                422, "audio_empty",
                "no se ha oído nada en la grabación: habla más cerca del micro "
                "y mantén pulsado mientras hablas",
            )
        words = text.compare_words(expected, transcript)
        alts = item.get("alt_answers") or []
        match = (
            text.equal(expected, transcript)
            or any(text.equal(alt, transcript) for alt in alts)
            or text.contains(expected, transcript)
            or any(text.contains(alt, transcript) for alt in alts)
        )
        close = (not match) and text.near_miss(expected, transcript, alts)
        _audio_log({
            "at": datetime.now().replace(microsecond=0).isoformat(),
            "drill": item_id,
            "pattern": item.get("pattern"),
            "expected": expected,
            "transcript": transcript,
            "match": bool(match),
            "close": bool(close),
            "duration_s": result.get("duration_s", 0.0),
            "pauses": result.get("pauses", 0),
            "bytes": len(upload["data"]),
        })
        return self._json(200, {
            "ok": True,
            "transcript": transcript,
            "expected": expected,
            "match": bool(match),
            "close": bool(close),
            "words": words,
            "duration_s": result.get("duration_s", 0.0),
            "pauses": result.get("pauses", 0),
            "engine": result.get("engine", "faster-whisper"),
        })

    # ------------------------------------------------------------------ shadowing
    def _shadow_upload(self):
        """multipart (pid + file) o binario crudo con ?pid=; ≤4 MB.

        El nombre del micro (``track.label``) llega como campo ``dev`` o como ``?dev=``.
        """
        query = parse_qs(urlparse(self.path).query)
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" in ctype:
            body = self._read_body()
            if not body:
                raise ApiError(400, "bad_request", "la subida llegó vacía")
            try:
                fields, files = audio.parse_multipart(ctype, body)
            except ValueError as exc:
                raise ApiError(400, "bad_request", str(exc)) from exc
            pid = (fields.get("pid") or fields.get("id") or "").strip()
            dev = fields.get("dev") or (query.get("dev") or [""])[0]
            upload = files.get("file") or files.get("audio")
            if not pid:
                raise ApiError(400, "bad_request", "falta el campo 'pid'")
            if not upload or not upload.get("data"):
                raise ApiError(400, "bad_request", "falta el fichero 'file' o llegó vacío")
            data = upload["data"]
            filename = upload.get("filename") or "toma.webm"
        else:
            pid = ((query.get("pid") or query.get("id") or [""])[0] or "").strip()
            dev = (query.get("dev") or [""])[0]
            if not pid:
                raise ApiError(400, "bad_request", "falta 'pid' (multipart o ?pid=)")
            data = self._read_body()
            if not data:
                raise ApiError(400, "bad_request", "la subida llegó vacía")
            filename = "toma%s" % (mimetypes.guess_extension(ctype.split(";")[0].strip())
                                   or ".webm")

        try:
            item = shadow.save_take(pid, filename, data, dev=dev)
        except shadow.ShadowUnavailable as exc:
            _log("shadow_unavailable: %s" % exc)
            raise ApiError(
                503, "shadow_unavailable",
                "el análisis de shadowing no está disponible en este servidor "
                "(falta el intérprete o el worker del analizador)",
                id=exc.take_id,
            ) from exc
        except shadow.ShadowInvalid as exc:
            status = 413 if exc.code == "too_large" else 422 if exc.code == "audio_invalid" else 400
            raise ApiError(status, exc.code, str(exc)) from exc
        return self._json(200, {"ok": True, "id": item["id"], "status": item["status"]})

    def _shadow_ingest(self):
        """Pega una URL: arranca descarga + transcripción + corte en un hilo."""
        data = self._json_body()
        url = data.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ApiError(400, "bad_request", "falta 'url' con el enlace del vídeo")
        try:
            inicio = _ingest_seconds(
                data.get("inicio"), "inicio", shadow_youtube.DEFAULT_INICIO)
            fin = _ingest_seconds(data.get("fin"), "fin", shadow_youtube.DEFAULT_FIN)
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from exc
        try:
            job = shadow_youtube.start(url.strip(), inicio, fin)
        except shadow_youtube.IngestUnavailable as exc:
            _log("ingest_unavailable: %s" % exc)
            raise ApiError(503, "ingest_unavailable", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from exc
        return self._json(200, {"ok": True, "id": job["id"], "status": job["status"]})

    def _shadow_forget(self):
        """Quita una frase del banco y borra su WAV (único paso manual, y opcional)."""
        data = self._json_body()
        pid = data.get("pid")
        if not isinstance(pid, str) or not pid.strip():
            raise ApiError(400, "bad_request", "falta 'pid'")
        try:
            removed = shadow.forget(pid.strip())
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)) from exc
        if removed is None:
            raise ApiError(404, "phrase_not_found", "no existe la frase %s" % pid)
        return self._json(200, {"ok": True, "pid": removed.get("id"), "removed": removed})

    def _shadow_retry(self, take_id: str):
        try:
            item = shadow.retry(take_id)
        except shadow.ShadowNotFound as exc:
            raise ApiError(404, "shadow_not_found", "no existe la toma %s" % exc) from exc
        except shadow.ShadowUnavailable as exc:
            raise ApiError(
                503, "shadow_unavailable",
                "el análisis de shadowing no está disponible en este servidor",
                id=exc.take_id,
            ) from exc
        except shadow.ShadowInvalid as exc:
            raise ApiError(422, exc.code, str(exc)) from exc
        return self._json(200, {"ok": True, "id": item["id"], "status": item["status"]})

    def _media(self, path, cache: str = "no-store"):
        if path is None or not path.is_file():
            raise ApiError(404, "not_found", "no hay audio")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        return self.wfile.write(data)

    def _talk_media(self, rest: str):
        """Sirve el WAV de una frase; nunca deja que el nombre salga de la sesión."""
        parts = rest.split("/")
        if len(parts) != 2:
            raise ApiError(400, "bad_request", "ruta de audio inválida")
        session_id, filename = parts
        if (
            session_id in (".", "..")
            or ".." in session_id
            or not RE_TALK_SESSION.fullmatch(session_id)
            or not RE_TALK_WAV.fullmatch(filename)
            or ".." in filename
        ):
            raise ApiError(400, "bad_request", "nombre de audio inválido")
        session_dir = config.data_dir() / "talk" / "sessions" / session_id
        if not session_dir.is_dir():
            raise ApiError(404, "not_found", "no existe la sesión")
        return self._media(session_dir / "tts" / filename)

    # ------------------------------------------------------------------ static
    def _tts(self, text: str):
        """Voz del sistema para la tarjeta modelo (espeak-ng, offline, sin red).

        Chromium en Linux no ve las voces de speech-dispatcher aunque estén instaladas, así que
        sintetizar aquí es lo único que funciona en cualquier navegador sin tocar flags.
        """
        clean = " ".join(str(text or "").split())
        if not clean:
            raise ApiError(400, "bad_request", "falta el texto")
        if len(clean) > 400:
            clean = clean[:400]
        import hashlib
        import shutil
        import subprocess

        engine = shutil.which("espeak-ng")
        if engine is None:
            raise ApiError(501, "tts_unavailable", "instala espeak-ng para oír las frases modelo")
        cache_dir = config.data_dir() / "tts"
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(("en-us|145|" + clean).encode("utf-8")).hexdigest()[:16]
        wav = cache_dir / ("%s.wav" % key)
        if not wav.exists():
            tmp = cache_dir / ("%s.tmp.wav" % key)
            try:
                subprocess.run(
                    [engine, "-v", "en-us", "-s", "145", "-w", str(tmp), clean],
                    capture_output=True, timeout=20, check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                _log("tts_error: %s" % str(exc)[:200])
                raise ApiError(502, "tts_failed", "no se pudo sintetizar la voz") from exc
            os.replace(tmp, wav)
        data = wav.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        return self.wfile.write(data)

    def _static(self, path: str):
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        if rel == "" or rel.endswith("/"):
            rel = (rel + "index.html").lstrip("/")
        rel = rel or "index.html"

        root = config.STATIC
        candidate = (root / rel)
        try:
            resolved = candidate.resolve()
            if root.resolve() not in resolved.parents and resolved != root.resolve():
                raise ApiError(404, "not_found", path)
        except OSError:
            raise ApiError(404, "not_found", path)

        if not resolved.is_file():
            if path == "/":
                # sin frontend todavía: 404 limpio, nunca un traceback
                return self._error(404, "static_missing",
                                   "no hay static/index.html; sirve solo la API")
            return self._error(404, "not_found", path)

        data = resolved.read_bytes()
        ctype = MIME.get(resolved.suffix.lower()) or mimetypes.guess_type(str(resolved))[0] \
            or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return self.wfile.write(data)

    def do_OPTIONS(self):  # el frontend puede preflight-ear si algún día se separa
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None) -> int:
    claves = config.load_env()  # claves del LLM desde ~/.config/refire/env o ./.env
    for nombre in claves:
        print("clave cargada: %s" % nombre, flush=True)
    host, port = config.host(), config.port()
    config.data_dir()  # asegura data/
    httpd = Server((host, port), Handler)
    print("Refire en http://%s:%d  (agente: %s)" % (host, port, agent.agent_mode()),
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ncortado", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
