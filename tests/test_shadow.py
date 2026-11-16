"""API de shadowing: banco, toma, análisis simulado y degradación sin worker."""
from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import time
import unittest
import wave
from pathlib import Path

import support
from support import FIXTURES, TestEnv, http, json_of, run_server

BOUNDARY = "----refireshadow"


def multipart(fields: dict, files: dict):
    chunks = []
    for name, value in fields.items():
        chunks.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
             % (BOUNDARY, name, value)).encode("utf-8")
        )
    for name, (filename, data, ctype) in files.items():
        chunks.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (BOUNDARY, name, filename, ctype)).encode("utf-8")
        )
        chunks.append(data + b"\r\n")
    chunks.append(("--%s--\r\n" % BOUNDARY).encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=%s" % BOUNDARY


def wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """WAV mono 16 kHz válido, sin depender de ficheros de audio del proyecto."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(rate * seconds))
        )
        wf.writeframes(frames)
    return buffer.getvalue()


def post_take(base: str, pid: str = "p1", dev: str | None = None):
    fields = {"pid": pid}
    if dev is not None:
        fields["dev"] = dev
    body, ctype = multipart(
        fields,
        {"file": ("toma.wav", wav_bytes(), "audio/wav")},
    )
    return http("POST", base + "/api/shadow", body=body,
                headers={"Content-Type": ctype}, timeout=60)


def wait_ready(base: str, take_id: str, timeout: float = 20.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        status, raw, _ = http("GET", base + "/api/shadow/" + take_id)
        assert status == 200, raw[:300]
        last = json_of(raw)
        if last.get("status") in ("listo", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError("la toma no terminó a tiempo: %r" % (last,))


class ShadowApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills.json").__enter__()
        os.environ["REFIRE_SHADOW_PYTHON"] = sys.executable
        os.environ["REFIRE_SHADOW_WORKER"] = str(FIXTURES / "shadow-worker.py")
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def test_01_phrases_are_the_four_of_the_bank(self):
        status, raw, _ = http("GET", self.base + "/api/shadow/phrases")
        self.assertEqual(status, 200, raw[:300])
        body = json_of(raw)
        self.assertTrue(body["ok"])
        phrases = body["phrases"]
        # El banco es dato del usuario: puede crecer con ingesta de clips. Este test solo
        # comprueba que las cuatro frases originales siguen ahí y con su referencia.
        ids = [p["id"] for p in phrases]
        for pid in ("p1", "p2", "p3", "p4"):
            self.assertIn(pid, ids)
        by_id = {p["id"]: p for p in phrases}
        self.assertEqual(by_id["p1"]["text"],
                         "Even if it has side effects, we should still ship it.")
        self.assertTrue(all(by_id[pid]["has_ref"] for pid in ("p1", "p2", "p3", "p4")))

    def test_02_upload_is_analyzed_and_returns_two_curves(self):
        status, raw, _ = post_take(self.base)
        self.assertEqual(status, 200, raw[:300])
        body = json_of(raw)
        self.assertTrue(body["ok"])
        take_id = body["id"]

        item = wait_ready(self.base, take_id)
        self.assertEqual(item["status"], "listo", item)
        self.assertEqual(item["pid"], "p1")
        self.assertIsNone(item["error"])
        for key in ("mio", "ref"):
            f0 = item["result"][key]["f0"]
            self.assertEqual(len(f0), 120)
            self.assertTrue(any(isinstance(v, (int, float)) for v in f0))
            self.assertTrue(any(v is None for v in f0))

        result = item["result"]
        self.assertEqual(result["sonidos_bien"], 82)
        self.assertEqual(result["score"], 77.0)
        self.assertEqual(result["transcrito"],
                         "EVEN IF IT HAS SIDE EFFECTS WE SHALL STILL SHIP IT")
        self.assertEqual(result["fonemas"][0],
                         {"palabra": "should", "de": "ʊd", "a": "ul"})
        self.assertEqual(item["compare"]["kind"], "first")

        status, raw, headers = http("GET", self.base + "/media/shadow/" + take_id)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/wav")
        self.assertTrue(raw.startswith(b"RIFF"))

        status, raw, _ = http("GET", self.base + "/media/shadow/ref/p1")
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"RIFF"))

        takes = json.loads((self.env.data / "shadow" / "takes.json").read_text("utf-8"))
        stored = takes["takes"][take_id]
        self.assertEqual(stored["status"], "listo")
        self.assertEqual(stored["pid"], "p1")
        self.assertIsNone(stored["dev"])
        self.assertEqual(stored["result"]["sonidos_bien"], 82)
        self.assertEqual(stored["result"]["score"], 77.0)
        self.assertEqual(stored["result"]["fonemas"][0]["palabra"], "should")

    def test_03_empty_upload_is_rejected(self):
        body, ctype = multipart({"pid": "p1"}, {"file": ("toma.wav", b"", "audio/wav")})
        status, raw, _ = http("POST", self.base + "/api/shadow", body=body,
                              headers={"Content-Type": ctype})
        self.assertEqual(status, 400, raw[:300])
        self.assertFalse(json_of(raw)["ok"])

    def test_04_giant_upload_is_413(self):
        status, raw, _ = http(
            "POST", self.base + "/api/shadow?pid=p1",
            body=b"x" * (4 * 1024 * 1024 + 1),
            headers={"Content-Type": "audio/webm"},
        )
        self.assertEqual(status, 413, raw[:300])
        self.assertEqual(json_of(raw)["error"], "too_large")

    def test_05_unknown_take_and_phrase(self):
        status, raw, _ = http("GET", self.base + "/api/shadow/no-existe")
        self.assertEqual(status, 404)
        self.assertEqual(json_of(raw)["error"], "shadow_not_found")

        body, ctype = multipart({"pid": "p9"}, {"file": ("toma.wav", wav_bytes(), "audio/wav")})
        status, raw, _ = http("POST", self.base + "/api/shadow", body=body,
                              headers={"Content-Type": ctype})
        self.assertEqual(status, 400, raw[:300])
        self.assertEqual(json_of(raw)["error"], "bad_request")

    def test_06_retry_relaunches_a_finished_take(self):
        status, raw, _ = post_take(self.base, "p2")
        self.assertEqual(status, 200, raw[:300])
        take_id = json_of(raw)["id"]
        wait_ready(self.base, take_id)

        status, raw, _ = http("POST", self.base + "/api/shadow/%s/retry" % take_id)
        self.assertEqual(status, 200, raw[:300])
        self.assertEqual(json_of(raw)["status"], "pendiente")
        item = wait_ready(self.base, take_id)
        self.assertEqual(item["status"], "listo", item)

    def test_07_compare_with_previous_take_of_the_same_mic(self):
        status, raw, _ = post_take(self.base, "p3", dev="Micro de prueba")
        self.assertEqual(status, 200, raw[:300])
        first = wait_ready(self.base, json_of(raw)["id"])
        self.assertEqual(first["compare"]["kind"], "first")
        self.assertEqual(first["dev"], "Micro de prueba")

        status, raw, _ = post_take(self.base, "p3", dev="Micro de prueba")
        self.assertEqual(status, 200, raw[:300])
        second = wait_ready(self.base, json_of(raw)["id"])
        self.assertEqual(second["compare"]["kind"], "same_mic", second["compare"])
        self.assertEqual(second["compare"]["before"], 82)
        self.assertEqual(second["dev"], "Micro de prueba")

    def test_08_previous_take_of_another_mic_is_not_comparable(self):
        status, raw, _ = post_take(self.base, "p4", dev="Micro A")
        self.assertEqual(status, 200, raw[:300])
        wait_ready(self.base, json_of(raw)["id"])

        status, raw, _ = post_take(self.base, "p4", dev="Micro B")
        self.assertEqual(status, 200, raw[:300])
        second = wait_ready(self.base, json_of(raw)["id"])
        self.assertEqual(second["compare"]["kind"], "other_mic", second["compare"])
        self.assertNotIn("before", second["compare"])


class ShadowCurvasFallbackTest(unittest.TestCase):
    """Worker viejo (solo ``curvas``): la pestaña sigue con las curvas y sin número."""

    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills.json").__enter__()
        os.environ["REFIRE_SHADOW_PYTHON"] = sys.executable
        os.environ["REFIRE_SHADOW_WORKER"] = str(FIXTURES / "shadow-worker-curvas.py")
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def test_01_old_worker_falls_back_to_curvas_without_score(self):
        status, raw, _ = post_take(self.base, "p1", dev="Micro A")
        self.assertEqual(status, 200, raw[:300])
        take_id = json_of(raw)["id"]
        item = wait_ready(self.base, take_id)
        self.assertEqual(item["status"], "listo", item)
        self.assertTrue(item["result"].get("fallback"))
        self.assertNotIn("sonidos_bien", item["result"])
        self.assertNotIn("score", item["result"])
        self.assertEqual(len(item["result"]["mio"]["f0"]), 120)
        self.assertEqual(len(item["result"]["ref"]["f0"]), 120)


class ShadowUnavailableTest(unittest.TestCase):
    """Sin venv/worker: 503, pero la toma queda guardada con estado error."""

    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills.json").__enter__()
        os.environ["REFIRE_SHADOW_PYTHON"] = str(cls.env.tmp / "no-hay-python")
        os.environ["REFIRE_SHADOW_WORKER"] = str(FIXTURES / "shadow-worker.py")
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def test_01_missing_worker_is_503_and_take_is_error(self):
        status, raw, _ = post_take(self.base)
        self.assertEqual(status, 503, raw[:300])
        body = json_of(raw)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "shadow_unavailable")
        self.assertTrue(body.get("id"))
        take_id = body["id"]

        status, raw, _ = http("GET", self.base + "/api/shadow/" + take_id)
        self.assertEqual(status, 200)
        item = json_of(raw)
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"]["code"], "shadow_unavailable")
        self.assertTrue((self.env.data / "shadow" / "audio" / (take_id + ".wav")).is_file())

        status, raw, _ = http("POST", self.base + "/api/shadow/%s/retry" % take_id)
        self.assertEqual(status, 503, raw[:300])
        self.assertEqual(json_of(raw)["error"], "shadow_unavailable")


class ShadowIngestApiTest(unittest.TestCase):
    """Endpoints nuevos: 503 claro sin yt-dlp, 404 de ingesta y quitar del banco."""

    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills.json").__enter__()
        os.environ["REFIRE_SHADOW_PYTHON"] = sys.executable
        os.environ["REFIRE_SHADOW_WORKER"] = str(FIXTURES / "shadow-worker.py")
        os.environ["REFIRE_YTDLP"] = str(cls.env.tmp / "no-hay-yt-dlp")
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def test_01_bad_requests_are_400_before_touching_ytdlp(self):
        status, raw, _ = http("POST", self.base + "/api/shadow/ingest", payload={})
        self.assertEqual(status, 400, raw[:300])
        self.assertEqual(json_of(raw)["error"], "bad_request")

        status, raw, _ = http("POST", self.base + "/api/shadow/ingest",
                              payload={"url": "https://youtu.be/x", "inicio": 120,
                                       "fin": 60})
        self.assertEqual(status, 400, raw[:300])

        status, raw, _ = http("POST", self.base + "/api/shadow/ingest",
                              payload={"url": "no-es-una-url"})
        self.assertEqual(status, 400, raw[:300])

    def test_02_missing_ytdlp_is_503_with_a_readable_message(self):
        status, raw, _ = http("POST", self.base + "/api/shadow/ingest",
                              payload={"url": "https://youtu.be/x"})
        self.assertEqual(status, 503, raw[:300])
        body = json_of(raw)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "ingest_unavailable")
        self.assertIn("yt-dlp", body["detail"])

    def test_03_unknown_ingest_is_404(self):
        status, raw, _ = http("GET", self.base + "/api/shadow/ingest/no-existe")
        self.assertEqual(status, 404)
        self.assertEqual(json_of(raw)["error"], "ingest_not_found")

    def test_04_forget_removes_the_phrase_and_its_wav(self):
        folder = self.env.data / "shadow"
        refs = folder / "refs"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "y001.wav").write_bytes(wav_bytes(0.5))
        (folder / "phrases.json").write_text(json.dumps({"phrases": [
            {"id": "p1", "text": "Old."},
            {"id": "y001", "text": "This one goes away.",
             "source": "https://youtu.be/x", "license": "youtube",
             "speaker": "Canal", "start": 61.0, "end": 64.0, "duration": 3.0},
        ]}), encoding="utf-8")

        status, raw, _ = http("POST", self.base + "/api/shadow/forget",
                              payload={"pid": "y001"})
        self.assertEqual(status, 200, raw[:300])
        self.assertEqual(json_of(raw)["pid"], "y001")
        self.assertFalse((refs / "y001.wav").exists())
        saved = json.loads((folder / "phrases.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in saved["phrases"]], ["p1"])

        status, raw, _ = http("GET", self.base + "/api/shadow/phrases")
        self.assertEqual([p["id"] for p in json_of(raw)["phrases"]], ["p1"])

        status, raw, _ = http("POST", self.base + "/api/shadow/forget",
                              payload={"pid": "y001"})
        self.assertEqual(status, 404)
        self.assertEqual(json_of(raw)["error"], "phrase_not_found")

        status, raw, _ = http("POST", self.base + "/api/shadow/forget",
                              payload={"pid": "../bank"})
        self.assertEqual(status, 400)
        self.assertEqual(json_of(raw)["error"], "bad_request")


if __name__ == "__main__":
    unittest.main()
