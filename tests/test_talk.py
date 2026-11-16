"""M5b: puente ``lib.talk`` y las cuatro rutas HTTP, con fakes y sin red."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
import wave
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import support

import app
from lib import config as app_config
from lib import talk, vault, yamlmini


VOICEKIT_AVAILABLE = talk.available()


# Escenario de prueba con la munición tal cual, incluido el hueco ``[his figure]``:
# build_system_prompt no debe rellenarlo ni añadir cifras por su cuenta.
SCENARIO = {
    "scenario": "A migration review where the new message broker is behind schedule",
    "you_play": "a sceptical staff engineer who owns the platform team",
    "i_play": "the tech lead defending the migration plan",
    "brief": {
        "situation": "The new message broker is two weeks late and the old one still carries all the traffic.",
        "my_position": "You want to keep the migration date and cut scope instead of moving the date.",
        "stake": "Your team already told the business the date; moving it again costs you credibility.",
    },
    "ammo": [
        "The old broker handled peak traffic last year without an incident.",
        "Cutting the replay feature buys us two weeks without moving the date.",
        "The last rehearsal lost [his figure] rows, which is why we cut scope.",
    ],
    "objections": [
        "Cutting scope means shipping something half-done - who signs off on that?",
        "What happens if the old broker fails between now and the new date?",
    ],
    "opener": "Straight to it - you are two weeks late. Why should we keep the date instead of moving it?",
    "useful": ["The way I see it, ...", "That's fair, but ..."],
}

HARD_RULES = (
    "Reply in at most two short sentences, then ask one question.",
    "Never use markdown, bullet points or lists: your answer is read aloud.",
    "Do not invent facts about the candidate. If a number is missing, ask for it.",
    "Stay in character for the whole session.",
)

# Turnos con rol, como los devuelve `TalkSession.end()`. La segunda corrección
# válida se apoya en el error de la última frase del alumno.
WRAPUP_TURNS = [
    {
        "number": 1,
        "role": "assistant",
        "text": "Straight to it: you are two weeks late. Why should we keep the date?",
    },
    {
        "number": 2,
        "role": "user",
        "text": "I think we should keep the date because the old broker handled the peak traffic last year.",
    },
    {
        "number": 3,
        "role": "assistant",
        "text": "And what about the replay feature?",
    },
    {
        "number": 4,
        "role": "user",
        "text": "We can cutted the replay feature and still deliver on time.",
    },
]

WRAPUP_BLOCK = """session:
  scores:
    fluency: 3
    grammar: 3
    lexis: 4
    organisation: 3
    interaction: 4
  corrections:
    - said: "I think we should keep the date because the old broker handled the peak traffic last year."
      better: "I think we should keep the date because the old broker handled peak traffic last year."
      pattern: collocations
    - said: "We can cutted the replay feature and still deliver on time."
      better: "We can cut the replay feature and still deliver on time."
      pattern: verb-form
  new_vocab:
    - "peak traffic"
    - "cut scope"
  next_goal: "Practise conditionals."
"""


def silence_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """WAV mono de silencio de 1 s, para no depender de fixtures binarias."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def multipart(fields: dict, files: dict):
    boundary = "----englishtalkboundary"
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


def parse_sse(raw: bytes) -> list[dict]:
    events = []
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("data:"):
            continue
        events.append(json.loads(line[len("data:"):].strip()))
    return events


class _QuietHandler(app.Handler):
    def log_message(self, fmt, *args):
        pass


@contextlib.contextmanager
def in_process_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@contextlib.contextmanager
def voicekit_data_dir():
    """Datos temporales para el motor y para la app: nada cae en refire/data/."""
    tmp = tempfile.TemporaryDirectory(prefix="talk-test-")
    keys = ("VOICEKIT_DATA_DIR", "REFIRE_DATA")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["VOICEKIT_DATA_DIR"] = tmp.name
    os.environ["REFIRE_DATA"] = tmp.name
    try:
        yield tmp.name
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tmp.cleanup()


class AvailabilityTests(unittest.TestCase):
    def test_available_matches_whether_voicekit_imports(self):
        try:
            import voicekit  # noqa: F401
            expected = True
        except Exception:  # noqa: BLE001
            expected = False
        self.assertEqual(talk.available(), expected)

    def test_simulated_import_error_returns_false(self):
        with mock.patch.object(talk.importlib, "import_module",
                               side_effect=ImportError("sin voicekit")):
            self.assertFalse(talk.available())

    def test_every_talk_route_is_503_when_voicekit_does_not_import(self):
        with in_process_server() as base, mock.patch.object(
            talk.importlib, "import_module", side_effect=ImportError("sin voicekit")
        ):
            calls = [
                ("POST", "/api/talk/start", {}),
                ("POST", "/api/talk/turn", {}),
                ("GET", "/api/talk/events?since=0", None),
                ("POST", "/api/talk/end", {}),
            ]
            for method, path, payload in calls:
                status, raw, _headers = support.http(
                    method, base + path, payload=payload, timeout=5
                )
                self.assertEqual(status, 503, (path, raw[:300]))
                body = json.loads(raw.decode("utf-8"))
                self.assertFalse(body["ok"])
                self.assertEqual(body["error"], "talk_unavailable")


