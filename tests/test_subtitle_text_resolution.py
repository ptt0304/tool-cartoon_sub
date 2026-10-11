import unittest

from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.segmentation_service import (
    SubtitleSegmentationService, resolve_vietnamese_text,
)
from cartoon_sub.subtitle.segmentation import LocalSegmentationEngine, SegmentationSettings, content_token_count


class SubtitleTextResolutionTests(unittest.TestCase):
    def make_project(self, text="Một câu phụ đề Việt đủ rõ ràng để hiển thị."):
        return Project("demo", "", segments=[Utterance(1, 0, 6, "中文", vi_subtitle=text,
                                                          vi_dubbing="Bản lồng tiếng", speaker_id="SPK_01")])

    def test_utterance_vietnamese_creates_non_empty_display_segment(self):
        project = self.make_project(); service = SubtitleSegmentationService()
        service.auto_segment(project)
        self.assertTrue(all(item.vi_text.strip() for item in project.utterances[0].display_segments))

    def test_vi_subtitle_source_resolves_canonical_utterance_state(self):
        project = self.make_project("Bản dịch đang nằm trong trạng thái Subtitle.")
        # Segmentation cache stores fingerprints only; VI Subtitle's canonical
        # cache/state is the Utterance field used by the UI.
        project.segmentation_cache["1"] = {"legacy_cache_value": "không được dùng làm nguồn"}
        self.assertEqual(resolve_vietnamese_text(project, project.utterances[0]),
                         "Bản dịch đang nằm trong trạng thái Subtitle.")

    def test_parent_and_children_share_resolved_vietnamese_source(self):
        project = self.make_project("Một câu dài có thể được chia thành nhiều phần hiển thị an toàn.")
        service = SubtitleSegmentationService(); service.auto_segment(project, [1], force=True)
        parent = project.utterances[0]
        self.assertEqual(" ".join(item.vi_text for item in parent.display_segments).split(), parent.vi_subtitle.split())

    def test_true_empty_vietnamese_still_raises(self):
        with self.assertRaisesRegex(ValueError, "Display segment Vietnamese text must not be empty"):
            DisplaySegment("1.1", 1, 0, 1, "")

    def test_all_and_selected_use_same_resolution(self):
        text = "Một câu rất dài, có nhiều nhịp ngắt, để bộ tách tạo cùng nội dung ở hai đường chạy."
        all_project, selected_project = self.make_project(text), self.make_project(text)
        service = SubtitleSegmentationService()
        service.auto_segment(all_project, force=True)
        service.auto_segment(selected_project, [1], force=True)
        self.assertEqual([item.vi_text for item in all_project.utterances[0].display_segments],
                         [item.vi_text for item in selected_project.utterances[0].display_segments])

    def test_reset_to_utterance_preserves_resolved_vietnamese(self):
        project = self.make_project("Bản phụ đề cần còn nguyên sau khi reset.")
        project.utterances[0].set_display_segments([DisplaySegment("1.1", 1, 0, 6, "Bản phụ đề cũ")])
        SubtitleSegmentationService().reset(project, [1])
        self.assertEqual(" ".join(item.vi_text for item in project.utterances[0].display_segments).split(),
                         project.utterances[0].vi_subtitle.split())

    def test_punctuation_whitespace_boundary_never_creates_empty_child(self):
        project = self.make_project("Ngươi? Sao có thể không lấy được ngươi?")
        SubtitleSegmentationService().auto_segment(project, [1], force=True)
        self.assertTrue(all(item.vi_text.strip() for item in project.utterances[0].display_segments))

    def test_max_token_hard_gate_and_balanced_fallback(self):
        settings = SegmentationSettings(max_syllables=14, preferred_syllables_max=8)
        engine = LocalSegmentationEngine(custom_settings=settings, profile="CUSTOM")
        short = Utterance(1, 0, 6, "", vi_subtitle="Hãy nhớ kỹ, ta chỉ phục tùng Đấng Sáng Tạo.")
        self.assertEqual(len(engine.segment(short).parts), 1)
        text = " ".join(["một"] * 15)
        parts = engine.segment(Utterance(2, 0, 6, "", vi_subtitle=text)).parts
        self.assertEqual([content_token_count(x) for x in parts], [8, 7])

    def test_at_or_below_token_limit_never_splits_for_qc_signals(self):
        settings = SegmentationSettings(
            min_duration=1, preferred_duration_min=2, preferred_duration_max=4,
            max_duration=5, preferred_syllables_min=8, preferred_syllables_max=8,
            max_syllables=14, max_lines=1, preferred_chars_per_line=10,
            hard_max_chars_per_line=12,
        )
        text = "một hai ba bốn năm sáu bảy tám chín mười mười một mười hai"
        plan = LocalSegmentationEngine(profile="CUSTOM", custom_settings=settings).segment(
            Utterance(1, 0, 12, "", vi_subtitle=text)
        )
        self.assertEqual(content_token_count(text), 14)
        self.assertEqual(plan.parts, (text,))

    def test_sentence_candidates_choose_nearest_midpoint_not_first(self):
        settings = SegmentationSettings(max_syllables=14, preferred_syllables_max=8)
        text = (
            "một hai ba bốn. năm sáu bảy tám chín mười mười một mười hai "
            "mười ba mười bốn mười lăm mười sáu. mười bảy mười tám"
        )
        plan = LocalSegmentationEngine(profile="CUSTOM", custom_settings=settings).segment(
            Utterance(1, 0, 10, "", vi_subtitle=text)
        )
        self.assertGreater(content_token_count(text), 14)
        self.assertTrue(any(part.rstrip().endswith("mười sáu.") for part in plan.parts))
        self.assertTrue(all(content_token_count(part) <= 14 for part in plan.parts))

    def test_recursive_split_and_reconstruction_preserve_everything(self):
        settings = SegmentationSettings(max_syllables=14, preferred_syllables_max=8)
        text = (
            "một hai ba bốn năm sáu bảy, tám chín mười mười một mười hai mười ba mười bốn, "
            "mười lăm mười sáu mười bảy mười tám mười chín hai mươi hai mươi mốt hai mươi hai."
        )
        plan = LocalSegmentationEngine(profile="CUSTOM", custom_settings=settings).segment(
            Utterance(1, 0, 12, "", vi_subtitle=text)
        )
        self.assertGreater(len(plan.parts), 2)
        self.assertTrue(all(content_token_count(part) <= 14 for part in plan.parts))
        self.assertEqual("".join(plan.parts), text)
        self.assertTrue(plan.preserves_source_text())


if __name__ == "__main__":
    unittest.main()
