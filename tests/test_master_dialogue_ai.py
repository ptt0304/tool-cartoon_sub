import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from cartoon_sub.ai.model_selection import AIModelSelection
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.master_dialogue_ai import MasterDialogueAIService
from cartoon_sub.ui.tabs import translate_tab


_app = QApplication.instance() or QApplication([])


class Store:
    def load(self): return SimpleNamespace(retry_count=0)
    def get_key(self, _provider): return "test-key"
    def get_gemini_official_keys(self): return ["test-key"]
    def openrouter_key_pool(self): return "test-key"
    def openrouter_catalog_cache(self): return {"models": []}


class Client:
    def __init__(self, _credential): pass
    def close(self): pass
    def generate_json(self, _system, _prompt, schema, _model, **_kwargs):
        if "translations" in schema["properties"]:
            return {"translations": [{"id": 1, "vi": "Chào bạn", "review_note": "ngắn hơn",
                                      "meaning_preservation": "high", "compressed": True, "confidence": 0.9}]}
        return {"items": [{"id": 1, "status": "warning", "issue_type": "naturalness",
                           "original": "Xin chào", "suggested": "Chào bạn", "target": "vi_subtitle",
                           "reason": "Tự nhiên hơn"}]}


class MasterDialogueAITests(unittest.TestCase):
    def project(self):
        return Project("demo", "", segments=[Utterance(1, 0, 2, zh="你好", vi_subtitle="Xin chào",
                                                          vi_dubbing="Xin chào", speaker_id="SPK_01")])

    def test_dubbing_proposal_is_structured_and_does_not_overwrite_before_apply(self):
        with tempfile.TemporaryDirectory() as root, patch("cartoon_sub.translation.master_dialogue_ai.text_client_factory", return_value=Client):
            project = self.project(); service = MasterDialogueAIService(Store())
            result, _ = service.run(project, root, [1], "dubbing", AIModelSelection("openrouter", "model-a"))
            self.assertEqual(result.utterances[0].vi_dubbing, "Xin chào")
            self.assertEqual(result.master_dialogue_ai_results["dubbing"]["selection"],
                             {"provider": "openrouter", "model_id": "model-a"})
            self.assertEqual(result.master_dialogue_ai_results["dubbing"]["items"][0]["target"], "vi_dubbing")
            self.assertEqual(service.apply(result, root, "dubbing", [1]), [1])
            self.assertEqual(result.utterances[0].vi_dubbing, "Chào bạn")

    def test_qa_is_structured_and_preserves_timeline_before_apply(self):
        with tempfile.TemporaryDirectory() as root, patch("cartoon_sub.translation.master_dialogue_ai.text_client_factory", return_value=Client):
            project = self.project(); service = MasterDialogueAIService(Store())
            result, _ = service.run(project, root, [1], "qa", AIModelSelection("openrouter", "google/gemini-test"))
            item = result.master_dialogue_ai_results["qa"]["items"][0]
            self.assertEqual(item["status"], "warning")
            self.assertEqual(result.utterances[0].vi_subtitle, "Xin chào")
            self.assertEqual(service.apply(result, root, "qa", [1]), [1])
            self.assertEqual(result.utterances[0].vi_subtitle, "Chào bạn")

    def test_old_project_without_ai_results_loads(self):
        project = self.project().to_dict(); project.pop("master_dialogue_ai_results", None)
        self.assertEqual(Project.from_dict(project).master_dialogue_ai_results, {})

    def test_translate_tab_uses_requested_labels_and_provider_aware_selector(self):
        page = translate_tab.build()
        try:
            self.assertIn("OpenRouter", page.master_ai_model_search.placeholderText())
            tabs = page.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)[0]
            names = [tabs.tabText(index) for index in range(tabs.count())]
            self.assertIn("Ngữ cảnh văn phong", names)
            self.assertNotIn("Ngữ cảnh_văn phong", names)
            self.assertIn("AI tối ưu & QA/QC", names)
        finally:
            page.deleteLater(); _app.processEvents()


if __name__ == "__main__":
    unittest.main()
