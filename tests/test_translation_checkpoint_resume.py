import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import httpx

from cartoon_sub.ai.openrouter_client import _http_error
from cartoon_sub.ai.provider_errors import AIProviderError, ProviderErrorCategory
from cartoon_sub.app.settings import AISettings
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.translation.conversation_media import ConversationEvidence
from cartoon_sub.translation.pipeline import TranslationPipeline, TranslationResumableError
from cartoon_sub.translation.qa_service import TranslationQAService


def _payload(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


class Store:
    def __init__(self, *, model="vendor/model", genre=None):
        self.settings = AISettings(
            default_ai_model=model, translation_provider="openrouter",
            translation_chunk_size=1, retry_count=0,
        )
        self.genre = genre

    def load(self):
        return self.settings

    def openrouter_catalog_cache(self):
        return {"models": [{"id": self.settings.default_ai_model, "architecture": {
            "input_modalities": ["text", "video"], "output_modalities": ["text"],
        }}]}

    def openrouter_key_pool(self):
        return "secret-key-must-not-be-persisted"


class Media:
    def build(self, _project, _directory, targets, _metadata):
        uid = targets[0]["id"]
        return ConversationEvidence(
            "video_with_audio", (targets[0]["start"], targets[-1]["end"]),
            [("video/mp4", b"media")], f"evidence-{uid}",
            {"mode": "video_with_audio", "original_audio": True},
        )


class Client:
    def __init__(self, fail_id=None, category=None, malformed_id=None,
                 mismatch_id=None, cancel=None):
        self.fail_id = fail_id
        self.category = category
        self.malformed_id = malformed_id
        self.mismatch_id = mismatch_id
        self.cancel = cancel
        self.calls = []
        self.models = {}

    def generate_multimodal_json(self, _system, prompt, _media, _schema, _model, **_kwargs):
        data = _payload(prompt)
        uid = data["targets"][0]["id"]
        self.calls.append(uid)
        if uid == self.fail_id:
            raise AIProviderError(
                "provider failed", self.category or ProviderErrorCategory.SERVER_ERROR,
                status_code=429 if self.category == ProviderErrorCategory.RATE_LIMITED else 503,
            )
        if uid == self.malformed_id:
            return {"not_translations": []}
        returned_id = uid + 100 if uid == self.mismatch_id else uid
        if self.cancel is not None and uid == self.fail_id:
            self.cancel.set()
        return {
            "translations": [{
                "id": returned_id, "vi": f"Bản dịch câu {uid}.", "confidence": .9,
                "review_note": "", "meaning_preservation": "high", "compressed": False,
            }],
            "continuity_updates": [{
                "key": f"fact-{uid}", "value": f"value-{uid}", "confidence": .9,
            }],
            "uncertainties": [],
        }

    def close(self):
        pass


class NoopQA:
    def run(self, project, directory, **_kwargs):
        return project, Path(directory)


class FailingQA:
    def run(self, _project, _directory, **_kwargs):
        raise AIProviderError("QA timeout", ProviderErrorCategory.TIMEOUT)


class QAClient:
    def __init__(self, fail_id=None):
        self.fail_id = fail_id
        self.calls = []
        self.models = {}

    def generate_json(self, _system, prompt, _schema, _model, **_kwargs):
        data = _payload(prompt)
        uid = data["target"]["id"]
        self.calls.append(uid)
        if uid == self.fail_id:
            raise AIProviderError("QA timeout", ProviderErrorCategory.TIMEOUT)
        return {"id": uid, "status": "PASS", "issues": []}

    def close(self):
        pass


def make_project(count=20):
    return Project(
        "checkpoint", "missing.mp4", transcription_status="completed",
        segments=[Segment(i, i * 5.0, i * 5.0 + 1.0, f"第{i}句")
                  for i in range(1, count + 1)],
    )


def pipeline(store, client, qa=None):
    value = TranslationPipeline(store, lambda _credentials: client, Media())
    value.qa_service = qa or NoopQA()
    return value


def response_error(status, payload, headers=None):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status, json=payload, headers=headers, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _http_error(exc)
    raise AssertionError("Expected an HTTP error")


class OpenRouterDiagnosticsTests(unittest.TestCase):
    def test_http_semantics_are_not_collapsed(self):
        cases = [
            (401, {"error": {"message": "invalid api key", "code": "auth"}},
             ProviderErrorCategory.AUTH_INVALID),
            (429, {"error": {"message": "too many requests", "code": "rate_limit"}},
             ProviderErrorCategory.RATE_LIMITED),
            (402, {"error": {"message": "insufficient credits", "code": "payment"}},
             ProviderErrorCategory.INSUFFICIENT_CREDITS),
            (402, {"error": {"message": "payment required", "code": "payment"}},
             ProviderErrorCategory.PAYMENT_REQUIRED),
            (429, {"error": {"message": "key budget exceeded", "code": "budget"}},
             ProviderErrorCategory.KEY_BUDGET_EXCEEDED),
            (400, {"error": {"message": "invalid payload"}},
             ProviderErrorCategory.BAD_REQUEST),
            (404, {"error": {"message": "model not found"}},
             ProviderErrorCategory.MODEL_ERROR),
            (503, {"error": {"message": "provider unavailable"}},
             ProviderErrorCategory.SERVER_ERROR),
        ]
        for status, payload, expected in cases:
            with self.subTest(status=status, expected=expected):
                self.assertEqual(response_error(status, payload).error_category, expected)

    def test_timeout_network_and_sanitized_details(self):
        self.assertEqual(_http_error(httpx.TimeoutException("slow")).error_category,
                         ProviderErrorCategory.TIMEOUT)
        request = httpx.Request("POST", "https://openrouter.ai")
        self.assertEqual(_http_error(httpx.ConnectError("offline", request=request)).error_category,
                         ProviderErrorCategory.NETWORK_ERROR)
        error = response_error(429, {"error": {
            "message": "Bearer secret-token rate limited", "code": "rate_limit",
        }}, {"Retry-After": "12"})
        self.assertNotIn("secret-token", error.provider_message)
        self.assertEqual(error.retry_after_seconds, 12)


class TranslationCheckpointResumeTests(unittest.TestCase):
    def test_rate_limit_persists_seven_chunks_and_restart_resumes_at_eight(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store()
            first = Client(8, ProviderErrorCategory.RATE_LIMITED)
            with self.assertRaises(TranslationResumableError):
                pipeline(store, first).run(make_project(), temp)
            saved = ProjectManager().load(temp)
            self.assertEqual(sum(bool(row.vi) for row in saved.segments), 7)
            self.assertEqual(saved.translation_status, "failed_resumable")
            self.assertEqual(len(list((Path(temp) / "cache/translation/checkpoints").glob("*.json"))), 7)

            second = Client()
            result, _ = pipeline(store, second).run(saved, temp)
            self.assertEqual(second.calls, list(range(8, 21)))
            self.assertEqual(result.translation_status, "completed")
            self.assertEqual(result.translation_continuity_memory[-1]["key"], "fact-20")

            third = Client()
            pipeline(store, third).run(ProjectManager().load(temp), temp)
            self.assertEqual(third.calls, [])

    def test_transient_failures_retain_previous_chunks(self):
        for category in (ProviderErrorCategory.TIMEOUT, ProviderErrorCategory.NETWORK_ERROR,
                         ProviderErrorCategory.SERVER_ERROR):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temp:
                client = Client(3, category)
                with self.assertRaises(TranslationResumableError):
                    pipeline(Store(), client).run(make_project(5), temp)
                saved = ProjectManager().load(temp)
                self.assertEqual([row.id for row in saved.segments if row.vi], [1, 2])

    def test_malformed_and_id_mismatch_chunks_are_not_checkpointed(self):
        for option in ("malformed_id", "mismatch_id"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temp:
                client = Client(**{option: 3})
                with self.assertRaises(Exception):
                    pipeline(Store(), client).run(make_project(5), temp)
                saved = ProjectManager().load(temp)
                self.assertEqual([row.id for row in saved.segments if row.vi], [1, 2])
                completed = list((Path(temp) / "cache/translation/checkpoints").glob("*.json"))
                self.assertEqual(len(completed), 2)

    def test_cancel_retains_checkpoint_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            event = Event()
            first = Client()
            original = first.generate_multimodal_json

            def cancel_at_three(*args, **kwargs):
                data = _payload(args[1])
                if data["targets"][0]["id"] == 3:
                    event.set()
                return original(*args, **kwargs)

            first.generate_multimodal_json = cancel_at_three
            with self.assertRaises(CancelledError):
                pipeline(Store(), first).run(make_project(5), temp, cancel=event)
            saved = ProjectManager().load(temp)
            self.assertEqual([row.id for row in saved.segments if row.vi], [1, 2])
            second = Client()
            pipeline(Store(), second).run(saved, temp)
            self.assertEqual(second.calls, [3, 4, 5])

    def test_changed_guidance_invalidates_chain_but_preserves_canonical_stt(self):
        with tempfile.TemporaryDirectory() as temp:
            original = make_project(4)
            canonical = [(row.id, row.start, row.end, row.zh) for row in original.segments]
            done, _ = pipeline(Store(), Client()).run(original, temp)
            done.translation_genres = ["ancient"]
            changed = Client()
            result, _ = pipeline(Store(), changed).run(done, temp)
            self.assertEqual(changed.calls, [1, 2, 3, 4])
            self.assertEqual([(row.id, row.start, row.end, row.zh)
                              for row in result.segments], canonical)
            self.assertEqual(result.transcription_status, "completed")

    def test_each_semantic_setting_and_model_change_invalidates_translation(self):
        variants = (
            lambda project, store: project.translation_genres.append("ancient"),
            lambda project, store: setattr(project, "translation_preset", "Light Classical"),
            lambda project, store: project.glossary.update({"小白": "Tiểu Bạch"}),
            lambda project, store: setattr(project, "translation_prompt", "Dùng cách gọi cô chủ."),
            lambda project, store: setattr(store.settings, "default_ai_model", "vendor/new-model"),
        )
        for mutate in variants:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temp:
                first_store = Store()
                done, _ = pipeline(first_store, Client()).run(make_project(3), temp)
                next_store = Store()
                mutate(done, next_store)
                client = Client()
                pipeline(next_store, client).run(done, temp)
                self.assertEqual(client.calls, [1, 2, 3])
                self.assertEqual(done.transcription_status, "completed")

    def test_translation_complete_is_reused_when_qa_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Client()
            with self.assertRaises(AIProviderError):
                pipeline(Store(), first, FailingQA()).run(make_project(4), temp)
            saved = ProjectManager().load(temp)
            self.assertEqual(saved.translation_status, "completed")
            second = Client()
            pipeline(Store(), second, NoopQA()).run(saved, temp)
            self.assertEqual(second.calls, [])

    def test_qa_row_checkpoint_survives_failure_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(3)
            for row in project.segments:
                row.vi = f"Bản dịch câu {row.id}."
            first = QAClient(fail_id=2)
            service = TranslationQAService(Store(), lambda _credentials: first)
            suspect = {"status": "SUSPECT", "issues": [{
                "type": "SUSPECT_CONTEXT", "detail": "Cần kiểm tra ngữ cảnh.",
                "severity": "WARNING",
            }]}
            with patch("cartoon_sub.translation.qa_service.local_translation_qa",
                       return_value=suspect):
                with self.assertRaises(AIProviderError):
                    service.run(project, temp, model="vendor/model")
            saved = ProjectManager().load(temp)
            self.assertEqual(saved.chunk_states["translation_qa"]["1"]["status"],
                             "completed")
            second = QAClient()
            service = TranslationQAService(Store(), lambda _credentials: second)
            with patch("cartoon_sub.translation.qa_service.local_translation_qa",
                       return_value=suspect):
                service.run(saved, temp, model="vendor/model")
            self.assertEqual(second.calls, [2, 3])

    def test_explicit_reset_removes_checkpoints_and_preserves_canonical_source(self):
        with tempfile.TemporaryDirectory() as temp:
            result, _ = pipeline(Store(), Client()).run(make_project(4), temp)
            canonical = [(row.id, row.start, row.end, row.zh) for row in result.segments]
            result.translation_qa = {"1": {"status": "PASS"}}
            result.chunk_states["translation_qa"] = {"1": {"status": "completed"}}
            result.segments[0].vi_dubbing = "Bản lồng tiếng"
            result.segments[0].tts_audio_path = "audio/tts/segments/1.wav"
            result.segments[0].set_display_segments([])
            result.final_audio_status = "generated"
            ProjectStageResetService(result, temp).reset_translation()
            ProjectManager().save(result, temp)
            self.assertFalse(any((Path(temp) / "cache/translation").rglob("*.json")))
            self.assertFalse(any(row.vi for row in result.segments))
            self.assertEqual(result.translation_continuity_memory, [])
            self.assertEqual(result.translation_qa, {})
            self.assertNotIn("translation_qa", result.chunk_states)
            self.assertFalse(result.segments[0].vi_dubbing)
            self.assertIsNone(result.segments[0].tts_audio_path)
            self.assertEqual(result.final_audio_status, "not_generated")
            self.assertEqual([(row.id, row.start, row.end, row.zh)
                              for row in result.segments], canonical)
            self.assertEqual(result.transcription_status, "completed")
            client = Client()
            pipeline(Store(), client).run(result, temp)
            self.assertEqual(client.calls[0], 1)

    def test_checkpoint_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            pipeline(Store(), Client()).run(make_project(2), temp)
            text = "\n".join(path.read_text(encoding="utf-8")
                              for path in (Path(temp) / "cache/translation").rglob("*.json"))
            self.assertNotIn("secret-key-must-not-be-persisted", text)


if __name__ == "__main__":
    unittest.main()
