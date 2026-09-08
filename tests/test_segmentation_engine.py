import unittest

from cartoon_sub.subtitle.models import Utterance
from cartoon_sub.subtitle.segmentation import (LocalSegmentationEngine, SegmentationProfile,
    SegmentationSettings, segment_utterance, settings_for)
from cartoon_sub.syllable.vietnamese import count_syllables


class SegmentationEngineTests(unittest.TestCase):
    def utterance(self, text, duration=8):
        return Utterance(12, 10, 10 + duration, "中文", vi_subtitle=text, speaker_id="SPK_02")

    def test_short_acceptable_subtitle_remains_one_part(self):
        row = self.utterance("Chuyện này không liên quan đến cô.", 2.4)
        plan = segment_utterance(row)
        self.assertEqual(plan.parts, (row.vi_subtitle,))
        self.assertTrue(plan.accepted_as_one)
        self.assertFalse(plan.requires_timestamp_allocation)

    def test_long_semantic_subtitle_splits_without_mutating_utterance(self):
        text = ("Các khanh, năm Trẫm 28 tuổi, huynh đệ bị sát hại, phụ hoàng bị giam lỏng, "
                "một mình Trẫm chống đỡ cả bầu trời Đại Đường.")
        row = self.utterance(text, 8)
        plan = segment_utterance(row)
        self.assertGreater(len(plan.parts), 1)
        self.assertTrue(plan.preserves_source_text())
        self.assertTrue(plan.requires_timestamp_allocation)
        self.assertEqual(row.display_segments, [])
        self.assertTrue(any(reason in {"comma_boundary", "semantic_boundary", "sentence_boundary"}
                            for reason in plan.boundary_reasons))

    def test_commas_do_not_split_an_already_acceptable_subtitle(self):
        row = self.utterance("Anh nghe này, rồi về nhé.", 2.5)
        self.assertLessEqual(count_syllables(row.vi_subtitle), 18)
        plan = segment_utterance(row)
        self.assertEqual(len(plan.parts), 1)

    def test_protected_cao_cau_ly_is_never_a_boundary(self):
        row = self.utterance(
            "Hoàng đế quyết định chinh phạt Cao Câu Ly lần thứ ba, vì vậy toàn triều đều phản đối quyết định này.", 8)
        plan = segment_utterance(row)
        self.assertGreater(len(plan.parts), 1)
        for left, right in zip(plan.parts, plan.parts[1:]):
            self.assertFalse(left.rstrip().endswith("Cao"))
            self.assertFalse(right.lstrip().startswith("Câu Ly"))

    def test_syllable_overflow_triggers_semantic_segmentation(self):
        row = self.utterance(
            "Anh thật sự cho rằng hôm nay tôi đến đây chỉ vì muốn xin anh tha thứ cho những chuyện trước kia sao?", 7)
        self.assertGreater(count_syllables(row.vi_subtitle), 18)
        plan = segment_utterance(row)
        self.assertGreater(len(plan.parts), 1)
        self.assertTrue(plan.preserves_source_text())

    def test_cross_speaker_overlap_is_not_changed_by_planning(self):
        first = Utterance(1, 10, 14, "甲", vi="Anh nghe tôi giải thích, chuyện này không phải như anh nghĩ!",
            speaker_id="SPK_01")
        second = Utterance(2, 11, 13, "乙", vi="Tôi không muốn nghe nữa!", speaker_id="SPK_02")
        first_plan, second_plan = segment_utterance(first), segment_utterance(second)
        self.assertEqual((first.start, first.end, second.start, second.end), (10, 14, 11, 13))
        self.assertEqual((first_plan.speaker_id, second_plan.speaker_id), ("SPK_01", "SPK_02"))
        self.assertTrue(first_plan.preserves_source_text())
        self.assertTrue(second_plan.preserves_source_text())

    def test_profiles_and_custom_settings(self):
        self.assertEqual(settings_for(SegmentationProfile.BALANCED).max_syllables, 18)
        self.assertEqual(settings_for(SegmentationProfile.READING_COMFORT).max_syllables, 16)
        self.assertEqual(settings_for(SegmentationProfile.FAST_DIALOGUE).max_syllables, 14)
        self.assertEqual(settings_for(SegmentationProfile.PRESERVE_SENTENCES).max_syllables, 20)
        custom = SegmentationSettings(max_syllables=12, preferred_syllables_max=10)
        engine = LocalSegmentationEngine(SegmentationProfile.CUSTOM, custom)
        self.assertEqual(engine.settings.max_syllables, 12)


if __name__ == "__main__":
    unittest.main()
