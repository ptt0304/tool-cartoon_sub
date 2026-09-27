import os
import unittest
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.segmentation import SegmentationProfile, SegmentationSettings, segment_utterance, settings_for
from cartoon_sub.subtitle.segmentation_qc import review_display_segment
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.ui.tabs.subtitle_tab import SubtitlePage


class SubtitleSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_vietnamese_labels_descriptions_and_new_defaults(self):
        page = SubtitlePage()
        labels = {label.text() for label in page.findChildren(QLabel)}
        self.assertTrue({"Cấu hình", "Thời lượng khuyến nghị", "Thời lượng tối đa",
                         "Âm tiết khuyến nghị", "Tối đa âm tiết", "Tối đa số dòng",
                         "Ký tự/dòng khuyến nghị", "Tối đa ký tự/dòng"}.issubset(labels))
        self.assertGreaterEqual(sum("ngưỡng mềm" in text.lower() for text in labels), 2)
        self.assertEqual(page.apply_settings.text(), "Áp dụng cài đặt")
        new_project = Project.from_dict({"schema_version": 3, "name": "new", "source_video_path": "missing.mp4"})
        page.load_project(new_project)
        self.assertEqual((page.preferred_syllables.value(), page.max_syllables.value(), page.max_lines.value()),
                         (8, 14, 1))
        page.close()

    def test_gui_values_become_custom_and_validation_is_not_silent(self):
        page = SubtitlePage()
        page.set_settings(settings_for(SegmentationProfile.BALANCED))
        page.profile.setCurrentIndex(page.profile.findData(SegmentationProfile.BALANCED.value))
        page.preferred_duration.setValue(2.5);page.max_duration.setValue(4.0)
        page.preferred_syllables.setValue(8);page.max_syllables.setValue(14);page.max_lines.setValue(1)
        page.chars_per_line.setValue(36);page.hard_chars_per_line.setValue(44)
        profile, settings = page.values()
        self.assertEqual(profile, SegmentationProfile.CUSTOM.value)
        self.assertEqual((settings.preferred_duration_max, settings.max_duration,
                          settings.preferred_syllables_max, settings.max_syllables, settings.max_lines,
                          settings.preferred_chars_per_line, settings.hard_max_chars_per_line),
                         (2.5, 4.0, 8, 14, 1, 36, 44))
        page.preferred_syllables.setValue(15);page.max_syllables.setValue(10)
        with self.assertRaisesRegex(ValueError, "Tối đa âm tiết"):
            page.values()
        page.close()

    def test_old_custom_project_values_are_preserved(self):
        old_settings = SegmentationSettings(preferred_syllables_max=16, max_syllables=18, max_lines=2)
        old = Project.from_dict({"schema_version": 3, "name": "old", "source_video_path": "missing.mp4",
                                 "segmentation_profile": "CUSTOM",
                                 "segmentation_settings": asdict(old_settings)})
        profile, loaded = SubtitleSegmentationService().settings_for(old)
        self.assertEqual(profile, SegmentationProfile.CUSTOM)
        self.assertEqual((loaded.preferred_syllables_max, loaded.max_syllables, loaded.max_lines), (16, 18, 2))

    def test_apply_model_recomputes_qc_without_resegmenting(self):
        text = "một hai ba bốn năm sáu bảy tám chín mười xuân hạ"
        utterance = Utterance(1, 0, 5, "中文", vi_subtitle=text)
        segment = DisplaySegment("1.1", 1, 0, 5, text)
        utterance.set_display_segments([segment])
        project = Project("test", "missing.mp4", segments=[utterance])
        service = SubtitleSegmentationService()
        before_identity = id(utterance.display_segments[0])
        strict = SegmentationSettings(preferred_syllables_max=8, max_syllables=10, max_lines=1)
        service.update_settings(project, SegmentationProfile.CUSTOM, strict)
        self.assertIn("TOO_MANY_SYLLABLES", segment.qc_flags)
        self.assertEqual(id(utterance.display_segments[0]), before_identity)
        reloaded = Project.from_dict(project.to_dict())
        self.assertEqual(SubtitleSegmentationService().settings_for(reloaded)[1], strict)
        relaxed = SegmentationSettings(preferred_syllables_max=8, max_syllables=14, max_lines=1)
        service.update_settings(project, SegmentationProfile.CUSTOM, relaxed)
        self.assertNotIn("TOO_MANY_SYLLABLES", segment.qc_flags)

    def test_preferred_chars_is_soft_and_hard_chars_is_qc_limit(self):
        settings = SegmentationSettings(preferred_chars_per_line=36, hard_max_chars_per_line=44)
        soft = DisplaySegment("1.1", 1, 0, 3, "a" * 39)
        hard = DisplaySegment("1.2", 1, 0, 3, "a" * 45)
        self.assertNotIn("TOO_MANY_CHARS_PER_LINE", review_display_segment(soft, settings))
        self.assertIn("TOO_MANY_CHARS_PER_LINE", review_display_segment(hard, settings))

    def test_max_lines_one_is_consumed_by_auto_segment(self):
        text = "Một hai ba bốn năm sáu bảy tám, chín mười một hai ba bốn năm sáu."
        utterance = Utterance(1, 0, 6, "中文", vi_subtitle=text)
        settings = SegmentationSettings(preferred_syllables_max=8, max_syllables=14, max_lines=1,
                                        preferred_chars_per_line=36, hard_max_chars_per_line=44)
        plan = segment_utterance(utterance, SegmentationProfile.CUSTOM, settings)
        self.assertGreater(len(plan.parts), 1)
        self.assertTrue(all(len(part.strip()) <= 44 for part in plan.parts))


if __name__ == "__main__":
    unittest.main()
