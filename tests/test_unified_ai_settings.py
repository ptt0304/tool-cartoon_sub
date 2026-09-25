import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from cartoon_sub.ai.text_client import MODEL_PRESETS, PROVIDER_CATALOG, model_metadata, provider_models
from cartoon_sub.app.settings import AISettings, SettingsStore, load_api_keys
from cartoon_sub.ui.settings_dialog import SettingsDialog


class UnifiedAISettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self, root, text, settings=None):
        path = Path(root) / "api_key.txt"; path.write_text(text, encoding="utf-8")
        store = SettingsStore(Path(root) / "settings")
        settings = settings or AISettings(api_key_file=str(path))
        store.save(settings)
        return path, store, SettingsDialog(SimpleNamespace(settings_store=store))

    def test_catalog_and_parser_use_exact_provider_key_mapping(self):
        self.assertEqual(len(PROVIDER_CATALOG), 10)
        for provider, catalog in PROVIDER_CATALOG.items():
            self.assertTrue(catalog["models"], provider)
            self.assertEqual(MODEL_PRESETS[provider], tuple(model["id"] for model in catalog["models"]))
            for model in catalog["models"]:
                self.assertEqual(set(model["capabilities"]), {"text", "transcription", "vision"})
                self.assertIn(model["status"], {"stable", "preview"})
                self.assertIs(model_metadata(provider, model["id"]), model)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "api_key.txt"
            path.write_text("gemini_key: A:with:colon\nclaude_key: CLAUDE\ndeepseek_key: NULL\n"
                            "groq_key: GROQ\nopenai_key: none\n", encoding="utf-8")
            keys = load_api_keys(path)
            self.assertEqual(keys["gemini"], "A:with:colon")
            self.assertEqual(keys["anthropic"], "CLAUDE")
            self.assertEqual(keys["groq"], "GROQ")
            self.assertIsNone(keys["deepseek"])
            self.assertIsNone(keys["openai"])

    def test_all_and_specific_provider_filter_models_by_key_and_capability(self):
        with tempfile.TemporaryDirectory() as root:
            path, _, dialog = self.make_dialog(root,
                "gemini_key: GEMINI\nclaude_key: CLAUDE\ndeepseek_key: null\ngroq_key: GROQ\n")
            self.assertEqual([dialog.provider.itemData(i) for i in range(dialog.provider.count())],
                             ["all", "gemini", "anthropic", "groq"])
            translation = [dialog.translation_model.itemData(i) for i in range(dialog.translation_model.count())]
            self.assertTrue(any(provider == "gemini" for provider, _ in translation))
            self.assertTrue(any(provider == "anthropic" for provider, _ in translation))
            self.assertTrue(any(provider == "groq" for provider, _ in translation))
            self.assertFalse(any(provider == "deepseek" for provider, _ in translation))
            transcription = [dialog.transcription_model.itemData(i) for i in range(dialog.transcription_model.count())]
            self.assertEqual({provider for provider, _ in transcription}, {"gemini", "groq"})
            dialog.provider.setCurrentIndex(dialog.provider.findData("anthropic"))
            self.assertEqual(dialog.transcription_model.count(), 1)
            self.assertFalse(dialog.transcription_model.isEnabled())
            self.assertIsNone(dialog.transcription_model.currentData())
            self.assertEqual([dialog.translation_model.itemData(i)[0]
                              for i in range(dialog.translation_model.count())], ["anthropic"] * 3)
            values = dialog.values()
            self.assertEqual(values.transcription_provider, "gemini")
            self.assertEqual(values.translation_provider, "anthropic")
            path.write_text("gemini_key: GEMINI\nclaude_key: CLAUDE\ndeepseek_key: DEEPSEEK\ngroq_key: GROQ\n",
                            encoding="utf-8")
            dialog.reload_api_file()
            self.assertGreaterEqual(dialog.provider.findData("deepseek"), 0)
            dialog.close()

    def test_model_metadata_routes_provider_and_save_reloads_path(self):
        with tempfile.TemporaryDirectory() as root:
            path, store, dialog = self.make_dialog(root,
                "gemini_key: GEMINI\nclaude_key: CLAUDE\ngroq_key: GROQ\n")
            dialog.provider.setCurrentIndex(dialog.provider.findData("all"))
            target = ("groq", provider_models("groq")[1])
            dialog.translation_model.setCurrentIndex(dialog._find_model(dialog.translation_model, target))
            values = dialog.values(); store.save(values)
            self.assertEqual((values.translation_provider, values.translation_model), target)
            self.assertEqual(store.get_key("groq"), "GROQ")
            reopened = SettingsDialog(SimpleNamespace(settings_store=store))
            self.assertEqual(tuple(reopened.translation_model.currentData()), target)
            self.assertEqual(reopened.key_file.text(), str(path))
            reopened.close(); dialog.close()

    def test_obsolete_saved_model_falls_back_within_same_provider_and_capability(self):
        with tempfile.TemporaryDirectory() as root:
            settings = AISettings(api_key_file=str(Path(root) / "api_key.txt"), provider_filter="deepseek",
                                  translation_provider="deepseek", translation_model="deepseek-chat")
            _, store, dialog = self.make_dialog(root, "gemini_key: G\ndeepseek_key: D\n", settings)
            self.assertEqual(tuple(dialog.translation_model.currentData()),
                             ("deepseek", provider_models("deepseek", "text")[0]))
            self.assertEqual(store.load().translation_model, provider_models("deepseek", "text")[0])
            self.assertNotIn("deepseek-chat", [dialog.translation_model.itemText(i)
                                                for i in range(dialog.translation_model.count())])
            dialog.close()

    def test_model_capabilities_are_filtered_per_model(self):
        self.assertIn("whisper-large-v3", provider_models("groq", "transcription"))
        self.assertNotIn("whisper-large-v3", provider_models("groq", "text"))
        self.assertIn("openai/gpt-oss-120b", provider_models("groq", "text"))
        self.assertNotIn("openai/gpt-oss-120b", provider_models("groq", "transcription"))
        self.assertEqual(provider_models("anthropic", "transcription"), ())

    def test_gemini_active_generations_and_transcribe_are_filtered_correctly(self):
        translation = provider_models("gemini", "text")
        transcription = provider_models("gemini", "transcription")
        expected_general = (
            "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
            "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
            "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro",
            "gemini-2.5-flash", "gemini-2.5-flash-lite",
        )
        self.assertEqual(translation, expected_general)
        self.assertEqual(transcription, expected_general[:5] + ("gemini-3.5-transcribe",) + expected_general[6:])
        self.assertNotIn("gemini-3.5-transcribe", translation)
        self.assertNotIn("gemini-3.1-flash-lite", transcription)
        for excluded in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"):
            self.assertNotIn(excluded, MODEL_PRESETS["gemini"])


if __name__ == "__main__":
    unittest.main()
