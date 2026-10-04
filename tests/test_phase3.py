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
from cartoon_sub.translation.context_service import ContextService, source_fingerprint, context_config_fingerprint
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
    project.context_approved_config_hash = context_config_fingerprint(project)
    return project


def store():
    result = Mock()
    result.load.return_value = AISettings(default_ai_model="test/model",
                                          translation_chunk_size=30, retry_count=0)
    result.openrouter_catalog_cache.return_value = {"models": [{
        "id": "test/model", "architecture": {
            "input_modalities": ["text", "image"], "output_modalities": ["text"]}}]}
    result.openrouter_key_pool.return_value = "fake-test-key"
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
            original.context_approved_config_hash = context_config_fingerprint(original)
            def interrupted(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] != 1:
                    raise KeyboardInterrupt()
                reply = translated_reply(system, prompt, *args, **kwargs)
                for row in reply["segments"]:
                    row["vi"] = "Bản mới " + row["vi"]
                return reply
            client.generate_json.side_effect = interrupted
            with self.assertRaises(KeyboardInterrupt):
                pipeline.run(original, directory)
            interrupted_project = ProjectManager().load(directory)
            self.assertNotEqual([s.vi for s in interrupted_project.segments], old_texts)
            self.assertTrue(interrupted_project.segments[0].vi.startswith("Bản mới"))
            client.generate_json.side_effect = GeminiError("still unavailable")
            with self.assertRaises(GeminiError):
                pipeline.run(interrupted_project, directory)
            resumed = ProjectManager().load(directory)
            self.assertTrue(resumed.segments[0].vi.startswith("Bản mới"))
            self.assertEqual(resumed.translation_status, "failed_resumable")

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

    def test_context_visual_candidate_does_not_auto_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            project = make_project(3)
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            project.source_video_path = str(source)
            project.story_context["narration"] = "Ngôi thứ nhất do người dùng chốt"
            project.translation_genres = ["cultivation", "ancient"]
            project.translation_preset = "Natural Vietnamese"
            project.proper_name_mode = "sino_vietnamese"
            project.glossary = {"顾沉": "Cố Trầm"}
            project.translation_prompt = "Không dùng mày/tao."
            client = Mock()
            config = store()
            factory = Mock(return_value=client)
            service = ContextService(config, openrouter_factory=factory)
            proposal = StoryContext(summary="Đã đối chiếu video").to_dict()
            proposal["visual_contexts"] = [{
                "id": row.id, "scene_mode": "PRESENT",
                "speaker": {"spk_id": row.speaker_id, "character_id": "", "confidence": 0.4},
                "addressee": {"character_id": "", "confidence": 0.2},
                "visible_characters": [], "referents": [], "visible_objects": [],
                "notes": "unknown is valid", "confidence": 0.4, "analysis_status": "LOW_CONFIDENCE",
            } for row in project.segments]
            with patch("cartoon_sub.translation.context_service.VisualContextAnalyzer.analyze",
                       return_value=proposal):
                result, _ = service.analyze(project, directory, model="test/model")
            self.assertEqual(result.story_context, project.story_context)
            self.assertEqual(result.context_status, "proposal_ready")
            self.assertEqual(result.visual_context_status, "proposal_ready")
            self.assertEqual(len(result.context_proposal["visual_contexts"]), 3)

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
            self.assertGreaterEqual(client.generate_json.call_count, 2)
            second = parse_prompt(client.generate_json.call_args_list[1].args[1])
            first_target = second["targets"][0]["id"]
            last_target = second["targets"][-1]["id"]
            self.assertEqual([r["id"] for r in second["reference_before"]],
                             list(range(first_target - 5, first_target)))
            self.assertEqual([r["id"] for r in second["reference_after"]],
                             list(range(last_target + 1, min(66, last_target + 6))))
            self.assertEqual([r["id"] for r in second["previous_translation"]],
                             list(range(first_target - 5, first_target)))
            self.assertTrue((Path(directory) / "subtitle" / "vi.srt").exists())
            self.assertEqual(result.translation_status, "completed")

    def test_resume_only_failed_chunks_and_preserve_partial_result(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            first = True
            def fail_second(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] != 1:
                    raise GeminiError("temporary", True)
                return translated_reply(system, prompt, *args, **kwargs)
            client.generate_json.side_effect = fail_second
            pipeline = TranslationPipeline(store(), lambda key: client)
            with self.assertRaises(GeminiError):
                pipeline.run(make_project(), directory)
            partial = ProjectManager().load(directory)
            self.assertEqual(partial.translation_status, "failed_resumable")
            completed = [row for row in partial.segments if row.vi]
            self.assertTrue(completed)
            self.assertLess(len(completed), len(partial.segments))
            client.generate_json.reset_mock()
            client.generate_json.side_effect = translated_reply
            final, _ = pipeline.run(partial, directory)
            self.assertGreaterEqual(client.generate_json.call_count, 2)
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
            final.context_approved_config_hash = context_config_fingerprint(final)
            mark_stale(final, config.load())
            self.assertEqual(final.translation_status, "stale")
            client.generate_json.side_effect = GeminiError("bad request")
            with self.assertRaises(GeminiError):
                pipeline.run(final, directory)
            saved = ProjectManager().load(directory)
            self.assertEqual([s.vi for s in saved.segments], [s.vi for s in final.segments])
            self.assertEqual(saved.translation_status, "failed_resumable")

    def test_cancel_preserves_completed_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            cancel = Event()
            client = Mock()
            def cancelled(system, prompt, *args, **kwargs):
                if parse_prompt(prompt)["targets"][0]["id"] != 1:
                    cancel.set()
                return translated_reply(system, prompt, *args, **kwargs)
            client.generate_json.side_effect = cancelled
            pipeline = TranslationPipeline(store(), lambda key: client)
            with self.assertRaises(CancelledError):
                pipeline.run(make_project(), directory, cancel=cancel)
            project = ProjectManager().load(directory)
            self.assertEqual(project.translation_status, "partial")
            self.assertTrue(project.segments[0].vi)
            self.assertTrue(any(not row.vi for row in project.segments))
            client.close.assert_called_once()

    def test_translation_is_allowed_without_approved_context(self):
        with tempfile.TemporaryDirectory() as directory:
            project = make_project(2)
            project.story_context = StoryContext().to_dict()
            project.context_source_hash = ""
            project.context_approved_config_hash = ""
            project.context_status = "not_started"
            client = Mock()
            client.generate_json.side_effect = translated_reply
            result, _ = TranslationPipeline(store(), lambda key: client).run(project, directory)
        self.assertEqual(result.translation_status, "completed")
        client.generate_json.assert_called()

    def test_candidate_does_not_overwrite_approved_and_config_hash_persists(self):
        from cartoon_sub.app.controller import Controller
        with tempfile.TemporaryDirectory() as directory:
            project = make_project(2)
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"source")
            project.source_video_path = str(video)
            project.story_context["summary"] = "Approved cũ"
            project.context_approved_config_hash = context_config_fingerprint(project)
            controller = Controller()
            controller.accept((project, Path(directory)))
            candidate = StoryContext(summary="User đã sửa candidate").to_dict()
            candidate["visual_contexts"] = [{
                "id": row.id, "scene_mode": "UNKNOWN",
                "speaker": {"spk_id": row.speaker_id, "character_id": "", "confidence": 0.3},
                "addressee": {"character_id": "", "confidence": 0.2},
                "visible_characters": [], "referents": [], "visible_objects": [],
                "notes": "Đã xem, chưa đủ bằng chứng", "confidence": 0.3,
                "analysis_status": "LOW_CONFIDENCE",
            } for row in project.segments]
            controller.project.context_proposal = StoryContext(summary="Raw AI").to_dict()
            controller.project.visual_context_status = "proposal_ready"
            self.assertEqual(controller.project.story_context["summary"], "Approved cũ")
            controller.apply_context(candidate)
            loaded = ProjectManager().load(directory)
        self.assertEqual(loaded.story_context["summary"], "User đã sửa candidate")
        self.assertEqual(loaded.context_status, "applied")
        self.assertEqual(loaded.context_approved_config_hash, context_config_fingerprint(loaded))

    def test_translation_becomes_stale_without_touching_legacy_context(self):
        from cartoon_sub.app.controller import Controller
        with tempfile.TemporaryDirectory() as directory:
            project = make_project(2)
            project.translation_status = "completed"
            project.context_approved_config_hash = context_config_fingerprint(project)
            controller = Controller()
            controller.accept((project, Path(directory)))
            controller.update_translation_options("Light Classical", "Không dùng mày/tao.",
                                                  "顾沉 = Cố Trầm", ["cultivation", "ancient"],
                                                  "sino_vietnamese")
        self.assertEqual(controller.project.context_status, "applied")
        self.assertEqual(controller.project.translation_status, "stale")

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
