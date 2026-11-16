"""Pipeline del informe en proceso: fila, drills, idempotencia, 422 y E/S confinada."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import support
from support import TestEnv

from lib import agent, config, session, srs, vault

_OPENS = []
_RECORD = False


def _audit(event, args):
    if not _RECORD or event != "open":
        return
    path, mode = args[0], args[1]
    if isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+")):
        _OPENS.append((str(path), mode))


sys.addaudithook(_audit)


class ReportPipelineTest(unittest.TestCase):
    def setUp(self):
        self.env = TestEnv("agent-drills.json").__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.raw = support.report_text()

    def _report_files(self):
        return sorted((self.env.data / "reports").glob("*"))

    def test_report_writes_one_row_and_drills(self):
        before = config.vault_path().read_bytes()
        out = session.process_report(self.raw)

        self.assertTrue(out["ok"])
        self.assertTrue(out["row_written"])
        self.assertEqual(out["session"]["date"], "2026-11-02")
        self.assertGreaterEqual(out["drills_added"], 7)
        self.assertLessEqual(out["drills_added"], 20)
        self.assertNotIn("drills_pending", out["warnings"])

        rows = vault.parse_sessions()
        self.assertEqual(len(rows), 2)  # la de prueba + la nueva
        self.assertEqual([r["date"] for r in rows].count("2026-11-02"), 2)  # mismo día distinto

        after = config.vault_path().read_bytes()
        text_after = after.decode("utf-8")
        inserted = [ln for ln in text_after.splitlines() if ln.startswith("| 2026-11-02 | 12 |")]
        self.assertEqual(len(inserted), 1)
        restored = text_after.replace(inserted[0] + "\n", "", 1).encode("utf-8")
        self.assertEqual(support.sha256_bytes(restored), support.sha256_bytes(before))

        names = {p.name for p in self._report_files()}
        self.assertIn("2026-11-02-raw.txt", names)
        self.assertIn("2026-11-02.json", names)
        doc = json.loads((self.env.data / "reports" / "2026-11-02.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["raw_sha256"], support.sha256_bytes(self.raw.encode("utf-8")))
        self.assertEqual(doc["row"]["date"], "2026-11-02")

    def test_drills_come_from_corrections_only(self):
        session.process_report(self.raw)
        state = srs.load()
        items = state["items"]
        self.assertTrue(items)
        for item in items:
            self.assertIn(item["pattern"], (
                "conditionals", "articles", "prepositions", "collocations",
                "verb-form", "word-choice", "discourse",
            ))
            self.assertTrue(item["source"]["said"])
            self.assertTrue(item["answer"])
        # ningún drill puede ser una reescritura abierta: eso no se puede acertar nunca
        self.assertFalse(any("Say it the natural way" in i["prompt"] for i in items))
        self.assertTrue(all("___" in i["prompt"] for i in items))
        self.assertTrue(all(len(i["answer"].split()) <= 4 for i in items))
        # 3-5 por patrón como máximo
        by_pattern = {}
        for item in items:
            by_pattern[item["pattern"]] = by_pattern.get(item["pattern"], 0) + 1
        self.assertTrue(all(count <= 5 for count in by_pattern.values()))

    def test_same_report_twice_is_duplicate_without_new_row(self):
        session.process_report(self.raw)
        rows_before = len(vault.parse_sessions())
        with self.assertRaises(session.DuplicateReport):
            session.process_report(self.raw)
        self.assertEqual(len(vault.parse_sessions()), rows_before)
        self.assertEqual(len(list((self.env.data / "reports").glob("*.json"))), 1)
        self.assertEqual(len(srs.load()["items"]), 10)

    def test_idempotency_is_by_exact_text(self):
        """El hash es del texto exacto pegado: un byte de más ya es otro informe."""
        session.process_report(self.raw)
        out = session.process_report(self.raw + "\n")
        self.assertTrue(out["ok"])
        self.assertEqual(len(vault.parse_sessions()), 3)
        self.assertEqual(len(list((self.env.data / "reports").glob("*.json"))), 2)

    def test_broken_yaml_keeps_vault_untouched_and_saves_raw(self):
        before = support.sha256_file(config.vault_path())
        broken = "session:\n  date: [2026-11-02\n  corrections: oops\n"
        with self.assertRaises(session.ReportInvalid) as ctx:
            session.process_report(broken)
        self.assertEqual(support.sha256_file(config.vault_path()), before)
        raw_path = ctx.exception.raw_path
        self.assertIsNotNone(raw_path)
        self.assertTrue(raw_path.exists())
        self.assertEqual(raw_path.read_text(encoding="utf-8"), broken)
        self.assertRegex(raw_path.name, r"^\d{4}-\d{2}-\d{2}-\d{4}-raw\.txt$")
        self.assertEqual(srs.load()["items"], [])

    def test_report_without_vault_saves_it_with_a_warning(self):
        """RF-15: sin nota del registro el informe no se pierde; se guarda y se avisa."""
        saved = os.environ.get("REFIRE_VAULT")
        os.environ["REFIRE_VAULT"] = str(self.env.tmp / "no-existe.md")
        try:
            out = session.process_report(self.raw)
        finally:
            if saved is None:
                os.environ.pop("REFIRE_VAULT", None)
            else:
                os.environ["REFIRE_VAULT"] = saved

        self.assertTrue(out["ok"])
        self.assertFalse(out["row_written"])
        self.assertIn("vault_no_encontrado", out["warnings"])
        date = out["session"]["date"]
        self.assertTrue((config.reports_dir() / ("%s.json" % date)).is_file())
        self.assertTrue((config.reports_dir() / ("%s-raw.txt" % date)).is_file())

    def test_agent_failure_still_writes_row_with_warning(self):
        os.environ["REFIRE_AGENT_FIXTURE"] = str(support.FIXTURES / "agent-garbage.txt")
        out = session.process_report(self.raw)
        self.assertTrue(out["ok"])
        self.assertTrue(out["row_written"])
        self.assertEqual(out["drills_added"], 0)
        self.assertIn("drills_pending", out["warnings"])
        self.assertEqual(len(vault.parse_sessions()), 2)

    def test_regenerate_drills_after_failure(self):
        os.environ["REFIRE_AGENT_FIXTURE"] = str(support.FIXTURES / "agent-garbage.txt")
        session.process_report(self.raw)
        self.assertEqual(srs.load()["items"], [])

        os.environ["REFIRE_AGENT_FIXTURE"] = str(support.FIXTURES / "agent-drills.json")
        out = session.regenerate_drills("2026-11-02")
        self.assertTrue(out["ok"])
        self.assertEqual(out["drills_added"], 10)
        self.assertEqual(len(srs.load()["items"]), 10)

    def test_regenerate_for_unknown_date(self):
        with self.assertRaises(session.ReportInvalid):
            session.regenerate_drills("2020-01-01")

    def test_two_sessions_same_day_are_two_sessions(self):
        """En un día puede haber varias sesiones: cada informe es una, no se pisan por fecha."""
        first = support.report_text()
        session.process_report(first)
        second = first.replace("architecture review", "payments incident postmortem")
        session.process_report(second)

        state = session.state()
        same_day = [s for s in state["sessions"] if s["date"] == "2026-11-02"]
        # tres filas: la escrita a mano del fixture (sin informe) + las dos procesadas
        self.assertEqual(len(same_day), 3)
        with_report = [s for s in same_day if s["report_id"]]
        self.assertEqual(len(with_report), 2)
        scenarios = " ".join(s["scenario"] for s in with_report)
        self.assertIn("payments incident postmortem", scenarios)
        self.assertIn("architecture review", scenarios)
        # cada informe aporta sus propias correcciones y su id
        self.assertEqual(len({s["report_id"] for s in with_report}), 2)
        self.assertTrue(all(s["corrections"] for s in with_report))

    def test_regenerating_by_date_with_two_reports_refuses_to_guess(self):
        first = support.report_text()
        session.process_report(first)
        session.process_report(first.replace("architecture review", "another chat"))
        with self.assertRaises(session.ReportInvalid) as ctx:
            session.regenerate_drills("2026-11-02")
        self.assertIn("indica cuál", str(ctx.exception))

    def test_state_exposes_vault_sessions_and_patterns(self):
        session.process_report(self.raw)
        state = session.state()
        self.assertEqual(len(state["sessions"]), 2)
        last = state["sessions"][-1]
        self.assertEqual(last["date"], "2026-11-02")
        self.assertEqual(last["minutes"], 12)
        self.assertEqual(last["scores"]["fluency"], 4)
        self.assertEqual(len(last["corrections"]), 7)
        names = {p["name"] for p in state["patterns"]}
        self.assertIn("conditionals", names)
        self.assertEqual(state["drills"]["total"], 10)
        self.assertEqual(state["stats"]["sessions"], 2)
        self.assertIsNone(state["stats"]["measured"]["fillers_per_minute"])
        self.assertEqual(state["stats"]["estimated"]["fillers_per_minute"], 6.0)

    def test_scenario_uses_agent_output(self):
        os.environ["REFIRE_AGENT_FIXTURE"] = str(support.FIXTURES / "agent-scenario.json")
        out = session.scenario("B2", ["conditionals"], 10)
        self.assertEqual(out["scenario"], "A migration review where the new message broker is behind schedule")
        self.assertEqual(out["chatgpt_url"], "https://chatgpt.com/")
        self.assertIn("YOU PLAY: a sceptical staff engineer", out["block_b"])
        self.assertIn("LEVEL: B2", out["block_b"])
        self.assertNotIn("{{", out["block_b"])
        self.assertNotIn("warnings", out)

    def test_scenario_falls_back_when_agent_fails(self):
        os.environ["REFIRE_AGENT_FIXTURE"] = str(support.FIXTURES / "agent-garbage.txt")
        out = session.scenario("C1", ["articles"], 8)
        self.assertTrue(out["ok"])
        self.assertIn("scenario_fallback", out["warnings"])
        self.assertTrue(out["scenario"])
        self.assertNotIn("{{", out["block_b"])
        self.assertIn("LEVEL: C1", out["block_b"])

    def test_writes_stay_inside_data_and_the_vault_note(self):
        global _RECORD
        _OPENS.clear()
        _RECORD = True
        try:
            session.process_report(self.raw)
        finally:
            _RECORD = False

        allowed_root = self.env.data.resolve()
        vault_path = config.vault_path().resolve()
        offenders = []
        for path, _mode in _OPENS:
            resolved = Path(path).resolve()
            if resolved == vault_path:
                continue
            if allowed_root == resolved or allowed_root in resolved.parents:
                continue
            offenders.append(str(resolved))
        self.assertEqual(offenders, [], "escrituras fuera de data/ y del vault: %s" % offenders)


class ModelCardPipelineTest(unittest.TestCase):
    """Correcciones sin hueco -> frase modelo (`kind=model`), nunca un hueco inventado."""

    def setUp(self):
        self.env = TestEnv("agent-drills-model.json").__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.raw = support.report_text()

    def test_fixture_corrections_are_four_gaps_and_three_models(self):
        """Las 7 correcciones del fixture: cada una con su tarjeta, ni una más ni una menos.

        Son 4 huecos + 3 frases modelo, no 6+1: en este fixture hay TRES correcciones cuyo
        arreglo es borrar una palabra (`in`, `to`, `also`) y un borrado no se puede tapar con
        `___` (la respuesta sería vacía). Las tres acaban como `kind=model`.
        """
        sess, _warnings = session.parse_report(self.raw)
        self.assertEqual(len(sess["corrections"]), 7)
        drills = session._local_drills(sess)
        kinds = [d["kind"] for d in drills]
        self.assertEqual(kinds.count("gap"), 4)
        self.assertEqual(kinds.count("model"), 3)
        self.assertEqual(len(drills), 7)

        models = {d["prompt"]: d for d in drills if d["kind"] == "model"}
        self.assertEqual(
            set(models),
            {
                "I am the responsible for the deploy",
                "We must to check the logs",
                "Also, moreover, we need to reschedule",
            },
        )
        self.assertEqual(models["I am the responsible for the deploy"]["answer"],
                         "I am responsible for the deploy")
        self.assertEqual(models["I am the responsible for the deploy"]["pattern"], "articles")
        self.assertNotIn("___", models["I am the responsible for the deploy"]["prompt"])

        # ninguna corrección se queda sin tarjeta, y los huecos siguen siendo huecos
        covered = {(d["source"]["said"], d["source"]["better"]) for d in drills}
        for correction in sess["corrections"]:
            self.assertIn((correction["said"], correction["better"]), covered)
        for gap in (d for d in drills if d["kind"] == "gap"):
            self.assertIn("___", gap["prompt"])
            self.assertLessEqual(len(gap["answer"].split()), 4)

    def test_llm_model_card_that_invents_a_sentence_is_discarded(self):
        sess, _ = session.parse_report(self.raw)
        warnings = []
        accepted = session._validate_drills(
            [
                {"kind": "model", "pattern": "articles",
                 "prompt": "I am the responsible for the deploy",
                 "answer": "I am responsible for the deploy", "alt_answers": [],
                 "source": {"said": "I am the responsible for the deploy",
                            "better": "I am responsible for the deploy"}},
                {"kind": "model", "pattern": "word-choice",
                 "prompt": "I am agree with the plan", "answer": "I agree with the plan",
                 "alt_answers": [],
                 "source": {"said": "I am agree with the plan",
                            "better": "I agree with the plan"}},
            ],
            sess,
            warnings,
        )
        self.assertEqual([d["kind"] for d in accepted], ["model"])
        self.assertEqual(accepted[0]["answer"], "I am responsible for the deploy")
        self.assertTrue(any("fuera_de_corrections" in w for w in warnings))

    def test_report_generates_model_card_without_touching_pattern_stats(self):
        out = session.process_report(self.raw)
        self.assertTrue(out["ok"])
        self.assertEqual(out["drills_added"], 11)  # 10 huecos + 1 frase modelo

        deck = srs.load()
        models = [i for i in deck["items"] if i["kind"] == "model"]
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["prompt"], "I am the responsible for the deploy")
        self.assertTrue(all("___" in i["prompt"] for i in deck["items"] if i["kind"] == "gap"))

        # una frase modelo se marca a mano y no puntúa por patrón
        before = {r["name"]: r for r in srs.pattern_rows(deck, "2026-11-02")}["articles"]["attempts"]
        srs.answer(deck, models[0]["id"], correct=False, today="2026-11-02")
        self.assertEqual(deck["model_marks"], {"seen": 1, "to_review": 1})
        stats = deck.get("pattern_stats") or {}
        self.assertNotIn(models[0]["pattern"], stats)
        after = {r["name"]: r for r in srs.pattern_rows(deck, "2026-11-02")}["articles"]["attempts"]
        self.assertEqual(after, before)


class ScenarioPromptTest(unittest.TestCase):
    """El prompt del alumno es de cada uno: el del equipo manda y el ejemplo es la reserva."""

    def test_user_prompt_wins_and_the_example_is_the_fallback(self):
        from lib import prompts

        tmp = Path(tempfile.mkdtemp(prefix="refire-prompt-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        propio = tmp / "scenario.md"
        saved = os.environ.pop("REFIRE_SCENARIO_PROMPT", None)
        try:
            os.environ["REFIRE_SCENARIO_PROMPT"] = str(propio)
            self.assertIn("LEARNER", prompts.load("scenario.md"))
            propio.write_text("mi prompt de alumno", encoding="utf-8")
            self.assertEqual(prompts.load("scenario.md"), "mi prompt de alumno")
            self.assertEqual(prompts.load("scenario"), "mi prompt de alumno")
        finally:
            os.environ.pop("REFIRE_SCENARIO_PROMPT", None)
            if saved is not None:
                os.environ["REFIRE_SCENARIO_PROMPT"] = saved


class AgentTest(unittest.TestCase):
    def test_agent_error_when_command_missing(self):
        saved = os.environ.pop("REFIRE_AGENT_FIXTURE", None)
        saved_cmd = os.environ.get("REFIRE_AGENT_CMD")
        os.environ["REFIRE_AGENT_CMD"] = "definitely-not-a-real-binary-xyz"
        try:
            with self.assertRaises(agent.AgentError):
                agent.call_agent("hola")
        finally:
            if saved is not None:
                os.environ["REFIRE_AGENT_FIXTURE"] = saved
            if saved_cmd is None:
                os.environ.pop("REFIRE_AGENT_CMD", None)
            else:
                os.environ["REFIRE_AGENT_CMD"] = saved_cmd

    def test_extract_json_from_text_with_noise(self):
        self.assertEqual(agent.extract_json('bla {"a": 1} bla'), {"a": 1})
        self.assertEqual(
            agent.extract_json('```json\n{"scenario": "x", "you_play": "y", "i_play": "z"}\n```'),
            {"scenario": "x", "you_play": "y", "i_play": "z"},
        )
        with self.assertRaises(agent.AgentError):
            agent.extract_json("no json at all")

    def test_agent_without_command_says_how_to_configure_it(self):
        """Sin comando ni fixture no hay modelo: error explicado, no un fallo opaco."""
        saved_fixture = os.environ.pop("REFIRE_AGENT_FIXTURE", None)
        saved_cmd = os.environ.pop("REFIRE_AGENT_CMD", None)
        try:
            self.assertEqual(agent.agent_mode(), "none")
            with self.assertRaises(agent.AgentError) as ctx:
                agent.call_agent("hola")
            self.assertIn("REFIRE_AGENT_CMD", str(ctx.exception))
        finally:
            if saved_fixture is not None:
                os.environ["REFIRE_AGENT_FIXTURE"] = saved_fixture
            if saved_cmd is not None:
                os.environ["REFIRE_AGENT_CMD"] = saved_cmd

    def test_agent_command_receives_the_prompt(self):
        """REFIRE_AGENT_CMD es el único camino al modelo: el prompt llega entero."""
        saved_fixture = os.environ.pop("REFIRE_AGENT_FIXTURE", None)
        saved_cmd = os.environ.get("REFIRE_AGENT_CMD")
        os.environ["REFIRE_AGENT_CMD"] = "echo {prompt}"
        try:
            self.assertEqual(agent.agent_mode(), "cmd")
            self.assertEqual(agent.call_agent("hola"), "hola")
        finally:
            if saved_fixture is not None:
                os.environ["REFIRE_AGENT_FIXTURE"] = saved_fixture
            if saved_cmd is None:
                os.environ.pop("REFIRE_AGENT_CMD", None)
            else:
                os.environ["REFIRE_AGENT_CMD"] = saved_cmd


if __name__ == "__main__":
    unittest.main()
