import tempfile
import unittest
from pathlib import Path

import pysubs2

from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.parser import export_srt
from cartoon_sub.subtitle.segmentation import SegmentationSettings
from cartoon_sub.subtitle.segmentation_qc import review_display_segment
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService, presentation_segments, segmentation_fingerprint


class SegmentationPhase4Tests(unittest.TestCase):
    def settings(self):
        return SegmentationSettings(min_duration=1, preferred_duration_min=1,
            preferred_duration_max=2, max_duration=2, preferred_syllables_min=2,
            preferred_syllables_max=4, max_syllables=4, max_lines=1,
            preferred_chars_per_line=20, hard_max_chars_per_line=30)

    def test_qc_reports_each_presentation_warning(self):
        settings = self.settings()
        self.assertIn("TOO_SHORT", review_display_segment(DisplaySegment("1.1", 1, 0, .5, "ngắn"), settings))
        self.assertIn("TOO_LONG", review_display_segment(DisplaySegment("1.2", 1, 0, 3, "vừa đủ"), settings))
        self.assertIn("TOO_MANY_SYLLABLES", review_display_segment(
            DisplaySegment("1.3", 1, 0, 2, "một hai ba bốn năm"), settings))
        self.assertIn("TOO_MANY_LINES", review_display_segment(
            DisplaySegment("1.4", 1, 0, 2, "một\nhai"), settings))
        self.assertIn("HIGH_READING_SPEED", review_display_segment(
            DisplaySegment("1.5", 1, 0, 1, "một hai ba bốn năm sáu"), settings))
        self.assertIn("BAD_SPLIT", review_display_segment(
            DisplaySegment("1.6", 1, 0, 2, "Ly"), settings, "Cao Câu Ly xuất hiện"))
        self.assertIn("MANUAL_REVIEW", review_display_segment(
            DisplaySegment("1.7", 1, 0, 2, "đã sửa", manual=True), settings))
        self.assertEqual(review_display_segment(
            DisplaySegment("1.8", 1, 0, 2, "vừa đủ", segmentation_reason="local"), settings), ["OK"])

    def test_auto_manual_merge_reset_and_cache_scope(self):
        utterance = Utterance(1, 0, 8, "原文", vi="Câu đầu tiên khá dài, câu thứ hai cũng khá dài để kiểm tra.",
            speaker_id="SPK_01")
        project = Project("p", "source.mp4", segments=[utterance])
        service = SubtitleSegmentationService()
        changed, skipped = service.auto_segment(project)
        self.assertEqual((changed, skipped), ([1], []))
        self.assertGreaterEqual(len(utterance.display_segments), 1)
        before = segmentation_fingerprint(utterance, *service.settings_for(project))
        project.subtitle_style.font = "Noto Sans"
        self.assertEqual(before, segmentation_fingerprint(utterance, *service.settings_for(project)))
        service.split_manual(project, 1, utterance.display_segments[0].id, 2)
        self.assertTrue(project.segmentation_cache["1"]["manual"])
        self.assertTrue(any(item.manual for item in utterance.display_segments))
        service.merge_manual(project, 1, [item.id for item in utterance.display_segments[:2]])
        self.assertTrue(all(item.speaker_id == "SPK_01" for item in utterance.display_segments))
        service.reset(project, [1])
        self.assertEqual([(item.start, item.end, item.vi_text) for item in utterance.display_segments],
            [(0, 8, utterance.vi_subtitle)])
        self.assertIn("1", project.segmentation_cache)

    def test_vietnamese_srt_exports_display_segments_and_keeps_overlap(self):
        first = Utterance(1, 0, 4, "甲", vi="một hai", speaker_id="SPK_01", display_segments=[
            DisplaySegment("1.1", 1, 0, 2, "một"), DisplaySegment("1.2", 1, 2, 4, "hai")])
        second = Utterance(2, 1, 3, "乙", vi="ba", speaker_id="SPK_02", display_segments=[
            DisplaySegment("2.1", 2, 1, 3, "ba")])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "vi.srt"
            export_srt([first, second], output, "vi")
            events = pysubs2.load(str(output))
        self.assertEqual([(event.start, event.end, event.plaintext) for event in events],
            [(0, 2000, "một"), (2000, 4000, "hai"), (1000, 3000, "ba")])
        self.assertEqual([item.speaker_id for item in first.display_segments + second.display_segments],
            ["SPK_01", "SPK_01", "SPK_02"])

    def test_untranslated_utterance_has_no_temporary_empty_display_segment(self):
        utterance = Utterance(1, 0, 2, "你好", vi="", speaker_id="SPK_01")
        project = Project("p", "source.mp4", segments=[utterance])
        self.assertEqual(presentation_segments(utterance), [])
        self.assertEqual(SubtitleSegmentationService().rows(project), [(utterance, [])])


if __name__ == "__main__":
    unittest.main()
