import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.app.settings import AISettings
from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.translation.pipeline import TranslationPipeline, TranslationResumableError


def prompt_payload(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


class Store:
    def __init__(self):
        self.settings = AISettings(
            default_ai_model="google/gemini-2.5-flash-lite",
            translation_provider="openrouter", retry_count=0,
            tab_model_overrides={"translate": {
                "provider": "openrouter", "model_id": "google/gemini-2.5-flash-lite",
            }},
        ).validate()

    def load(self):
        return self.settings

    def openrouter_key_pool(self):
        return "test-key"

    def openrouter_catalog_cache(self):
        return {"models": []}


class Client:
    def __init__(self, _credential, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.prompts = []
        self.models = {}

    def generate_json(self, _system, prompt, _schema, _model, **_kwargs):
        self.prompts.append(prompt)
        payload = prompt_payload(prompt)
        row = payload["targets"][0]
        if row["id"] in self.fail_ids:
            raise RuntimeError("simulated translation failure")
        return {"translations": [{
            "id": row["id"], "vi": f"Bản dịch mới {row['id']}", "confidence": .9,
            "review_note": "", "meaning_preservation": "high", "compressed": False,
        }], "continuity_updates": [], "uncertainties": []}

    def close(self):
        pass


def project():
    rows = [Segment(index, index - 1, index, f"中文{index}",
                    vi_subtitle=f"Cũ {index}", vi_dubbing=f"Lồng tiếng cũ {index}")
            for index in range(1, 5)]
    return Project("selected", "video.mp4", transcription_status="completed", segments=rows)


class RetranslateSelectedTests(unittest.TestCase):
    def test_clear_selected_only_removes_selected_vietnamese_and_related_cache(self):
        value = project()
        store = Store()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            ProjectManager().save(value, root)
            cache = root / "cache" / "translation"
            cache.mkdir(parents=True, exist_ok=True)
            selected_cache = cache / "selected.json"
            selected_cache.write_text(json.dumps({"response": {"translations": [{"id": 1}]}}), encoding="utf-8")
            untouched_cache = cache / "other.json"
            untouched_cache.write_text(json.dumps({"response": {"translations": [{"id": 3}]}}), encoding="utf-8")
            controller = Controller.__new__(Controller)
            controller.manager = ProjectManager()
            controller.project = value
            controller.directory = root
            controller.settings_store = store
            controller.segmentation_service = SubtitleSegmentationService()
            controller.clear_selected_for_retranslation([1])
            saved = ProjectManager().load(root / "project.json")
            selected_cache_exists = selected_cache.exists()
            untouched_cache_exists = untouched_cache.exists()
        self.assertEqual(saved.segments[0].vi_subtitle, "")
        self.assertEqual(saved.segments[0].vi_dubbing, "")
        self.assertEqual(saved.segments[1].vi_subtitle, "Cũ 2")
        self.assertEqual(saved.segments[1].vi_dubbing, "Lồng tiếng cũ 2")
        self.assertFalse(selected_cache_exists)
        self.assertTrue(untouched_cache_exists)

    def test_only_selected_rows_are_translated_with_two_row_context_and_dubbing_stays_blank(self):
        client = Client("test-key")
        value = project()
        value.segments[0].vi_subtitle = value.segments[0].vi_dubbing = ""
        with tempfile.TemporaryDirectory() as root:
            result, _ = TranslationPipeline(Store(), lambda _key: client).retranslate_selected(
                value, Path(root), [1])
        self.assertEqual(result.segments[0].vi_subtitle, "Bản dịch mới 1")
        self.assertEqual(result.segments[0].vi_dubbing, "")
        self.assertEqual(result.segments[1].vi_subtitle, "Cũ 2")
        self.assertEqual(result.segments[1].vi_dubbing, "Lồng tiếng cũ 2")
        payload = prompt_payload(client.prompts[0])
        self.assertEqual([row["id"] for row in payload["targets"]], [1])
        self.assertEqual([row["id"] for row in payload["reference_after"]], [2, 3])

    def test_multiple_selected_rows_update_only_those_rows(self):
        client = Client("test-key")
        value = project()
        for index in (0, 2):
            value.segments[index].vi_subtitle = value.segments[index].vi_dubbing = ""
        with tempfile.TemporaryDirectory() as root:
            result, _ = TranslationPipeline(Store(), lambda _key: client).retranslate_selected(
                value, Path(root), [1, 3])
        self.assertEqual(result.segments[0].vi_subtitle, "Bản dịch mới 1")
        self.assertEqual(result.segments[2].vi_subtitle, "Bản dịch mới 3")
        self.assertEqual(result.segments[0].vi_dubbing, "")
        self.assertEqual(result.segments[2].vi_dubbing, "")
        self.assertEqual(result.segments[1].vi_subtitle, "Cũ 2")
        self.assertEqual(result.segments[3].vi_subtitle, "Cũ 4")

    def test_failed_selected_row_remains_blank_while_successful_row_is_saved(self):
        client = Client("test-key", fail_ids={2})
        value = project()
        for row in value.segments[:2]:
            row.vi_subtitle = row.vi_dubbing = ""
        with tempfile.TemporaryDirectory() as root:
            ProjectManager().save(value, root)
            with self.assertRaisesRegex(TranslationResumableError, "ID: 2"):
                TranslationPipeline(Store(), lambda _key: client).retranslate_selected(
                    value, Path(root), [1, 2])
            saved = ProjectManager().load(Path(root) / "project.json")
        self.assertEqual(saved.segments[0].vi_subtitle, "Bản dịch mới 1")
        self.assertEqual(saved.segments[0].vi_dubbing, "")
        self.assertEqual(saved.segments[1].vi_subtitle, "")
        self.assertEqual(saved.segments[1].vi_dubbing, "")
        self.assertEqual(saved.segments[2].vi_subtitle, "Cũ 3")


if __name__ == "__main__":
    unittest.main()
