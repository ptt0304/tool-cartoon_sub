import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Mask, Project
from cartoon_sub.transcription.video.roi import DEFAULT_VIDEO_SUBTITLE_ROI, image_rect, normalized_to_display
from cartoon_sub.subtitle.mask_roi import DEFAULT_MASK_ROI, sync_mask_geometry
from cartoon_sub.ui.tabs.mask_style_tab import MaskStylePage
from cartoon_sub.ui.transcript_roi_dialog import MaskRoiDialog, RoiCanvas


class MaskRoiEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_roi_and_normalized_project_round_trip(self):
        page = MaskStylePage()
        project = Project("mask", "video.mp4", metadata={"width": 1920, "height": 1080})
        page.load_project(project)
        self.assertEqual(page.values()[0].roi, DEFAULT_VIDEO_SUBTITLE_ROI)
        with tempfile.TemporaryDirectory() as folder:
            project.mask = page.values()[0]
            ProjectManager().save(project, folder)
            self.assertEqual(ProjectManager().load(folder).mask.roi, DEFAULT_VIDEO_SUBTITLE_ROI)
        page.close()

    def test_shared_canvas_move_edges_corners_and_clamp(self):
        canvas = RoiCanvas(QPixmap(1000, 500), DEFAULT_VIDEO_SUBTITLE_ROI)
        moved = canvas._apply_drag(canvas.roi, "move", .2, -.4)
        self.assertGreaterEqual(moved["x_pct"], 0); self.assertGreaterEqual(moved["y_pct"], 0)
        wider = canvas._apply_drag(canvas.roi, "right", .1, 0)
        taller = canvas._apply_drag(canvas.roi, "bottom", 0, .1)
        corner = canvas._apply_drag(canvas.roi, "top_left", -.2, -.2)
        self.assertGreater(wider["w_pct"], canvas.roi["w_pct"])
        self.assertGreater(taller["h_pct"], canvas.roi["h_pct"])
        self.assertLessEqual(corner["x_pct"] + corner["w_pct"], 1)
        self.assertLessEqual(corner["y_pct"] + corner["h_pct"], 1)
        canvas.close()

    def test_cancel_discards_dialog_edits_and_ok_value_is_normalized(self):
        preview = QPixmap(640, 360)
        before = dict(DEFAULT_VIDEO_SUBTITLE_ROI)
        dialog = MaskRoiDialog(preview, before)
        dialog.canvas.set_roi({"x_pct": .2, "y_pct": .6, "w_pct": .6, "h_pct": .2})
        dialog.reject()
        self.assertEqual(before, DEFAULT_VIDEO_SUBTITLE_ROI)
        accepted = MaskRoiDialog(preview, before)
        accepted.canvas.set_roi({"x_pct": .2, "y_pct": .6, "w_pct": .6, "h_pct": .2})
        self.assertEqual(accepted.value(), {"x_pct": .2, "y_pct": .6, "w_pct": .6, "h_pct": .2})
        accepted.close()

    def test_roi_selection_preserves_all_mask_types(self):
        page = MaskStylePage(); page.canvas.pixmap = QPixmap(320, 180)
        project = Project("mask", "video.mp4", metadata={"width": 320, "height": 180})
        for kind in ("solid", "gaussian", "none"):
            project.mask = Mask(True, kind, 0, 0, 0, 0)
            page.load_project(project)
            page._set_roi({"x_pct": .15, "y_pct": .65, "w_pct": .7, "h_pct": .2})
            self.assertEqual(page.values()[0].kind, kind)
            self.assertEqual(page.values()[0].roi["x_pct"], .15)
        page.close()

    def test_numeric_controls_sync_normalized_roi_without_touching_transcript_roi(self):
        page = MaskStylePage()
        project = Project("mask", "video.mp4", metadata={"width": 1920, "height": 1080},
                          video_subtitle_roi={"x_pct": .2, "y_pct": .1, "w_pct": .4, "h_pct": .2})
        page.load_project(project)
        page._set_roi({"x_pct": .2, "y_pct": .75, "w_pct": .6, "h_pct": .15})
        self.assertEqual([spin.value() for spin in page.coords], [384, 810, 1152, 162])
        page.update_mask()
        self.assertEqual(page.values()[0].roi, {"x_pct": .2, "y_pct": .75, "w_pct": .6, "h_pct": .15})
        self.assertEqual(project.video_subtitle_roi, {"x_pct": .2, "y_pct": .1, "w_pct": .4, "h_pct": .2})
        page.close()

    def test_letterbox_mapping_and_renderer_pixel_sync(self):
        roi = {"x_pct": .2, "y_pct": .75, "w_pct": .6, "h_pct": .15}
        # A 16:9 image in a 4:3 canvas has letterbox margins; mapping uses image rect.
        rectangle = image_rect(800, 600, 1920, 1080)
        x, y, width, height = normalized_to_display(roi, rectangle)
        self.assertEqual((round(x), round(y), round(width), round(height)), (160, 412, 480, 68))
        mask = Mask(True, "gaussian", 0, 0, 0, 0, roi=roi)
        sync_mask_geometry(mask, 1920, 1080)
        self.assertEqual((mask.x, mask.y, mask.width, mask.height), (384, 810, 1152, 162))

    def test_mask_default_is_independent_value_not_transcript_object(self):
        self.assertEqual(DEFAULT_MASK_ROI, DEFAULT_VIDEO_SUBTITLE_ROI)
        self.assertIsNot(DEFAULT_MASK_ROI, DEFAULT_VIDEO_SUBTITLE_ROI)


if __name__ == "__main__":
    unittest.main()

