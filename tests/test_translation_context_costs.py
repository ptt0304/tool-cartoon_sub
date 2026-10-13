import json
import unittest

from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.gemini_translator import TRANSLATION_SCHEMA, validate_translation_response
from cartoon_sub.translation.requests import CachedRequests
from cartoon_sub.project.cache import atomic_json, content_hash
from cartoon_sub.translation.prompts import (
    PROMPT_VERSION, dialogue_rows, dubbing_prompt, editorial, manual_translation_qa_prompt,
    semantic_qa_prompt, translation_prompt,
)
from cartoon_sub.transcription.semantic_boundary_resolver import ChineseSemanticBoundaryResolver


def payload(prompt):
    return json.loads(prompt[prompt.index("{"):])


class TranslationContextCostTests(unittest.TestCase):
    def project(self):
        return Project(
            "p", "video.mp4", glossary={"罗峰": "La Phong", "萧炎": "Tiêu Viêm", "global:tone": {"value": "Giữ giọng kể tự nhiên", "global": True}},
            translation_prompt="Không đổi tên user đã khóa.",
            translation_continuity_memory=[
                {"key": "罗峰", "value": "La Phong", "confidence": .9},
                {"key": "萧炎", "value": "Tiêu Viêm", "confidence": .9},
                {"key": "khác", "value": "Sự kiện không liên quan", "confidence": .9},
            ],
        )

    def rows(self):
        return ([{"id": 1, "zh": "罗峰来了", "voice_id": "v", "target_syllables": 12,
                  "estimated_syllables_per_second": 4.2, "available_duration": 3.0,
                  "translation_mode": "balanced_dubbing"}],
                [{"id": 2, "zh": "他回来了", "voice_id": "other"}],
                [{"id": 3, "zh": "大家安静", "voice_id": "other"}])

    def test_relevant_glossary_and_continuity_only(self):
        target, before, after = self.rows()
        data = payload(translation_prompt(self.project(), target, before, after, [], {"mode": "text_only"}))
        editorial_data = data["editorial"]
        self.assertIn("罗峰", editorial_data["glossary"])
        self.assertIn("global:tone", editorial_data["glossary"])
        self.assertNotIn("萧炎", editorial_data["glossary"])
        self.assertEqual([item["key"] for item in editorial_data["continuity_memory"]], ["罗峰"])
        self.assertEqual(data["reference_before"], [{"id": 2, "zh": "他回来了"}])
        self.assertEqual(data["targets"], [{"id": 1, "zh": "罗峰来了"}])
        self.assertEqual(editorial_data["user_rules"], "Không đổi tên user đã khóa.")
        self.assertNotIn("context_instruction", editorial_data)

    def test_normal_translation_excludes_dubbing_fields_but_dubbing_keeps_constraints(self):
        target, before, after = self.rows()
        normal = payload(translation_prompt(self.project(), target, before, after, [], {"mode": "text_only"}))
        self.assertEqual(set(normal["targets"][0]), {"id", "zh"})
        dubbing_target = {**target[0], "chinese_source": target[0]["zh"], "vi_subtitle": "La Phong đến rồi.",
                          "current_vi": "La Phong đã đến.", "current_delta": 3,
                          "maximum_allowed_delta": 2, "maximum_syllables": 14}
        dubbed = json.loads(dubbing_prompt(self.project(), [dubbing_target], before, after))
        self.assertIn("maximum_syllables", dubbed["targets"][0])
        self.assertIn("current_vi", dubbed["targets"][0])
        self.assertNotIn("voice_id", dubbed["reference_before"][0])

    def test_qa_keeps_target_and_current_vi_with_lean_editorial(self):
        target, before, after = self.rows()
        data = payload(semantic_qa_prompt(self.project(), target[0], "La Phong đến rồi.", before, after))
        self.assertEqual(data["target"], {"id": 1, "zh": "罗峰来了"})
        self.assertEqual(data["current_vietnamese"], "La Phong đến rồi.")
        self.assertNotIn("context_instruction", data["editorial"])
        manual = payload(manual_translation_qa_prompt(self.project(), target[0], before, after, "La Phong đến rồi.", "La Phong đã đến.", 1, ["x" * 1000]))
        self.assertLessEqual(len(manual["previous_issues"][0]), 300)
        self.assertEqual(manual["target"]["current_vi_subtitle"], "La Phong đến rồi.")

    def test_translation_schema_and_parser_no_longer_require_confidence(self):
        row_schema = TRANSLATION_SCHEMA["properties"]["translations"]["items"]
        self.assertNotIn("confidence", row_schema["properties"])
        result = validate_translation_response({"translations": [{"id": 1, "vi": "Xin chào.", "review_note": "", "meaning_preservation": "high", "compressed": False}]}, [1])
        self.assertNotIn("confidence", result["translations"][0])
        self.assertEqual(PROMPT_VERSION, "multimodal-conversation-translation-v2")

    def test_old_prompt_cache_key_is_not_reused(self):
        class Store:
            def openrouter_key_pool(self): return "test"
            def openrouter_catalog_cache(self): return {"models": []}
        class Client:
            calls = 0
            def generate_json(self, *_args, **_kwargs):
                self.calls += 1
                return {"ok": True}
            def close(self): pass
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            old_key = content_hash({"version": "multimodal-conversation-translation-v1", "provider": "openrouter", "model": "m", "task": "TRANSLATION", "system": "s", "prompt": "p", "schema": {"type": "object"}})
            atomic_json(f"{root}/{old_key}.json", {"status": "completed", "response": {"ok": False}})
            client = Client()
            request = CachedRequests(Store(), root, "m", 0, client_factory=lambda _key: client, task="TRANSLATION")
            value, current_key = request.request("s", "p", {"type": "object"}, lambda data: data, "test")
            self.assertEqual(value, {"ok": True})
            self.assertNotEqual(old_key, current_key)
            self.assertEqual(client.calls, 1)

    def test_semantic_boundary_accepts_new_compact_decision_without_type(self):
        target = {"target_id": 1, "source": "你好世界", "existing_boundaries": [1]}
        result = ChineseSemanticBoundaryResolver._validate({"targets": [{"target_id": 1, "boundaries": [
            {"after_index": 1, "action": "KEEP", "confidence": .9}
        ]}]}, [target])
        self.assertEqual(result[1][0]["action"], "KEEP")


if __name__ == "__main__":
    unittest.main()