class SystemPromptTests(unittest.TestCase):
    """M5f: el escenario se convierte en el prompt de sistema del motor."""

    def test_prompt_contains_scenario_parts(self):
        prompt = talk.build_system_prompt(SCENARIO)

        self.assertIn(SCENARIO["scenario"], prompt)
        self.assertIn(SCENARIO["you_play"], prompt)
        self.assertIn(SCENARIO["i_play"], prompt)
        for value in SCENARIO["brief"].values():
            self.assertIn(value, prompt)
        for item in SCENARIO["ammo"]:
            self.assertIn(item, prompt)
        for item in SCENARIO["objections"]:
            self.assertIn(item, prompt)
        self.assertIn(SCENARIO["opener"], prompt)
        self.assertIn("[his figure]", prompt)

    def test_prompt_has_hard_rules(self):
        prompt = talk.build_system_prompt(SCENARIO)

        for rule in HARD_RULES:
            self.assertIn(rule, prompt)

    def test_without_scenario_the_prompt_is_neutral(self):
        prompt = talk.build_system_prompt(None)

        self.assertIn("technical interviewer", prompt)
        for rule in HARD_RULES:
            self.assertIn(rule, prompt)
        self.assertNotRegex(prompt, r"\d")
        for item in SCENARIO["ammo"]:
            self.assertNotIn(item, prompt)


