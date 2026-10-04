import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton

from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import approve_review, review_complete
from cartoon_sub.subtitle.models import DisplaySegment, Project, Segment
from cartoon_sub.ui.main_window import MainWindow


def populated_project(source):
    rows = [
        Segment(1, 0.0, 2.0, "你好", vi_subtitle="Xin chào", vi_dubbing="Chào nhé",
                speaker_id="SPK_01", dubbing_status="manual", translation_source="imported_srt"),
        Segment(2, 2.0, 4.0, "再见", vi_subtitle="Tạm biệt", vi_dubbing="Tạm biệt",
                speaker_id="SPK_02"),
    ]
    rows[0].set_display_segments([
        DisplaySegment("1.1", 1, 0.0, 2.0, "Xin chào", manual=True),
    ])
    rows[0].tts_audio_path = "audio/tts/segments/one.wav"
    rows[0].tts_generation_status = "generated"
    rows[0].tts_fingerprint = "tts"
    project = Project("reset", str(source), segments=rows)
    project.speakers = {
        "SPK_01": asdict(Speaker("SPK_01", "Girl", tts_voice_id="voice-girl")),
        "SPK_02": asdict(Speaker("SPK_02", "Cat", tts_voice_id="voice-cat")),
    }
    approve_review(project)
    project.story_context["setting"] = "Approved setting"
    project.context_source_hash = "approved"
    project.context_approved_config_hash = "config"
    project.context_status = "applied"
    project.visual_context_status = "applied"
    project.context_proposal = {**project.story_context, "summary": "candidate"}
    project.context_proposal_hash = "candidate"
    project.speaker_evidence = [{"utterance_id": 1}]
    project.speaker_proposals = {"utterances": [{"utterance_id": 1, "status": "PROPOSED"}]}
    project.translation_status = "completed"
    project.translation_notes = {"1": "review"}
    project.translation_qa = {"1": {"status": "PASS"}}
    project.segmentation_cache = {"1": {"manual": True}}
    project.cache_hashes = {"transcription_raw": "raw", "translation": "translated"}
    project.chunk_states = {"translation": {"batch": {}}, "dubbing": {"batch": {}}}
    project.final_audio_status = "ready"
    project.final_audio_fingerprint = "final"
    return project


def seed_files(root):
    files = (
        "audio/source.wav", "audio/source.json", "audio/tts/segments/one.wav",
        "audio/tts/dubbed_mix.wav", "audio/tts/tts_cache.json", "audio/final_audio.wav",
        "cache/transcription/raw.json", "cache/transcription_semantic/semantic.json",
        "cache/translation/request.json", "cache/translation_qa/qa.json",
        "cache/manual_translation_qa/manual.json", "cache/dubbing/dub.json",
        "cache/dubbing_duration/duration.json", "cache/audio_timing/timing.json",
        "cache/tts/previews/preview.wav", "cache/visual_context/result.json",
        "cache/visual_context/frames/frame.jpg", "cache/speaker_fusion/fusion.json",
        "cache/unrelated/keep.bin", "subtitle/zh.srt", "subtitle/vi.srt",
        "subtitle/vi_dubbing.srt", "subtitle/segments.json", "api_key.txt",
    )
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")


class StageResetServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "source.mp4"
        source.write_bytes(b"video")
        project = populated_project(source)
        ProjectManager().save(project, root)
        seed_files(root)
        return temporary, root, project

    def test_transcript_full_reset_clears_downstream_and_raw_stt_only_when_selected(self):
        temporary, root, project = self.make_fixture()
        try:
            ProjectStageResetService(project, root).reset_transcript(clear_stt_cache=True)
            ProjectManager().save(project, root)
            loaded = ProjectManager().load(root / "project.json")
            self.assertEqual(loaded.utterances, [])
            self.assertEqual(loaded.transcription_status, "not_started")
            self.assertEqual((loaded.speakers, loaded.story_context["setting"]), ({}, ""))
            self.assertEqual(loaded.translation_status, "not_started")
            self.assertTrue(source := Path(loaded.source_video_path).is_file())
            self.assertTrue((root / "audio/source.wav").is_file())
            self.assertFalse((root / "cache/transcription/raw.json").exists())
            self.assertTrue((root / "cache/visual_context/frames/frame.jpg").is_file())
            self.assertTrue((root / "cache/unrelated/keep.bin").is_file())
            self.assertTrue((root / "api_key.txt").is_file())
        finally:
            temporary.cleanup()

        temporary, root, project = self.make_fixture()
        try:
            ProjectStageResetService(project, root).reset_transcript(clear_stt_cache=False)
            self.assertTrue((root / "cache/transcription/raw.json").is_file())
            self.assertEqual(project.cache_hashes.get("transcription_raw"), "raw")
        finally:
            temporary.cleanup()

    def test_translation_reset_preserves_chinese_speaker_context_and_clears_downstream(self):
        temporary, root, project = self.make_fixture()
        try:
            before = [(row.id, row.zh, row.start, row.end, row.speaker_id) for row in project.utterances]
            approved = dict(project.story_context)
            ProjectStageResetService(project, root).reset_translation()
            ProjectManager().save(project, root)
            loaded = ProjectManager().load(root / "project.json")
            self.assertEqual([(row.id, row.zh, row.start, row.end, row.speaker_id)
                              for row in loaded.utterances], before)
            self.assertTrue(review_complete(loaded))
            self.assertEqual(loaded.story_context, approved)
            self.assertTrue(all(not row.vi_subtitle and not row.vi_dubbing for row in loaded.utterances))
            self.assertEqual(loaded.translation_status, "not_started")
            self.assertTrue(all(row.tts_audio_path is None for row in loaded.utterances))
            self.assertFalse((root / "cache/translation/request.json").exists())
            self.assertTrue((root / "cache/transcription/raw.json").is_file())
            self.assertTrue((root / "cache/visual_context/frames/frame.jpg").is_file())
        finally:
            temporary.cleanup()

    def test_subtitle_reset_preserves_upstream_and_audio(self):
        temporary, root, project = self.make_fixture()
        try:
            before = [(row.zh, row.vi_subtitle, row.vi_dubbing, row.tts_audio_path)
                      for row in project.utterances]
            ProjectStageResetService(project, root).reset_subtitle()
            self.assertEqual([(row.zh, row.vi_subtitle, row.vi_dubbing, row.tts_audio_path)
                              for row in project.utterances], before)
            self.assertTrue(all(not row.display_segments for row in project.utterances))
            self.assertEqual(project.segmentation_cache, {})
            self.assertFalse((root / "cache/audio_timing/timing.json").exists())
            self.assertTrue((root / "audio/tts/segments/one.wav").is_file())
        finally:
            temporary.cleanup()

    def test_audio_reset_preserves_manual_text_voice_assets_and_upstream(self):
        temporary, root, project = self.make_fixture()
        try:
            before = [(row.zh, row.vi_subtitle, row.vi_dubbing, row.dubbing_status)
                      for row in project.utterances]
            voices = {key: value.get("tts_voice_id") for key, value in project.speakers.items()}
            ProjectStageResetService(project, root).reset_audio()
            self.assertEqual([(row.zh, row.vi_subtitle, row.vi_dubbing, row.dubbing_status)
                              for row in project.utterances], before)
            self.assertEqual({key: value.get("tts_voice_id") for key, value in project.speakers.items()}, voices)
            self.assertTrue(all(row.tts_generation_status == "not_generated" for row in project.utterances))
            self.assertFalse((root / "audio/tts/segments/one.wav").exists())
            self.assertFalse((root / "audio/final_audio.wav").exists())
            self.assertTrue((root / "audio/source.wav").is_file())
            self.assertTrue((root / "cache/translation/request.json").is_file())
        finally:
            temporary.cleanup()

    def test_context_reset_preserves_user_confirmed_speakers(self):
        temporary, root, project = self.make_fixture()
        try:
            speaker_ids = [(row.id, row.speaker_id) for row in project.utterances]
            review_hash = project.speaker_review_hash
            ProjectStageResetService(project, root).reset_context()
            self.assertEqual([(row.id, row.speaker_id) for row in project.utterances], speaker_ids)
            self.assertEqual(project.speaker_review_hash, review_hash)
            self.assertTrue(review_complete(project))
            self.assertEqual(project.story_context["setting"], "")
            self.assertEqual((project.speaker_evidence, project.speaker_proposals), ([], {}))
            self.assertFalse((root / "cache/visual_context/result.json").exists())
            self.assertTrue((root / "cache/visual_context/frames/frame.jpg").is_file())
        finally:
            temporary.cleanup()

    def test_buttons_exist_and_cancel_changes_nothing(self):
        window = MainWindow()
        temporary, root, project = self.make_fixture()
        try:
            window.controller.project = project
            window.controller.directory = root
            labels = [window.pages[index].reset_ai_button.text() for index in (1, 2, 3, 5)]
            self.assertEqual(labels, ["Xóa dữ liệu AI / Chạy lại"] * 4)
            before = project.to_dict()
            with patch.object(window, "_confirm_ai_stage_reset", return_value=(False, True)), \
                    patch.object(window.controller, "reset_stage") as reset:
                window.pages[1].reset_ai_button.click()
            reset.assert_not_called()
            self.assertEqual(project.to_dict(), before)
        finally:
            window.voice_sync_timer.stop()
            window.close()
            temporary.cleanup()

    def test_confirmed_reset_refreshes_ui_without_restart(self):
        window = MainWindow()
        temporary, root, project = self.make_fixture()
        try:
            window.controller.project = project
            window.controller.directory = root
            window.refresh()
            with patch.object(window, "_confirm_ai_stage_reset", return_value=(True, True)), \
                    patch.object(window, "refresh", wraps=window.refresh) as refresh:
                window.pages[3].reset_ai_button.click()
            refresh.assert_called()
            self.assertEqual(project.segmentation_cache, {})
            self.assertTrue(all(not row.display_segments for row in project.utterances))
        finally:
            window.voice_sync_timer.stop()
            window.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
