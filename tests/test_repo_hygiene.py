"""El banco de shadowing que viaja en el repo es texto propio, no material de terceros.

La ingesta de YouTube escribe las frases que saca de un vídeo en `data/shadow/phrases.json`, que es
el único fichero de `data/shadow/` que git versiona, y les pone `source`, `source_url`, `speaker` y
`license`. Sin este test, usar la app y commitear publica transcripciones de terceros en un repo
MIT: el fallo no lo ve ningún otro test porque no rompe nada.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

BANK = Path(support.APP) / "data" / "shadow" / "phrases.json"
PROVENANCE = ("source", "source_url", "license", "speaker")


class PublishedBankTests(unittest.TestCase):
    def test_bank_lives_in_the_repo(self):
        self.assertTrue(BANK.is_file(), "falta el banco versionado: %s" % BANK)

    def test_bank_has_no_third_party_provenance(self):
        doc = json.loads(BANK.read_text(encoding="utf-8"))
        frases = doc.get("phrases") or []
        sucias = [f for f in frases if any(k in f for k in PROVENANCE)]
        self.assertEqual(
            [], sucias,
            "el banco versionado llevaría %d frase(s) con procedencia de terceros (%s). "
            "La ingesta de YouTube escribe en este fichero: sácalas antes de commitear."
            % (len(sucias), ", ".join(sorted({k for f in sucias for k in f if k in PROVENANCE}))),
        )

    def test_bank_has_no_youtube_urls(self):
        texto = BANK.read_text(encoding="utf-8").lower()
        for marca in ("youtu.be", "youtube.com", "ytimg"):
            self.assertNotIn(marca, texto, "el banco versionado cita %s" % marca)
