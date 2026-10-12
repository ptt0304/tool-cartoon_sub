import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget

from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.ui.tabs import translate_tab

_app = QApplication.instance() or QApplication([])


class RemoveAIOptimizeQaqcTests(unittest.TestCase):
    def test_translate_has_only_context_and_master_dialogue_tabs(self):
        page = translate_tab.build()
        try:
            tabs = page.findChildren(QTabWidget)[0]
            self.assertEqual(
                [tabs.tabText(index) for index in range(tabs.count())],
                ["Ngữ cảnh văn phong", "Master dialogue timeline"],
            )
            self.assertFalse(hasattr(page, "master_ai_dubbing_button"))
            self.assertFalse(hasattr(page, "master_ai_results"))
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_master_timeline_actions_remain(self):
        page = translate_tab.build()
        try:
            for attribute in (
                "retranslate_selected_button", "optimize_button", "optimize_bulk_button",
                "manual_qa_button", "revert_optimize_button", "edit_button",
                "apply_edits_button", "revert_edits_button",
            ):
                self.assertTrue(hasattr(page, attribute), attribute)
        finally:
            page.deleteLater()
            _app.processEvents()

    def test_legacy_project_proposal_state_loads_safely(self):
        project = Project("demo", "", segments=[Utterance(1, 0, 1, zh="你好")])
        payload = project.to_dict()
        payload["master_dialogue_ai_results"] = {
            "qa": {"selection": {"provider": "openrouter", "model_id": "legacy"}, "items": []}
        }
        loaded = Project.from_dict(payload)
        self.assertEqual(loaded.master_dialogue_ai_results["qa"]["selection"]["model_id"], "legacy")

    def test_legacy_master_dialogue_model_override_loads_safely(self):
        settings = AISettings(tab_model_overrides={
            "master_dialogue": {"provider": "openrouter", "model_id": "google/gemini-2.5-flash"}
        }).validate()
        self.assertEqual(settings.tab_model_overrides["master_dialogue"]["model_id"], "google/gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
