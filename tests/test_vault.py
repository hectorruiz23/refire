"""Tabla del registro: una fila y byte a byte igual el resto (criterios 3, 5 y 9)."""
from __future__ import annotations

import unittest

import support
from support import TestEnv

from lib import config, vault


ROW = {
    "date": "2026-11-09",
    "minutes": 12,
    "topics": "revisión de arquitectura; migración",
    "primary_correction": "conditionals (2) — «Even it look fine» → «Even if it looks fine»",
    "new_vocab": '"follow up on a decision"',
    "next_goal": "hablar de un tema no profesional",
}


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.env = TestEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_parse_sessions_reads_only_the_registry_table(self):
        rows = vault.parse_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-02")
        self.assertIsNone(rows[0]["minutes"])  # "—" no es un número
        self.assertIn("planificar un viaje", rows[0]["topics"])

    def test_append_adds_exactly_one_row_and_keeps_everything_else(self):
        before = config.vault_path().read_bytes()
        line = vault.append_row(ROW)
        after = config.vault_path().read_bytes()

        self.assertEqual(before.decode("utf-8").count("| 2026-11-02 |"), 1)
        self.assertEqual(after.decode("utf-8").count("\n| 2026-11-09 |"), 1)
        # quitar la línea insertada devuelve el fichero original bit a bit
        restored = after.decode("utf-8").replace(line + "\n", "", 1).encode("utf-8")
        self.assertEqual(support.sha256_bytes(restored), support.sha256_bytes(before))

    def test_row_lands_after_the_last_table_row_and_before_the_yaml_fence(self):
        vault.append_row(ROW)
        text = config.vault_path().read_text(encoding="utf-8")
        pos_row = text.index("| 2026-11-09 |")
        pos_old = text.index("| 2026-11-02 |")
        pos_fence = text.index("```yaml")
        self.assertLess(pos_old, pos_row)
        self.assertLess(pos_row, pos_fence)

    def test_pipes_and_newlines_in_values_do_not_break_the_table(self):
        dirty = dict(ROW, topics="a | b", primary_correction="línea1\nlínea2")
        line = vault.append_row(dirty)
        self.assertEqual(line.count("|"), 7)  # 6 columnas -> 7 barras
        self.assertNotIn("\n", line)
        rows = vault.parse_sessions()
        self.assertEqual(rows[-1]["topics"], "a / b")

    def test_parse_sees_the_new_row(self):
        self.assertFalse(vault.has_date("2026-11-09"))
        vault.append_row(ROW)
        self.assertTrue(vault.has_date("2026-11-09"))
        self.assertEqual(len(vault.parse_sessions()), 2)

    def test_missing_section_raises(self):
        path = config.vault_path()
        path.write_text("# otra nota\n\nsin registro\n", encoding="utf-8")
        with self.assertRaises(vault.VaultError):
            vault.append_row(ROW)


if __name__ == "__main__":
    unittest.main()
