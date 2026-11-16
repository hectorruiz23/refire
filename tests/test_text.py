"""Criterio 6: comparación tolerante (mayúsculas, puntuación, contracciones)."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (añade APP al sys.path)

from lib import text


class TolerantComparisonTest(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertTrue(text.equal("i was really lucky", "I was really lucky."))
        self.assertTrue(text.equal("I was really lucky.", "  i WAS really lucky!! "))

    def test_contractions_expand_both_ways(self):
        self.assertTrue(text.equal("I'd have caught it", "I would have caught it"))
        self.assertTrue(text.equal("don't", "do not"))
        self.assertTrue(text.equal("it's", "it is"))
        self.assertTrue(text.equal("we're", "we are"))
        self.assertTrue(text.equal("can't", "can not"))
        self.assertTrue(text.equal("cannot", "can not"))
        self.assertTrue(text.equal("won't ship it", "will not ship it"))
        self.assertTrue(text.equal("I've seen it", "I have seen it"))

    def test_different_answers_do_not_match(self):
        self.assertFalse(text.equal("I was really lucky", "I was very lucky"))
        self.assertFalse(text.equal("Even if it has side effects", "Even it have side effects"))
        self.assertFalse(text.equal("", "anything"))

    def test_normalize_collapses_space_and_punctuation(self):
        self.assertEqual(text.normalize("  Even   if it has  side-effects. "),
                         "even if it has side effects")
        self.assertEqual(text.normalize("It’s"), "it is")

    def test_compare_words_flags_only_the_wrong_word(self):
        words = text.compare_words("even if it has side effects",
                                   "even if it has side effect")
        self.assertEqual(len(words), 6)
        self.assertTrue(all(w["ok"] for w in words[:5]))
        self.assertFalse(words[5]["ok"])
        self.assertEqual(words[5]["expected"], "effects")

    def test_compare_words_with_equal_inputs(self):
        words = text.compare_words("shared responsibility", "Shared responsibility.")
        self.assertTrue(all(w["ok"] for w in words))
        self.assertEqual([w["heard"] for w in words], ["shared", "responsibility"])

    def test_near_miss_is_not_a_hard_fail(self):
        # share/shared: el reconocedor no ha entendido la -d final, no es un error de léxico
        self.assertTrue(text.near_miss("shared", "share"))
        self.assertFalse(text.near_miss("shared", "shared"))
        self.assertFalse(text.near_miss("shared", "i said shared"))
        # have/has sí es un fallo de forma verbal, no un "casi"
        self.assertFalse(text.near_miss("has", "have"))
        self.assertFalse(text.near_miss("has", "is"))


    def test_contains_accepts_the_whole_sentence_spoken(self):
        # drill de hueco: la respuesta es una palabra, el audio trae la frase completa
        self.assertTrue(text.contains(
            "if", "Even if it has side effects, we should still ship it."))
        self.assertTrue(text.contains(
            "has", "even if it has side effects"))
        self.assertFalse(text.contains(
            "have", "even if it has side effects"))
        self.assertTrue(text.contains(
            "the nightly deploy", "i ran the nightly deploy on that account"))
        # el orden importa: no vale tener las palabras sueltas
        self.assertFalse(text.contains(
            "deploy nightly", "i ran the nightly deploy"))


if __name__ == "__main__":
    unittest.main()
