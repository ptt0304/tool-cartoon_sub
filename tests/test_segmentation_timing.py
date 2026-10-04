import unittest

from cartoon_sub.subtitle.models import Utterance
from cartoon_sub.subtitle.segmentation import LocalSegmentationEngine, SegmentationPlan
from cartoon_sub.subtitle.segmentation_timing import allocate_display_segments


class SegmentationTimingTests(unittest.TestCase):
    def row(self, text, end=10, speaker="SPK_01"):
        return Utterance(12, 0, end, "中文", vi_subtitle=text, speaker_id=speaker)

    def plan(self, row, parts):
        return SegmentationPlan(row.id, row.speaker_id, row.vi_subtitle, tuple(parts),
            tuple("semantic_boundary" for _ in parts[:-1]), (), False, len(parts) > 1)

    def test_allocated_segments_cover_utterance_without_gaps(self):
        row = self.row("Các khanh, năm Trẫm 28 tuổi, huynh đệ bị sát hại, phụ hoàng bị giam lỏng, "
                       "một mình Trẫm chống đỡ cả bầu trời Đại Đường.")
        plan = LocalSegmentationEngine().segment(row)
        result = allocate_display_segments(row, plan)
        segments = result.segments
        self.assertGreater(len(segments), 1)
        self.assertEqual(segments[0].start, row.start)
        self.assertEqual(segments[-1].end, row.end)
        self.assertTrue(all(left.end == right.start for left, right in zip(segments, segments[1:])))
        self.assertTrue(all(segment.end > segment.start for segment in segments))
        self.assertEqual("".join(segment.vi_text for segment in segments), row.vi_subtitle)
        self.assertEqual(row.display_segments, [])

    def test_word_timestamps_take_priority(self):
        row = self.row("Một hai ba bốn", 10)
        plan = self.plan(row, ("Một hai ", "ba bốn"))
        words = [{"text": "Một", "start": 0, "end": 1}, {"text": "hai", "start": 1, "end": 4},
                 {"text": "ba", "start": 4, "end": 7}, {"text": "bốn", "start": 7, "end": 10}]
        result = allocate_display_segments(row, plan, word_timestamps=words)
        self.assertEqual(result.timing_source, "word_timestamps")
        self.assertEqual(result.segments[0].end, 4)

    def test_source_clause_position_precedes_proportional_estimate(self):
        row = self.row("Một hai ba bốn năm", 6)
        plan = self.plan(row, ("Một ", "hai ba bốn năm"))
        result = allocate_display_segments(row, plan, clause_timestamps=[{"position": 4, "time": 3.5}])
        self.assertEqual(result.timing_source, "source_clause_position")
        self.assertEqual(result.segments[0].end, 3.5)

    def test_syllable_then_character_proportion_fallbacks(self):
        row = self.row("Một hai ba bốn năm sáu", 6)
        plan = self.plan(row, ("Một hai ", "ba bốn năm sáu"))
        result = allocate_display_segments(row, plan)
        self.assertEqual(result.timing_source, "syllable_proportion")
        self.assertEqual(result.segments[0].end, 2)

        punctuation = self.row("... ...", 7)
        punctuation_plan = self.plan(punctuation, ("...", " ..."))
        punctuation_result = allocate_display_segments(punctuation, punctuation_plan)
        self.assertEqual(punctuation_result.timing_source, "character_proportion")
        self.assertEqual(punctuation_result.segments[0].end, 3)

    def test_accurate_word_timing_is_not_rebalanced_to_soft_minimum(self):
        row = self.row("Một hai ba bốn năm sáu", 5)
        plan = self.plan(row, ("Một ", "hai ba bốn ", "năm sáu"))
        words = [{"start": 0, "end": .2}, {"start": .2, "end": 1}, {"start": 1, "end": 2},
                 {"start": 2, "end": 4}, {"start": 4, "end": 4.5}, {"start": 4.5, "end": 5}]
        result = allocate_display_segments(row, plan, word_timestamps=words)
        self.assertEqual([segment.start for segment in result.segments], [0, .2, 4])
        self.assertEqual(result.segments[-1].end, 5)
        self.assertIn("TOO_SHORT", result.segments[0].qc_flags)

    def test_cross_speaker_overlap_remains_independent(self):
        first = self.row("Anh nghe tôi giải thích, chuyện này không phải như anh nghĩ, "
                         "xin anh đừng vội kết luận khi chưa hiểu rõ mọi chuyện.", 4, "SPK_01")
        second = Utterance(13, 11, 13, "乙", vi="Tôi không muốn nghe nữa!", speaker_id="SPK_02")
        first_result = allocate_display_segments(first, LocalSegmentationEngine().segment(first))
        second_result = allocate_display_segments(second, LocalSegmentationEngine().segment(second))
        self.assertEqual((first.start, first.end, second.start, second.end), (0, 4, 11, 13))
        self.assertEqual((first_result.segments[0].start, first_result.segments[-1].end), (0, 4))
        self.assertEqual((second_result.segments[0].start, second_result.segments[-1].end), (11, 13))
        self.assertEqual({segment.speaker_id for segment in first_result.segments}, {"SPK_01"})
        self.assertEqual({segment.speaker_id for segment in second_result.segments}, {"SPK_02"})


if __name__ == "__main__":
    unittest.main()