class WrapupTests(unittest.TestCase):
    """M5g1: la transcripción se convierte en el bloque `session:` del informe."""

    def _llm(self, raw, seen=None):
        def llm(prompt):
            if seen is not None:
                seen.append(prompt)
            return raw

        return llm

    def test_session_block_parses(self):
        block = talk.build_session_block(
            WRAPUP_TURNS, SCENARIO, 8, "B2", llm=self._llm(WRAPUP_BLOCK)
        )

        data = yamlmini.parse(block)
        self.assertIsInstance(data, dict)
        session = data["session"]
        self.assertEqual(session["date"], date.today().isoformat())
        self.assertEqual(session["minutes"], 8)
        self.assertEqual(session["difficulty"], "B2")
        self.assertEqual(session["scenario"], SCENARIO["scenario"])
        self.assertEqual(session["scores"]["fluency"], 3)
        self.assertEqual(len(session["corrections"]), 2)
        self.assertEqual(session["new_vocab"], ["peak traffic", "cut scope"])
        self.assertNotIn("warnings", data)

    def test_inline_scores_are_parsed(self):
        raw = WRAPUP_BLOCK.replace(
            "  scores:\n"
            "    fluency: 3\n"
            "    grammar: 3\n"
            "    lexis: 4\n"
            "    organisation: 3\n"
            "    interaction: 4\n",
            "  scores: {fluency: 5, grammar: 4, lexis: 3, organisation: 2, interaction: 5}\n",
            1,
        )
        block = talk.build_session_block(
            WRAPUP_TURNS, SCENARIO, 8, "B2", llm=self._llm(raw)
        )

        session = yamlmini.parse(block)["session"]
        self.assertEqual(
            session["scores"],
            {
                "fluency": 5,
                "grammar": 4,
                "lexis": 3,
                "organisation": 2,
                "interaction": 5,
            },
        )

    def test_inline_map_any_key(self):
        raw = WRAPUP_BLOCK.replace(
            '  new_vocab:\n    - "peak traffic"\n    - "cut scope"\n',
            '  new_vocab: {a: "one", b: "two"}\n',
            1,
        )
        session = talk._parse_session_yaml(raw)

        self.assertEqual(session["new_vocab"], {"a": "one", "b": "two"})

    def test_quoted_comma_is_kept(self):
        raw = WRAPUP_BLOCK.replace(
            '  new_vocab:\n    - "peak traffic"\n    - "cut scope"\n',
            '  new_vocab: {a: "x, y", b: "two"}\n',
            1,
        )
        session = talk._parse_session_yaml(raw)

        self.assertEqual(session["new_vocab"], {"a": "x, y", "b": "two"})

    def test_prompt_bans_flow_maps(self):
        prompt = (app_config.PROMPTS / "wrapup.md").read_text(encoding="utf-8")

        self.assertIn("YAML en bloque", prompt)
        self.assertIn("nunca llaves `{ }` ni corchetes en línea", prompt)

    def test_unparseable_inline_map_is_reported(self):
        raw = WRAPUP_BLOCK.replace(
            "  scores:\n"
            "    fluency: 3\n"
            "    grammar: 3\n"
            "    lexis: 4\n"
            "    organisation: 3\n"
            "    interaction: 4\n",
            "  scores: {fluency: 5, grammar}\n",
            1,
        )

        with self.assertRaises(ValueError):
            talk._parse_session_yaml(raw)

    def test_python_overrides_date_and_minutes(self):
        raw = WRAPUP_BLOCK.replace(
            "session:\n",
            "session:\n"
            "  date: 1999-01-01\n"
            "  minutes: 99\n"
            "  difficulty: A1\n"
            "  scenario: invented scenario\n",
            1,
        )
        block = talk.build_session_block(
            WRAPUP_TURNS, SCENARIO, 7, "B2", llm=self._llm(raw)
        )

        session = yamlmini.parse(block)["session"]
        self.assertEqual(session["date"], date.today().isoformat())
        self.assertEqual(session["minutes"], 7)
        self.assertEqual(session["difficulty"], "B2")
        self.assertEqual(session["scenario"], SCENARIO["scenario"])
        self.assertNotIn("1999-01-01", block)
        self.assertNotIn("invented scenario", block)

    def test_prompt_carries_transcript_and_rules(self):
        seen = []
        talk.build_session_block(
            WRAPUP_TURNS, SCENARIO, 7, "B2", llm=self._llm(WRAPUP_BLOCK, seen)
        )

        self.assertEqual(len(seen), 1)
        prompt = seen[0]
        for turn in WRAPUP_TURNS:
            self.assertIn(turn["text"], prompt)
        self.assertIn('"role": "user"', prompt)
        self.assertIn('"role": "assistant"', prompt)
        self.assertIn(SCENARIO["scenario"], prompt)
        for rule in ("copiada literalmente", "no inventes", "solo el bloque YAML"):
            self.assertIn(rule, prompt)

    def test_strips_surrounding_text(self):
        variants = (
            "Here is the report:\n\n" + WRAPUP_BLOCK,
            "```yaml\n" + WRAPUP_BLOCK + "```",
            "Sure, this is the wrap-up:\n```yaml\n" + WRAPUP_BLOCK + "```\nThanks!",
            "Here is the report:\n" + WRAPUP_BLOCK + "Hope it helps.",
        )
        for raw in variants:
            with self.subTest(raw=raw[:40]):
                block = talk.build_session_block(
                    WRAPUP_TURNS, SCENARIO, 6, "B2", llm=self._llm(raw)
                )
                session = yamlmini.parse(block)["session"]
                self.assertEqual(session["date"], date.today().isoformat())
                self.assertEqual(session["minutes"], 6)
                self.assertEqual(len(session["corrections"]), 2)

    def test_drops_corrections_not_in_transcript(self):
        raw = WRAPUP_BLOCK.replace(
            '  new_vocab:\n',
            '    - said: "The migration will be finished next week."\n'
            '      better: "The migration will be finished next week."\n'
            "      pattern: verb-form\n"
            "  new_vocab:\n",
            1,
        )
        block = talk.build_session_block(
            WRAPUP_TURNS, SCENARIO, 6, "B2", llm=self._llm(raw)
        )

        data = yamlmini.parse(block)
        session = data["session"]
        self.assertEqual(len(session["corrections"]), 2)
        self.assertTrue(data["warnings"])
        self.assertTrue(
            any("transcripción" in warning for warning in data["warnings"])
        )

class _ReportLlm:
    """LLM falso: conversa con ``OK.`` y devuelve el bloque cuando el prompt es el wrap-up."""

    def __init__(self, raw: str = WRAPUP_BLOCK, error: Exception | None = None):
        self.raw = raw
        self.error = error
        self.prompts: list[str] = []

    def stream(self, messages, cancel):
        prompt = messages[-1].get("content", "")
        self.prompts.append(prompt)
        is_report = "WHAT TO PRODUCE" in prompt
        if is_report and self.error is not None:
            raise self.error
        yield self.raw if is_report else "OK."


