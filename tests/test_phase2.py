import io
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from cartoon_sub.app.settings import AISettings, SettingsStore
from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError, safe_error
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber, validate_response
from cartoon_sub.transcription.pipeline import TranscriptionPipeline


def audio_file(path, seconds=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        for i in range(seconds):
            audio.writeframes((i + 1).to_bytes(2, "little") * 16000)


def response(text="你好"):
    return {"segments": [{"id": 1, "start": 0, "end": 0.5, "zh": text}]}


class Phase2Tests(unittest.TestCase):
    def test_stable_pipeline_splits_77_second_audio_at_60_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio-77s.wav"
            audio_file(path, 77)
            client = Mock()
            client.transcribe_json.side_effect = [
                {"segments": [{"start": 1.0, "end": 59.5, "zh": "稳"}]},
                {"segments": [{"start": 1.0, "end": 16.5, "zh": "定"}]},
            ]
            transcriber = GeminiTranscriber(lambda: "key", "model", Path(directory) / "cache", 0,
                                             lambda _: client)
            output = transcriber.transcribe(path)
            self.assertEqual(client.transcribe_json.call_count, 2)
            self.assertEqual([(row.start, row.end) for row in output], [(1.0, 59.5), (61.0, 76.5)])

    def test_transcript_normalizes_labels_explicit_time_formats_and_extra_fields(self):
        payload = {"segments": [
            {"id": 0, "start": "00:01.250", "end": "2.5", "zh": "你好", "vi": ""},
            {"id": 9, "start": "00:00:03,000", "end": 4, "zh": "再见"}]}
        segments = validate_response(payload, 5)
        self.assertEqual([s.id for s in segments], [1, 2])
        self.assertEqual([(s.start, s.end) for s in segments], [(1.25, 2.5), (3, 4)])
        self.assertEqual([s.zh for s in segments], ["你好", "再见"])

    def test_validation_failure_retains_diagnostic_and_retries_with_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio_file(audio)
            client = Mock()
            bad = {"segments": [{"id": 1, "start": 0, "end": 8, "zh": "你好"}]}
            client.transcribe_json.return_value = bad
            cache = Path(directory) / "cache"
            transcriber = GeminiTranscriber(lambda: "key", "model", cache, 0, lambda key: client)
            with self.assertRaises(GeminiError) as caught:
                transcriber.transcribe(audio)
            self.assertIn("end=8.000s", str(caught.exception))
            state = json.loads(next(cache.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(json.loads(state["raw_response"]), bad)
            self.assertEqual(state["status"], "failed")
            transcriber.retry_count = 1
            client.transcribe_json.side_effect = [bad, response()]
            cancel = Mock()
            cancel.is_set.return_value = False
            cancel.wait.return_value = False
            self.assertEqual(len(transcriber.transcribe(audio, cancel=cancel)), 1)
            prompt = client.transcribe_json.call_args.args[1]
            self.assertIn("end=8.000s", prompt)
            self.assertEqual(json.loads(next(cache.glob("*.json")).read_text())["status"], "completed")

    def test_404_guidance_and_legacy_metadata_does_not_claim_inference_access(self):
        from types import SimpleNamespace
        error = safe_error(SimpleNamespace(code=404))
        self.assertIn("Settings > AI", str(error))
        self.assertFalse(error.retryable)
        sdk = Mock()
        sdk.models.get.return_value = SimpleNamespace(supported_actions=["generateContent"])
        with patch("google.genai.Client", return_value=sdk):
            gateway = GeminiClient("fake-key")
            message = gateway.test_connection("models/gemini-2.5-flash")
            self.assertIn("metadata model thành công", message)
            self.assertIn("Chưa kiểm tra quyền generateContent", message)
            sdk.models.generate_content.assert_not_called()
            gateway.close()

    def test_new_default_preserves_existing_selected_model(self):
        self.assertEqual(AISettings().transcription_model, "gemini-3.8-flash")
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(directory, Mock())
            store.save(AISettings(transcription_model="gemini-2.5-flash"))
            self.assertEqual(store.load().transcription_model, "gemini-2.5-flash")

    def test_key_only_saved_to_vault_and_blank_keeps_key(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Mock()
            store = SettingsStore(directory, vault)
            store.save(AISettings(), "fake-secret-for-test")
            vault.set_password.assert_called_once_with(store.service, store.account, "fake-secret-for-test")
            self.assertNotIn("fake-secret", (Path(directory) / "settings.json").read_text())
            store.save(AISettings(retry_count=4), "")
            self.assertEqual(vault.set_password.call_count, 1)
            self.assertEqual(store.load().retry_count, 4)

    def test_keyring_failure_has_explicit_env_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GEMINI_API_KEY=dev-test-value\n")
            vault = Mock()
            vault.get_password.side_effect = RuntimeError("unavailable")
            vault.set_password.side_effect = RuntimeError("secret-must-not-leak")
            store = SettingsStore(directory, vault, path)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(store.get_key(), "dev-test-value")
            with self.assertRaises(RuntimeError) as caught:
                store.save(AISettings(), "fake-key")
            self.assertNotIn("secret-must-not-leak", str(caught.exception))
            self.assertFalse((Path(directory) / "settings.json").exists())

    def test_invalid_transcript_rejected(self):
        bad_values = ["not-json", {"segments": [{"id": True, "start": 0, "end": 1, "zh": "啊"}]},
                      {"segments": [{"id": 1, "start": 0, "end": 30, "zh": "啊"}]},
                      {"segments": [{"id": 1, "start": 0, "end": 1, "zh": ""}]}]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(GeminiError):
                validate_response(value, 2)
        self.assertEqual(validate_response({"segments": []}, 2), [])

    def test_chunks_resume_only_failed_and_ids_offsets(self):
        with tempfile.TemporaryDirectory() as directory, patch("cartoon_sub.transcription.gemini_transcriber.CHUNK_SECONDS", 1):
            path = Path(directory) / "audio.wav"
            audio_file(path, 3)
            client = Mock()
            client.transcribe_json.side_effect = [response(), GeminiError("temporary", True)]
            factory = Mock(return_value=client)
            transcriber = GeminiTranscriber(lambda: "fake-key", "model-a", Path(directory) / "cache", 0, factory)
            with self.assertRaises(GeminiError):
                transcriber.transcribe(path)
            self.assertEqual(client.transcribe_json.call_count, 2)
            client.transcribe_json.reset_mock(side_effect=True)
            client.transcribe_json.return_value = response()
            segments = transcriber.transcribe(path)
            self.assertEqual(client.transcribe_json.call_count, 2)
            self.assertEqual([s.id for s in segments], [1, 2, 3])
            self.assertEqual([s.start for s in segments], [0, 1, 2])
            factory.reset_mock()
            transcriber.key_provider = Mock(side_effect=AssertionError("cached run must not need key"))
            self.assertEqual(transcriber.transcribe(path), segments)
            factory.assert_not_called()

    def test_retry_transient_not_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            audio_file(path)
            client = Mock()
            client.transcribe_json.side_effect = [GeminiError("retry", True), response()]
            transcriber = GeminiTranscriber(lambda: "key", "model", Path(directory) / "cache", 1, lambda key: client)
            cancel = Mock()
            cancel.is_set.return_value = False
            cancel.wait.return_value = False
            self.assertEqual(len(transcriber.transcribe(path, cancel=cancel)), 1)
            self.assertEqual(client.transcribe_json.call_count, 2)
            cancel.wait.assert_called_once_with(2)
            transcriber.model = "different-model"
            client.transcribe_json.reset_mock(side_effect=True)
            client.transcribe_json.side_effect = GeminiError("bad key", False)
            with self.assertRaises(GeminiError):
                transcriber.transcribe(path)
            self.assertEqual(client.transcribe_json.call_count, 1)

    def test_cancel_does_not_cache_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            audio_file(path)
            cancel = Event()
            client = Mock()
            def cancelled_response(*args, **kwargs):
                cancel.set()
                return response()
            client.transcribe_json.side_effect = cancelled_response
            transcriber = GeminiTranscriber(lambda: "key", "model", Path(directory) / "cache", 0, lambda key: client)
            with self.assertRaises(CancelledError):
                transcriber.transcribe(path, cancel=cancel)
            state = json.loads(next((Path(directory) / "cache").glob("*.json")).read_text())
            self.assertNotEqual(state["status"], "completed")
            client.close.assert_called_once()

    @patch("cartoon_sub.transcription.pipeline.probe", return_value={"audio_codec": "pcm_s16le"})
    def test_pipeline_artifacts_audio_cache_and_failure_preservation(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"fake local source")
            project = Project("test", str(source))
            store = Mock()
            store.load.return_value = AISettings()
            media = Mock()
            media.extract_audio.side_effect = lambda video, output, **kw: audio_file(output)
            transcriber = Mock()
            transcriber.transcribe.return_value = [Segment(1, 0, 0.5, "你好")]
            pipeline = TranscriptionPipeline(store, media, lambda *args: transcriber)
            updated, _ = pipeline.run(project, root)
            self.assertEqual(updated.transcription_status, "completed")
            self.assertTrue((root / "subtitle" / "zh.srt").exists())
            self.assertIn("你好", (root / "subtitle" / "zh.srt").read_text(encoding="utf-8"))
            self.assertEqual(import_srt(root / "subtitle" / "zh.srt")[0].zh, "你好")
            updated.subtitle_style.font_size = 60
            pipeline.run(updated, root)
            self.assertEqual(media.extract_audio.call_count, 1)
            transcriber.transcribe.side_effect = GeminiError("failed")
            with self.assertRaises(GeminiError):
                pipeline.run(updated, root)
            persisted = ProjectManager().load(root)
            self.assertEqual(persisted.transcription_status, "failed")
            self.assertEqual(persisted.segments, updated.segments)

    def test_imported_srt_never_calls_ai_or_extracts_audio(self):
        pipeline = TranscriptionPipeline(Mock(), Mock(), Mock())
        with self.assertRaises(ValueError):
            pipeline.run(Project("p", "missing", transcription_status="imported"), "unused")
        pipeline.media.extract_audio.assert_not_called()
        pipeline.transcriber_factory.assert_not_called()

    def test_transcript_token_limit_retry_and_block_diagnostics(self):
        from types import SimpleNamespace
        def reply(reason, text='partial'):
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=reason)], text=text)
        sdk = Mock()
        with patch('google.genai.Client', return_value=sdk):
            gateway = GeminiClient('fake-key')
            sdk.models.generate_content.side_effect = [reply('MAX_TOKENS'), reply('STOP', '{"segments": []}')]
            self.assertEqual(gateway.transcribe_json(b'audio', 'prompt', {}, 'model'), '{"segments": []}')
            self.assertEqual([c.kwargs['config'].max_output_tokens for c in sdk.models.generate_content.call_args_list], [16384, 32768])
            sdk.models.generate_content.reset_mock()
            sdk.models.generate_content.side_effect = [reply('MAX_TOKENS'), reply('MAX_TOKENS')]
            with self.assertRaisesRegex(GeminiError, 'MAX_TOKENS'):
                gateway.transcribe_json(b'audio', 'prompt', {}, 'model')
            self.assertEqual(sdk.models.generate_content.call_count, 2)
            sdk.models.generate_content.reset_mock()
            sdk.models.generate_content.side_effect = [reply('SAFETY')]
            with self.assertRaisesRegex(GeminiError, 'SAFETY'):
                gateway.transcribe_json(b'audio', 'prompt', {}, 'model')
            self.assertEqual(sdk.models.generate_content.call_count, 1)
            sdk.models.generate_content.side_effect = [SimpleNamespace(candidates=[], prompt_feedback=SimpleNamespace(block_reason='PROHIBITED_CONTENT'))]
            with self.assertRaisesRegex(GeminiError, 'PROHIBITED_CONTENT'):
                gateway.transcribe_json(b'audio', 'prompt', {}, 'model')

    def test_sdk_mapping_uses_audio_structured_output_no_raw_errors(self):
        from google.genai import types
        from types import SimpleNamespace
        sdk = Mock()
        sdk.models.generate_content.return_value = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)], text=json.dumps(response()))
        with patch("google.genai.Client", return_value=sdk):
            gateway = GeminiClient("fake-key")
            text = gateway.transcribe_json(b"audio", "prompt", {"type": "OBJECT"}, "model")
            self.assertEqual(json.loads(text), response())
            args = sdk.models.generate_content.call_args.kwargs
            self.assertEqual(args["contents"][1].inline_data.mime_type, "audio/wav")
            self.assertEqual(args["config"].response_mime_type, "application/json")
            sdk.models.generate_content.side_effect = RuntimeError("fake-secret request body")
            with self.assertRaises(GeminiError) as caught:
                gateway.transcribe_json(b"audio", "prompt", {}, "model")
            self.assertNotIn("fake-secret", str(caught.exception))
            gateway.close()
            sdk.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
