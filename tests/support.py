"""Utilidades compartidas por los tests: entorno temporal, servidor y HTTP."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

ENV_KEYS = (
    "REFIRE_DATA",
    "REFIRE_VAULT",
    "REFIRE_AGENT_FIXTURE",
    "REFIRE_AGENT_CMD",
    "REFIRE_PORT",
    "REFIRE_SHADOW_PYTHON",
    "REFIRE_SHADOW_WORKER",
    "REFIRE_YTDLP",
)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tone_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """WAV mono 16 kHz con un tono: el repo no versiona audio, los tests lo fabrican."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(rate * seconds))
        ))
    return buffer.getvalue()


def seed_ref_wavs(data_dir, pids=("p1", "p2", "p3", "p4")) -> None:
    """Crea las referencias de audio del banco en el data/ temporal de la prueba."""
    refs = Path(data_dir) / "shadow" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    for pid in pids:
        path = refs / ("%s.wav" % pid)
        if not path.exists():
            path.write_bytes(tone_wav())


def report_text() -> str:
    return (FIXTURES / "report-2026-11-02.yaml").read_text(encoding="utf-8")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestEnv:
    """Directorio temporal con su propio data/ y su copia del vault."""

    def __init__(self, agent_fixture: str | None = "agent-drills.json"):
        self.agent_fixture = agent_fixture
        self._saved = {}

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refire-test-"))
        self.data = self.tmp / "data"
        self.data.mkdir()
        seed_ref_wavs(self.data)
        self.vault = self.tmp / "Inglés hablado — B2+ hacia C1.md"
        shutil.copyfile(FIXTURES / "vault-note.md", self.vault)
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        os.environ["REFIRE_DATA"] = str(self.data)
        os.environ["REFIRE_VAULT"] = str(self.vault)
        os.environ.pop("REFIRE_AGENT_CMD", None)
        os.environ.pop("REFIRE_SHADOW_PYTHON", None)
        os.environ.pop("REFIRE_SHADOW_WORKER", None)
        if self.agent_fixture:
            os.environ["REFIRE_AGENT_FIXTURE"] = str(FIXTURES / self.agent_fixture)
        else:
            os.environ.pop("REFIRE_AGENT_FIXTURE", None)
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def env_for_server(self) -> dict:
        env = os.environ.copy()
        env["REFIRE_DATA"] = str(self.data)
        env["REFIRE_VAULT"] = str(self.vault)
        return env


@contextlib.contextmanager
def run_server(env: TestEnv, wait: float = 15.0):
    port = free_port()
    envvars = env.env_for_server()
    envvars["REFIRE_PORT"] = str(port)
    out = open(env.tmp / "server.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, str(APP / "app.py")],
        env=envvars,
        stdout=out,
        stderr=subprocess.STDOUT,
        cwd=str(APP),
    )
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + wait
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                out.close()
                raise RuntimeError("el servidor murió:\n%s" % (env.tmp / "server.log").read_text())
            try:
                status, _raw, _hdrs = http("GET", base + "/api/health", timeout=1)
                if status == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("el servidor no arrancó en %ss" % wait)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()


def http(method, url, payload=None, body=None, headers=None, timeout=30):
    """Devuelve (status, bytes, headers). No lanza en 4xx/5xx."""
    hdrs = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(), dict(exc.headers)
        finally:
            exc.close()


def json_of(raw: bytes):
    return json.loads(raw.decode("utf-8"))
