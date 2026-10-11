import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton

from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.transcription.video.roi import (
    DEFAULT_VIDEO_SUBTITLE_ROI, clamp_roi, display_to_normalized, image_rect,
    normalized_to_display,
)
from cartoon_sub.ui.transcript_roi_dialog import TranscriptRoiDialog, format_region_time, parse_region_time
from cartoon_sub.ui.tabs import transcript_tab


class RoiGeometryTests(unittest.TestCase):
    def test_region_time_format_and_parse(self):
        for seconds, text in ((0, "00h00m00s"), (10, "00h00m10s"), (65, "00h01m05s"),
                              (816, "00h13m36s"), (840, "00h14m00s"), (1098, "00h18m18s")):
            self.assertEqual(format_region_time(seconds), text)
            self.assertEqual(parse_region_time(text), float(seconds))

    def test_region_time_rejects_invalid_minutes_seconds_and_shape(self):
        for text in ("00h60m00s", "00h00m60s", "00:00:10", "abc"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_region_time(text)

    def test_default_is_valid_and_round_trips(self):
        self.assertEqual(clamp_roi(DEFAULT_VIDEO_SUBTITLE_ROI), DEFAULT_VIDEO_SUBTITLE_ROI)
        display = normalized_to_display(DEFAULT_VIDEO_SUBTITLE_ROI, (100, 50, 800, 400))
        self.assertEqual(display_to_normalized(display, (100, 50, 800, 400)), DEFAULT_VIDEO_SUBTITLE_ROI)

    def test_invalid_or_outside_values_are_clamped_inside_frame(self):
        roi = clamp_roi({"x_pct": 2, "y_pct": 2, "w_pct": 0, "h_pct": 0})
        self.assertGreater(roi["w_pct"], 0)
        self.assertGreater(roi["h_pct"], 0)
        self.assertLessEqual(roi["x_pct"] + roi["w_pct"], 1)
        self.assertLessEqual(roi["y_pct"] + roi["h_pct"], 1)

    def test_letterbox_mapping_uses_image_not_widget_bounds(self):
        rect = image_rect(1000, 1000, 1920, 1080)
        for actual, expected in zip(rect, (0.0, 218.75, 1000.0, 562.5)):
            self.assertAlmostEqual(actual, expected)
        roi = display_to_normalized((100, 331.25, 800, 337.5), rect)
        for key, expected in {"x_pct": .1, "y_pct": .2, "w_pct": .8, "h_pct": .6}.items():
            self.assertAlmostEqual(roi[key], expected)


class RoiPersistenceTests(unittest.TestCase):
    def _controller(self, folder):
        controller = Controller(settings_store=SimpleNamespace())
        controller.project = Project("p", "v.mp4", transcript_source_mode="video")
        controller.directory = Path(folder)
        ProjectManager().save(controller.project, folder)
        return controller

    def test_confirm_persists_normalized_roi(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = self._controller(folder)
            controller.set_video_subtitle_roi(DEFAULT_VIDEO_SUBTITLE_ROI)
            self.assertEqual(ProjectManager().load(folder).video_subtitle_roi, DEFAULT_VIDEO_SUBTITLE_ROI)

    def test_empty_roi_round_trips_for_old_or_reset_project(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = self._controller(folder)
            controller.set_video_subtitle_roi({})
            self.assertEqual(ProjectManager().load(folder).video_subtitle_roi, {})


class RoiDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cancel_does_not_commit_dialog_value(self):
        with tempfile.TemporaryDirectory() as folder:
            preview = Path(folder) / "frame.ppm"
            preview.write_text("P3\n2 2\n255\n255 255 255  255 255 255\n255 255 255  255 255 255\n")
            stored = dict(DEFAULT_VIDEO_SUBTITLE_ROI)
            dialog = TranscriptRoiDialog(preview, stored)
            dialog.canvas.resize(800, 450)
            dialog.canvas.set_roi(dialog.canvas._apply_drag(stored, "bottom_right", .1, .1))
            dialog.reject()
            self.assertEqual(stored, DEFAULT_VIDEO_SUBTITLE_ROI)
            dialog.close()

    def test_audio_mode_hides_roi_control(self):
        tab = transcript_tab.build()
        try:
            transcript_tab.set_mode(tab, "audio")
            self.assertTrue(tab.roi_button.isHidden())
            transcript_tab.set_mode(tab, "video")
            self.assertFalse(tab.roi_button.isHidden())
        finally:
            tab.close()


if __name__ == "__main__":
    unittest.main()

class RoiStaticPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        folder = tempfile.TemporaryDirectory()
        preview = Path(folder.name) / "frame.ppm"
        preview.write_text("P3\n2 2\n255\n255 255 255  255 255 255\n255 255 255  255 255 255\n")
        dialog = TranscriptRoiDialog(preview, [
            {"id": "region_001", "start_time": 1.0, "end_time": 8.0, "roi": dict(DEFAULT_VIDEO_SUBTITLE_ROI)},
            {"id": "region_002", "start_time": 12.0, "end_time": 20.0,
             "roi": {"x_pct": .2, "y_pct": .7, "w_pct": .6, "h_pct": .2}},
        ], duration=30)
        return folder, dialog

    def test_preview_time_sets_start_and_end(self):
        folder, dialog = self._dialog()
        try:
            dialog.time_spin.setValue(2.5); dialog._set_boundary("start_time")
            dialog.time_spin.setValue(7.5); dialog._set_boundary("end_time")
            self.assertEqual((dialog.regions[0]["start_time"], dialog.regions[0]["end_time"]), (2.5, 7.5))
        finally:
            dialog.close(); folder.cleanup()

    def test_hms_input_drives_preview_and_direct_numeric_boundaries(self):
        folder, dialog = self._dialog()
        try:
            calls = []; dialog.preview_at = lambda moment: calls.append(moment) or Path(folder.name) / "frame.ppm"
            dialog.time_spin.setText("00h00m07s"); dialog.time_spin.editingFinished.emit()
            dialog._preview_current(); dialog._set_boundary("start_time")
            dialog.end_spin.setText("00h00m08s"); dialog.end_spin.editingFinished.emit()
            self.assertEqual(calls, [7.0])
            self.assertEqual(dialog.regions[0]["start_time"], 7.0)
            self.assertEqual(dialog.regions[0]["end_time"], 8.0)
            self.assertEqual(dialog.start_spin.text(), "00h00m07s")
            self.assertEqual(dialog.end_spin.text(), "00h00m08s")
        finally:
            dialog.close(); folder.cleanup()

    def test_select_region_loads_its_independent_roi(self):
        folder, dialog = self._dialog()
        try:
            dialog.table.selectRow(1)
            self.assertEqual(dialog.canvas.roi, dialog.regions[1]["roi"])
        finally:
            dialog.close(); folder.cleanup()

    def test_roi_edit_and_direct_boundaries_are_region_specific(self):
        folder, dialog = self._dialog()
        try:
            dialog.canvas.set_roi({"x_pct": .3, "y_pct": .6, "w_pct": .5, "h_pct": .2})
            dialog.start_spin.setValue(2.0); dialog.end_spin.setValue(7.0)
            first = dict(dialog.regions[0]["roi"])
            dialog.table.selectRow(1)
            self.assertNotEqual(dialog.canvas.roi, first)
            self.assertEqual(dialog.regions[0]["start_time"], 2.0)
            self.assertEqual(dialog.regions[0]["end_time"], 7.0)
        finally:
            dialog.close(); folder.cleanup()

    def test_preview_callback_uses_current_timestamp_and_replaces_frame(self):
        folder, dialog = self._dialog()
        try:
            output = Path(folder.name) / "replacement.ppm"
            output.write_text("P3\n2 2\n255\n0 0 0  0 0 0\n0 0 0  0 0 0\n")
            calls = []
            dialog.preview_at = lambda moment: calls.append(moment) or output
            dialog.time_spin.setValue(12.345)
            dialog._preview_current()
            self.assertEqual(calls, [12.345])
            self.assertFalse(dialog.canvas.pixmap.isNull())
        finally:
            dialog.close(); folder.cleanup()

    def test_canvas_resizes_from_edges_and_corners_within_bounds(self):
        folder, dialog = self._dialog()
        try:
            canvas = dialog.canvas; canvas.resize(800, 450)
            initial = {"x_pct": .2, "y_pct": .2, "w_pct": .4, "h_pct": .4}
            moved = canvas._apply_drag(initial, "move", .1, .1)
            self.assertAlmostEqual(moved["x_pct"], .3)
            self.assertAlmostEqual(moved["y_pct"], .3)
            self.assertAlmostEqual(canvas._apply_drag(initial, "right", .1, 0)["w_pct"], .5)
            self.assertAlmostEqual(canvas._apply_drag(initial, "left", .1, 0)["x_pct"], .3)
            self.assertAlmostEqual(canvas._apply_drag(initial, "top", 0, .1)["y_pct"], .3)
            corner = canvas._apply_drag(initial, "bottom_right", .1, .1)
            self.assertAlmostEqual(corner["w_pct"], .5)
            self.assertAlmostEqual(corner["h_pct"], .5)
            clamped = canvas._apply_drag(initial, "bottom_right", 2, 2)
            self.assertLessEqual(clamped["x_pct"] + clamped["w_pct"], 1.0)
            self.assertLessEqual(clamped["y_pct"] + clamped["h_pct"], 1.0)
        finally:
            dialog.close(); folder.cleanup()

    def test_canvas_resize_enforces_minimum_and_keeps_regions_independent(self):
        folder, dialog = self._dialog()
        try:
            canvas = dialog.canvas; canvas.resize(800, 450)
            initial = {"x_pct": .2, "y_pct": .2, "w_pct": .4, "h_pct": .4}
            resized = canvas._apply_drag(initial, "top_left", 2, 2)
            image = canvas._image_rect()
            self.assertGreaterEqual(resized["w_pct"], canvas.MINIMUM_ROI_PIXELS / image.width())
            self.assertGreaterEqual(resized["h_pct"], canvas.MINIMUM_ROI_PIXELS / image.height())
            canvas.set_roi(resized)
            first = dict(dialog.regions[0]["roi"])
            dialog.table.selectRow(1)
            canvas.set_roi({"x_pct": .1, "y_pct": .6, "w_pct": .7, "h_pct": .2})
            self.assertEqual(dialog.regions[0]["roi"], first)
            dialog.table.selectRow(0)
            self.assertEqual(canvas.roi, first)
        finally:
            dialog.close(); folder.cleanup()

    def test_default_roi_applies_only_to_selected_region_and_confirm_keeps_resize(self):
        folder, dialog = self._dialog()
        try:
            canvas = dialog.canvas; canvas.resize(800, 450)
            resized = canvas._apply_drag(dialog.regions[0]["roi"], "bottom_right", .1, .1)
            canvas.set_roi(resized)
            dialog.table.selectRow(1)
            second_before = dict(dialog.regions[1]["roi"])
            default = next(button for button in dialog.findChildren(QPushButton)
                           if button.text() == "ROI mặc định dưới giữa")
            default.click()
            self.assertEqual(dialog.regions[1]["roi"], DEFAULT_VIDEO_SUBTITLE_ROI)
            self.assertEqual(dialog.regions[0]["roi"], resized)
            self.assertNotEqual(second_before, dialog.regions[1]["roi"])
            dialog._confirm()
            self.assertEqual(dialog.value()[0]["roi"], resized)
        finally:
            dialog.close(); folder.cleanup()
