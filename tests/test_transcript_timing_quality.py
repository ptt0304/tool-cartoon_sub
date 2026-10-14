import unittest

from cartoon_sub.transcription.timing_quality import evaluate_timing, TRUSTED, SUSPICIOUS, INVALID
from cartoon_sub.subtitle.models import Utterance


class TranscriptTimingQualityTests(unittest.TestCase):
    def test_word_evidence_is_trusted(self):
        segments = [{"start": 0, "end": 1.2, "zh": "你好。"}]
        words = [{"word": "你", "start": .1, "end": .4}, {"word": "好", "start": .45, "end": .8}]
        self.assertEqual(evaluate_timing(segments, words, 0, 1.5).status, TRUSTED)

    def test_giant_short_text_is_suspicious(self):
        segments = [{"start": 0, "end": 39.2, "zh": "别跑了。"}]
        self.assertEqual(evaluate_timing(segments, [], 0, 39.2).status, SUSPICIOUS)

    def test_missing_timestamps_are_invalid(self):
        self.assertEqual(evaluate_timing([], [], 0, 2).status, INVALID)

    def test_backward_word_timestamps_are_suspicious(self):
        segments = [{"start": 0, "end": 2, "zh": "你好。"}]
        words = [{"word": "你", "start": .8, "end": 1.2}, {"word": "好", "start": .5, "end": 1.0}]
        self.assertEqual(evaluate_timing(segments, words, 0, 2).status, SUSPICIOUS)

    def test_canonical_utterance_segment_timestamps_do_not_require_dict_access(self):
        rows = [Utterance(1, 0, 1.2, "你好。")]
        quality = evaluate_timing(rows, [], 0, 1.5)
        self.assertEqual(quality.status, TRUSTED)
        self.assertEqual(quality.timestamp_capability, "SEGMENT")


if __name__ == "__main__":
    unittest.main()
