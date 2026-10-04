import tempfile
import unittest
from pathlib import Path

import pysubs2

from cartoon_sub.subtitle.models import Segment
from cartoon_sub.subtitle.parser import export_canonical_srt
from cartoon_sub.transcription.segmentation_normalizer import TranscriptSegmentationNormalizer


def timed_words(rows):
    return [{"word": text, "start": start, "end": end} for text, start, end in rows]


class TranscriptWordTimingAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = TranscriptSegmentationNormalizer()

    def test_minor_deletion_keeps_canonical_text_and_uses_word_anchors(self):
        parent = Segment(10, 0, 4, "小主人，你慢一点。")
        output = self.normalizer.normalize([parent], timed_words([
            ("小主", 1.2, 1.72), ("你", 1.82, 2.0), ("慢一点", 2.05, 2.7),
        ]))
        self.assertEqual([(row.id, row.zh) for row in output], [(1, parent.zh)])
        self.assertEqual((output[0].start, output[0].end), (1.2, 2.7))
        self.assertEqual(output[0].transcript_timing_provenance, "PARTIAL_WORD_ALIGNMENT")

    def test_insertion_deletion_and_substitution_do_not_drop_other_word_evidence(self):
        parent = Segment(1, 0, 8, "你不是说要保护我的吗。我们出发。")
        output = self.normalizer.normalize([parent], timed_words([
            ("你不是说保护我的吗", .2, 2.1), ("我门", 4.0, 4.5), ("出发", 4.6, 5.2),
        ]))
        self.assertEqual([row.zh for row in output], ["你不是说要保护我的吗。", "我们出发。"])
        self.assertEqual((output[0].start, output[0].end), (.2, 2.1))
        self.assertEqual((output[1].start, output[1].end), (4.0, 5.2))
        self.assertTrue(all(row.transcript_timing_provenance == "PARTIAL_WORD_ALIGNMENT" for row in output))

    def test_repeated_text_is_monotonic(self):
        parent = Segment(1, 0, 5, "好。好。好。")
        output = self.normalizer.normalize([parent], timed_words([
            ("好", .1, .3), ("好", 2.0, 2.3), ("好", 4.0, 4.4),
        ]))
        self.assertEqual([(row.start, row.end) for row in output], [(.1, .3), (2.0, 2.3), (4.0, 4.4)])
        self.assertTrue(all(left.end < right.start for left, right in zip(output, output[1:])))

    def test_punctuation_and_whitespace_do_not_prevent_word_alignment(self):
        parent = Segment(1, 0, 4, "你 好。\n再见！")
        output = self.normalizer.normalize([parent], timed_words([
            ("你", .1, .3), ("好", .4, .8), ("再见", 2.0, 2.5),
        ]))
        self.assertEqual([row.zh for row in output], ["你 好。", "再见！"])
        self.assertEqual([(row.start, row.end) for row in output], [(.1, .8), (2.0, 2.5)])

    def test_internal_unmatched_utterance_is_interpolated_only_between_anchors(self):
        parent = Segment(1, 0, 20, "甲乙。丙丁。戊己。")
        output = self.normalizer.normalize([parent], timed_words([
            ("甲乙", 1.0, 2.0), ("戊己", 10.0, 11.0),
        ]))
        self.assertEqual([row.zh for row in output], ["甲乙。", "丙丁。", "戊己。"])
        self.assertEqual((output[0].start, output[0].end), (1.0, 2.0))
        self.assertEqual((output[1].start, output[1].end), (2.0, 10.0))
        self.assertEqual((output[2].start, output[2].end), (10.0, 11.0))
        self.assertEqual(output[1].transcript_timing_provenance, "LOCAL_ESTIMATION")

    def test_word_timing_preserves_real_silence_and_chunk_relative_bounds(self):
        parent = Segment(1, 0, 60, "第一句。第二句。")
        output = self.normalizer.normalize([parent], timed_words([
            ("第一句", .42, 2.1), ("第二句", 14.5, 16.0),
        ]))
        self.assertEqual([(row.start, row.end) for row in output], [(.42, 2.1), (14.5, 16.0)])
        self.assertTrue(all(0 <= row.start < row.end <= 60 for row in output))
        self.assertTrue(all(row.transcript_timing_provenance == "WORD_TIMESTAMP" for row in output))

    def test_no_word_evidence_retains_weighted_last_resort(self):
        parent = Segment(1, 0, 20, "第一句。第二句话更长一些。")
        output = self.normalizer.normalize([parent])
        self.assertTrue(all(row.transcript_timing_provenance == "ESTIMATED" for row in output))
        self.assertEqual("".join(row.zh for row in output), parent.zh)
        self.assertEqual((output[0].start, output[-1].end), (0, 20))

    def test_unmatched_prefix_and_suffix_stay_inside_nearest_word_evidence(self):
        parent = Segment(1, 0, 12, "甲乙。丙丁。")
        output = self.normalizer.normalize([parent], timed_words([
            ("乙", 2.0, 2.4), ("丙", 8.0, 8.4),
        ]))
        self.assertEqual([(row.start, row.end) for row in output], [(2.0, 2.4), (8.0, 8.4)])
        self.assertTrue(all(0 <= row.start < row.end <= 12 for row in output))

    def test_srt_serializes_the_aligned_master_timeline_without_retiming(self):
        parent = Segment(1, 0, 5, "第一句。第二句。")
        output = self.normalizer.normalize([parent], timed_words([
            ("第一句", .42, 2.1), ("第二句", 3.05, 4.4),
        ]))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "aligned.srt"
            export_canonical_srt(output, path, "zh")
            events = pysubs2.load(str(path), encoding="utf-8-sig")
        self.assertEqual([(event.start, event.end, event.plaintext) for event in events], [
            (420, 2100, "第一句。"), (3050, 4400, "第二句。"),
        ])

    def test_utterance_serialization_keeps_timing_provenance(self):
        row = Segment(1, 1, 2, "你好", transcript_timing_provenance="WORD_TIMESTAMP",
                      transcript_timing_confidence=1.0)
        restored = Segment.from_dict(row.to_dict())
        self.assertEqual((restored.transcript_timing_provenance, restored.transcript_timing_confidence),
                         ("WORD_TIMESTAMP", 1.0))


if __name__ == "__main__":
    unittest.main()