@unittest.skipUnless(VOICEKIT_AVAILABLE, "voicekit no está importable")
class EndWithReportTests(unittest.TestCase):
    """M5g2: cerrar la sesión y que el informe entre por el camino existente."""

    def setUp(self):
        from voicekit.providers.fakes import FakeStt, FakeTts

        self.env = support.TestEnv("agent-drills.json").__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.addCleanup(self._close_session)

        self.stt = FakeStt()
        self.tts = FakeTts()
        self.report_llm = _ReportLlm()
        self._providers = mock.patch.object(
            talk,
            "_build_providers",
            lambda config: (self.stt, self.report_llm, self.tts),
        )
        self._providers.start()
        self.addCleanup(self._providers.stop)
        self.session = None

    def _close_session(self):
        current = talk.current()
        if current is not None:
            try:
                current.end()
            except Exception:  # noqa: BLE001
                pass

    def _start_with_turns(self):
        self.session = talk.TalkSession.start(scenario=SCENARIO, difficulty="B2")
        for said in (
            "I think we should keep the date because the old broker handled the peak traffic last year.",
            "We can cutted the replay feature and still deliver on time.",
        ):
            number = self.session.say(said)
            self.assertTrue(self.session.wait(number, timeout=5))

    def test_end_with_report_writes_vault_row(self):
        self._start_with_turns()

        payload = self.session.end(report=True)

        self.assertEqual(payload["state"], "closed")
        self.assertIsNone(talk.current())
        today = date.today().isoformat()
        self.assertEqual(payload["report"]["row"]["date"], today)
        self.assertEqual(payload["report"]["row"]["minutes"], 1)
        self.assertTrue(payload["report"]["drills"])
        rows = [row for row in vault.parse_sessions() if row["date"] == today]
        self.assertEqual(len(rows), 1)

    def test_report_drills_are_queryable(self):
        self._start_with_turns()
        payload = self.session.end(report=True)
        prompts = {drill["prompt"] for drill in payload["report"]["drills"]}
        self.assertTrue(prompts)

        with in_process_server() as base:
            status, raw, _headers = support.http("GET", base + "/api/drills")
        self.assertEqual(status, 200, raw[:300])
        body = json.loads(raw.decode("utf-8"))
        self.assertTrue(body["items"])
        self.assertTrue(prompts & {item["prompt"] for item in body["items"]})

    def test_minutes_come_from_clock(self):
        self._start_with_turns()
        # El modelo pide 99 minutos en el YAML; el reloj de la sesión manda.
        self.report_llm.raw = WRAPUP_BLOCK.replace(
            "session:\n", "session:\n  minutes: 99\n", 1
        )
        self.session._started_at -= timedelta(seconds=2)

        payload = self.session.end(report=True)

        self.assertEqual(payload["report"]["row"]["minutes"], 1)
        today = date.today().isoformat()
        raw_path = self.env.data / "reports" / ("%s-raw.txt" % today)
        self.assertTrue(raw_path.is_file())
        self.assertIn("minutes: 1", raw_path.read_text(encoding="utf-8"))
        self.assertNotIn("minutes: 99", raw_path.read_text(encoding="utf-8"))

    def test_report_failure_keeps_session(self):
        self._start_with_turns()
        self.report_llm.error = RuntimeError("el modelo no responde")

        with self.assertRaises(talk.TalkReportFailed) as ctx:
            self.session.end(report=True)

        self.assertIn("el modelo no responde", str(ctx.exception))
        self.assertIsNone(talk.current())
        self.assertEqual(self.session._store.state, "closed")

        payload = self.session.end(report=False)
        self.assertEqual(payload["state"], "closed")
        self.assertTrue(any(turn["role"] == "user" for turn in payload["turns"]))

    def test_report_failure_route_is_502(self):
        with in_process_server() as base:
            status, raw, _headers = support.http(
                "POST", base + "/api/talk/start", payload={}
            )
            self.assertEqual(status, 200, raw[:300])
            self.report_llm.error = RuntimeError("el modelo no responde")

            status, raw, _headers = support.http(
                "POST", base + "/api/talk/end", payload={"report": True}
            )

        self.assertEqual(status, 502, raw[:300])
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["error"], "report_failed")
        self.assertIn("el modelo no responde", body["detail"])
        self.assertIsNone(talk.current())

    def test_repeated_end_does_not_duplicate(self):
        self._start_with_turns()

        first = self.session.end(report=True)
        rows = [row for row in vault.parse_sessions() if row["date"] == date.today().isoformat()]
        self.assertEqual(len(rows), 1)

        second = self.session.end(report=True)

        self.assertEqual(len(
            [row for row in vault.parse_sessions() if row["date"] == date.today().isoformat()]
        ), 1)
        self.assertEqual(second["report"]["row"]["date"], first["report"]["row"]["date"])
        self.assertTrue(
            any("ya procesado" in warning for warning in second["report"]["warnings"])
        )


