import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber
from cartoon_sub.translation.source_modes import TranslationSourceService
from cartoon_sub.ui.tabs import transcript_tab


class TranscriptUtteranceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_audio_utterance_renders_language_status_and_prepares_timeline_snapshot(self):
        utterance = Utterance(7, 1.0, 2.0, "你好", transcript_language_status="PASS")
        project = Project(
            "audio", "video.mp4", transcription_status="completed",
            transcript_source_mode="audio", segments=[utterance],
        )
        tab = transcript_tab.build()
        try:
            transcript_tab.populate(tab, project)
            self.assertEqual(tab.table.rowCount(), 1)
            self.assertEqual(tab.table.item(0, 5).text(), "PASS")
        finally:
            tab.close()

        service = TranslationSourceService()
        snapshot = service.ensure(project, "audio")
        self.assertEqual(snapshot["rows"][0]["zh"], "你好")
        self.assertTrue(service.available(project, "audio"))
        working = service.working_project(project, "audio")
        self.assertIsInstance(working.segments[0], Utterance)

    def test_completed_audio_transcript_cache_reuses_utterances_without_a_client(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def transcribe_json(self, *_args, **_kwargs):
                self.calls += 1
                return {"segments": [{"start": 0, "end": 0.5, "zh": "你好"}]}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "audio.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                output.writeframes(b"\x01\x00" * 16000)
            first_client = Client()
            first = GeminiTranscriber(lambda: "test-key", "test-model", root / "cache", 0,
                                      lambda _key: first_client)
            first_rows = first.transcribe(audio)
            self.assertIsInstance(first_rows[0], Utterance)
            self.assertEqual(first_client.calls, 1)

            cache_client = Client()
            cached = GeminiTranscriber(lambda: (_ for _ in ()).throw(AssertionError("cache requested a key")),
                                       "test-model", root / "cache", 0, lambda _key: cache_client)
            cached_rows = cached.transcribe(audio)
            self.assertIsInstance(cached_rows[0], Utterance)
            self.assertEqual(cached_rows[0].zh, "你好")
            self.assertEqual(cache_client.calls, 0)


if __name__ == "__main__":
    unittest.main()
