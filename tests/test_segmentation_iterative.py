import unittest
from unittest.mock import Mock

from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.subtitle.segmentation import SegmentationProfile, SegmentationSettings
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.syllable.vietnamese import count_syllables


CASE_65 = "Tỉnh lại đi chứ, lung lay thế này... tiểu chủ nhân, người mà cứ lắc nữa là hắn từ cấp cứu thành về với miêu tinh luôn đấy."
CASE_138 = "Tiểu chủ nhân, sao chưa đi tìm chị? Đồ mặc mãi khán giả chán rồi. Tôi phải diện thật xinh để lát nữa tặng chị bất ngờ."


def settings(max_syllables=14):
    return SegmentationSettings(preferred_syllables_min=8, preferred_syllables_max=min(8, max_syllables),
                                max_syllables=max_syllables, max_lines=1,
                                preferred_chars_per_line=36, hard_max_chars_per_line=44,
                                preferred_duration_min=2, preferred_duration_max=4, max_duration=5)


class IterativeSegmentationTests(unittest.TestCase):
    def project(self):
        return Project("real-cases", "source.mp4", segments=[
            Utterance(65, 203.372, 212.222, "原文", vi_subtitle=CASE_65, vi_dubbing=CASE_65),
            Utterance(138, 436.71, 445.13, "原文", vi_subtitle=CASE_138, vi_dubbing=CASE_138),
        ], subtitle_text_source="vi_dubbing")

    def test_real_cases_recursively_satisfy_hard_limits_and_log_children(self):
        semantic = Mock()
        service = SubtitleSegmentationService(semantic)
        project = self.project()
        service.update_settings(project, SegmentationProfile.CUSTOM, settings())
        with self.assertLogs("cartoon_sub.subtitle.segmentation_service", level="INFO") as captured:
            changed, skipped = service.auto_segment(project)
        self.assertEqual((changed, skipped), ([65, 138], []))
        self.assertIn("preferred_syllables=8 max_syllables=14 max_lines=1", captured.output[0])
        self.assertTrue(any("parent=65 child=65." in line for line in captured.output))
        self.assertTrue(any("parent=138 child=138." in line for line in captured.output))
        for utterance in project.utterances:
            self.assertGreater(len(utterance.display_segments), 2)
            self.assertTrue(all(count_syllables(child.vi_text) <= 14 for child in utterance.display_segments))
            self.assertTrue(all(len(child.vi_text.strip()) <= 44 for child in utterance.display_segments))
            self.assertTrue(all("TOO_MANY_SYLLABLES" not in child.qc_flags for child in utterance.display_segments))
        semantic.split.assert_not_called()

    def test_settings_changes_propagate_and_second_run_is_idempotent(self):
        service = SubtitleSegmentationService(Mock())
        project = self.project()
        snapshots = {}
        for maximum in (18, 14, 10):
            service.update_settings(project, SegmentationProfile.CUSTOM, settings(maximum))
            changed, _ = service.auto_segment(project)
            self.assertEqual(changed, [65, 138])
            self.assertTrue(all(count_syllables(child.vi_text) <= maximum
                                for row in project.utterances for child in row.display_segments))
            snapshots[maximum] = [[(child.vi_text, child.start, child.end) for child in row.display_segments]
                                  for row in project.utterances]
        changed, skipped = service.auto_segment(project)
        self.assertEqual((changed, skipped), ([], []))
        self.assertEqual(snapshots[10], [[(child.vi_text, child.start, child.end)
                                          for child in row.display_segments] for row in project.utterances])


if __name__ == "__main__":
    unittest.main()
