"""API HTTP real en 127.0.0.1 (criterios 1, 2, 3, 4, 5, 8 y 11)."""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path

import support
from support import TestEnv, http, json_of, run_server

from lib import audio, config


def multipart(fields: dict, files: dict):
    boundary = "----refireboundary"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
             % (boundary, name, value)).encode("utf-8")
        )
    for name, (filename, data, ctype) in files.items():
        chunks.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, name, filename, ctype)).encode("utf-8")
        )
        chunks.append(data + b"\r\n")
    chunks.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=%s" % boundary


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills.json").__enter__()
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def _state(self):
        status, raw, _hdrs = http("GET", self.base + "/api/state")
        self.assertEqual(status, 200)
        return json_of(raw)

    # ---------------------------------------------------------------- 01
    def test_01_state_serves_vault_sessions(self):
        state = self._state()
        self.assertIn("sessions", state)
        self.assertEqual(len(state["sessions"]), 1)
        self.assertEqual(state["sessions"][0]["date"], "2026-11-02")
        self.assertEqual(state["drills"], {"due_now": 0, "total": 0, "retired": 0})
        self.assertEqual(state["stats"]["sessions"], 1)
        self.assertIsNone(state["stats"]["measured"]["fillers_per_minute"])
        names = {p["name"] for p in state["patterns"]}
        self.assertTrue({"conditionals", "articles", "prepositions", "word-choice"} <= names)

    # ---------------------------------------------------------------- 02
    def test_02_report_posts_row_and_drills(self):
        self.before_sha = support.sha256_file(config.vault_path())
        status, raw, _ = http(
            "POST", self.base + "/api/report", payload={"raw": support.report_text()}
        )
        self.assertEqual(status, 200, raw[:400])
        out = json_of(raw)
        self.assertTrue(out["ok"])
        self.assertTrue(out["row_written"])
        self.assertEqual(out["session"]["date"], "2026-11-02")
        self.assertEqual(out["drills_added"], 10)
        self.assertNotIn("drills_pending", out["warnings"])

        state = self._state()
        self.assertEqual(len(state["sessions"]), 2)
        self.assertEqual(state["drills"]["total"], 10)
        self.assertEqual(state["stats"]["estimated"]["fillers_per_minute"], 6.0)

    def test_02b_drills_endpoint_lists_and_filters_items(self):
        status, raw, _ = http("GET", self.base + "/api/drills")
        self.assertEqual(status, 200)
        body = json_of(raw)
        self.assertEqual(len(body["items"]), 10)
        self.assertEqual(body["total"], 10)
        self.assertTrue(all(i["prompt"] and i["answer"] for i in body["items"]))

        status, raw, _ = http("GET", self.base + "/api/drills?pattern=conditionals")
        filtered = json_of(raw)["items"]
        self.assertTrue(filtered)
        self.assertTrue(all(i["pattern"] == "conditionals" for i in filtered))

    # ---------------------------------------------------------------- 03
    def test_03_same_report_twice_is_409(self):
        status, raw, _ = http(
            "POST", self.base + "/api/report", payload={"raw": support.report_text()}
        )
        self.assertEqual(status, 409, raw[:300])
        body = json_of(raw)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "duplicate")
        self.assertEqual(len(self._state()["sessions"]), 2)

    # ---------------------------------------------------------------- 04
    def test_04_broken_yaml_is_422_and_vault_untouched(self):
        before = support.sha256_file(config.vault_path())
        broken = "session:\n  date: [2026-11-02\n  minutes: 9\n"
        status, raw, _ = http("POST", self.base + "/api/report", payload={"raw": broken})
        self.assertEqual(status, 422, raw[:300])
        body = json_of(raw)
        self.assertEqual(body["error"], "yaml_invalid")
        self.assertTrue(body["retry"])
        self.assertTrue(body["raw_saved"])
        self.assertTrue(Path(body["raw_saved"]).exists())
        self.assertEqual(support.sha256_file(config.vault_path()), before)

    # ---------------------------------------------------------------- 05
    def test_05_scenario_returns_block_b_ready_to_paste(self):
        status, raw, _ = http(
            "POST", self.base + "/api/scenario",
            payload={"difficulty": "B2", "focus": ["conditionals"], "minutes": 10},
        )
        self.assertEqual(status, 200, raw[:300])
        out = json_of(raw)
        self.assertTrue(out["ok"])
        self.assertEqual(out["chatgpt_url"], "https://chatgpt.com/")
        self.assertIn("SETUP", out["block_b"])
        self.assertIn("LEVEL: B2", out["block_b"])
        self.assertIn(out["scenario"], out["block_b"])
        self.assertNotIn("{{", out["block_b"])

    # ---------------------------------------------------------------- 06
    def test_06_drill_answer_schedules_and_retires(self):
        srs_doc = json.loads((self.env.data / "srs.json").read_text(encoding="utf-8"))
        item_id = srs_doc["items"][0]["id"]

        status, raw, _ = http(
            "POST", self.base + "/api/drill/answer", payload={"id": item_id, "correct": False}
        )
        self.assertEqual(status, 200, raw[:300])
        item = json_of(raw)["item"]
        self.assertEqual(item["due"], (date.today() + timedelta(days=1)).isoformat())
        self.assertEqual(item["streak"], 0)
        self.assertEqual(item["wrong"], 1)

        for _ in range(3):
            status, raw, _ = http(
                "POST", self.base + "/api/drill/answer",
                payload={"id": item_id, "correct": True},
            )
            self.assertEqual(status, 200, raw[:300])
            item = json_of(raw)["item"]
        self.assertTrue(item["retired"])
        self.assertIsNone(item["due"])
        self.assertEqual(item["streak"], 3)

        state = self._state()
        self.assertEqual(state["drills"]["total"], 9)
        self.assertEqual(state["drills"]["retired"], 1)

    def test_07_answer_unknown_drill_is_404(self):
        status, raw, _ = http(
            "POST", self.base + "/api/drill/answer", payload={"id": "d-9999", "correct": True}
        )
        self.assertEqual(status, 404)
        self.assertEqual(json_of(raw)["error"], "drill_not_found")

    # ---------------------------------------------------------------- 08
    @unittest.skipIf(audio.voice_ready(), "faster-whisper sí está instalado")
    def test_08_audio_without_faster_whisper_is_501(self):
        srs_doc = json.loads((self.env.data / "srs.json").read_text(encoding="utf-8"))
        item_id = srs_doc["items"][1]["id"]
        body, ctype = multipart(
            {"id": item_id},
            {"file": ("answer.webm", b"\x1aE\xdf\xa3fake-webm-bytes", "audio/webm")},
        )
        status, raw, _ = http(
            "POST", self.base + "/api/drill/audio", body=body,
            headers={"Content-Type": ctype},
        )
        self.assertEqual(status, 501, raw[:300])
        out = json_of(raw)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "audio_unavailable")

    # ---------------------------------------------------------------- 09
    def test_09_export_returns_markdown(self):
        status, raw, headers = http("GET", self.base + "/api/export")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers.get("Content-Type", ""))
        text = raw.decode("utf-8")
        self.assertIn("## Registro de sesiones", text)
        self.assertIn("| 2026-11-02 | 12 |", text)

    def test_10_unknown_api_route_is_json_404(self):
        status, raw, _ = http("GET", self.base + "/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json_of(raw)["error"], "not_found")

    def test_11_bad_json_body_is_400(self):
        status, raw, _ = http(
            "POST", self.base + "/api/report", body=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json_of(raw)["error"], "bad_request")

    def test_12_root_serves_static_or_404(self):
        status, raw, _ = http("GET", self.base + "/")
        self.assertIn(status, (200, 404))
        if status == 404:
            self.assertEqual(json_of(raw)["error"], "static_missing")


class ModelCardsApiTest(unittest.TestCase):
    """`kind` en /api/drills y frases modelo fuera de la tasa por patrón."""

    @classmethod
    def setUpClass(cls):
        cls.env = TestEnv("agent-drills-model.json").__enter__()
        cls._ctx = run_server(cls.env)
        cls.base = cls._ctx.__enter__()
        status, raw, _ = http(
            "POST", cls.base + "/api/report", payload={"raw": support.report_text()}
        )
        assert status == 200, raw[:400]

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.env.__exit__(None, None, None)

    def _state(self):
        status, raw, _h = http("GET", self.base + "/api/state")
        self.assertEqual(status, 200)
        return json_of(raw)

    def test_every_item_has_kind_and_models_are_listed(self):
        status, raw, _ = http("GET", self.base + "/api/drills")
        self.assertEqual(status, 200)
        body = json_of(raw)
        self.assertTrue(body["items"])
        self.assertTrue(all(i.get("kind") in ("gap", "model") for i in body["items"]))
        models = [i for i in body["items"] if i["kind"] == "model"]
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["prompt"], "I am the responsible for the deploy")
        self.assertEqual(models[0]["answer"], "I am responsible for the deploy")
        self.assertEqual(body["models"]["total"], 1)
        # los contadores de drills siguen contando solo huecos
        self.assertEqual(body["total"], 10)

    def test_marking_a_model_card_does_not_touch_pattern_stats(self):
        before = {p["name"]: p for p in self._state()["patterns"]}
        status, raw, _ = http("GET", self.base + "/api/drills")
        model_id = next(i["id"] for i in json_of(raw)["items"] if i["kind"] == "model")

        status, raw, _ = http(
            "POST", self.base + "/api/drill/answer", payload={"id": model_id, "correct": False}
        )
        self.assertEqual(status, 200, raw[:300])
        item = json_of(raw)["item"]
        self.assertEqual(item["kind"], "model")
        self.assertEqual(item["streak"], 0)
        self.assertEqual(item["due"], (date.today() + timedelta(days=1)).isoformat())

        state = self._state()
        after = {p["name"]: p for p in state["patterns"]}
        self.assertEqual(before["articles"]["attempts"], after["articles"]["attempts"])
        self.assertEqual(before["articles"]["correct"], after["articles"]["correct"])
        self.assertEqual(state["stats"]["models"], {"seen": 1, "to_review": 1})


if __name__ == "__main__":
    unittest.main()