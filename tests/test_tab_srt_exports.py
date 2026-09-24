import hashlib
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QGroupBox

from cartoon_sub.app.controller import Controller
from cartoon_sub.subtitle.export_service import export_current_srt, next_export_path
from cartoon_sub.subtitle.models import DisplaySegment, Project, Segment
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.ui.tabs.export_tab import build as build_export_tab
from cartoon_sub.ui.tabs.subtitle_tab import build as build_subtitle_tab
from cartoon_sub.ui.tabs.transcript_tab import build as build_transcript_tab
from cartoon_sub.ui.tabs.translate_tab import build as build_translate_tab


def sample_project():
    first = Segment(1, 1.0, 3.0, "你好世界", vi="Xin chào thế giới")
    first.set_display_segments([
        DisplaySegment("1.1", 1, 1.0, 2.0, "Xin chào", segmentation_reason="manual", manual=True),
        DisplaySegment("1.2", 1, 2.0, 3.0, "thế giới", segmentation_reason="manual", manual=True),
    ])
    return Project("export", "missing.mp4", segments=[first])


class TabSrtExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transcript_exports_latest_unicode_without_overwrite(self):
        project = sample_project()
        with tempfile.TemporaryDirectory() as directory:
            first = export_current_srt(project, directory, "transcript")
            original = first.read_bytes()
            project.utterances[0].zh = "新的中文"
            second = export_current_srt(project, directory, "transcript")
            self.assertEqual(first.name, "transcript_0.srt")
            self.assertEqual(second.name, "transcript_1.srt")
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(import_srt(second)[0].zh, "新的中文")

    def test_translate_uses_latest_canonical_text_not_display_or_cache(self):
        project = sample_project()
        with tempfile.TemporaryDirectory() as directory:
            first = export_current_srt(project, directory, "translate")
            project.utterances[0].vi_subtitle = "Chào bạn mới"
            second = export_current_srt(project, directory, "translate")
            self.assertEqual(first.name, "translate_0.srt")
            self.assertEqual(second.name, "translate_1.srt")
            self.assertEqual(import_srt(second)[0].zh, "Chào bạn mới")

    def test_subtitle_controller_syncs_stale_display_segments(self):
        project = sample_project()
        project.utterances[0].vi_subtitle = "Bản phụ đề mới nhất"
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller()
            controller.accept((project, Path(directory)))
            first = controller.export_subtitle_srt()
            cues = import_srt(first)
            self.assertEqual(first.name, "subtitle_0.srt")
            self.assertEqual(" ".join(cue.zh for cue in cues), "Bản phụ đề mới nhất")
            self.assertNotEqual([cue.zh for cue in cues], ["Xin chào", "thế giới"])

    def test_next_path_uses_filesystem_max_and_survives_new_service_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in (0, 1, 5):
                (root / f"subtitle_{index}.srt").write_text(str(index), encoding="utf-8")
            self.assertEqual(next_export_path(root, "subtitle").name, "subtitle_6.srt")
            (root / "subtitle_6.srt").write_text("6", encoding="utf-8")
            self.assertEqual(next_export_path(Path(directory), "subtitle").name, "subtitle_7.srt")

    def test_previous_export_hash_is_unchanged(self):
        project = sample_project()
        with tempfile.TemporaryDirectory() as directory:
            first = export_current_srt(project, directory, "translate")
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            project.utterances[0].vi_subtitle = "Nội dung thứ hai"
            export_current_srt(project, directory, "translate")
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), digest)

    def test_buttons_live_in_responsible_tabs_and_not_export_tab(self):
        transcript = build_transcript_tab()
        translate = build_translate_tab()
        subtitle = build_subtitle_tab()
        export = build_export_tab()
        self.assertEqual(transcript.export_transcript_button.text(), "Export Transcript SRT")
        self.assertEqual(translate.export_translate_button.text(), "Export Translate SRT")
        self.assertEqual(subtitle.export_subtitle_button.text(), "Export Subtitle SRT")
        self.assertFalse(hasattr(export, "export_button"))
        self.assertNotIn("SUBTITLE", [group.title() for group in export.findChildren(QGroupBox)])


if __name__ == "__main__":
    unittest.main()
