import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea

from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.models import Project
from cartoon_sub.translation.pipeline import translation_fingerprint
from cartoon_sub.translation.prompts import build_context_instruction, editorial
from cartoon_sub.ui.tabs.translate_tab import build


class TranslateCustomRulesUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_custom_rules_are_independent_and_only_active_values_affect_cache(self):
        project = Project("p", "video.mp4", translation_genres=["custom"],
                          translation_preset="Natural Vietnamese", proper_name_mode="sino_vietnamese",
                          translation_custom_genre="Tiên hiệp hài")
        first = translation_fingerprint(project, AISettings())
        project.translation_custom_style = "Không được dùng"
        project.translation_custom_name_rule = "Không được dùng"
        self.assertEqual(translation_fingerprint(project, AISettings()), first)
        self.assertIn("Tiên hiệp hài", build_context_instruction(project))
        self.assertNotIn("Không được dùng", build_context_instruction(project))

        project.translation_preset = "Custom"
        project.translation_custom_style = "Thoại rất ngắn"
        project.proper_name_mode = "custom"
        project.translation_custom_name_rule = "Giữ pinyin"
        rendered = editorial(project)
        self.assertEqual(rendered["style"], "Thoại rất ngắn")
        self.assertIn("Giữ pinyin", rendered["context_instruction"])
        self.assertNotEqual(translation_fingerprint(project, AISettings()), first)

    def test_legacy_name_mode_migrates_to_custom(self):
        project = Project.from_dict({"schema_version": 3, "name": "old", "source_video_path": "x.mp4",
                                     "proper_name_mode": "Theo Mapping của user"})
        self.assertEqual(project.proper_name_mode, "custom")

    def test_four_columns_three_custom_boxes_and_no_wheel_selection(self):
        page = build()
        page.resize(600, 220)
        page.show()
        self.app.processEvents()
        self.assertEqual(len(page.genres), 28)
        self.assertIn("custom", page.genres)
        self.assertEqual(page.genres["custom"].text(), "Tùy chỉnh")
        for key in ("modern", "transmigration", "ancient", "cultivation"):
            page.genres[key].setChecked(True)
        self.assertEqual(sum(box.isChecked() for box in page.genres.values()), 3)
        self.assertEqual(page.custom_genre.maximumHeight(), page.custom_style.maximumHeight())
        self.assertEqual(page.custom_style.maximumHeight(), page.custom_name_rule.maximumHeight())
        scroll = page.findChildren(QScrollArea)[0]
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)

        combo = page.preset
        combo.setCurrentIndex(0)
        event = QWheelEvent(QPointF(2, 2), QPointF(2, 2), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        combo.wheelEvent(event)
        self.app.processEvents()
        self.assertEqual(combo.currentIndex(), 0)
        self.assertGreater(scroll.verticalScrollBar().value(), 0)


if __name__ == "__main__":
    unittest.main()
