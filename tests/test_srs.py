"""SRS: fallo -> mañana, 3 aciertos -> retirado, un retirado no vuelve (criterio 7)."""
from __future__ import annotations

import unittest

import support
from support import TestEnv

from lib import srs


def drill(prompt="Complete: Even if the cache ___ cold, the first request is slow.",
          pattern="conditionals", answer="is"):
    return {
        "pattern": pattern,
        "prompt": prompt,
        "answer": answer,
        "alt_answers": [],
        "source": {"said": "Even it look fine", "better": "Even if it looks fine"},
    }


class SrsTest(unittest.TestCase):
    def setUp(self):
        self.env = TestEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_new_item_is_available_immediately(self):
        state = srs.load()
        added, warnings = srs.merge_drills(state, [drill()], created="2026-11-02")
        self.assertEqual(len(added), 1)
        item = added[0]
        self.assertEqual(item["id"], "d-0001")
        self.assertEqual(item["due"], "2026-11-02")
        self.assertEqual(item["streak"], 0)
        self.assertEqual(item["wrong"], 0)
        self.assertFalse(item["retired"])
        self.assertEqual(warnings, [])
        self.assertEqual(len(srs.due_now(state, "2026-11-02")), 1)

    def test_failure_schedules_tomorrow(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        item = srs.answer(state, "d-0001", correct=False, today="2026-11-02")
        self.assertEqual(item["due"], "2026-11-03")
        self.assertEqual(item["streak"], 0)
        self.assertEqual(item["wrong"], 1)
        self.assertFalse(item["retired"])
        self.assertEqual([i["id"] for i in srs.due_now(state, "2026-11-02")], [])
        self.assertEqual([i["id"] for i in srs.due_now(state, "2026-11-03")], ["d-0001"])

    def test_three_hits_retire_and_never_come_back(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        srs.answer(state, "d-0001", correct=True, today="2026-11-02")
        srs.answer(state, "d-0001", correct=True, today="2026-11-03")
        item = srs.answer(state, "d-0001", correct=True, today="2026-11-04")
        self.assertEqual(item["streak"], 3)
        self.assertTrue(item["retired"])
        self.assertIsNone(item["due"])
        self.assertEqual(srs.due_now(state, "2030-01-01"), [])
        counts = srs.counts(state, "2030-01-01")
        self.assertEqual(counts, {"due_now": 0, "total": 0, "retired": 1})

    def test_retired_item_with_past_due_does_not_return(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        state["items"][0]["retired"] = True
        state["items"][0]["due"] = "2020-01-01"
        self.assertEqual(srs.due_now(state, "2026-11-10"), [])

    def test_wrong_answer_does_not_unretire(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        state["items"][0]["retired"] = True
        state["items"][0]["streak"] = 3
        item = srs.answer(state, "d-0001", correct=False, today="2026-11-06")
        self.assertTrue(item["retired"])
        self.assertEqual(item["streak"], 0)
        self.assertEqual(srs.due_now(state, "2026-11-07"), [])

    def test_duplicate_prompt_is_not_added_twice(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        added, warnings = srs.merge_drills(state, [drill()], created="2026-11-03")
        self.assertEqual(added, [])
        self.assertTrue(any("duplicado" in w for w in warnings))
        self.assertEqual(len(state["items"]), 1)

    def test_ids_are_sequential(self):
        state = srs.load()
        srs.merge_drills(state, [drill("p1"), drill("p2")], created="2026-11-02")
        added, _ = srs.merge_drills(state, [drill("p3")], created="2026-11-02")
        self.assertEqual(added[0]["id"], "d-0003")

    def test_pattern_stats_and_pass_rate(self):
        state = srs.load()
        srs.merge_drills(state, [drill("p1"), drill("p2")], created="2026-11-02")
        srs.answer(state, "d-0001", correct=True, today="2026-11-02")
        srs.answer(state, "d-0002", correct=False, today="2026-11-02")
        rows = {r["name"]: r for r in srs.pattern_rows(state, "2026-11-02")}
        self.assertEqual(rows["conditionals"]["attempts"], 2)
        self.assertEqual(rows["conditionals"]["correct"], 1)
        self.assertEqual(rows["conditionals"]["pass_rate"], 0.5)
        # los dos fallaron/esperan mañana: hoy ninguno vence, mañana los dos
        self.assertEqual(rows["conditionals"]["due"], 0)
        tomorrow = {r["name"]: r for r in srs.pattern_rows(state, "2026-11-03")}
        self.assertEqual(tomorrow["conditionals"]["due"], 2)

    def test_state_persists_through_atomic_save(self):
        state = srs.load()
        srs.merge_drills(state, [drill()], created="2026-11-02")
        srs.answer(state, "d-0001", correct=False, today="2026-11-02")
        srs.save(state)
        reloaded = srs.load()
        self.assertEqual(len(reloaded["items"]), 1)
        self.assertEqual(reloaded["items"][0]["due"], "2026-11-03")
        self.assertFalse((self.env.data / "srs.json.tmp").exists())
        self.assertIsNotNone(reloaded["updated"])

    def test_missing_drill_raises(self):
        with self.assertRaises(LookupError):
            srs.answer(srs.load(), "d-9999", True)


def model_drill(prompt="I am the responsible for the deploy",
                answer="I am responsible for the deploy", pattern="articles"):
    """Corrección que no admite hueco: se dijo de más una preposición."""
    return {
        "kind": "model",
        "pattern": pattern,
        "prompt": prompt,
        "answer": answer,
        "alt_answers": [],
        "source": {"said": prompt, "better": answer},
    }


class ModelCardTest(unittest.TestCase):
    """Frases modelo: se marcan a mano y nunca puntúan por patrón."""

    def setUp(self):
        self.env = TestEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_deck_without_kind_is_treated_as_gap(self):
        """Un mazo viejo (sin `kind`) sigue funcionando igual: gap por defecto."""
        state = srs.load()
        state["items"] = [{
            "id": "d-0001", "pattern": "articles", "prompt": "He was ___ architect.",
            "answer": "the", "alt_answers": [], "source": {"said": "", "better": ""},
            "created": "2026-11-02", "streak": 0, "wrong": 0, "last_seen": None,
            "retired": False, "due": "2026-11-02",
        }]
        srs.save(state)
        reloaded = srs.load()
        self.assertEqual(reloaded["items"][0]["kind"], "gap")
        self.assertEqual(srs.kind_of({"prompt": "sin kind"}), "gap")

        item = srs.answer(reloaded, "d-0001", correct=True, today="2026-11-02")
        self.assertEqual(item["streak"], 1)
        self.assertEqual(reloaded["pattern_stats"]["articles"]["correct"], 1)

    def test_model_card_never_touches_pattern_stats(self):
        state = srs.load()
        srs.merge_drills(state, [model_drill()], created="2026-11-02")
        self.assertEqual(state["items"][0]["kind"], "model")
        srs.answer(state, "d-0001", correct=True, today="2026-11-02")
        self.assertNotIn("articles", state.get("pattern_stats") or {})
        self.assertEqual(state["model_marks"], {"seen": 1, "to_review": 0})
        rows = {r["name"]: r for r in srs.pattern_rows(state, "2026-11-02")}
        self.assertEqual(rows["articles"]["attempts"], 0)
        self.assertIsNone(rows["articles"]["pass_rate"])

    def test_model_mark_for_review_comes_back_tomorrow(self):
        state = srs.load()
        srs.merge_drills(state, [model_drill()], created="2026-11-02")
        item = srs.answer(state, "d-0001", correct=False, today="2026-11-02")
        self.assertEqual(item["due"], "2026-11-03")
        self.assertEqual(item["streak"], 0)
        self.assertEqual(state["model_marks"], {"seen": 1, "to_review": 1})
        self.assertEqual(srs.model_stats(state, "2026-11-02")["due_now"], 0)
        self.assertEqual(srs.model_stats(state, "2026-11-03")["due_now"], 1)

    def test_three_marks_retire_the_model_card(self):
        state = srs.load()
        srs.merge_drills(state, [model_drill()], created="2026-11-02")
        srs.answer(state, "d-0001", correct=True, today="2026-11-02")
        srs.answer(state, "d-0001", correct=True, today="2026-11-03")
        item = srs.answer(state, "d-0001", correct=True, today="2026-11-04")
        self.assertEqual(item["streak"], 3)
        self.assertTrue(item["retired"])
        self.assertIsNone(item["due"])
        self.assertEqual(state["model_marks"]["seen"], 3)
        self.assertEqual(srs.model_stats(state, "2030-01-01")["due_now"], 0)

    def test_counts_keep_models_out_of_the_drill_numbers(self):
        state = srs.load()
        srs.merge_drills(state, [drill(), model_drill()], created="2026-11-02")
        self.assertEqual(srs.counts(state, "2026-11-02"), {"due_now": 1, "total": 1, "retired": 0})
        stats = srs.model_stats(state, "2026-11-02")
        self.assertEqual((stats["due_now"], stats["total"], stats["seen"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
