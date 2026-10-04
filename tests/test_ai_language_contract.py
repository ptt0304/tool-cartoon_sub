import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.ai.language_contract import (
    USER_FACING_AI_INSTRUCTION,
    USER_FACING_AI_LANGUAGE,
    USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
    UserFacingLanguageError,
    story_context_user_facing_values,
    validate_user_facing_language,
)
from cartoon_sub.speaker.fusion_service import load_visual_evidence_cache
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_service import enforce_context_language_contract
from cartoon_sub.translation.character_registry import CharacterRegistry
from cartoon_sub.translation.gemini_translator import validate_translation
from cartoon_sub.translation.qa_service import validate_semantic_qa
from cartoon_sub.translation.visual_context import (
    VISUAL_CONTEXT_SYSTEM,
    VISUAL_PROMPT_SCHEMA_VERSION,
    VisualContextAnalyzer,
)


def visual_context(setting, notes):
    value = StoryContext(setting=setting, summary=setting, narration=notes).to_dict()
    value["visual_contexts"] = [{
        "id": 1,
        "scene_mode": "PRESENT",
        "speaker": {"spk_id": "SPK_01", "character_id": "CHAR_01", "confidence": .9},
        "addressee": {"character_id": "UNKNOWN", "confidence": .5},
        "visible_characters": [],
        "referents": [{
            "source_expression": "小主人", "character_id": "CHAR_01",
            "gender_context": "unknown", "confidence": .8,
        }],
        "visible_objects": [],
        "notes": notes,
        "confidence": .9,
        "analysis_status": "ANALYZED",
    }]
    value["new_character_candidates"] = [{
        "temporary_id": "CHAR_01", "description": "cô gái mặc áo xanh",
        "aliases": ["cô gái"], "gender_context": "female", "confidence": .9,
        "uncertain": False, "evidence_ids": [1],
        "bindings": [{"id": 1, "role": "speaker"}],
    }]
    return value


class QueueVisualClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.systems = []
        self.last_http_status = 200

    def generate_video_json(self, system, prompt, video_bytes, mime_type, schema, model,
                            cancel=None, progress=None):
        self.systems.append(system)
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FakeProxyAnalyzer(VisualContextAnalyzer):
    def _proxy(self, source, start, end, fps, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "proxy.mp4"
        path.write_bytes(b"fake")
        return path


class VietnameseAIOutputContractTests(unittest.TestCase):
    def project(self, root):
        video = root / "video.mp4"
        video.write_bytes(b"video")
        return Project(
            "language", str(video), metadata={"duration": 1.0},
            segments=[Utterance(1, 0, 1, "小主人", speaker_id="SPK_01")],
        )

    def test_english_visual_response_gets_one_corrective_retry(self):
        english = visual_context(
            "An outdoor setting with a river and trees in the background.",
            "The girl is visible and she is likely the speaker in this scene.",
        )
        vietnamese = visual_context(
            "Cảnh ngoài trời bên bờ sông, phía sau có nhiều cây.",
            "Cô gái xuất hiện trong cảnh; người nói được xác định theo bằng chứng hình ảnh.",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            client = QueueVisualClient([english, vietnamese])
            result = FakeProxyAnalyzer(client, "model").analyze(project, root)
            self.assertEqual(len(client.prompts), 2)
            self.assertIn("Bạn đã trả lời sai ngôn ngữ", client.prompts[1])
            self.assertEqual(result["visual_contexts"][0]["id"], 1)
            self.assertEqual(project.utterances[0].zh, "小主人")
            cache = next((root / "cache" / "visual_context").glob("*.json"))
            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(saved["user_facing_ai_language"], USER_FACING_AI_LANGUAGE)
            self.assertEqual(saved["language_contract_version"],
                             USER_FACING_AI_LANGUAGE_CONTRACT_VERSION)

    def test_first_request_carries_same_vietnamese_contract_for_all_visual_stages(self):
        vietnamese = visual_context(
            "Cảnh ngoài trời bên bờ sông, cô gái đang nói chuyện với con mèo.",
            "Cô gái xuất hiện trong cảnh; danh tính được đối chiếu bằng hình ảnh.",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = QueueVisualClient([vietnamese])
            project = self.project(root)
            result = FakeProxyAnalyzer(client, "model").analyze(project, root)
            self.assertEqual(len(client.prompts), 1)
            self.assertTrue(client.systems[0].startswith(USER_FACING_AI_INSTRUCTION))
            request = json.loads(client.prompts[0])
            contract = request["output_language_contract"]
            self.assertEqual(contract["language"], "vi")
            self.assertEqual(contract["instruction"], USER_FACING_AI_INSTRUCTION)
            self.assertIn("global_context_discovery", contract["applies_to"])
            self.assertIn("global_character_registry", contract["applies_to"])
            self.assertIn("window_visual_context", contract["applies_to"])
            self.assertIn("new_character_candidate", contract["applies_to"])
            self.assertIn("final_reconciliation_story_context", contract["applies_to"])
            self.assertIn("new_character_candidates.description",
                          contract["user_facing_fields"])
            self.assertIn("chinese_source", contract["preserve_verbatim"])
            self.assertEqual(result["visual_contexts"][0]["id"], 1)
            self.assertEqual(project.utterances[0].zh, "小主人")

    def test_english_new_character_description_triggers_only_one_retry(self):
        first = visual_context(
            "Cảnh ngoài trời bên bờ sông.",
            "Cô gái xuất hiện trong cảnh.",
        )
        first["new_character_candidates"][0]["description"] = (
            "The young girl character is standing beside the river in the background."
        )
        corrected = visual_context(
            "Cảnh ngoài trời bên bờ sông.",
            "Cô gái xuất hiện trong cảnh.",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = QueueVisualClient([first, corrected])
            FakeProxyAnalyzer(client, "model").analyze(self.project(root), root)
            self.assertEqual(len(client.prompts), 2)
            self.assertIn("Bạn đã trả lời sai ngôn ngữ", client.prompts[1])

    def test_registry_reconciliation_preserves_machine_ids_and_uses_vietnamese_note(self):
        value = visual_context(
            "Cảnh ngoài trời bên bờ sông.",
            "Cô gái xuất hiện trong cảnh.",
        )
        value.pop("new_character_candidates")
        value["visual_contexts"][0]["speaker"]["character_id"] = "CHAR_CAT"
        reconciled = CharacterRegistry().reconcile_response(value, {1})
        row = reconciled["visual_contexts"][0]
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["speaker"]["spk_id"], "SPK_01")
        self.assertEqual(row["speaker"]["character_id"], "UNKNOWN")
        self.assertEqual(row["scene_mode"], "PRESENT")
        self.assertIn("registry chuẩn hóa", row["notes"])
        self.assertNotIn("CÔ_GÁI", json.dumps(reconciled, ensure_ascii=False))

    def test_visual_prompt_cache_version_is_vietnamese_first(self):
        self.assertIn("vietnamese-first", VISUAL_PROMPT_SCHEMA_VERSION)
        self.assertIn("new_character_candidates.description", VISUAL_CONTEXT_SYSTEM)

    def test_language_retry_is_limited_to_one(self):
        english = visual_context(
            "An outdoor setting with a river and trees in the background.",
            "The girl is visible and she is likely the speaker in this scene.",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = QueueVisualClient([english, english])
            with self.assertRaises(UserFacingLanguageError):
                FakeProxyAnalyzer(client, "model").analyze(self.project(root), root)
            self.assertEqual(len(client.prompts), 2)

    def test_vietnamese_with_chinese_alias_is_accepted(self):
        value = visual_context(
            "Cảnh ngoài trời bên bờ sông, cô gái đang nói chuyện với con mèo.",
            "小主人 là cách con mèo gọi cô gái trong câu thoại này.",
        )
        validate_user_facing_language(story_context_user_facing_values(value))
        self.assertEqual(value["visual_contexts"][0]["speaker"]["spk_id"], "SPK_01")
        self.assertEqual(value["visual_contexts"][0]["speaker"]["character_id"], "CHAR_01")
        self.assertIn("小主人", value["visual_contexts"][0]["referents"][0]["source_expression"])

    def test_user_facing_translation_and_qa_explanations_are_validated(self):
        with self.assertRaises(UserFacingLanguageError):
            validate_translation({"translations": [{
                "id": 1, "vi": "This is an English translation with the wrong output language.",
                "review_note": "The sentence is uncertain because the speaker is not visible.",
                "meaning_preservation": "high", "compressed": False,
            }]}, [1])
        accepted = validate_semantic_qa({
            "id": 1, "status": "FAIL",
            "issues": [{"type": "MEANING", "detail": "Câu dịch thiếu ý phủ định của câu nguồn."}],
        }, 1)
        self.assertEqual(accepted["id"], 1)
        with self.assertRaises(UserFacingLanguageError):
            validate_semantic_qa({
                "id": 1, "status": "FAIL",
                "issues": [{"type": "MEANING", "detail":
                            "The translation is missing important meaning from the source sentence."}],
            }, 1)

    def test_old_candidate_cache_is_not_reused_and_approved_context_survives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache" / "visual_context"
            cache.mkdir(parents=True)
            (cache / "old.json").write_text(json.dumps({
                "status": "completed",
                "response": visual_context("Cảnh ngoài trời bên sông.", "Cô gái đang nói."),
            }, ensure_ascii=False), encoding="utf-8")
            candidate, files = load_visual_evidence_cache(root, {1})
            self.assertFalse(files)
            self.assertFalse(candidate["visual_contexts"])

        project = Project("candidate", "video.mp4", segments=[Utterance(1, 0, 1, "你好")])
        project.context_proposal = visual_context("Old English setting in the scene.", "Old note.")
        project.context_status = project.visual_context_status = "proposal_ready"
        self.assertTrue(enforce_context_language_contract(project))
        self.assertEqual(project.context_status, "stale")
        project.context_status = project.visual_context_status = "applied"
        approved = visual_context("Human approved context.", "Human approved note.")
        project.story_context = approved
        self.assertFalse(enforce_context_language_contract(project))
        self.assertEqual(project.story_context, approved)
        self.assertEqual(project.context_status, "applied")


if __name__ == "__main__":
    unittest.main()
