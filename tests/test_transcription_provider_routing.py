import io
import os
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel

from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.openai_transcription_client import OpenAITranscriptionClient
from cartoon_sub.app.settings import AISettings
from cartoon_sub.transcription.pipeline import resolve_transcription_client
from cartoon_sub.ui.tabs.transcript_tab import build


def wav_bytes(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000 * seconds)
    return stream.getvalue()


class TranscriptionProviderRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transcript_tab_uses_generic_button_and_help(self):
        tab = build()
        self.assertEqual(tab.transcribe_button.text(), "Run Chinese Transcript")
        labels = " ".join(label.text() for label in tab.findChildren(QLabel))
        self.assertNotIn("Gemini", tab.transcribe_button.text())
        self.assertNotIn("Gemini transcription", labels)
        self.assertNotIn("audio lên Google", labels)
        self.assertIn("provider/model transcription", labels)
        tab.close()

    def test_gemini_and_openai_resolve_from_saved_selection(self):
        store = Mock()
        gemini = AISettings(transcription_provider="gemini", transcription_model="gemini-3.8-flash")
        provider, _, factory = resolve_transcription_client(gemini, store)
        self.assertEqual(provider, "gemini"); self.assertIs(factory, GeminiClient)

        openai = AISettings(provider_filter="all", transcription_provider="openai",
                            transcription_model="gpt-4o-transcribe", api_key_file="keys.txt")
        store.get_api_key.return_value = "secret"
        provider, key_provider, factory = resolve_transcription_client(openai, store)
        self.assertEqual(provider, "openai"); self.assertIs(factory, OpenAITranscriptionClient)
        self.assertEqual(key_provider(), "secret")
        store.get_api_key.assert_called_once_with("openai", openai)
        store.get_gemini_keys.assert_not_called()

    def test_missing_adapter_has_precise_message(self):
        settings = AISettings(transcription_provider="groq", transcription_model="whisper-large-v3")
        with self.assertRaisesRegex(ValueError, "chưa được Cartoon_Sub hỗ trợ"):
            resolve_transcription_client(settings, Mock())

    def test_openai_adapter_returns_pipeline_schema(self):
        client = OpenAITranscriptionClient("secret")
        response = Mock()
        response.json.return_value = {"segments": [{"start": 0, "end": .5, "text": "你好"}]}
        client.client = Mock(); client.client.post.return_value = response
        payload = client.transcribe_json(wav_bytes(), "ignored", {}, "gpt-4o-transcribe")
        self.assertEqual(payload["segments"][0]["zh"], "你好")
        self.assertEqual(payload["segments"][0]["speaker_id"], "SPK_UNKNOWN")
        self.assertEqual(client.client.post.call_args.kwargs["data"]["model"], "gpt-4o-transcribe")


if __name__ == "__main__":
    unittest.main()
