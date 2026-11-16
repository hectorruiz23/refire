"""Parser YAML mínimo y contrato del informe (BRIEF 4)."""
from __future__ import annotations

import unittest

import support

from lib import patterns, session, yamlmini


class YamlParserTest(unittest.TestCase):
    def setUp(self):
        self.raw = support.report_text()

    def test_fixture_parses_to_expected_session(self):
        data = yamlmini.parse(self.raw)
        self.assertIsInstance(data, dict)
        sess = data["session"]
        self.assertEqual(sess["date"], "2026-11-02")
        self.assertEqual(sess["minutes"], 12)
        self.assertEqual(sess["difficulty"], "B2")
        self.assertEqual(sess["scenario"], "architecture review")
        self.assertEqual(sess["scores"]["fluency"], 4)
        self.assertEqual(sess["scores"]["interaction"], 4)
        self.assertEqual(len(sess["corrections"]), 7)
        self.assertEqual(sess["corrections"][0]["pattern"], "conditionals")
        self.assertEqual(sess["corrections"][0]["said"], "Even it look fine, the report is wrong")
        self.assertEqual(sess["new_vocab"], ["follow up on a decision", "wrap up a meeting"])
        self.assertEqual(sess["fillers_per_minute"], 6)
        self.assertEqual(sess["previously_corrected_used_right"], [])

    def test_report_with_text_around_and_fence(self):
        raw = "Here is your report, hope it helps:\n\n```yaml\n" + self.raw + "```\n\nBye!"
        sess, warnings = session.parse_report(raw)
        self.assertEqual(sess["date"], "2026-11-02")
        self.assertEqual(len(sess["corrections"]), 7)
        self.assertEqual(warnings, [])

    def test_report_with_prose_before_session_key(self):
        raw = "Sure. This is the wrap up.\n" + self.raw
        sess, _ = session.parse_report(raw)
        self.assertEqual(sess["minutes"], 12)

    def test_colon_inside_quoted_value(self):
        raw = (
            "session:\n"
            "  date: 2026-11-03\n"
            "  minutes: 8\n"
            "  corrections:\n"
            '    - said: "We need a trade-off: speed or cost"\n'
            '      better: "We need a trade-off between speed and cost"\n'
            "      pattern: word-choice\n"
        )
        sess, _ = session.parse_report(raw)
        self.assertEqual(sess["corrections"][0]["said"], "We need a trade-off: speed or cost")

    def test_broken_yaml_is_rejected(self):
        with self.assertRaises(session.ReportInvalid):
            session.parse_report("session:\n  date: [2026-11-02\n  minutes: 5\n")
        with self.assertRaises(session.ReportInvalid):
            session.parse_report("this is not a report at all")

    def test_missing_date_is_rejected(self):
        with self.assertRaises(session.ReportInvalid):
            session.parse_report("session:\n  minutes: 5\n")

    def test_bad_date_format_is_rejected(self):
        with self.assertRaises(session.ReportInvalid):
            session.parse_report("session:\n  date: 02/11/2026\n  minutes: 5\n")

    def test_null_fillers_and_quoted_scores(self):
        raw = (
            "session:\n"
            "  date: 2026-11-04\n"
            "  minutes: 10\n"
            '  scores:\n    fluency: "3"\n    grammar: 3\n'
            "  fillers_per_minute: null\n"
            "  next_goal: null\n"
        )
        sess, warnings = session.parse_report(raw)
        self.assertIsNone(sess["fillers_per_minute"])
        self.assertEqual(sess["scores"]["fluency"], 3)
        self.assertIsNone(sess["next_goal"])
        self.assertEqual(warnings, [])


