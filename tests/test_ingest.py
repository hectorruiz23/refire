"""Segmentación pura de clips y volcado al banco, sin whisper ni ffmpeg."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (deja el proyecto en sys.path)
from lib.shadow_ingest import (
    accept_candidates,
    add_auto_candidates,
    forget_phrase,
    normalize_text,
    passes_quality,
    segment_words,
    select_new_candidates,
    word_count,
)


def word(text: str, start: float, end: float, probability: float = 0.95) -> dict:
    return {"word": text, "start": start, "end": end, "probability": probability}


def candidate(text: str, start: float = 0.0, end: float = 3.0,
              confidence: float = 0.90) -> dict:
    return {"text": text, "start": start, "end": end,
            "duration": round(end - start, 3), "confidence": confidence}


class SegmentWordsTest(unittest.TestCase):
    def test_cuts_on_a_pause_longer_than_500_ms(self):
        words = [
            word("We", 0.0, 0.3), word("should", 0.35, 0.7),
            word("finish", 0.75, 1.2), word("this", 1.25, 1.5),
            word("today.", 1.55, 2.3),
            word("Then", 3.0, 3.3), word("we", 3.35, 3.5),
            word("can", 3.55, 3.8), word("go", 3.85, 4.0),
            word("home", 4.05, 4.4), word("now.", 4.45, 5.3),
        ]
        phrases = segment_words(words)
        self.assertEqual([p["text"] for p in phrases],
                         ["We should finish this today.", "Then we can go home now."])
        self.assertEqual(phrases[0]["start"], 0.0)
        self.assertEqual(phrases[0]["end"], 2.3)
        self.assertEqual(phrases[1]["start"], 3.0)

    def test_a_short_pause_does_not_cut(self):
        words = [
            word("One", 0.0, 0.4), word("two", 0.55, 0.9),
            word("three", 1.05, 1.4), word("four", 1.55, 1.9),
            word("five", 2.05, 2.6),
        ]
        phrases = segment_words(words)
        self.assertEqual(len(phrases), 1)
        self.assertEqual(phrases[0]["text"], "One two three four five")

    def test_final_punctuation_cuts_even_without_a_pause(self):
        words = [
            word("We", 0.0, 0.3), word("are", 0.35, 0.6),
            word("almost", 0.65, 1.0), word("done?", 1.05, 2.2),
            word("Next", 2.25, 2.6), word("we", 2.65, 2.8),
            word("go", 2.85, 3.1), word("home", 3.15, 3.5),
            word("now.", 3.55, 4.6),
        ]
        phrases = segment_words(words)
        self.assertEqual([p["text"] for p in phrases],
                         ["We are almost done?", "Next we go home now."])
        self.assertEqual([(p["start"], p["end"]) for p in phrases],
                         [(0.0, 2.2), (2.25, 4.6)])

    def test_discards_fragments_outside_the_duration_range(self):
        too_short = [word("Yes", 0.0, 0.4), word("we", 0.45, 0.8),
                     word("can", 0.85, 1.9)]
        too_long = [word("Yes", 0.0, 2.0), word("we", 2.1, 4.0),
                    word("can", 4.1, 8.1)]
        self.assertEqual(segment_words(too_short), [])
        self.assertEqual(segment_words(too_long), [])

    def test_discards_fragments_with_less_than_three_words(self):
        words = [word("Yes,", 0.0, 0.8), word("indeed.", 0.85, 2.5)]
        self.assertEqual(segment_words(words), [])

    def test_discards_fragments_without_clear_voice(self):
        words = [
            word("mumble", 0.0, 0.6, 0.10), word("something", 0.65, 1.4, 0.05),
            word("unclear", 1.45, 2.5, 0.08),
        ]
        self.assertEqual(segment_words(words), [])

    def test_respects_max_phrases(self):
        words = []
        start = 0.0
        for index in range(4):
            words.extend([
                word("phrase%d" % index, start, start + 0.6),
                word("number%d" % index, start + 0.65, start + 1.3),
                word("ends.", start + 1.35, start + 2.1),
            ])
            start += 2.8
        phrases = segment_words(words, max_phrases=2)
        self.assertEqual([p["text"] for p in phrases],
                         ["phrase0 number0 ends.", "phrase1 number1 ends."])

    def test_bounds_fall_on_word_edges_and_punctuation_is_joined(self):
        words = [
            word("Wait", 0.5, 0.8), word(",", 0.81, 0.82),
            word("what", 0.83, 1.2), word("never", 1.30, 2.0),
            word("mind?", 2.05, 3.0),
        ]
        phrases = segment_words(words)
        self.assertEqual(len(phrases), 1)
        self.assertEqual(phrases[0]["text"], "Wait, what never mind?")
        self.assertEqual((phrases[0]["start"], phrases[0]["end"]), (0.5, 3.0))

    def test_ignores_words_with_broken_times(self):
        words = [word("bad", 2.0, 1.0), word("also-bad", -1.0, 0.5)]
        self.assertEqual(segment_words(words), [])


class AcceptCandidatesTest(unittest.TestCase):
    def test_copies_refs_and_appends_with_the_next_clip_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "candidates" / "charla"
            refs = root / "refs"
            folder.mkdir(parents=True)
            (folder / "1.wav").write_bytes(b"uno")
            (folder / "2.wav").write_bytes(b"dos")
            index = folder / "index.json"
            index.write_text(json.dumps({
                "source": "charla.mp3",
                "license": "CC0",
                "speaker": "Ana",
                "candidates": [
                    {"n": 1, "file": "1.wav", "start": 0.5, "end": 3.0, "text": "One."},
                    {"n": 2, "file": "2.wav", "start": 4.0, "end": 6.5, "text": "Two."},
                ],
            }), encoding="utf-8")
            bank = root / "phrases.json"
            bank.write_text(json.dumps({
                "phrases": [{"id": "p1", "text": "Old."}],
            }), encoding="utf-8")

            new = accept_candidates(index, [2], refs, bank)
            self.assertEqual([p["id"] for p in new], ["c001"])
            self.assertEqual((refs / "c001.wav").read_bytes(), b"dos")
            saved = json.loads(bank.read_text(encoding="utf-8"))
            self.assertEqual([p["id"] for p in saved["phrases"]], ["p1", "c001"])
            self.assertEqual(saved["phrases"][1], {
                "id": "c001", "text": "Two.", "source": "charla.mp3",
                "license": "CC0", "speaker": "Ana", "start": 4.0, "end": 6.5,
                "duration": 2.5,
            })

            new = accept_candidates(index, [1], refs, bank)
            self.assertEqual([p["id"] for p in new], ["c002"])
            self.assertEqual((refs / "c002.wav").read_bytes(), b"uno")

    def test_unknown_candidate_does_not_touch_the_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "candidates" / "charla"
            folder.mkdir(parents=True)
            index = folder / "index.json"
            index.write_text(json.dumps({"source": "charla.mp3", "candidates": []}),
                             encoding="utf-8")
            bank = root / "phrases.json"
            bank.write_text(json.dumps({"phrases": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                accept_candidates(index, [9], root / "refs", bank)
            self.assertEqual(json.loads(bank.read_text(encoding="utf-8")),
                             {"phrases": []})


class QualityFilterTest(unittest.TestCase):
    def test_accepts_a_clear_phrase_inside_the_range(self):
        self.assertTrue(passes_quality(candidate("This is a useful phrase.")))
        self.assertEqual(word_count("This is a useful phrase."), 5)

    def test_rejects_fewer_than_four_words(self):
        self.assertFalse(passes_quality(candidate("Only three words")))
        self.assertTrue(passes_quality(candidate("Now there are four words")))

    def test_duration_bounds_are_inclusive_and_computed_from_times(self):
        self.assertTrue(passes_quality(candidate("Four words in two seconds", 10.0, 12.0)))
        self.assertTrue(passes_quality(candidate("Four words in eight seconds", 0.0, 8.0)))
        self.assertFalse(passes_quality(candidate("Four words too short here", 0.0, 1.99)))
        self.assertFalse(passes_quality(candidate("Four words too long here", 0.0, 8.01)))
        no_duration = {"text": "Four words without duration", "start": 1.0, "end": 4.0,
                       "confidence": 0.9}
        self.assertTrue(passes_quality(no_duration))

    def test_confidence_threshold_is_080(self):
        self.assertTrue(passes_quality(candidate("This is clear enough", confidence=0.80)))
        self.assertFalse(passes_quality(candidate("This is clear enough", confidence=0.79)))
        missing = {"text": "This is clear enough", "start": 0.0, "end": 3.0,
                   "duration": 3.0}
        self.assertFalse(passes_quality(missing))

    def test_rejects_empty_or_letterless_text(self):
        self.assertFalse(passes_quality(candidate("")))
        self.assertFalse(passes_quality(candidate("1234 5678 9012 3456")))
        self.assertFalse(passes_quality(None))

    def test_normalize_text_ignores_case_punctuation_and_spacing(self):
        self.assertEqual(
            normalize_text("  Even if it looks fine, we should still ship it.  "),
            "even if it looks fine we should still ship it",
        )
        self.assertEqual(normalize_text("It's  a  TEST!"), "it's a test")


class SelectCandidatesTest(unittest.TestCase):
    def test_dedupes_against_the_bank_without_case_or_punctuation(self):
        bank = ["Already here, and this is old."]
        candidates = [
            candidate("already here and THIS is old!"),
            candidate("This is a brand new phrase."),
        ]
        chosen = select_new_candidates(candidates, bank)
        self.assertEqual([c["text"] for c in chosen],
                         ["This is a brand new phrase."])

    def test_dedupes_repeated_text_inside_the_same_video(self):
        candidates = [
            candidate("This phrase appears twice."),
            candidate("this phrase appears twice"),
            candidate("This phrase only appears once."),
        ]
        chosen = select_new_candidates(candidates, [])
        self.assertEqual([c["text"] for c in chosen],
                         ["This phrase appears twice.", "This phrase only appears once."])

    def test_respects_max_new_and_quality(self):
        candidates = [
            candidate("Phrase number %d with words." % index, index * 3.0, index * 3.0 + 2.5)
            for index in range(25)
        ]
        candidates.insert(0, candidate("three words only"))
        self.assertEqual(len(select_new_candidates(candidates, [], max_new=20)), 20)
        self.assertEqual(select_new_candidates(candidates, [], max_new=0), [])


class AutoCandidatesTest(unittest.TestCase):
    def _index(self, root: Path, candidates: list[dict]) -> Path:
        folder = root / "candidates" / "youtube"
        folder.mkdir(parents=True)
        for item in candidates:
            (folder / item["file"]).write_bytes(b"wav-" + item["file"].encode())
        index = folder / "index.json"
        index.write_text(json.dumps({
            "source_url": "https://youtu.be/abc123",
            "license": "youtube",
            "speaker": "Canal de prueba",
            "offset_s": 60,
            "candidates": candidates,
        }), encoding="utf-8")
        return index

    def test_only_quality_non_duplicates_are_added_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = self._index(root, [
                {"n": 1, "file": "1.wav", "start": 1.0, "end": 4.5, "duration": 3.5,
                 "confidence": 0.95, "text": "This is a useful phrase."},
                {"n": 2, "file": "2.wav", "start": 5.0, "end": 7.0, "duration": 2.0,
                 "confidence": 0.95, "text": "Already here, and this is old!"},
                {"n": 3, "file": "3.wav", "start": 8.0, "end": 10.5, "duration": 2.5,
                 "confidence": 0.40, "text": "This one is not clear at all."},
                {"n": 4, "file": "4.wav", "start": 11.0, "end": 13.5, "duration": 2.5,
                 "confidence": 0.92, "text": "Three words only"},
            ])
            bank = root / "phrases.json"
            bank.write_text(json.dumps({
                "phrases": [{"id": "p1", "text": "Already here, and this is old."}],
            }), encoding="utf-8")
            refs = root / "refs"

            added = add_auto_candidates(index, refs, bank)
            self.assertEqual([p["id"] for p in added], ["y001"])
            self.assertEqual(added[0], {
                "id": "y001",
                "text": "This is a useful phrase.",
                "source": "https://youtu.be/abc123",
                "license": "youtube",
                "speaker": "Canal de prueba",
                "start": 61.0,
                "end": 64.5,
                "duration": 3.5,
            })
            self.assertEqual((refs / "y001.wav").read_bytes(), b"wav-1.wav")
            saved = json.loads(bank.read_text(encoding="utf-8"))["phrases"]
            self.assertEqual([p["id"] for p in saved], ["p1", "y001"])

            self.assertEqual(add_auto_candidates(index, refs, bank), [])

    def test_forget_removes_the_entry_and_its_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            refs = root / "refs"
            refs.mkdir()
            (refs / "y001.wav").write_bytes(b"uno")
            (refs / "y002.wav").write_bytes(b"dos")
            bank = root / "phrases.json"
            bank.write_text(json.dumps({"phrases": [
                {"id": "p1", "text": "Old."},
                {"id": "y001", "text": "One."},
                {"id": "y002", "text": "Two."},
            ]}), encoding="utf-8")

            removed = forget_phrase("y001", refs, bank)
            self.assertEqual(removed["text"], "One.")
            self.assertFalse((refs / "y001.wav").exists())
            saved = json.loads(bank.read_text(encoding="utf-8"))["phrases"]
            self.assertEqual([p["id"] for p in saved], ["p1", "y002"])
            self.assertIsNone(forget_phrase("y999", refs, bank))
            with self.assertRaises(ValueError):
                forget_phrase("../bank", refs, bank)


if __name__ == "__main__":
    unittest.main()
