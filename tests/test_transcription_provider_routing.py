import io
import os
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock
import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel

from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.openai_transcription_client import OpenAITranscriptionClient
from cartoon_sub.ai.openrouter_client import (OPENROUTER_TRANSCRIPTION_URL,
    OpenRouterKeyPool, OpenRouterTranscriptionClient)
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

    def test_openrouter_uses_dedicated_transcription_adapter(self):
        store = Mock(); pool = store.openrouter_key_pool.return_value
        settings = AISettings(transcription_provider="openrouter",
                              transcription_model="openai/whisper-large-v3")
        provider, key_provider, factory = resolve_transcription_client(settings, store)
        self.assertEqual(provider, "openrouter")
        self.assertIs(factory, OpenRouterTranscriptionClient)
        self.assertIs(key_provider(), pool)

        client = OpenRouterTranscriptionClient("secret", Mock())
        response = Mock(); response.json.return_value = {
            "segments": [{"start": 0, "end": .5, "text": "你好"}]}
        client.client.post.return_value = response
        payload = client.transcribe_json(wav_bytes(), "ignored", {}, "openai/whisper-large-v3")
        self.assertEqual(payload["segments"][0]["zh"], "你好")
        call = client.client.post.call_args
        self.assertEqual(call.args[0], OPENROUTER_TRANSCRIPTION_URL)
        self.assertEqual(call.kwargs["json"]["model"], "openai/whisper-large-v3")
        self.assertIn("input_audio", call.kwargs["json"])

    def test_openrouter_transcription_uses_shared_pool_failover(self):
        denied = Mock(status_code=401, headers={})
        denied.request = httpx.Request("POST", OPENROUTER_TRANSCRIPTION_URL)
        denied.json.return_value = {"error": {"message": "invalid api key"}}
        denied.raise_for_status.side_effect = httpx.HTTPStatusError(
            "hidden", request=denied.request, response=denied)
        success = Mock(status_code=200, headers={})
        success.raise_for_status.return_value = None
        success.json.return_value = {"segments": [{"start": 0, "end": .5, "text": "你好"}]}
        http = Mock(); http.post.side_effect = [denied, success]
        pool = OpenRouterKeyPool(["A", "B"])
        payload = OpenRouterTranscriptionClient(pool, http).transcribe_json(
            wav_bytes(), "ignored", {}, "openai/whisper-large-v3")
        self.assertEqual(payload["segments"][0]["zh"], "你好")
        self.assertEqual(http.post.call_count, 2)
        self.assertEqual(pool.state("A"), "AUTH_INVALID")
        self.assertEqual(http.post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer B")

    def test_openrouter_preserves_optional_response_evidence(self):
        response = Mock(); response.raise_for_status.return_value = None
        response.json.return_value = {
            "language": "zh",
            "segments": [{
                "start": 0, "end": .5, "text": "你好", "speaker_id": "spk_3",
                "speaker_confidence": .82, "confidence": .94,
                "words": [{"word": "你", "start": 0, "end": .2, "probability": .9}],
            }],
        }
        http = Mock(); http.post.return_value = response
        payload = OpenRouterTranscriptionClient("A", http).transcribe_json(
            wav_bytes(), "ignored", {}, "provider/dedicated-stt")
        self.assertEqual(payload["segments"][0]["speaker_id"], "SPK_03")
        self.assertEqual(payload["segments"][0]["speaker_confidence"], .82)
        self.assertEqual(payload["segments"][0]["transcript_confidence"], .94)
        self.assertEqual(payload["words"][0]["word"], "你")
        self.assertEqual(payload["language"], "zh")
        self.assertIn("SPEAKER_HINTS", payload["transcription_capabilities"])
        self.assertIn("WORD_TIMESTAMPS", payload["transcription_capabilities"])

    def test_openrouter_normalizes_legacy_schema_types_for_structured_output(self):
        from cartoon_sub.ai.openrouter_client import OpenRouterClient
        gateway = OpenRouterClient("secret", models=[{
            "id": "vendor/model", "supported_parameters": ["structured_outputs"],
        }])
        body = gateway._json_body("system", "prompt", {
            "type": "OBJECT", "properties": {
                "rows": {"type": "ARRAY", "items": {"type": "STRING"}},
            }, "required": ["rows"],
        }, "vendor/model")
        schema = body["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["rows"]["type"], "array")
        self.assertEqual(schema["properties"]["rows"]["items"]["type"], "string")
        gateway.close()

    def test_openrouter_parses_fenced_and_content_part_json(self):
        from cartoon_sub.ai.openrouter_client import _parse_json_object_content
        self.assertEqual(_parse_json_object_content("```json\n{\"ok\": true}\n```"), {"ok": True})
        self.assertEqual(_parse_json_object_content([
            {"type": "text", "text": "Result:\n"},
            {"type": "text", "text": "{\"ok\": true}"},
        ]), {"ok": True})

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
