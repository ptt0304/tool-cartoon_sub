import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.transcription.mode import TranscriptSourceMode, normalize_mode, normalize_roi
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.transcription.video.visual_detector import COARSE_FPS, REFINE_FPS
from cartoon_sub.transcription.video.pipeline import VideoTranscriptNotImplemented
from cartoon_sub.ui.tabs import transcript_tab

ROI = {"x_pct": 0.1, "y_pct": 0.8, "w_pct": 0.8, "h_pct": 0.15}


class ProjectFieldTests(unittest.TestCase):
    def test_defaults_and_old_project_payload_load(self):
        data = Project("p", "v.mp4").to_dict()
        data.pop("transcript_source_mode"); data.pop("video_subtitle_roi")
        project = Project.from_dict(data)
        self.assertEqual(project.transcript_source_mode, "audio")
        self.assertEqual(project.video_subtitle_roi, {})

    def test_round_trip_and_invalid_values(self):
        project = Project("p", "v.mp4", transcript_source_mode="video_subtitle", video_subtitle_roi=dict(ROI))
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.transcript_source_mode, "video_subtitle")
        legacy = project.to_dict(); legacy["transcript_source_mode"] = "video"
        self.assertEqual(Project.from_dict(legacy).transcript_source_mode, "video_subtitle")
        self.assertEqual(loaded.video_subtitle_roi, ROI)
        for bad in ("speech", None, ""):
            data = project.to_dict(); data["transcript_source_mode"] = bad
            with self.assertRaises(ValueError):
                Project.from_dict(data)
        for bad in ({"x_pct": 0.5}, {**ROI, "w_pct": 0.8, "x_pct": 0.5}, {**ROI, "h_pct": 0},
                    {**ROI, "x_pct": -0.1}, {**ROI, "y_pct": float("nan")}, "roi"):
            data = project.to_dict(); data["video_subtitle_roi"] = bad
            with self.assertRaises(ValueError):
                Project.from_dict(data)

    def test_helpers(self):
        self.assertEqual(normalize_mode("audio"), "audio")
        self.assertEqual(normalize_mode("import_srt"), "import_srt")
        self.assertEqual(normalize_roi(None), {})
        self.assertEqual(normalize_roi({"x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1})["w_pct"], 1.0)

    def test_project_file_round_trip_keeps_roi_outside_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project("p", "v.mp4", transcript_source_mode="video_subtitle", video_subtitle_roi=dict(ROI))
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(Path(folder))
            self.assertEqual(loaded.video_subtitle_roi, ROI)
            self.assertFalse(ProjectPaths(folder).transcript_video_cache_dir.exists())
            self.assertEqual(ProjectPaths(folder).transcript_video_cache_dir,
                             ProjectPaths(folder).root / "cache" / "transcript" / "video")


class ProvenanceAndSettingsTests(unittest.TestCase):
    def test_video_subtitle_provenance_and_existing_values(self):
        for value in ("VIDEO_SUBTITLE", "ESTIMATED", "WORD_TIMESTAMP", "LEGACY"):
            row = Utterance(1, 0.0, 1.0, "你好", transcript_timing_provenance=value)
            self.assertEqual(Utterance.from_dict(row.to_dict()).transcript_timing_provenance, value)
        with self.assertRaises(ValueError):
            Utterance(1, 0.0, 1.0, "你好", transcript_timing_provenance="NOPE")

    def test_transcript_video_model_key_is_independent(self):
        selection = {"provider": "openrouter", "model_id": "vendor/vision-x"}
        settings = AISettings(tab_model_overrides={"transcript_video": selection}).validate()
        self.assertEqual(settings.tab_selection("transcript_video").model_id, "vendor/vision-x")
        self.assertNotEqual(settings.tab_selection("transcript").model_id, "vendor/vision-x")
        self.assertNotEqual(settings.tab_selection("transcript_stt").model_id, "vendor/vision-x")

    def test_transcript_video_has_independent_production_default(self):
        settings = AISettings(default_ai_model="vendor/text-default").validate()
        self.assertEqual(settings.tab_selection("transcript_video").model_id, "google/gemini-2.5-flash")
        self.assertEqual(settings.tab_selection("transcript_video").provider, "openrouter")
        settings.tab_model_overrides["transcript_video"] = {
            "provider": "openrouter", "model_id": "vendor/video-x"}
        self.assertEqual(settings.validate().tab_selection("transcript_video").model_id, "vendor/video-x")


class RoutingTests(unittest.TestCase):
    def controller(self, mode, roi=None, status="not_started"):
        controller = Controller(settings_store=SimpleNamespace())
        controller.project = Project("p", "v.mp4", transcript_source_mode=mode,
                                     video_subtitle_roi=roi or {}, transcription_status=status)
        controller.directory = Path(".")
        calls = []
        controller.pipeline = SimpleNamespace(run=lambda *a, **k: calls.append("audio") or "audio-result")
        return controller, calls

    def test_audio_mode_uses_unchanged_audio_pipeline(self):
        controller, calls = self.controller("audio")
        self.assertEqual(controller.run_transcript(), "audio-result")
        self.assertEqual(calls, ["audio"])

    def test_video_mode_never_touches_audio_pipeline(self):
        controller, calls = self.controller("video_subtitle", dict(ROI))
        controller.video_pipeline = SimpleNamespace(run=lambda *a, **k: "video-result")
        self.assertEqual(controller.run_transcript(), "video-result")
        self.assertEqual(calls, [])

    def test_video_mode_requires_roi_and_respects_imported_srt(self):
        controller, calls = self.controller("video_subtitle")
        with self.assertRaisesRegex(ValueError, "vùng phụ đề"):
            controller.run_transcript()
        controller, _ = self.controller("video_subtitle", dict(ROI), "imported")
        with self.assertRaisesRegex(ValueError, "import SRT"):
            controller.run_transcript()

    def test_import_mode_never_starts_an_ai_pipeline(self):
        controller, calls = self.controller("import_srt")
        with self.assertRaisesRegex(ValueError, "Import Chinese SRT"):
            controller.run_transcript()
        self.assertEqual(calls, [])

    def test_set_mode_persists_to_project_file(self):
        with tempfile.TemporaryDirectory() as folder:
            controller, _ = self.controller("audio")
            controller.directory = Path(folder)
            ProjectManager().save(controller.project, folder)
            controller.set_transcript_source_mode("video_subtitle")
            self.assertEqual(ProjectManager().load(Path(folder)).transcript_source_mode, "video_subtitle")
            with self.assertRaises(ValueError):
                controller.set_transcript_source_mode("bogus")
            self.assertEqual(controller.project.transcript_source_mode, "video_subtitle")


class TranscriptTabModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_three_modes_and_mode_specific_controls(self):
        tab = transcript_tab.build()
        try:
            self.assertEqual([tab.mode_combo.itemData(i) for i in range(tab.mode_combo.count())],
                             ["audio", "video_subtitle", "import_srt"])
            self.assertEqual(tab.mode_combo.itemText(0), "Lấy transcript từ Audio")
            self.assertEqual(tab.mode_combo.itemText(1), "Lấy transcript từ Video")
            self.assertEqual(tab.mode_combo.itemText(2), "Import Chinese SRT")
            self.assertEqual(tab.transcribe_button.text(), "Run Chinese Transcript")
            self.assertTrue(tab.import_button.isHidden())
            transcript_tab.set_mode(tab, "video_subtitle")
            self.assertTrue(tab.stt_model.isHidden())
            self.assertFalse(tab.video_mode_hint.isHidden())
            self.assertFalse(tab.roi_button.isHidden())
            self.assertFalse(tab.video_model.isHidden())
            self.assertFalse(tab.video_qaqc_button.isHidden())
            self.assertFalse(tab.video_qaqc_undo_button.isHidden())
            self.assertTrue(tab.import_button.isHidden())
            self.assertEqual(tab.transcribe_button.text(), "Run Video Transcript")
            transcript_tab.set_mode(tab, "import_srt")
            self.assertFalse(tab.import_button.isHidden())
            self.assertFalse(tab.import_hint.isHidden())
            self.assertTrue(tab.transcribe_button.isHidden())
            self.assertTrue(tab.stt_model.isHidden())
            self.assertTrue(tab.video_model.isHidden())
            self.assertTrue(tab.video_qaqc_button.isHidden())
            transcript_tab.set_mode(tab, "audio")
            self.assertFalse(tab.stt_model.isHidden())
            self.assertTrue(tab.video_mode_hint.isHidden())
            self.assertTrue(tab.roi_button.isHidden())
            self.assertTrue(tab.video_qaqc_button.isHidden())
            self.assertTrue(tab.import_button.isHidden())
            self.assertEqual(tab.transcribe_button.text(), "Run Chinese Transcript")
        finally:
            tab.close()

    def test_video_status_uses_production_detector_values(self):
        status = transcript_tab.video_status_text({"candidate_events": 2})
        self.assertIn(f"{COARSE_FPS:.1f} fps", status)
        self.assertIn(f"{REFINE_FPS:.1f} fps", status)


class TranscriptCacheIsolationTests(unittest.TestCase):
    def test_audio_and_video_cache_clear_are_isolated_and_keep_regions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = Project("p", "v.mp4", transcript_source_mode="video_subtitle", video_subtitle_roi=dict(ROI),
                              video_subtitle_regions=[{"id": "region_001", "start_time": 0.0,
                                  "end_time": 1.0, "roi": dict(ROI)}])
            audio = root / "cache" / "transcription"; semantic = root / "cache" / "transcription_semantic"
            video = root / "cache" / "transcript" / "video"
            for directory, name in ((audio, "audio.json"), (semantic, "semantic.json"), (video, "visual_events.json")):
                directory.mkdir(parents=True, exist_ok=True); (directory / name).write_text("{}", encoding="utf-8")
            service = ProjectStageResetService(project, root)
            service.clear_audio_transcript_cache()
            self.assertFalse(audio.exists()); self.assertFalse(semantic.exists())
            self.assertTrue((video / "visual_events.json").exists())
            self.assertEqual(project.video_subtitle_regions[0]["id"], "region_001")
            audio.mkdir(parents=True); (audio / "audio.json").write_text("{}", encoding="utf-8")
            service.clear_video_transcript_cache()
            self.assertFalse(video.exists())
            self.assertTrue((audio / "audio.json").exists())
            self.assertEqual(project.video_subtitle_regions[0]["roi"], ROI)
            self.assertNotIn("video_transcript_qaqc", project.selected_models)

    def test_video_model_invalidation_keeps_detector_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = Project("p", "v.mp4", video_subtitle_regions=[])
            video = root / "cache" / "transcript" / "video"
            ocr = video / "ocr"
            ocr.mkdir(parents=True)
            (video / "visual_events.json").write_text("{}", encoding="utf-8")
            (video / "ocr_results.json").write_text("{}", encoding="utf-8")
            (ocr / "old.json").write_text("{}", encoding="utf-8")
            ProjectStageResetService(project, root).invalidate_video_ocr_cache()
            self.assertTrue((video / "visual_events.json").exists())
            self.assertFalse((video / "ocr_results.json").exists())
            self.assertFalse(ocr.exists())


if __name__ == "__main__":
    unittest.main()
