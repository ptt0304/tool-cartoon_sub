import tempfile
import unittest
from pathlib import Path

from cartoon_sub.ai.openrouter_client import (
    OPENROUTER_CHAT_URL, TASK_OUTPUT_CAPS, OpenRouterClient, _parse_json_object_content,
)
from cartoon_sub.ai.provider_errors import AIProviderError, ProviderErrorCategory
from cartoon_sub.translation.requests import CachedRequests
from cartoon_sub.translation.gemini_translator import validate_translation_response


class Response:
    status_code = 200
    headers = {}
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class Http:
    def __init__(self):
        self.bodies = []
    def post(self, _url, *, headers, json):
        self.bodies.append(json)
        return Response({"choices": [{"message": {"content": '{"ok": true}'}}]})


class Store:
    def openrouter_key_pool(self):
        return "test-key"
    def openrouter_catalog_cache(self):
        return {"models": []}


class RetryClient:
    def __init__(self):
        self.calls = 0
    def close(self):
        pass
    def generate_json(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise AIProviderError("temporary", ProviderErrorCategory.TIMEOUT, retryable=True)
        return {"ok": True}


class StructuredOutputCostTests(unittest.TestCase):
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    def _client(self, supported):
        http = Http()
        client = OpenRouterClient("test-key", [{"id": "vendor/model", "supported_parameters": supported}], http)
        return client, http

    def test_strict_schema_is_not_duplicated_in_prompt_and_has_task_cap(self):
        client, http = self._client(["response_format", "max_tokens"])
        self.assertEqual(client.generate_json("system", "user prompt", self.schema, "vendor/model", task="VIDEO_SUBTITLE_OCR"), {"ok": True})
        body = http.bodies[-1]
        self.assertIn("response_format", body)
        self.assertEqual(body["max_tokens"], TASK_OUTPUT_CAPS["VIDEO_SUBTITLE_OCR"])
        prompt = body["messages"][1]["content"]
        self.assertIn("JSON schema đã cung cấp", prompt)
        self.assertNotIn('"properties"', prompt)
        self.assertEqual(body["response_format"]["json_schema"]["schema"]["properties"], self.schema["properties"])

    def test_unsupported_structured_output_keeps_textual_schema_fallback(self):
        client, http = self._client(["max_tokens"])
        client.generate_json("system", "user prompt", self.schema, "vendor/model", task="SEMANTIC_BOUNDARY")
        body = http.bodies[-1]
        self.assertNotIn("response_format", body)
        self.assertIn('"properties"', body["messages"][1]["content"])
        self.assertEqual(body["max_tokens"], TASK_OUTPUT_CAPS["SEMANTIC_BOUNDARY"])

    def test_caps_are_task_specific_and_translation_is_safely_larger_than_ocr(self):
        client, http = self._client(["response_format", "max_completion_tokens"])
        client.generate_json("system", "ocr", self.schema, "vendor/model", task="VIDEO_SUBTITLE_OCR")
        ocr = http.bodies[-1]["max_completion_tokens"]
        client.generate_json("system", "batch", self.schema, "vendor/model", task="TRANSLATION")
        translation = http.bodies[-1]["max_completion_tokens"]
        self.assertEqual(ocr, 256)
        self.assertEqual(translation, 16384)
        self.assertGreater(translation, ocr)
        self.assertEqual(client.generate_json("system", "many rows", self.schema, "vendor/model", task="TRANSLATION"), {"ok": True})

    def test_all_required_tasks_have_safe_caps_and_unsupported_models_receive_none(self):
        self.assertEqual(set(TASK_OUTPUT_CAPS), {
            "VIDEO_SUBTITLE_OCR", "SEMANTIC_BOUNDARY", "TRANSCRIPT_REPAIR", "TRANSLATION",
            "TRANSLATION_QA", "MANUAL_QA", "DUBBING_OPTIMIZE", "DUBBING_DURATION_REWRITE",
        })
        client, http = self._client(["response_format"])
        client.generate_json("system", "prompt", self.schema, "vendor/model", task="DUBBING_OPTIMIZE")
        self.assertNotIn("max_tokens", http.bodies[-1])
        self.assertNotIn("max_completion_tokens", http.bodies[-1])

    def test_translation_multi_row_fixture_validates_with_the_translation_cap(self):
        payload = {"translations": [
            {"id": 1, "vi": "Dòng dịch thứ nhất.", "confidence": 0.9, "review_note": "", "meaning_preservation": "high", "compressed": False},
            {"id": 2, "vi": "Dòng dịch thứ hai.", "confidence": 0.9, "review_note": "", "meaning_preservation": "high", "compressed": False},
            {"id": 3, "vi": "Dòng dịch thứ ba.", "confidence": 0.9, "review_note": "", "meaning_preservation": "high", "compressed": False},
        ], "continuity_updates": [], "uncertainties": []}
        self.assertEqual([row["id"] for row in validate_translation_response(payload, [1, 2, 3])["translations"]], [1, 2, 3])
        self.assertEqual(TASK_OUTPUT_CAPS["TRANSLATION"], 16384)

    def test_parser_and_cached_retry_contract_are_unchanged(self):
        self.assertEqual(_parse_json_object_content('reasoning {ignored} then {"ok": true}'), {"ok": True})
        with tempfile.TemporaryDirectory() as root:
            fake = RetryClient()
            requests = CachedRequests(Store(), Path(root), "vendor/model", 1, client_factory=lambda _key: fake, task="TRANSLATION")
            value, first_key = requests.request("system", "prompt", self.schema, lambda value: value, "translation")
            self.assertEqual(value, {"ok": True})
            self.assertEqual(fake.calls, 2)
            second = CachedRequests(Store(), Path(root), "vendor/model", 1, client_factory=lambda _key: self.fail("cache should be used"), task="TRANSLATION")
            cached, second_key = second.request("system", "prompt", self.schema, lambda value: value, "translation")
            self.assertEqual((cached, second_key), ({"ok": True}, first_key))
            other_task = CachedRequests(Store(), Path(root), "vendor/model", 1, client_factory=lambda _key: fake, task="TRANSLATION_QA")
            _, other_key = other_task.request("system", "prompt", self.schema, lambda value: value, "qa")
            self.assertNotEqual(other_key, first_key)
            self.assertEqual(fake.calls, 3)


if __name__ == "__main__":
    unittest.main()
