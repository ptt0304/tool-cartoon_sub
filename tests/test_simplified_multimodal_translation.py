import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.translation.chunker import translation_batches
from cartoon_sub.translation.context_service import translation_readiness
from cartoon_sub.translation.conversation_media import (
    ConversationEvidence,
    ConversationMediaBuilder,
    derive_media_window,
)
from cartoon_sub.translation.gemini_translator import (
    TranslationValidationError,
    validate_translation,
)
from cartoon_sub.translation.pipeline import TranslationPipeline
from cartoon_sub.translation.prompts import semantic_qa_prompt, translation_prompt
from cartoon_sub.translation.qa_service import TranslationQAService, validate_semantic_qa
from cartoon_sub.translation.qc import local_translation_qa


def prompt_payload(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


def project_rows(count=4):
    return Project(
        "simple", "missing.mp4", transcription_status="completed",
        segments=[Segment(index, (index - 1) * 1.5, index * 1.5,
                          f"第{index}句") for index in range(1, count + 1)],
    )


class FakeStore:
    def __init__(self, metadata=None):
        self.settings = AISettings(
            default_ai_model="vendor/model", translation_provider="openrouter",
            translation_chunk_size=30, retry_count=0,
        )
        self.metadata = metadata or {
            "id": "vendor/model",
            "architecture": {"input_modalities": ["text", "video"],
                             "output_modalities": ["text"]},
        }

    def load(self):
        return self.settings

    def openrouter_catalog_cache(self):
        return {"models": [self.metadata]}

    def openrouter_key_pool(self):
        return "not-a-real-key"


class FakeConversationClient:
    def __init__(self, *_args):
        self.prompts = []
        self.media_calls = []
        self.models = {}

    def _response(self, prompt):
        payload = prompt_payload(prompt)
        return {
            "translations": [{
                "id": row["id"], "vi": f"Câu dịch tự nhiên số {row['id']}.",
                "confidence": .92, "review_note": "",
                "meaning_preservation": "high", "compressed": False,
            } for row in payload["targets"]],
            "continuity_updates": [{
                "key": "cách gọi cô gái", "value": "cô chủ", "confidence": .9,
            }],
            "uncertainties": [],
        }

    def generate_multimodal_json(self, _system, prompt, media, _schema, _model, **_kwargs):
        self.prompts.append(prompt)
        self.media_calls.append(media)
        return self._response(prompt)

    def generate_json(self, _system, prompt, _schema, _model, **_kwargs):
        self.prompts.append(prompt)
        return self._response(prompt)

    def close(self):
        pass


class FakeMediaBuilder:
    def build(self, _project, _directory, targets, _metadata):
        start, end = derive_media_window(targets, 100)
        return ConversationEvidence(
            "video_with_audio", (start, end), [("video/mp4", b"clip-with-audio")],
            f"evidence-{targets[0]['id']}",
            {"mode": "video_with_audio", "window": [start, end],
             "original_audio": True, "media_count": 1},
        )


class SimplifiedMultimodalTranslationTests(unittest.TestCase):
    def test_transcript_is_only_translation_gate(self):
        for state in ("not_started", "VISUAL_CONTEXT_FAILED", "applied"):
            project = project_rows(1)
            project.visual_context_status = state
            project.context_status = state
            project.context_source_hash = ""
            project.speaker_review_hash = ""
            self.assertEqual(translation_readiness(project),
                             (True, "Transcript sẵn sàng — có thể dịch trực tiếp."))

    def test_user_guidance_and_priority_reach_prompt_without_story_context(self):
        project = project_rows(1)
        project.translation_genres = ["cultivation"]
        project.translation_preset = "Natural Vietnamese"
        project.translation_prompt = "Con mèo gọi cô gái là cô chủ."
        project.glossary = {"小白": "Tiểu Bạch"}
        project.story_context = {"summary": "Legacy AI context must not be used"}
        text = translation_prompt(
            project, [{"id": 1, "start": 0, "end": 1.5, "zh": "第1句"}],
            [], [], [], {"mode": "video_with_audio", "original_audio": True})
        payload = prompt_payload(text)
        editorial = payload["editorial"]
        self.assertEqual(editorial["user_defined_context"], project.translation_prompt)
        self.assertEqual(editorial["proper_name_rules"], project.glossary)
        self.assertIn("Tiên hiệp", editorial["context_instruction"])
        self.assertIn("Tiếng Việt", editorial["style"])
        self.assertEqual(editorial["context_priority"][:2],
                         ["user_defined_context", "user_mappings"])
        self.assertNotIn("approved_context", editorial)

    def test_adaptive_chunking_uses_gap_and_neighbors_are_context_only(self):
        project = project_rows(5)
        project.segments[3].start = 20
        project.segments[3].end = 21
        project.segments[4].start = 21
        project.segments[4].end = 22
        chunks = list(translation_batches(project.segments, 30))
        self.assertEqual([[row["id"] for row in group[0]] for group in chunks],
                         [[1, 2, 3], [4, 5]])
        self.assertEqual([row["id"] for row in chunks[1][1]], [1, 2, 3])
        with self.assertRaises(TranslationValidationError):
            validate_translation({"translations": [
                {"id": 4, "vi": "Bốn"}, {"id": 5, "vi": "Năm"},
                {"id": 3, "vi": "Không được trả ID context"},
            ]}, [4, 5])

    def test_media_window_comes_only_from_canonical_timestamps(self):
        targets = [{"id": 8, "start": 10.0, "end": 12.0, "zh": "一"},
                   {"id": 9, "start": 12.2, "end": 15.0, "zh": "二"}]
        self.assertEqual(derive_media_window(targets, 20), (8.0, 17.0))

    def test_direct_video_clip_preserves_original_audio_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"source-video")
            project = Project("media", str(source), metadata={"duration": 30})
            targets = [{"id": 1, "start": 3.0, "end": 7.0, "zh": "你好"}]
            commands = []

            def fake_run(command, cwd=None):
                commands.append(command)
                Path(command[-1]).write_bytes(b"x" * 2048)

            metadata = {"id": "model", "architecture": {
                "input_modalities": ["text", "video"], "output_modalities": ["text"]}}
            with patch("cartoon_sub.translation.conversation_media.run_process", fake_run):
                evidence = ConversationMediaBuilder().build(project, root, targets, metadata)
            self.assertEqual(evidence.mode, "video_with_audio")
            self.assertTrue(evidence.diagnostic["original_audio"])
            self.assertIn("0:a:0?", commands[0])
            self.assertEqual(commands[0][commands[0].index("-c:a") + 1], "aac")

    def test_pipeline_multimodal_preserves_source_and_generates_unapproved_final_vi(self):
        project = project_rows(3)
        project.translation_prompt = "Đây là hoạt hình; mèo gọi cô gái là cô chủ."
        project.glossary = {"小白": "Tiểu Bạch"}
        before = [(row.id, row.start, row.end, row.zh) for row in project.segments]
        client = FakeConversationClient()
        with tempfile.TemporaryDirectory() as temp:
            result, _ = TranslationPipeline(
                FakeStore(), lambda _key: client, FakeMediaBuilder()).run(project, temp)
        self.assertEqual([(row.id, row.start, row.end, row.zh) for row in result.segments], before)
        self.assertTrue(all(row.vi_subtitle.startswith("Câu dịch tự nhiên")
                            for row in result.segments))
        self.assertEqual(result.selected_models["translation_evidence_mode"],
                         "video_with_audio")
        self.assertTrue(client.media_calls)
        self.assertEqual(result.context_status, "not_started")
        self.assertLessEqual(len(result.translation_continuity_memory), 64)

    def test_user_context_reaches_every_conversation_request(self):
        project = project_rows(4)
        project.segments[2].start = 20
        project.segments[2].end = 21
        project.segments[3].start = 21
        project.segments[3].end = 22
        project.translation_prompt = "Quan hệ chủ-tớ; dùng cô chủ / tôi."
        client = FakeConversationClient()
        with tempfile.TemporaryDirectory() as temp:
            TranslationPipeline(FakeStore(), lambda _key: client, FakeMediaBuilder()).run(
                project, temp)
        self.assertEqual(len(client.prompts), 2)
        for prompt in client.prompts:
            payload = prompt_payload(prompt)
            self.assertEqual(payload["editorial"]["user_defined_context"],
                             project.translation_prompt)

    def test_memory_is_compact_bounded_and_rejects_low_confidence(self):
        project = project_rows(1)
        updates = [{"key": f"fact-{index}", "value": "quy ước", "confidence": .9}
                   for index in range(100)]
        updates.append({"key": "uncertain", "value": "không chắc", "confidence": .2})
        TranslationPipeline._update_continuity_memory(project, updates)
        self.assertEqual(len(project.translation_continuity_memory), 64)
        self.assertNotIn("uncertain", {row["key"] for row in project.translation_continuity_memory})

    def test_local_qa_detects_chinese_and_malformed_incomplete_output(self):
        project = project_rows(2)
        project.segments[0].vi = "Còn chữ 甘堕落."
        project.segments[1].vi = r"Câu lỗi \\u4f60"
        first = {row["type"] for row in local_translation_qa(project, project.segments[0])["issues"]}
        second = {row["type"] for row in local_translation_qa(project, project.segments[1])["issues"]}
        self.assertIn("UNTRANSLATED_HAN", first)
        self.assertIn("MALFORMED_OUTPUT", second)

    def test_qa_contract_covers_viewer_coherence_addressing_and_style(self):
        project = project_rows(3)
        project.translation_genres = ["ancient"]
        project.translation_preset = "Light Classical"
        source = [{"id": row.id, "zh": row.zh, "current_vi": f"Câu {row.id}"}
                  for row in project.segments]
        prompt = semantic_qa_prompt(project, source[1], "Câu 2", source[:1], source[2:])
        for phrase in ("khán giả Việt", "mạch hội thoại", "xưng hô/quan hệ",
                       "Hán-Việt", "không hiện đại hóa"):
            self.assertIn(phrase, prompt)
        verdict = validate_semantic_qa({
            "id": 2, "status": "FAIL", "issues": [{
                "type": "ADDRESSING_INCONSISTENCY",
                "detail": "Cách gọi cô chủ và chủ nhân thay đổi không có lý do trong cùng hội thoại.",
            }],
        }, 2)
        self.assertEqual(verdict["issues"][0]["type"], "ADDRESSING_INCONSISTENCY")

    def test_targeted_revision_is_limited_to_one_automatic_cycle(self):
        self.assertEqual(TranslationQAService.MAX_TRANSLATION_RETRIES, 1)

    def test_translation_reset_preserves_master_timeline_and_stt(self):
        project = project_rows(2)
        project.segments[0].vi = "Một"
        project.translation_continuity_memory = [{"key": "x", "value": "y", "confidence": 1}]
        before = [(row.id, row.start, row.end, row.zh) for row in project.segments]
        with tempfile.TemporaryDirectory() as temp:
            ProjectStageResetService(project, temp).reset_translation()
        self.assertEqual([(row.id, row.start, row.end, row.zh) for row in project.segments], before)
        self.assertEqual(project.transcription_status, "completed")
        self.assertEqual(project.translation_continuity_memory, [])

    def test_guidance_change_invalidates_translation_not_stt(self):
        project = project_rows(1)
        project.translation_status = "completed"
        project.translation_qa = {"1": {"status": "PASS"}}
        controller = Controller(settings_store=Mock())
        controller.settings_store.load.return_value = AISettings()
        controller.project = project
        controller.update_translation_options(
            "Natural Vietnamese", "Bối cảnh mới", "小白 = Tiểu Bạch", ["modern"])
        self.assertEqual(project.translation_status, "stale")
        self.assertEqual(project.translation_qa, {})
        self.assertEqual(project.transcription_status, "completed")

    def test_legacy_project_loads_and_downstream_vi_contract_is_unchanged(self):
        legacy = Project.from_dict({
            "schema_version": 3, "name": "legacy", "source_video_path": "missing.mp4",
            "master_timeline": [{"id": 1, "start": 0, "end": 1, "zh": "你好",
                                 "vi": "Xin chào"}],
            "story_context": {"summary": "Legacy data"},
            "visual_context_status": "VISUAL_CONTEXT_FAILED",
        })
        self.assertEqual(legacy.translation_continuity_memory, [])
        self.assertEqual(legacy.utterances[0].vi_subtitle, "Xin chào")
        self.assertTrue(translation_readiness(legacy)[0])

    def test_cache_does_not_store_api_key(self):
        project = project_rows(1)
        client = FakeConversationClient()
        with tempfile.TemporaryDirectory() as temp:
            TranslationPipeline(FakeStore(), lambda _key: client, FakeMediaBuilder()).run(
                project, temp)
            contents = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in (Path(temp) / "cache").rglob("*.json"))
        self.assertNotIn("not-a-real-key", contents)


if __name__ == "__main__":
    unittest.main()