class BlockScalarTest(unittest.TestCase):
    """Escalares de bloque y valores partidos en varias líneas.

    ChatGPT los manda a menudo: `next_goal: >-` con el texto debajo, o una
    corrección larga sin comillas que sigue en la línea siguiente. Antes el
    parser los rechazaba con "indentación inconsistente".
    """

    def test_folded_block_scalar_is_one_line(self):
        raw = (
            "session:\n"
            "  date: 2026-11-03\n"
            "  next_goal: >-\n"
            "    Practise structured answers.\n"
            "    State a position and support it.\n"
            "  minutes: 12\n"
        )
        sess = yamlmini.parse(raw)["session"]
        self.assertEqual(
            sess["next_goal"],
            "Practise structured answers. State a position and support it.",
        )
        # el bloque no se come la clave que viene después
        self.assertEqual(sess["minutes"], 12)

    def test_folded_keeps_paragraph_breaks(self):
        raw = (
            "session:\n"
            "  next_goal: >\n"
            "    Primera frase.\n"
            "\n"
            "    Segunda frase.\n"
        )
        sess = yamlmini.parse(raw)["session"]
        self.assertEqual(sess["next_goal"], "Primera frase.\nSegunda frase.\n")

    def test_literal_block_keeps_newlines(self):
        raw = "session:\n  next_goal: |\n    linea uno\n    linea dos\n"
        sess = yamlmini.parse(raw)["session"]
        self.assertEqual(sess["next_goal"], "linea uno\nlinea dos\n")

    def test_chomping_indicators(self):
        strip = yamlmini.parse("session:\n  next_goal: |-\n    a\n    b\n")
        self.assertEqual(strip["session"]["next_goal"], "a\nb")
        clip = yamlmini.parse("session:\n  next_goal: |\n    a\n    b\n")
        self.assertEqual(clip["session"]["next_goal"], "a\nb\n")
        keep = yamlmini.parse("session:\n  next_goal: |+\n    a\n\n")
        self.assertEqual(keep["session"]["next_goal"], "a\n\n")

    def test_block_scalar_inside_sequence_item(self):
        raw = (
            "session:\n"
            "  corrections:\n"
            "    - said: \"my eager to learn more\"\n"
            "      better: |-\n"
            "        My eagerness to keep learning.\n"
            "      pattern: word-choice\n"
        )
        correction = yamlmini.parse(raw)["session"]["corrections"][0]
        self.assertEqual(correction["better"], "My eagerness to keep learning.")
        self.assertEqual(correction["pattern"], "word-choice")

    def test_block_scalar_as_sequence_item(self):
        raw = "session:\n  new_vocab:\n    - |-\n      hit the ground running\n    - plain\n"
        self.assertEqual(
            yamlmini.parse(raw)["session"]["new_vocab"],
            ["hit the ground running", "plain"],
        )

    def test_plain_scalar_split_over_lines_is_joined(self):
        raw = (
            "session:\n"
            "  scenario: architecture review of the\n"
            "    payment service\n"
            "  minutes: 12\n"
        )
        sess = yamlmini.parse(raw)["session"]
        self.assertEqual(sess["scenario"], "architecture review of the payment service")
        self.assertEqual(sess["minutes"], 12)

    def test_real_report_with_block_scalars_end_to_end(self):
        raw = (support.FIXTURES / "report-block-scalars.yaml").read_text(encoding="utf-8")
        sess, warnings = session.parse_report(raw)
        self.assertEqual(warnings, [])
        self.assertEqual(sess["date"], "2026-11-03")
        self.assertEqual(sess["difficulty"], "C1")
        self.assertEqual(len(sess["corrections"]), 8)
        self.assertEqual(len(sess["new_vocab"]), 5)
        self.assertIsNone(sess["fillers_per_minute"])
        self.assertTrue(sess["next_goal"].startswith("Practise structured answers"))
        self.assertTrue(sess["next_goal"].endswith("sentence structures."))
        self.assertNotIn("\n", sess["next_goal"])
        self.assertEqual(len(sess["previously_corrected_used_right"]), 3)


class PatternEnumTest(unittest.TestCase):
    def test_enum_and_aliases(self):
        for name in patterns.PATTERNS:
            self.assertEqual(patterns.normalize(name), (name, None))
        self.assertEqual(patterns.normalize("Verb Form")[0], "verb-form")
        self.assertEqual(patterns.normalize("word choice")[0], "word-choice")

    def test_unknown_pattern_falls_back_with_warning(self):
        value, warning = patterns.normalize("syntax")
        self.assertEqual(value, "word-choice")
        self.assertIn("syntax", warning)

    def test_unknown_pattern_does_not_reject_report(self):
        raw = (support.FIXTURES / "report-unknown-pattern.yaml").read_text(encoding="utf-8")
        sess, warnings = session.parse_report(raw)
        self.assertEqual(len(sess["corrections"]), 2)
        self.assertEqual(sess["corrections"][0]["pattern"], "word-choice")
        self.assertTrue(any("patrón desconocido" in w for w in warnings))

    def test_out_of_range_score_is_clamped_with_warning(self):
        raw = (
            "session:\n  date: 2026-11-06\n  minutes: 5\n"
            "  scores:\n    fluency: 9\n"
        )
        sess, warnings = session.parse_report(raw)
        self.assertEqual(sess["scores"]["fluency"], 5)
        self.assertTrue(any("fuera de 1-5" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
