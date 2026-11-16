"""Las claves del LLM se leen de ficheros de entorno sin pisar lo ya exportado."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (añade APP al sys.path)

from lib import config

CLAVE = "REFIRE_TEST_KEY"


class LoadEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "env"
        self.previo = os.environ.pop(CLAVE, None)

    def tearDown(self):
        os.environ.pop(CLAVE, None)
        if self.previo is not None:
            os.environ[CLAVE] = self.previo
        self.tmp.cleanup()

    def test_reads_pairs_and_strips_quotes(self):
        self.path.write_text('# comentario\n%s="secreto"\notra=1\n' % CLAVE, encoding="utf-8")
        añadidas = config.load_env([self.path])
        self.assertIn(CLAVE, añadidas)
        self.assertEqual(os.environ[CLAVE], "secreto")

    def test_does_not_override_exported(self):
        os.environ[CLAVE] = "del shell"
        self.path.write_text("%s=fichero\n" % CLAVE, encoding="utf-8")
        config.load_env([self.path])
        self.assertEqual(os.environ[CLAVE], "del shell")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(config.load_env([Path(self.tmp.name) / "no-existe"]), [])


if __name__ == "__main__":
    unittest.main()
