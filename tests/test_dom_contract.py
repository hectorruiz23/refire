"""Contrato DOM: todo id que app.js busca con getElementById tiene que existir.

Es el único fallo silencioso del front: si un rediseño renombra `#panel-drills`, la app no da
error, simplemente deja de responder en esa pestaña. Un test convierte eso en un rojo inmediato
en vez de en un "no funciona y no sé por qué" tres días después.

La fuente de ids válidos es la unión de:
  - el HTML estático (id="algo")
  - los que app.js crea en tiempo de ejecución, en llamadas el('tag', {id: 'algo'})
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = STATIC / "app.js"
INDEX = STATIC / "index.html"

NEEDED_RE = re.compile(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)")
HTML_ID_RE = re.compile(r"\bid=['\"]([^'\"]+)['\"]")
JS_ID_RE = re.compile(r"\bid:\s*['\"]([^'\"]+)['\"]")


class DomContractTest(unittest.TestCase):
    def test_every_id_queried_by_app_js_exists(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        index = INDEX.read_text(encoding="utf-8")

        needed = set(NEEDED_RE.findall(app_js))
        available = set(HTML_ID_RE.findall(index)) | set(JS_ID_RE.findall(app_js))

        missing = sorted(needed - available)
        self.assertEqual(
            missing,
            [],
            "app.js busca ids que ya no existen en el HTML ni se crean en el JS: %s" % missing,
        )

    def test_app_js_still_has_the_behaviour_we_depend_on(self):
        """Ancla mínima: si alguien reescribe app.js y se lleva por delante estas llamadas,
        la app deja de hablar con el backend aunque el diseño se vea perfecto."""
        app_js = APP_JS.read_text(encoding="utf-8")
        for endpoint in (
            "/api/state",
            "/api/scenario",
            "/api/report",
            "/api/drills",
            "/api/drill/answer",
            "/api/drill/audio",
            "/api/shadow/phrases",
            "/api/shadow",
        ):
            self.assertIn(endpoint, app_js, "app.js ya no llama a %s" % endpoint)

    def test_shadow_feedback_speaks_the_brief_words(self):
        """El resultado tiene que explicar el número y sus límites sin inventar métricas."""
        app_js = APP_JS.read_text(encoding="utf-8")
        all_text = app_js + INDEX.read_text(encoding="utf-8")
        for text in (
            "sonidos bien",
            "esta frase con este micro",
            "primera toma de esta frase con este micro",
            "no comparable: la última vez usaste otro micro",
            "ningún sonido claramente fuera",
            "La línea es el tono de tu voz",
        ):
            self.assertIn(text, all_text, "falta el texto de feedback %r" % text)
        self.assertIn("sonidos_bien", app_js)

    def test_shadow_cancel_button_is_hidden_until_recording(self):
        """Cancelar vive junto a Escuchar/Grabar y arranca oculto: solo lo enseña la grabación."""
        index = INDEX.read_text(encoding="utf-8")
        actions = re.search(r'<div class="shadow__actions">(.*?)</div>', index, re.S)
        self.assertIsNotNone(actions, "falta el bloque .shadow__actions")
        button = re.search(
            r'<button[^>]*id="btn-shadow-cancel"[^>]*>(.*?)</button>',
            actions.group(1),
            re.S,
        )
        self.assertIsNotNone(button, "falta el botón #btn-shadow-cancel en .shadow__actions")
        self.assertIn("hidden", button.group(0), "Cancelar tiene que empezar oculto")
        self.assertIn("Cancelar", button.group(1))

        app_js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("getElementById('btn-shadow-cancel')", app_js)
        self.assertIn("shadowCancel", app_js)
        self.assertIn("'Escape'", app_js)

    def test_index_keeps_the_three_panels(self):
        index = INDEX.read_text(encoding="utf-8")
        for panel in ("panel-sesion", "panel-registro", "panel-drills", "panel-shadow"):
            self.assertIn('id="%s"' % panel, index, "falta el panel %s" % panel)


if __name__ == "__main__":
    unittest.main()
