import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.translation.context_profiles import PROFILES
from cartoon_sub.translation.glossary import parse_glossary
from cartoon_sub.translation.pipeline import translation_fingerprint
from cartoon_sub.translation.prompts import BASE_TRANSLATION_INSTRUCTION, build_context_instruction
from cartoon_sub.ui.tabs.translate_tab import build


class TranslationContextProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registry_has_27_complete_unique_profiles(self):
        self.assertEqual(len(PROFILES), 27)
        self.assertEqual(len(PROFILES), len(set(PROFILES)))
        for key, profile in PROFILES.items():
            self.assertEqual(profile.id, key)
            self.assertTrue(profile.display_name.strip())
            self.assertTrue(profile.description.strip())
            self.assertTrue(profile.prompt_instruction.strip())

    def test_single_profile_sends_only_concise_instruction(self):
        project = Project("demo", "video.mp4", translation_genres=["ancient"])
        instruction = build_context_instruction(project)
        self.assertIn(PROFILES["ancient"].prompt_instruction, instruction)
        self.assertNotIn(PROFILES["ancient"].description, instruction)
        self.assertNotIn(PROFILES["modern"].prompt_instruction, instruction)
        self.assertEqual(instruction.count(BASE_TRANSLATION_INSTRUCTION), 1)

    def test_multi_select_custom_name_mode_and_mapping_priority(self):
        project = Project(
            "demo", "video.mp4",
            translation_genres=["ancient", "transmigration", "comedy"],
            translation_prompt="SPK_01 là hoàng đế.",
            proper_name_mode="user_mapping",
            glossary={"Xuanyi": "Huyền Thiên"},
        )
        instruction = build_context_instruction(project)
        for key in project.translation_genres:
            self.assertIn(PROFILES[key].prompt_instruction, instruction)
        self.assertIn("SPK_01 là hoàng đế.", instruction)
        self.assertIn("Xuanyi => Huyền Thiên", instruction)
        self.assertIn("độ ưu tiên cao nhất", instruction)
        self.assertIn("Chỉ đổi tên riêng theo mapping", instruction)

    def test_glossary_accepts_equals_arrow_comments_and_rejects_conflicts(self):
        value = parse_glossary("# names\nXuanyi = Huyền Nhất\nQingyun -> Thanh Vân\n")
        self.assertEqual(value, {"Xuanyi": "Huyền Nhất", "Qingyun": "Thanh Vân"})
        with self.assertRaises(ValueError):
            parse_glossary("Xuanyi = Huyền Nhất\nXuanyi -> Huyền Ý")

    def test_persistence_fingerprint_and_legacy_migration(self):
        project = Project(
            "demo", "video.mp4", translation_genres=["ancient", "comedy"],
            proper_name_mode="preserve_source", translation_prompt="Yêu cầu riêng",
            glossary={"玄一": "Huyền Nhất"},
        )
        settings = AISettings()
        baseline = translation_fingerprint(project, settings)
        project.cache_hashes["transcription"] = "unchanged"
        project.translation_genres.append("transmigration")
        self.assertNotEqual(translation_fingerprint(project, settings), baseline)
        self.assertEqual(project.cache_hashes["transcription"], "unchanged")
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(folder)
        self.assertEqual(loaded.translation_genres, project.translation_genres)
        self.assertEqual(loaded.proper_name_mode, "preserve_source")
        self.assertEqual(loaded.glossary, project.glossary)
        self.assertEqual(loaded.translation_prompt, "Yêu cầu riêng")

        migrated = Project.from_dict({
            "name": "old", "source_video_path": "video.mp4", "schema_version": 2,
            "translation_genres": ["historical", "rebirth", "face_slap"],
        })
        self.assertEqual(migrated.translation_genres, ["ancient", "palace"])
        self.assertIn("Trọng sinh", migrated.translation_prompt)
        self.assertIn("Vả mặt", migrated.translation_prompt)
        phrase = Project.from_dict({
            "name": "old", "source_video_path": "video.mp4", "schema_version": 2,
            "translation_genres": "Hiện đại xuyên không",
        })
        self.assertEqual(phrase.translation_genres, ["modern", "transmigration"])

    def test_ui_multi_select_shows_descriptions_and_name_modes(self):
        page = build()
        page.genres["ancient"].setChecked(True)
        page.genres["transmigration"].setChecked(True)
        self.assertIn("Cổ trang", page.selected_contexts.text())
        self.assertIn("Xuyên không", page.selected_contexts.text())
        shown = page.context_descriptions.toPlainText()
        self.assertIn(PROFILES["ancient"].description, shown)
        self.assertIn(PROFILES["transmigration"].description, shown)
        self.assertEqual(page.proper_name_mode.count(), 3)
        self.assertTrue(page.apply_context_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
