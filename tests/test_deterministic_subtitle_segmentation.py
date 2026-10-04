import unittest

from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.segmentation import (SegmentationPlan, SegmentationProfile,
    SegmentationSettings, part_is_hard_valid, segment_utterance)
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.subtitle.segmentation_timing import allocate_display_segments
from cartoon_sub.subtitle.segmentation_qc import review_display_segments


def settings(*, hard_chars=60, max_duration=5.0):
    return SegmentationSettings(
        min_duration=1.0, preferred_duration_min=2.0,
        preferred_duration_max=3.0, max_duration=max_duration,
        preferred_syllables_min=8, preferred_syllables_max=10,
        max_syllables=14, max_lines=1,
        preferred_chars_per_line=min(36, hard_chars),
        hard_max_chars_per_line=hard_chars,
    )


def plan(text, *, hard_chars=60, duration=4.0, maximum=5.0):
    utterance = Utterance(1, 0, duration, "中文", vi_subtitle=text, speaker_id="SPK_01")
    result = segment_utterance(utterance, SegmentationProfile.CUSTOM,
                               settings(hard_chars=hard_chars, max_duration=maximum))
    return utterance, result


class DeterministicSubtitleSegmentationTests(unittest.TestCase):
    def test_valid_current_regression_stays_one_segment(self):
        text = "Vậy thì phải xem các hạ có bao nhiêu bản lĩnh rồi."
        _, result = plan(text)
        self.assertEqual(result.parts, (text,))

    def test_semicolon_and_comma_do_not_force_a_valid_split(self):
        for text in ("Mệnh lệnh đã rõ; chúng ta lập tức xuất phát.",
                     "Cô chủ, chúng ta đi thôi."):
            with self.subTest(text=text):
                _, result = plan(text)
                self.assertEqual(result.parts, (text,))

    def test_required_split_prefers_semicolon_then_comma(self):
        cases = (
            ("Mệnh lệnh đã rõ ràng; chúng ta lập tức xuất phát ngay.", 35, "semicolon_boundary"),
            ("Cô chủ hãy đi trước, chúng ta sẽ theo sau ngay.", 30, "comma_boundary"),
        )
        for text, hard_chars, reason in cases:
            with self.subTest(reason=reason):
                _, result = plan(text, hard_chars=hard_chars)
                self.assertGreater(len(result.parts), 1)
                self.assertEqual(result.boundary_reasons[0], reason)

    def test_required_split_without_punctuation_is_balanced_and_hard_valid(self):
        text = "Chúng ta lập tức lên đường tìm cô chủ trước khi trời tối"
        utterance, result = plan(text, hard_chars=30)
        active = settings(hard_chars=30)
        self.assertEqual(result.boundary_reasons, ("whitespace_boundary",))
        self.assertTrue(all(part_is_hard_valid(part, utterance.duration * len(part) / len(text), active)
                            for part in result.parts))
        lengths = [len(part.strip()) for part in result.parts]
        self.assertLessEqual(max(lengths) - min(lengths), 5)

    def test_required_split_does_not_leave_roi_as_an_orphan(self):
        text = "Vậy thì phải xem các hạ có bao nhiêu bản lĩnh rồi."
        _, result = plan(text, hard_chars=30)
        self.assertNotIn("rồi.", [part.strip().casefold() for part in result.parts])

    def test_word_timestamp_boundary_is_not_rebalanced(self):
        utterance = Utterance(1, 0, 5, "中文", vi_subtitle="Một hai ba bốn", speaker_id="SPK_01")
        split = SegmentationPlan(1, "SPK_01", utterance.vi_subtitle,
            ("Một hai ", "ba bốn"), ("whitespace_boundary",), (), False, True)
        marks = [
            {"text": "Một", "start": 0.0, "end": 0.7},
            {"text": "hai", "start": 0.8, "end": 1.8},
            {"text": "ba", "start": 3.0, "end": 3.7},
            {"text": "bốn", "start": 4.0, "end": 4.8},
        ]
        result = allocate_display_segments(utterance, split, settings(), word_timestamps=marks)
        self.assertEqual(result.timing_source, "word_timestamps")
        self.assertEqual(result.segments[0].end, 1.8)
        self.assertEqual(result.segments[1].start, 1.8)

    def test_all_and_selected_service_paths_never_call_semantic_ai(self):
        rows = [
            Utterance(1, 0, 6, "中", vi_subtitle="Một hai ba bốn năm sáu bảy tám chín mười."),
            Utterance(2, 6, 12, "文", vi_subtitle="Cô chủ hãy đi trước, chúng ta sẽ theo sau ngay."),
        ]
        project = Project("local", "source.mp4", segments=rows)
        service = SubtitleSegmentationService()
        service.auto_segment(project, [1])
        service.auto_segment(project)
        self.assertFalse(hasattr(service, "semantic_service"))

    def test_qc_flags_split_of_hard_valid_source_and_orphan(self):
        active = settings()
        source = "Cô chủ, chúng ta đi thôi."
        segments = [
            DisplaySegment("1.1", 1, 0, 2, "Cô chủ, ", segmentation_reason="comma_boundary"),
            DisplaySegment("1.2", 1, 2, 4, "chúng ta đi thôi.", segmentation_reason="utterance_end"),
        ]
        reviewed = review_display_segments(segments, active, source, 4)
        self.assertTrue(all("BAD_SPLIT" in flags for flags in reviewed))

        orphan_source = "Chúng ta đi ngay bây giờ rồi."
        orphan = [
            DisplaySegment("2.1", 2, 0, 3, "Chúng ta đi ngay bây giờ ", segmentation_reason="semantic_boundary"),
            DisplaySegment("2.2", 2, 3, 4, "rồi.", segmentation_reason="utterance_end"),
        ]
        self.assertIn("BAD_SPLIT", review_display_segments(orphan, active, orphan_source, 6)[1])

    def test_qc_flags_poor_word_break_when_usable_comma_exists(self):
        active = settings(hard_chars=30)
        source = "Cô chủ hãy đi trước, chúng ta sẽ theo sau ngay."
        segments = [
            DisplaySegment("1.1", 1, 0, 1, "Cô chủ hãy ", segmentation_reason="whitespace_boundary"),
            DisplaySegment("1.2", 1, 1, 4, "đi trước, chúng ta sẽ theo sau ngay.", segmentation_reason="utterance_end"),
        ]
        reviewed = review_display_segments(segments, active, source, 4)
        self.assertTrue(all("BAD_SPLIT" in flags for flags in reviewed))


if __name__ == "__main__":
    unittest.main()
