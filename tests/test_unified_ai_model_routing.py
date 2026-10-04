import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from cartoon_sub.ai.model_resolver import AIModelNotConfiguredError, AIModelResolver
from cartoon_sub.app.settings import AISettings, SettingsStore
from cartoon_sub.app.controller import Controller
from cartoon_sub.project.cache import atomic_json


MODELS = [
    {"id": "vendor/text-a", "name": "Text A",
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "vendor/text-b", "name": "Text B",
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "vendor/vision", "name": "Vision",
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
    {"id": "vendor/vision-b", "name": "Vision B",
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
]


class FakeStore:
    def __init__(self, settings):
        self.settings = settings

    def load(self):
        return self.settings

    def openrouter_catalog_cache(self):
        return {"models": MODELS}


class UnifiedAIModelRoutingTests(unittest.TestCase):
    def test_missing_default_blocks(self):
        with self.assertRaises(AIModelNotConfiguredError):
            AIModelResolver(FakeStore(AISettings())).resolve("TRANSLATION", "translate")

    def test_default_and_override_hierarchy_and_propagation(self):
        settings = AISettings(default_ai_model="vendor/text-a")
        resolver = AIModelResolver(FakeStore(settings))
        self.assertEqual(resolver.resolve("TRANSLATION", "translate").model_id, "vendor/text-a")
        settings.tab_model_overrides = {"translate": "vendor/text-b"}
        self.assertEqual(resolver.resolve("CONTEXT", "translate").model_id, "vendor/text-b")
        settings.default_ai_model = "vendor/vision"
        self.assertEqual(resolver.resolve("TRANSLATION", "transcript").model_id, "vendor/vision")
        self.assertEqual(resolver.resolve("TRANSLATION", "translate").model_id, "vendor/text-b")
        settings.tab_model_overrides = {}
        self.assertEqual(resolver.resolve("TRANSLATION", "translate").model_id, "vendor/vision")

    def test_capability_validation_never_substitutes(self):
        resolver = AIModelResolver(FakeStore(AISettings(default_ai_model="vendor/text-a")))
        with self.assertRaisesRegex(ValueError, "không hỗ trợ hình ảnh"):
            resolver.resolve("CONTEXT_ANALYSIS", "translate", ("vision_frames",))
        accepted = AIModelResolver(FakeStore(
            AISettings(default_ai_model="vendor/vision"))).resolve(
                "CONTEXT_ANALYSIS", "translate", ("vision_frames",))
        self.assertEqual(accepted.model_id, "vendor/vision")

    def test_legacy_migration_once_and_exact_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder / "openrouter_models.json", {"updated_at": "now", "models": MODELS})
            (folder / "settings.json").write_text(json.dumps({
                "translation_model": "vendor/text-a", "vision_speaker_model": "vendor/vision"
            }), encoding="utf-8")
            store = SettingsStore(folder, SimpleNamespace(get_password=lambda *_: None))
            self.assertEqual(store.load().default_ai_model, "vendor/text-a")
            data = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(data["default_ai_model"], "vendor/text-a")
            data["default_ai_model"] = "vendor/text-b"
            (folder / "settings.json").write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(store.load().default_ai_model, "vendor/text-b")

    def test_tab_override_persists_as_application_preference(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            store = SettingsStore(folder, SimpleNamespace(get_password=lambda *_: None))
            store.save(AISettings(default_ai_model="vendor/text-a"))
            store.set_tab_model_override("translate", "vendor/text-b")
            self.assertEqual(store.load().tab_model_overrides, {"translate": "vendor/text-b"})
            store.set_tab_model_override("translate", "")
            self.assertEqual(store.load().tab_model_overrides, {})

    def test_context_controller_passes_exact_resolved_model(self):
        settings = AISettings(default_ai_model="vendor/vision", vision_input_mode="frames")
        controller = Controller.__new__(Controller)
        controller.settings_store = FakeStore(settings)
        controller.ai_model_resolver = AIModelResolver(controller.settings_store)
        controller.project = object(); controller.directory = Path("project")
        captured = {}
        controller.context_service = SimpleNamespace(analyze=lambda project, directory, **kwargs:
            captured.update(kwargs) or (project, directory))
        Controller.analyze_context(controller)
        self.assertEqual(captured["model"], "vendor/vision")
        self.assertEqual(captured["selection_source"], "GLOBAL_DEFAULT")
        settings.tab_model_overrides = {"translate": "vendor/vision-b"}
        Controller.analyze_context(controller)
        self.assertEqual(captured["model"], "vendor/vision-b")
        self.assertEqual(captured["selection_source"], "TAB_OVERRIDE")
        settings.tab_model_overrides = {"translate": "vendor/text-a"}
        with self.assertRaisesRegex(ValueError, "không hỗ trợ hình ảnh"):
            Controller.analyze_context(controller)


if __name__ == "__main__":
    unittest.main()
