import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import SettingsStore
from cartoon_sub.subtitle.models import Project, Segment


def preview_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\0\0" * 800)
    return output.getvalue()


class FakeClient:
    def __init__(self, settings, **kwargs):
        self.settings = settings

    def health(self):
        return {"status": "READY", "voices": 1}

    def list_ready_voices(self):
        return [{"voice_id": "ready", "display_name": "Ready Voice", "status": "READY", "engine": "vieneu"}]

    def voice_library(self):
        return {"revision": "rev-ready", "voices": self.list_ready_voices()}

    def preview_voice(self, voice_id):
        return preview_wav()

    def close(self):
        pass


class LocalTTSControllerTests(unittest.TestCase):
    def project(self):
        row = Segment(
            1, 0.0, 2.0, speaker_id="SPK_01", speaker_name="Một",
            tts_generation_status="generated", tts_fingerprint="old",
            tts_segment_id="utt_000001_old",
        )
        return Project(
            "ui", "source.mp4", metadata={"duration": 5.0}, segments=[row],
            speakers={"SPK_01": {
                "id": "SPK_01", "name": "Một", "voice_notes": "",
                "tts_voice_id": "gone", "tts_speed": 1.0,
            }},
        )

    def test_controller_persists_mapping_and_marks_audio_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = Controller(SettingsStore(folder=root / "settings", vault=Mock()))
            controller.project = self.project()
            controller.directory = root
            controller.manager.save(controller.project, root)
            controller.update_speaker_tts_voice("SPK_01", "ready", 1.15)
            loaded = controller.manager.load(root)
            self.assertEqual(loaded.speakers["SPK_01"]["tts_voice_id"], "ready")
            self.assertEqual(loaded.speakers["SPK_01"]["tts_speed"], 1.15)
            self.assertEqual(loaded.utterances[0].tts_generation_status, "stale")

    def test_connection_settings_and_preview_cache_are_controller_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SettingsStore(folder=root / "settings", vault=Mock())
            controller = Controller(store)
            controller.project = self.project()
            controller.directory = root
            controller.manager.save(controller.project, root)
            with patch("cartoon_sub.app.controller.LocalTTSClient", FakeClient):
                result = controller.test_local_tts_connection("https://tunnel.example/")
                preview = controller.preview_local_tts_voice("ready")
            self.assertEqual(result["health"]["status"], "READY")
            self.assertEqual(result["revision"], "rev-ready")
            self.assertEqual(result["voices"][0]["voice_id"], "ready")
            self.assertEqual(store.load_local_tts().base_url, "https://tunnel.example")
            self.assertTrue(preview.is_file())
            self.assertEqual(preview.parent, root / "cache" / "tts" / "previews")

    def test_voice_library_poll_uses_short_request_and_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(SettingsStore(folder=Path(directory) / "settings", vault=Mock()))
            with patch("cartoon_sub.app.controller.LocalTTSClient", wraps=FakeClient) as client_class:
                result = controller.fetch_local_tts_voice_library(timeout_seconds=2)
            self.assertEqual(result["revision"], "rev-ready")
            self.assertEqual(result["all_voice_ids"], ["ready"])
            self.assertEqual(client_class.call_args.kwargs["request_timeout_seconds"], 2)

    def test_deleted_voice_mapping_falls_back_to_first_api_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(SettingsStore(folder=Path(directory) / "settings", vault=Mock()))
            controller.project = self.project()
            controller.directory = Path(directory)
            controller.manager.save(controller.project, directory)
            changed = controller.fallback_deleted_tts_voice_mappings(
                [{"voice_id": "ready", "status": "READY"}], ["ready"],
            )
            self.assertEqual(changed, ["SPK_01"])
            self.assertEqual(controller.project.speakers["SPK_01"]["tts_voice_id"], "ready")


if __name__ == "__main__":
    unittest.main()
