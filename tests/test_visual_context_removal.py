import tempfile
import unittest
from pathlib import Path

from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_service import translation_readiness


class VisualContextRemovalTests(unittest.TestCase):
    def _project(self):
        return Project(
            name="legacy", source_video_path="video.mp4", metadata={},
            segments=[Utterance(id=1, start=0, end=1, zh="你好")],
        )

    def test_controller_has_no_visual_context_runtime_service(self):
        controller = Controller()
        self.assertFalse(hasattr(controller, "context_service"))
        self.assertFalse(hasattr(controller, "analyze_context"))

    def test_legacy_visual_project_data_loads_and_is_not_persisted(self):
        data = self._project().to_dict()
        data.update({
            "context_proposal": {"visual_contexts": [{"id": 1}]},
            "context_source_hash": "legacy", "context_status": "applied",
            "visual_context_status": "applied", "visual_context_signature": "legacy",
            "visual_context_error": "",
        })
        loaded = Project.from_dict(data)
        saved = loaded.to_dict()
        self.assertNotIn("visual_context_status", saved)
        self.assertNotIn("context_proposal", saved)
        self.assertTrue(translation_readiness(loaded)[0])

    def test_legacy_vision_setting_loads_without_active_visual_runtime(self):
        settings = AISettings(vision_input_mode="video").validate()
        self.assertEqual(settings.vision_input_mode, "video")

    def test_no_visual_context_cache_is_created_by_project_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project()
            controller = Controller()
            controller.project, controller.directory = project, root
            controller.accept((project, root))
            self.assertFalse((root / "cache" / "visual_context").exists())

    def test_translate_and_existing_ai_routes_remain_available(self):
        controller = Controller()
        self.assertTrue(callable(controller.translate))
        self.assertTrue(callable(controller.qa_translation))
        self.assertTrue(callable(controller.optimize_dubbing))
        self.assertTrue(callable(controller.video_pipeline.run))


if __name__ == "__main__":
    unittest.main()
