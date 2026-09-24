import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError, load_gemini_keys
from cartoon_sub.app.settings import AISettings, SettingsStore


class ProviderError(Exception):
    code = 429


class GeminiKeyPoolTests(unittest.TestCase):
    def test_file_loader_deduplicates_and_settings_persist_only_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_file = root / "khóa gemini.txt"
            key_file.write_text("# keys\n A \n\nB\nA\n", encoding="utf-8")
            self.assertEqual(load_gemini_keys(key_file), ["A", "B"])
            store = SettingsStore(root / "settings", Mock())
            store.save(AISettings(gemini_api_keys_file=str(key_file)))
            saved = (root / "settings" / "settings.json").read_text(encoding="utf-8")
            self.assertIn(str(key_file).replace("\\", "\\\\"), saved)
            self.assertNotIn('"A"', saved)
            self.assertEqual(store.get_gemini_keys(), ["A", "B"])
            key_file.write_text("C\n", encoding="utf-8")
            self.assertEqual(store.get_gemini_keys(), ["C"])

    def test_rotation_stops_at_first_success(self):
        order = []

        def factory(api_key, **_):
            order.append(api_key)
            sdk = Mock()
            if api_key != "C":
                sdk.models.get.side_effect = ProviderError()
            else:
                sdk.models.get.return_value = SimpleNamespace(supported_actions=["generateContent"])
            return sdk

        with patch("google.genai.Client", side_effect=factory):
            client = GeminiClient(["A", "B", "C"])
            result = client.test_connection("model")
            client.close()
        self.assertEqual(order, ["A", "B", "C"])
        self.assertIn("key 3/3", result)

    def test_rotation_is_three_complete_rounds(self):
        order = []

        def factory(api_key, **_):
            order.append(api_key)
            sdk = Mock(); sdk.models.get.side_effect = ProviderError()
            return sdk

        with patch("google.genai.Client", side_effect=factory):
            client = GeminiClient(["A", "B", "C"])
            with self.assertRaisesRegex(GeminiError, "3 vòng.*3 API keys"):
                client.test_connection("model")
            client.close()
        self.assertEqual(order, ["A", "B", "C"] * 3)


if __name__ == "__main__":
    unittest.main()
