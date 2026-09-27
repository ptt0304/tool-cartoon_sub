import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pysubs2
from PySide6.QtWidgets import QApplication

from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.export_service import export_current_srt
from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.renderer import save_ass
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService, subtitle_source_warning
from cartoon_sub.translation.artifacts import save_translation_artifacts
from cartoon_sub.tts.cache_identity import compute_tts_signature
from cartoon_sub.ui.tabs.subtitle_tab import SubtitlePage


FULL = "Đây là bản dịch đầy đủ."
SHORT = "Đây là bản ngắn."


def project(source="vi_subtitle"):
    row = Utterance(1, 0, 3, "这是译文", vi_subtitle=FULL, vi_dubbing=SHORT, speaker_id="SPK_01")
    return Project("source", "missing.mp4", metadata={"width": 320, "height": 180, "duration": 3},
                   segments=[row], subtitle_text_source=source)


class SubtitleTextSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_ui_and_project_roundtrip(self):
        old = Project.from_dict({"schema_version": 3, "name": "old", "source_video_path": "missing.mp4"})
        self.assertEqual(old.subtitle_text_source, "vi_subtitle")
        page = SubtitlePage();page.load_project(old)
        self.assertEqual([page.text_source.itemText(i) for i in range(page.text_source.count())],
                         ["VI Subtitle", "VI Dubbing"])
        self.assertEqual(page.text_source.currentData(), "vi_subtitle")
        page.close()
        with tempfile.TemporaryDirectory() as folder:
            chosen = project("vi_dubbing")
            ProjectManager().save(chosen, folder)
            self.assertEqual(ProjectManager().load(folder).subtitle_text_source, "vi_dubbing")

    def test_modes_switch_and_manual_stale_protection(self):
        service = SubtitleSegmentationService()
        chosen = project()
        service.sync_utterance(chosen, chosen.utterances[0])
        self.assertEqual(chosen.utterances[0].display_segments[0].vi_text, FULL)
        chosen.subtitle_text_source = "vi_dubbing"
        chosen.segmentation_cache["1"]["source_type"] = "vi_subtitle"
        self.assertEqual(service.sync_stale(chosen), [1])
        self.assertEqual(chosen.utterances[0].display_segments[0].vi_text, SHORT)

        chosen.utterances[0].set_display_segments([
            DisplaySegment("1.1", 1, 0, 1.5, "Đây là", manual=True),
            DisplaySegment("1.2", 1, 1.5, 3, "bản ngắn.", manual=True),
        ])
        chosen.segmentation_cache["1"] = {"manual": True, "source_type": "vi_dubbing"}
        chosen.utterances[0].vi_dubbing = "Đây là lời thuyết minh mới."
        service.sync_stale(chosen)
        self.assertEqual([(row.start, row.end) for row in chosen.utterances[0].display_segments], [(0, 1.5), (1.5, 3)])
        self.assertEqual(" ".join(row.vi_text for row in chosen.utterances[0].display_segments),
                         "Đây là lời thuyết minh mới.")

    def test_controller_switch_resets_segments_without_ai(self):
        chosen = project()
        chosen.utterances[0].set_display_segments([
            DisplaySegment("1.1", 1, 0, 1.5, "Đây là", manual=True),
            DisplaySegment("1.2", 1, 1.5, 3, "bản dịch đầy đủ.", manual=True),
        ])
        controller = Controller();controller.project = chosen;controller.save = Mock()
        semantic = Mock();controller.segmentation_service.semantic_service = semantic
        controller.update_subtitle_text_source("vi_dubbing")
        self.assertEqual(chosen.subtitle_text_source, "vi_dubbing")
        self.assertEqual([(row.start, row.end, row.vi_text) for row in chosen.utterances[0].display_segments],
                         [(0, 3, SHORT)])
        semantic.split.assert_not_called()

    def test_selected_source_renders_and_canonical_translate_stays_subtitle(self):
        chosen = project("vi_dubbing")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ass = pysubs2.load(str(save_ass(chosen, root / "render.ass")))
            self.assertEqual([" ".join(event.plaintext.replace(r"\n", " ").split()) for event in ass], [SHORT])
            subtitle_export = pysubs2.load(str(export_current_srt(chosen, root, "subtitle")))
            self.assertEqual([event.plaintext for event in subtitle_export], [SHORT])
            save_translation_artifacts(chosen, root)
            canonical = pysubs2.load(str(root / "exports" / "translate" / "vi_subtitle.srt"))
            self.assertEqual([event.plaintext for event in canonical], [FULL])

    def test_unselected_field_is_independent_and_tts_always_uses_dubbing(self):
        chosen = project("vi_subtitle")
        service = SubtitleSegmentationService();service.sync_utterance(chosen, chosen.utterances[0])
        before = [row.vi_text for row in chosen.utterances[0].display_segments]
        chosen.utterances[0].vi_dubbing = "Dubbing đã tối ưu."
        self.assertEqual(service.sync_stale(chosen), [])
        self.assertEqual([row.vi_text for row in chosen.utterances[0].display_segments], before)
        speaker = Speaker("SPK_01", "Một", tts_voice_id="voice")
        signature = compute_tts_signature(chosen.utterances[0], speaker, "http://127.0.0.1:8765")
        chosen.subtitle_text_source = "vi_dubbing"
        self.assertEqual(signature, compute_tts_signature(chosen.utterances[0], speaker, "http://127.0.0.1:8765"))

    def test_empty_dubbing_falls_back_with_warning(self):
        chosen = project("vi_dubbing");chosen.utterances[0].vi_dubbing = ""
        service = SubtitleSegmentationService();service.sync_utterance(chosen, chosen.utterances[0])
        self.assertEqual(chosen.utterances[0].display_segments[0].vi_text, FULL)
        self.assertIn("tạm dùng VI Subtitle", subtitle_source_warning(chosen, chosen.utterances[0]))


if __name__ == "__main__":
    unittest.main()
