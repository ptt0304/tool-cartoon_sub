import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QTableWidget, QVBoxLayout, QWidget

from cartoon_sub.app.settings import SettingsStore
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.ui.zoom import UIZoomManager
from cartoon_sub.ui.zoom_dialog import ZoomDialog


_app = QApplication.instance() or QApplication([])


class UIZoomTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.layout = QVBoxLayout(self.host)
        self.table = QTableWidget(3, 2)
        self.layout.addWidget(self.table)
        self.host.show()
        _app.processEvents()
        self.manager = UIZoomManager(_app)
        self.base_font = self.manager.base_font.pointSizeF()
        margins = self.layout.contentsMargins()
        self.base_margins = (margins.left(), margins.top(), margins.right(), margins.bottom())
        self.base_row_height = self.table.verticalHeader().defaultSectionSize()

    def tearDown(self):
        self.manager.set_percent(100)
        self.host.close()

    def test_zero_is_safe_and_hundred_restores_without_drift(self):
        self.manager.set_percent(0)
        self.assertEqual(self.manager.actual_scale, 0.5)
        self.assertGreaterEqual(_app.font().pointSizeF(), 6.0)
        self.assertLessEqual(self.table.verticalHeader().defaultSectionSize(), self.base_row_height)
        self.manager.set_percent(25)
        self.manager.set_percent(50)
        self.manager.set_percent(75)
        self.manager.set_percent(100)
        self.assertAlmostEqual(_app.font().pointSizeF(), self.base_font)
        margins = self.layout.contentsMargins()
        self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()),
                         self.base_margins)
        self.assertEqual(self.table.verticalHeader().defaultSectionSize(), self.base_row_height)

    def test_dialog_exposes_zero_to_hundred_and_applies_value(self):
        values = []
        dialog = ZoomDialog(self.manager, values.append)
        self.assertEqual((dialog.slider.minimum(), dialog.slider.maximum()), (0, 100))
        dialog.slider.setValue(50)
        self.assertEqual(values[-1], 50)
        self.assertEqual(dialog.value_label.text(), "50%")

    def test_zoom_is_persisted_in_global_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(folder=directory)
            store.save_ui_zoom(50)
            self.assertEqual(store.load().ui_zoom_percent, 50)
            store.save_ui_zoom(100)
            self.assertEqual(store.load().ui_zoom_percent, 100)

    def test_zoom_does_not_change_project_or_render_settings(self):
        project = Project("zoom", "video.mp4", segments=[
            Segment(1, 0, 1, "你好", vi="Xin chào"),
        ])
        before = project.to_dict()
        for percent in (0, 25, 50, 75, 100):
            self.manager.set_percent(percent)
        self.assertEqual(project.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