@unittest.skipUnless(VOICEKIT_AVAILABLE, "voicekit no está importable")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self._data = voicekit_data_dir()
        self.data_dir = self._data.__enter__()
        self.addCleanup(self._data.__exit__, None, None, None)
        self.addCleanup(self._close_session)

        from voicekit.providers.fakes import FakeLlm, FakeStt, FakeTts

        self.stt = FakeStt()
        self.llm = FakeLlm()
        self.tts = FakeTts()
        self._providers = mock.patch.object(
            talk,
            "_build_providers",
            lambda config: (self.stt, self.llm, self.tts),
        )
        self._providers.start()
        self.addCleanup(self._providers.stop)

    def _close_session(self):
        current = talk.current()
        if current is not None:
            try:
                current.end()
            except Exception:  # noqa: BLE001
                pass

    def test_start_creates_session_and_second_start_is_busy(self):
        first = talk.TalkSession.start()

        self.assertTrue(first.session)
        self.assertIs(talk.current(), first)
        with self.assertRaises(talk.TalkBusy):
            talk.TalkSession.start()

        payload = first.end()
        self.assertEqual(payload["state"], "closed")
        self.assertIsNone(talk.current())

        second = talk.TalkSession.start()
        self.assertNotEqual(second.session, first.session)
        second.end()

    def test_sessions_live_in_app_data(self):
        session = talk.TalkSession.start()
        session_dir = Path(self.data_dir) / "talk" / "sessions" / session.session

        self.assertTrue(session_dir.is_dir())
        self.assertFalse(
            (app_config.DATA_DEFAULT / "talk" / "sessions" / session.session).exists()
        )

        number = session.say("hello")
        self.assertTrue(session.wait(number, timeout=5))
        self.assertTrue((session_dir / "tts").is_dir())
        self.assertTrue(list((session_dir / "tts").glob("%d-*.wav" % number)))
        session.end()

    def test_sentence_events_carry_url(self):
        session = talk.TalkSession.start()

        number = session.say("hello")
        self.assertTrue(session.wait(number, timeout=5))

        sentences = [
            event for event in session.events(0) if event["type"] == "tts.sentence"
        ]
        self.assertTrue(sentences)
        for event in sentences:
            url = event["data"]["url"]
            self.assertRegex(
                url,
                r"^/media/talk/%s/%d-\d+\.wav$"
                % (re.escape(session.session), number),
            )
            path = (
                Path(self.data_dir)
                / "talk"
                / "sessions"
                / session.session
                / "tts"
                / Path(url).name
            )
            self.assertTrue(path.is_file())
        session.end()

    def test_tests_do_not_touch_real_data(self):
        real = app_config.DATA_DEFAULT
        before = {path for path in real.rglob("*")} if real.exists() else set()

        session = talk.TalkSession.start()
        number = session.say("hello")
        self.assertTrue(session.wait(number, timeout=5))
        self.assertEqual(app_config.data_dir(), Path(self.data_dir))
        session.end()

        after = {path for path in real.rglob("*")} if real.exists() else set()
        self.assertEqual(before, after)

    def test_turn_emits_events_and_end_returns_turns_and_metrics(self):
        session = talk.TalkSession.start()

        number = session.turn(silence_wav())
        self.assertTrue(session.wait(number, timeout=5))

        events = session.events(0)
        self.assertTrue(any(event["turn"] == number for event in events))
        self.assertEqual(
            [event["seq"] for event in events],
            sorted(event["seq"] for event in events),
        )
        for event in events:
            self.assertTrue(
                {"v", "session", "turn", "seq", "type"} <= set(event), event
            )

        payload = session.end()
        self.assertEqual(payload["session"], session.session)
        self.assertEqual(payload["state"], "closed")
        self.assertIsInstance(payload["turns"], list)
        self.assertIsInstance(payload["metrics"], list)
        self.assertTrue(any(row["role"] == "user" for row in payload["turns"]))
        self.assertTrue(any("stt_ms" in row for row in payload["metrics"]))

    def test_say_skips_stt(self):
        session = talk.TalkSession.start()

        number = session.say("hello from the keyboard")
        self.assertTrue(session.wait(number, timeout=5))

        self.assertEqual(self.stt.calls, [])
        finals = [event for event in session.events(0) if event["type"] == "stt.final"]
        self.assertEqual(finals[0]["data"]["text"], "hello from the keyboard")
        session.end()

    def test_turn_returns_immediately(self):
        session = talk.TalkSession.start()
        self.stt.delay = 0.5

        started = time.monotonic()
        number = session.turn(silence_wav())
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.1)
        self.assertTrue(session.wait(number, timeout=5))
        session.end()

    def test_no_blocking_helpers_left(self):
        source = (
            Path(__file__).resolve().parents[1] / "lib" / "talk.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(len(re.findall(r"_wait_stt|TalkEmptyAudio", source)), 0)

    def test_talk_session_has_no_cancel(self):
        session = talk.TalkSession.start()

        self.assertFalse(hasattr(talk.TalkSession, "cancel"))
        self.assertFalse(hasattr(session, "cancel"))
        session.end()


class VoicekitPathTests(unittest.TestCase):
    """El TOML del motor de voz vive en el equipo, no en el repo."""

    def test_defaults_to_the_user_config_dir_and_honours_the_env(self):
        saved = os.environ.pop("REFIRE_VOICEKIT", None)
        try:
            self.assertEqual(talk._config_path(),
                             Path("~/.config/refire/voicekit.toml").expanduser())
            os.environ["REFIRE_VOICEKIT"] = "/tmp/otro-voicekit.toml"
            self.assertEqual(talk._config_path(), Path("/tmp/otro-voicekit.toml"))
        finally:
            os.environ.pop("REFIRE_VOICEKIT", None)
            if saved is not None:
                os.environ["REFIRE_VOICEKIT"] = saved


@unittest.skipUnless(VOICEKIT_AVAILABLE, "voicekit no está importable")
class ConfiguredModelTests(unittest.TestCase):
    """M5d2: la app lee su ``voicekit.toml`` y se lo pasa al motor."""

    def setUp(self):
        self._data = voicekit_data_dir()
        self.data_dir = self._data.__enter__()
        self.addCleanup(self._data.__exit__, None, None, None)
        self.addCleanup(self._close_session)
        # Siempre el ejemplo versionado, nunca el TOML del que ejecuta los tests: así el
        # test prueba el código y no la instalación de cada uno.
        self.config_path = Path(self.data_dir) / "voicekit-desde-ejemplo.toml"
        self.config_path.write_bytes(
            (app_config.APP / "voicekit.example.toml").read_bytes()
        )

    def _close_session(self):
        current = talk.current()
        if current is not None:
            try:
                current.end()
            except Exception:  # noqa: BLE001
                pass

    def test_config_comes_from_app_toml(self):
        from voicekit.config import load_config

        config_path = self.config_path
        self.assertTrue(config_path.is_file())

        cfg = load_config(
            path=config_path,
            data_dir=app_config.data_dir() / "talk",
        )

        self.assertTrue(cfg.llm_base_url)
        self.assertTrue(cfg.llm_model)

    def test_talk_uses_configured_model(self):
        from voicekit.providers import llm_openai

        captured = {}

        class CapturingLlm:
            def __init__(self, base_url, model, **kwargs):
                captured["base_url"] = base_url
                captured["model"] = model

        with mock.patch.object(llm_openai, "OpenAiCompatLlm", CapturingLlm):
            with mock.patch.object(talk, "_config_path", return_value=self.config_path):
                session = talk.TalkSession.start()
                session.end()

        from voicekit.config import load_config

        cfg = load_config(path=self.config_path, data_dir=app_config.data_dir() / "talk")
        self.assertEqual(captured["base_url"], cfg.llm_base_url)
        self.assertEqual(captured["model"], cfg.llm_model)
        self.assertTrue(captured["base_url"] and captured["model"])

    def test_missing_config_is_llm_unavailable(self):
        missing = Path(self.data_dir) / "voicekit.toml"
        self.assertFalse(missing.exists())

        with mock.patch.object(talk, "_config_path", return_value=missing):
            session = talk.TalkSession.start()
            number = session.say("hello")
            self.assertTrue(session.wait(number, timeout=5))
            errors = [
                event
                for event in session.events(0)
                if event["turn"] == number and event["type"] == "error"
            ]
            session.end()

        self.assertTrue(errors)
        self.assertEqual(errors[0]["data"]["code"], "llm_unavailable")
        self.assertIn("llm.", errors[0]["data"]["detail"])


@unittest.skipUnless(VOICEKIT_AVAILABLE, "voicekit no está importable")
class HttpTalkTests(unittest.TestCase):
    def setUp(self):
        self._data = voicekit_data_dir()
        self.data_dir = self._data.__enter__()
        self.addCleanup(self._data.__exit__, None, None, None)

        from voicekit.providers.fakes import FakeLlm, FakeStt, FakeTts

        self.stt = FakeStt()
        self.llm = FakeLlm()
        self.tts = FakeTts()
        self._providers = mock.patch.object(
            talk,
            "_build_providers",
            lambda config: (self.stt, self.llm, self.tts),
        )
        self._providers.start()
        self.addCleanup(self._providers.stop)

        # La ruta genera el escenario con el agente: en tests se sustituye por un doble.
        self.scenario = SCENARIO
        self._scenario_patch = mock.patch.object(
            app.session, "scenario", return_value=self.scenario
        )
        self._scenario = self._scenario_patch.start()
        self.addCleanup(self._scenario_patch.stop)

        self._server = in_process_server()
        self.base = self._server.__enter__()
        self.addCleanup(self._server.__exit__, None, None, None)
        self.addCleanup(self._close_session)

    def _close_session(self):
        current = talk.current()
        if current is not None:
            try:
                current.end()
            except Exception:  # noqa: BLE001
                pass

    def _events(self, since: int = 0):
        status, raw, headers = support.http(
            "GET", self.base + "/api/talk/events?since=%d" % since, timeout=5
        )
        self.assertEqual(status, 200, raw[:300])
        self.assertIn("text/event-stream", headers.get("Content-Type", ""))
        return parse_sse(raw)

    def _wait_for(self, predicate, timeout: float = 5.0):
        deadline = time.time() + timeout
        last = []
        while time.time() < deadline:
            last = self._events()
            if predicate(last):
                return last
            time.sleep(0.02)
        self.fail("no llegó el evento esperado; últimos: %r" % (last[-3:],))

    def _start(self):
        status, raw, _headers = support.http("POST", self.base + "/api/talk/start")
        self.assertEqual(status, 200, raw[:300])
        body = json.loads(raw.decode("utf-8"))
        self.assertTrue(body["session"])
        return body["session"]

    def test_serves_sentence_wav(self):
        session_id = self._start()
        current = talk.current()
        number = current.say("hello")
        self.assertTrue(current.wait(number, timeout=5))

        sentence = next(
            event for event in current.events(0)
            if event["type"] == "tts.sentence" and event["turn"] == number
        )
        url = sentence["data"]["url"]
        self.assertEqual(url, "/media/talk/%s/%d-0.wav" % (session_id, number))
        path = (
            Path(self.data_dir)
            / "talk"
            / "sessions"
            / session_id
            / "tts"
            / Path(url).name
        )

        status, raw, headers = support.http("GET", self.base + url)
        self.assertEqual(status, 200, raw[:300])
        self.assertEqual(headers.get("Content-Type"), "audio/wav")
        self.assertEqual(headers.get("Content-Length"), str(len(raw)))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(raw, path.read_bytes())

    def test_media_rejects_unknown_and_traversal(self):
        session_id = self._start()

        status, raw, _headers = support.http(
            "GET", self.base + "/media/talk/%s/no-existe.wav" % session_id
        )
        self.assertEqual(status, 404, raw[:300])

        status, raw, _headers = support.http(
            "GET", self.base + "/media/talk/no-existe/1-0.wav"
        )
        self.assertEqual(status, 404, raw[:300])

        for path in (
            "/media/talk/../etc/passwd",
            "/media/talk/%s/../etc/passwd" % session_id,
            "/media/talk/%s/no%%2Fexiste.wav" % session_id,
            "/media/talk/%s/..%%2Fetc%%2Fpasswd" % session_id,
        ):
            status, raw, _headers = support.http("GET", self.base + path)
            self.assertIn(status, (400, 404), (path, raw[:300]))
            self.assertNotIn(b"root:", raw)

        status, raw, _headers = support.http(
            "GET", self.base + "/media/talk/%s/nota.txt" % session_id
        )
        self.assertEqual(status, 400, raw[:300])

    def test_start_returns_session_and_second_start_is_409(self):
        session = self._start()

        status, raw, _headers = support.http("POST", self.base + "/api/talk/start")
        self.assertEqual(status, 409, raw[:300])
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["error"], "talk_busy")
        self.assertEqual(talk.current().session, session)

    def test_start_generates_and_returns_scenario(self):
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/start",
            payload={"difficulty": "C1", "focus": ["conditionals"], "minutes": 8},
            timeout=10,
        )
        self.assertEqual(status, 200, raw[:300])
        body = json.loads(raw.decode("utf-8"))

        self.assertTrue(body["session"])
        self.assertEqual(body["scenario"], SCENARIO)
        self._scenario.assert_called_once_with(
            difficulty="C1", focus=["conditionals"], minutes=8
        )

    def test_engine_gets_system_prompt(self):
        from voicekit import engine as engine_module

        captured = {}
        real_engine = engine_module.Engine

        class CapturingEngine(real_engine):
            def __init__(self, *args, **kwargs):
                captured["system_prompt"] = kwargs.get("system_prompt")
                super().__init__(*args, **kwargs)

        with mock.patch.object(engine_module, "Engine", CapturingEngine):
            status, raw, _headers = support.http(
                "POST", self.base + "/api/talk/start", payload={}, timeout=10
            )

        self.assertEqual(status, 200, raw[:300])
        self.assertEqual(
            captured["system_prompt"], talk.build_system_prompt(SCENARIO)
        )

    def test_scenario_failure_uses_neutral_prompt(self):
        from voicekit import engine as engine_module

        captured = {}
        real_engine = engine_module.Engine

        class CapturingEngine(real_engine):
            def __init__(self, *args, **kwargs):
                captured["system_prompt"] = kwargs.get("system_prompt")
                super().__init__(*args, **kwargs)

        with mock.patch.object(
            app.session, "scenario", side_effect=RuntimeError("el agente no responde")
        ), mock.patch.object(engine_module, "Engine", CapturingEngine):
            status, raw, _headers = support.http(
                "POST", self.base + "/api/talk/start", payload={}, timeout=10
            )

        self.assertEqual(status, 200, raw[:300])
        body = json.loads(raw.decode("utf-8"))
        self.assertTrue(body["session"])
        self.assertIsNone(body["scenario"])

        prompt = captured["system_prompt"]
        self.assertIn("technical interviewer", prompt)
        for rule in HARD_RULES:
            self.assertIn(rule, prompt)
        self.assertNotRegex(prompt, r"\d")
        for item in SCENARIO["ammo"]:
            self.assertNotIn(item, prompt)
        self.assertNotIn(SCENARIO["scenario"], prompt)

    def test_turn_multipart_202_and_event_with_that_turn(self):
        self._start()
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )

        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        number = json.loads(raw.decode("utf-8"))["turn"]
        self.assertIsInstance(number, int)

        events = self._wait_for(
            lambda found: any(
                event["turn"] == number and event["type"] == "turn.done"
                and not event["data"].get("cancelled")
                for event in found
            )
        )
        self.assertTrue(any(event["turn"] == number for event in events))
        seqs = [event["seq"] for event in events]
        self.assertEqual(seqs, sorted(seqs))

    def test_sse_lines_are_json_with_the_wire_fields(self):
        self._start()
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])

        events = self._wait_for(
            lambda found: any(event["type"] == "turn.done" for event in found)
        )
        self.assertTrue(events)
        for event in events:
            self.assertTrue(
                {"v", "session", "turn", "seq", "type"} <= set(event), event
            )
            self.assertIsInstance(event["data"], dict)

        # `since` filtra por seq: el último evento no vuelve a salir.
        last = events[-1]["seq"]
        self.assertEqual(self._events(last), [])

    def test_cancel_route_is_gone(self):
        self._start()

        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/cancel", payload={"turn": 1}
        )
        self.assertEqual(status, 404, raw[:300])
        self.assertEqual(json.loads(raw.decode("utf-8"))["error"], "not_found")

        # La sesión sigue viva: después del 404 se puede abrir un turno normal.
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        number = json.loads(raw.decode("utf-8"))["turn"]
        self._wait_for(
            lambda found: any(
                event["type"] == "turn.done"
                and event["turn"] == number
                and not event["data"].get("cancelled")
                for event in found
            )
        )

    def test_turn_is_202_even_with_silent_audio(self):
        self._start()

        # El audio llega vacío: no se decide en la capa HTTP, lo dirá el motor.
        body, ctype = multipart({}, {"file": ("turn.wav", b"", "audio/wav")})
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        first = json.loads(raw.decode("utf-8"))["turn"]
        self._wait_for(
            lambda found: any(
                event["turn"] == first and event["type"] == "turn.done"
                for event in found
            )
        )

        # El audio no está vacío, pero la transcripción sí.
        self.stt.text = ""
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        second = json.loads(raw.decode("utf-8"))["turn"]
        self._wait_for(
            lambda found: any(
                event["turn"] == second and event["type"] == "turn.done"
                for event in found
            )
        )

    def test_empty_audio_event_carries_retry_hint(self):
        self._start()
        self.stt.text = ""
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        number = json.loads(raw.decode("utf-8"))["turn"]

        events = self._wait_for(
            lambda found: any(
                event["turn"] == number and event["type"] == "error"
                for event in found
            )
        )
        error = next(
            event for event in events
            if event["turn"] == number and event["type"] == "error"
        )
        self.assertEqual(error["data"]["code"], "stt_empty_audio")
        self.assertIs(error["data"]["retry"], True)
        self._wait_for(
            lambda found: any(
                event["turn"] == number and event["type"] == "turn.done"
                for event in found
            )
        )

    def test_empty_turn_not_in_history(self):
        self._start()
        self.stt.text = ""
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        number = json.loads(raw.decode("utf-8"))["turn"]
        self._wait_for(
            lambda found: any(
                event["turn"] == number and event["type"] == "turn.done"
                for event in found
            )
        )

        status, raw, _headers = support.http("POST", self.base + "/api/talk/end")
        self.assertEqual(status, 200, raw[:300])
        turns = json.loads(raw.decode("utf-8"))["turns"]
        self.assertFalse(
            [turn for turn in turns if turn["role"] == "user" and turn["number"] == number]
        )

    def test_end_returns_turns_and_metrics_and_allows_new_start(self):
        self._start()
        body, ctype = multipart(
            {}, {"file": ("turn.wav", silence_wav(), "audio/wav")}
        )
        status, raw, _headers = support.http(
            "POST", self.base + "/api/talk/turn", body=body,
            headers={"Content-Type": ctype}, timeout=10,
        )
        self.assertEqual(status, 202, raw[:300])
        self._wait_for(
            lambda found: any(event["type"] == "turn.done" for event in found)
        )

        status, raw, _headers = support.http("POST", self.base + "/api/talk/end")
        self.assertEqual(status, 200, raw[:300])
        payload = json.loads(raw.decode("utf-8"))
        self.assertTrue(payload["session"])
        self.assertEqual(payload["state"], "closed")
        self.assertIsInstance(payload["turns"], list)
        self.assertIsInstance(payload["metrics"], list)
        self.assertTrue(any(turn["role"] == "user" for turn in payload["turns"]))
        self.assertIsNone(talk.current())

        self._start()


if __name__ == "__main__":
    unittest.main()
