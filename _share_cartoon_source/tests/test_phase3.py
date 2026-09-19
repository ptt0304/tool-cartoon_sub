import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.media.process import CancelledError
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_service import ContextService, source_fingerprint
from cartoon_sub.translation.pipeline import TranslationPipeline, mark_stale
from cartoon_sub.translation.gemini_translator import validate_translation, validate_context, TranslationValidationError
from cartoon_sub.translation.qc import review_translation


def make_project(count=65):
    project = Project("test", "not-needed.mp4", segments=[
        Segment(i, i * 2, i * 2 + 1.5, f"第{i}句", speaker_id="SPK_01") for i in range(1, count + 1)], transcription_status="completed")
    from cartoon_sub.speaker.service import approve_review
    approve_review(project)
    project.context_source_hash = source_fingerprint(project)
    project.context_status = "applied"
    return project


def store():
    result = Mock()
    result.load.return_value = AISettings(translation_chunk_size=30, retry_count=0)
    result.get_key.return_value = "fake-test-key"
    return result


def parse_prompt(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


def translated_reply(system, prompt, schema, model, **kwargs):
    payload = parse_prompt(prompt)
    return {"segments": [{"id": row["id"], "vi": f"Câu dịch số {row['id']}.", "review_note": ""}
                          for row in reversed(payload["targets"])]}


class Phase3Tests(unittest.TestCase):
    def test_interrupted_replacement_does_not_mix_old_and_new_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.generate_json.side_effect = translated_reply
            config = store()
            pipeline = TranslationPipeline(config, lambda key: client)
            original, _ = pipeline.run(make_project(), directory)
            old_texts = [s.vi for s in original.segments]
            original.translation_prompt = "Dùng cách dịch mới"
            def interrupted(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] == 31:
                    raise KeyboardInterrupt()
                reply = translated_reply(system, prompt, *args, **kwargs)
                for row in reply["segments"]:
                    row["vi"] = "Bản mới " + row["vi"]
                return reply
            client.generate_json.side_effect = interrupted
            with self.assertRaises(KeyboardInterrupt):
                pipeline.run(original, directory)
            interrupted_project = ProjectManager().load(directory)
            self.assertEqual([s.vi for s in interrupted_project.segments], old_texts)
            client.generate_json.side_effect = GeminiError("still unavailable")
            with self.assertRaises(GeminiError):
                pipeline.run(interrupted_project, directory)
            resumed = ProjectManager().load(directory)
            self.assertEqual([s.vi for s in resumed.segments], old_texts)
            self.assertEqual(resumed.translation_status, "stale")

    def test_real_sdk_serializes_nested_schemas_with_local_http_transport(self):
        import httpx
        from google import genai
        from google.genai import types
        from cartoon_sub.translation.context_models import CONTEXT_SCHEMA
        from cartoon_sub.translation.gemini_translator import TRANSLATION_SCHEMA
        captured = []
        def handler(request):
            body = json.loads(request.content)
            captured.append(body)
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
                {"text": '{"segments": []}'}]}, "finishReason": "STOP"}]})
        gateway = GeminiClient.__new__(GeminiClient)
        gateway.client = genai.Client(api_key="test-key-not-real", vertexai=False, http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(handler)}, retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            for schema in (CONTEXT_SCHEMA, TRANSLATION_SCHEMA):
                gateway.generate_json("rules", "text only", schema, "gemini-3.5-flash")
        finally:
            gateway.close()
        self.assertEqual(len(captured), 2)
        self.assertIn("responseSchema", captured[0]["generationConfig"])
        self.assertIn("evidence_ids", str(captured[0]["generationConfig"]))
        self.assertNotIn("inlineData", str(captured))

    def test_old_project_loads_and_new_profile_roundtrips(self):
        old = {"name": "old", "source_video_path": "missing.mp4", "schema_version": 1}
        project = Project.from_dict(old)
        self.assertEqual(project.story_context, StoryContext().to_dict())
        self.assertEqual(project.translation_status, "not_started")
        project.translation_genres = ["cultivation", "system"]
        self.assertEqual(Project.from_dict(project.to_dict()).to_dict(), project.to_dict())

    def test_translation_strict_id_contract_and_no_timestamps(self):
        valid = {"segments": [{"id": 8, "vi": "Hai"}, {"id": 3, "vi": "Một"}]}
        self.assertEqual([r["id"] for r in validate_translation(valid, [3, 8])], [3, 8])
        for rows in ([{"id": 3, "vi": "Một"}], [{"id": 3, "vi": "Một"}, {"id": 3, "vi": "Lặp"}],
                     [{"id": 3, "vi": "Một", "start": 0}, {"id": 8, "vi": "Hai"}],
                     [{"id": True, "vi": "Một"}, {"id": 8, "vi": "Hai"}],
                     [{"id": 3, "vi": ""}, {"id": 8, "vi": "Hai"}]):
            with self.subTest(rows=rows), self.assertRaises(TranslationValidationError):
                validate_translation({"segments": rows}, [3, 8])

    def test_context_unknowns_and_evidence_validation(self):
        context = StoryContext(uncertainties=["Chưa xác định người nói"]).to_dict()
        context["characters"] = [{"source": "小美", "target": "Tiểu Mỹ", "notes": "Chưa rõ thân phận", "evidence_ids": [4]}]
        self.assertEqual(validate_context(context, [4]), context)
        with self.assertRaises(TranslationValidationError):
            validate_context(context, [1, 2])
        with self.assertRaises(TranslationValidationError):
            validate_context({}, [])

    def test_context_reads_all_batches_does_not_auto_apply_and_uses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            project = make_project(205)
            project.story_context["narration"] = "Ngôi thứ nhất do người dùng chốt"
            client = Mock()
            calls = []
            def proposal(system, prompt, *args, **kwargs):
                data = parse_prompt(prompt)
                calls.extend(r["id"] for r in data["transcript"])
                profile = StoryContext(summary=f"Đã đọc batch {data['batch']}").to_dict()
                return profile
            client.generate_json.side_effect = proposal
            config = store()
            factory = Mock(return_value=client)
            service = ContextService(config, factory)
            result, _ = service.analyze(project, directory)
            self.assertEqual(calls, list(range(1, 206)))
            self.assertEqual(result.story_context, project.story_context)
            self.assertEqual(result.context_status, "proposal_ready")
            self.assertEqual(result.context_proposal["summary"], "Đã đọc batch 3")
            factory.reset_mock()
            service.analyze(project, directory)
            factory.assert_not_called()

    def test_chunks_keep_timestamps_lookaround_and_previous_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = make_project()
            client = Mock()
            client.generate_json.side_effect = translated_reply
            pipeline = TranslationPipeline(store(), lambda key: client)
            result, _ = pipeline.run(project, directory)
            self.assertEqual([(s.id, s.start, s.end, s.zh) for s in result.segments],
                             [(s.id, s.start, s.end, s.zh) for s in project.segments])
            self.assertTrue(all(not s.vi for s in project.segments))
            self.assertEqual(client.generate_json.call_count, 3)
            second = parse_prompt(client.generate_json.call_args_list[1].args[1])
            self.assertEqual([r["id"] for r in second["reference_before"]], list(range(26, 31)))
            self.assertEqual([r["id"] for r in second["reference_after"]], list(range(61, 66)))
            self.assertEqual([r["id"] for r in second["previous_translation"]], list(range(26, 31)))
            self.assertTrue((Path(directory) / "subtitle" / "vi.srt").exists())
            self.assertEqual(result.translation_status, "completed")

    def test_resume_only_failed_chunks_and_preserve_partial_result(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            first = True
            def fail_second(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] == 31:
                    raise GeminiError("temporary", True)
                return translated_reply(system, prompt, *args, **kwargs)
            client.generate_json.side_effect = fail_second
            pipeline = TranslationPipeline(store(), lambda key: client)
            with self.assertRaises(GeminiError):
                pipeline.run(make_project(), directory)
            partial = ProjectManager().load(directory)
            self.assertEqual(partial.translation_status, "failed")
            self.assertTrue(partial.segments[29].vi)
            self.assertFalse(partial.segments[30].vi)
            client.generate_json.reset_mock()
            client.generate_json.side_effect = translated_reply
            final, _ = pipeline.run(partial, directory)
            self.assertEqual(client.generate_json.call_count, 2)
            self.assertEqual(final.translation_status, "completed")

    def test_style_cache_and_editorial_change_invalidation_preserves_old_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.generate_json.side_effect = translated_reply
            config = store()
            factory = Mock(return_value=client)
            pipeline = TranslationPipeline(config, factory)
            final, _ = pipeline.run(make_project(2), directory)
            final.subtitle_style.font_size = 70
            mark_stale(final, config.load())
            self.assertEqual(final.translation_status, "completed")
            factory.reset_mock()
            pipeline.run(final, directory)
            factory.assert_not_called()
            final.glossary = {"第": "Thứ"}
            mark_stale(final, config.load())
            self.assertEqual(final.translation_status, "stale")
            client.generate_json.side_effect = GeminiError("bad request")
            with self.assertRaises(GeminiError):
                pipeline.run(final, directory)
            saved = ProjectManager().load(directory)
            self.assertEqual([s.vi for s in saved.segments], [s.vi for s in final.segments])
            self.assertEqual(saved.translation_status, "stale")

    def test_cancel_preserves_completed_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = Event()
            client = Mock()
            def cancelled(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] == 31:
                    cancel.set()
                return translated_reply(system, prompt, *args, **kwargs)
            client.generate_json.side_effect = cancelled
            pipeline = TranslationPipeline(store(), lambda key: client)
            with self.assertRaises(CancelledError):
                pipeline.run(make_project(), directory, cancel=cancel)
            project = ProjectManager().load(directory)
            self.assertEqual(project.translation_status, "cancelled")
            self.assertTrue(project.segments[0].vi)
            self.assertFalse(project.segments[30].vi)
            client.close.assert_called_once()

    def test_requires_context_for_changed_transcript_before_any_api_call(self):
        project = make_project()
        project.segments[0].zh = "新文本"
        factory = Mock()
        with self.assertRaises(ValueError):
            TranslationPipeline(store(), factory).run(project, "unused")
        factory.assert_not_called()

    def test_qc_does_not_modify_translation(self):
        project = make_project(1)
        project.segments[0].zh = "小美"
        project.segments[0].vi = "小美 đang nói."
        project.glossary = {"小美": "Tiểu Mỹ"}
        project.translation_notes = {"1": "Chưa rõ người nói"}
        before = project.to_dict()
        notes = review_translation(project)["1"]
        self.assertTrue(any("Còn chữ Trung" in note for note in notes))
        self.assertTrue(any("thuật ngữ" in note for note in notes))
        self.assertEqual(project.to_dict(), before)

    def test_sdk_text_adapter_uses_system_instructions_not_audio(self):
        from google.genai import types
        sdk = Mock()
        sdk.models.generate_content.return_value = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)], text='{"segments": []}')
        with patch("google.genai.Client", return_value=sdk):
            gateway = GeminiClient("fake-test-key")
            gateway.generate_json("editorial rules", "transcript", {"type": "OBJECT"}, "model")
            args = sdk.models.generate_content.call_args.kwargs
            self.assertEqual(args["contents"], "transcript")
            self.assertEqual(args["config"].system_instruction, "editorial rules")
            gateway.close()


if __name__ == "__main__":
    unittest.main()
