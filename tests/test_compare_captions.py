"""The Claude-vs-local caption scorer: pure scoring over decoded exports."""
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "compare_captions", Path(__file__).resolve().parents[1] / "scripts" / "compare_captions.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def track(text, start=0, frames_per_word=10, preset=None, issues=()):
    words = [{"text": w, "start": start + i * frames_per_word, "end": start + (i + 1) * frames_per_word}
             for i, w in enumerate(text.split())]
    caption = {"id": str(start), "text": text, "start": words[0]["start"], "end": words[-1]["end"], "words": words}
    return {"captions": [caption], "issues": list(issues), "preset": preset}


REFERENCE = "Today we meet Priya, our new producer, at the studio."
STYLE = {"templateId": "Templates/Edit/Titles/Subtitles/Animated/Word Highlight",
         "inputs": {"textRed": 1, "allWordsOutlineThickness": 0.03, "position": [0.5, 0.12]}}


class ScoreTest(unittest.TestCase):
    def test_identical_tracks_pass(self):
        report = cc.score(track(REFERENCE, preset=STYLE), track(REFERENCE, preset=STYLE))
        self.assertEqual((report["word_error_rate"], report["timing_within_tolerance"], report["style_match"]), (0, 1, 1))
        self.assertTrue(report["passed"])
        self.assertEqual(report["failed"], [])

    def test_a_misspelt_name_counts_as_one_word_error(self):
        report = cc.score(track(REFERENCE), track(REFERENCE.replace("Priya", "Preeya")))
        self.assertAlmostEqual(report["word_error_rate"], 1 / 10)
        self.assertIn("word_error_rate", report["failed"])

    def test_case_and_punctuation_only_change_the_exact_rate(self):
        report = cc.score(track(REFERENCE), track(REFERENCE.lower().replace(",", "")))
        self.assertEqual(report["word_error_rate"], 0)
        self.assertGreater(report["word_error_rate_exact"], 0)
        self.assertTrue(report["passed"])

    def test_timing_is_compared_on_matched_words_within_tolerance(self):
        shifted = cc.score(track(REFERENCE), track(REFERENCE, start=5), tolerance=2)
        self.assertEqual(shifted["timing_within_tolerance"], 0)
        self.assertEqual(shifted["timing_max_error_frames"], 5)
        self.assertIn("timing_within_tolerance", shifted["failed"])
        self.assertEqual(cc.score(track(REFERENCE), track(REFERENCE, start=2), tolerance=2)["timing_within_tolerance"], 1)

    def test_style_mismatches_are_listed(self):
        other = {**STYLE, "inputs": {**STYLE["inputs"], "allWordsOutlineThickness": 0.05}}
        report = cc.score(track(REFERENCE, preset=STYLE), track(REFERENCE, preset=other))
        self.assertAlmostEqual(report["style_match"], 2 / 3)
        self.assertEqual(report["style_mismatches"], {"allWordsOutlineThickness": [0.03, 0.05]})
        self.assertIn("style_match", report["failed"])
        self.assertEqual(cc.score(track(REFERENCE, preset=STYLE), track(REFERENCE))["style_match"], 0)
        self.assertIsNone(cc.score(track(REFERENCE), track(REFERENCE, preset=STYLE))["style_match"])

    def test_qc_errors_and_unreadable_cards_fail_but_long_lines_do_not(self):
        issues = [{"id": "0", "code": "caption_overlap"}, {"id": "0", "code": "overflow_length", "severity": "warning"}]
        report = cc.score(track(REFERENCE), track(REFERENCE, issues=issues))
        self.assertEqual(report["check"], {"errors": ["caption_overlap"], "warnings": {"overflow_length": 1}})
        self.assertIn("check_errors", report["failed"])
        short = [{"id": "0", "code": "too_short", "severity": "warning"}]
        self.assertIn("too_short", cc.score(track(REFERENCE), track(REFERENCE, issues=short))["failed"])

    def test_words_without_timing_count_for_text_but_not_timing(self):
        candidate = track(REFERENCE)
        candidate["captions"][0]["words"][0]["start"] = None
        report = cc.score(track(REFERENCE), candidate)
        self.assertEqual(report["word_error_rate"], 0)
        self.assertEqual(report["timing_within_tolerance"], 1)

    def test_card_counts_are_reported(self):
        report = cc.score(track(REFERENCE), track(REFERENCE))
        self.assertEqual(report["cards"], {"reference": 1, "candidate": 1, "max_words_per_card": 10})


if __name__ == "__main__":
    unittest.main()
