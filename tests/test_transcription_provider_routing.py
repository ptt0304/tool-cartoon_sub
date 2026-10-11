import io
import os
import unittest
import wave
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel
from cartoon_sub.ai.openrouter_client import OPENROUTER_TRANSCRIPTION_URL, OpenRouterKeyPool, OpenRouterTranscriptionClient
from cartoon_sub.app.settings import AISettings
from cartoon_sub.transcription.pipeline import resolve_transcription_client
from cartoon_sub.ui.tabs.transcript_tab import build

def wav_bytes(seconds=1):
    stream=io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(b"\0\0" * 16000 * seconds)
    return stream.getvalue()

class TranscriptionProviderRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def test_transcript_tab_uses_generic_button_and_help(self):
        tab=build(); labels=" ".join(label.text() for label in tab.findChildren(QLabel))
        self.assertEqual(tab.transcribe_button.text(), "Run Chinese Transcript")
        self.assertNotIn("Gemini", labels); tab.close()
    def test_legacy_selection_normalizes_to_openrouter(self):
        store=Mock(); pool=store.openrouter_key_pool.return_value; store.openrouter_catalog_cache_for.return_value={"models":[{"id":"google/gemini-3.5-transcribe"}]}
        settings=AISettings(transcription_provider="gemini", transcription_model="gemini-3.5-transcribe").validate()
        provider, key_provider, factory=resolve_transcription_client(settings, store)
        self.assertEqual(provider,"openrouter"); self.assertIs(factory,OpenRouterTranscriptionClient); self.assertIs(key_provider(),pool)
    def test_openrouter_uses_dedicated_transcription_adapter(self):
        store=Mock(); pool=store.openrouter_key_pool.return_value; store.openrouter_catalog_cache_for.return_value={"models":[{"id":"openai/whisper-large-v3-turbo"}]}
        settings=AISettings(transcription_provider="openrouter", transcription_model="openai/whisper-large-v3-turbo").validate()
        provider,key_provider,factory=resolve_transcription_client(settings,store)
        self.assertEqual(provider,"openrouter"); self.assertIs(factory,OpenRouterTranscriptionClient); self.assertIs(key_provider(),pool)
        response=Mock(); response.json.return_value={"segments":[{"start":0,"end":.5,"text":"你好"}]}
        client=OpenRouterTranscriptionClient("secret",Mock()); client.client.post.return_value=response
        payload=client.transcribe_json(wav_bytes(),"ignored",{},"openai/whisper-large-v3-turbo")
        self.assertEqual(payload["segments"][0]["zh"],"你好")
        self.assertEqual(client.client.post.call_args.args[0],OPENROUTER_TRANSCRIPTION_URL)
    def test_openrouter_parses_word_timestamps(self):
        response=Mock(); response.raise_for_status.return_value=None; response.json.return_value={"language":"zh","segments":[{"start":0,"end":.5,"text":"你好","words":[{"word":"你","start":0,"end":.2}]}]}
        http=Mock(); http.post.return_value=response
        payload=OpenRouterTranscriptionClient("A",http).transcribe_json(wav_bytes(),"ignored",{},"openai/whisper-large-v3-turbo")
        self.assertEqual(payload["words"][0]["word"],"你")

if __name__ == "__main__": unittest.main()

