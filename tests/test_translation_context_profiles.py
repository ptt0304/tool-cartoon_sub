import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.translation.context_profiles import PROFILES
from cartoon_sub.translation.glossary import parse_glossary
from cartoon_sub.translation.pipeline import translation_fingerprint
from cartoon_sub.translation.prompts import (
    BASE_TRANSLATION_INSTRUCTION,
    CONTEXT_RULES,
    build_context_instruction,
    editorial,
    translation_prompt,
)
from cartoon_sub.translation.presets import STYLES
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
        self.assertIn("PRIORITY 1", instruction)
        self.assertIn("PRIORITY 2", instruction)
        self.assertLess(instruction.index("SPK_01 là hoàng đế."), instruction.index("Xuanyi => Huyền Thiên"))
        self.assertLess(instruction.index("Xuanyi => Huyền Thiên"), instruction.index("Quy tắc tên riêng"))
        self.assertIn("Chỉ đổi tên riêng theo mapping", instruction)
        self.assertIn("Primary genre", instruction)
        self.assertIn("Secondary genres", instruction)

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

    def test_full_context_workflow_state_survives_reopen(self):
        project = Project(
            "demo", "video.mp4",
            translation_genres=["cultivation", "ancient"],
            translation_preset="Natural Vietnamese",
            proper_name_mode="sino_vietnamese",
            translation_prompt="Không dùng mày/tao.",
            glossary={"顾沉": "Cố Trầm"},
            story_context={"summary": "Bản user đã duyệt"},
            context_proposal={"summary": "Candidate AI mới"},
            context_source_hash="source-approved",
            context_proposal_hash="source-candidate",
            context_approved_config_hash="config-approved",
            context_proposal_config_hash="config-candidate",
            context_status="proposal_ready",
        )
        with tempfile.TemporaryDirectory() as folder:
            ProjectManager().save(project, folder)
            loaded = ProjectManager().load(folder)
        self.assertEqual(loaded.translation_genres, ["cultivation", "ancient"])
        self.assertEqual(loaded.translation_preset, "Natural Vietnamese")
        self.assertEqual(loaded.proper_name_mode, "sino_vietnamese")
        self.assertEqual(loaded.glossary, {"顾沉": "Cố Trầm"})
        self.assertEqual(loaded.translation_prompt, "Không dùng mày/tao.")
        self.assertEqual(loaded.story_context["summary"], "Bản user đã duyệt")
        self.assertEqual(loaded.context_proposal["summary"], "Candidate AI mới")
        self.assertEqual(loaded.context_approved_config_hash, "config-approved")
        self.assertEqual(loaded.context_proposal_config_hash, "config-candidate")

    def test_old_project_genres_are_clamped_to_three(self):
        loaded = Project.from_dict({
            "name": "old", "source_video_path": "video.mp4", "schema_version": 3,
            "translation_genres": ["modern", "cultivation", "ancient", "comedy"],
        })
        self.assertEqual(loaded.translation_genres, ["modern", "cultivation", "ancient"])

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
        self.assertFalse(hasattr(page, "apply_context_button"))
        self.assertFalse(hasattr(page, "context_button"))
        self.assertTrue(page.analyze_button.isHidden())
        self.assertTrue(page.proposal_button.isHidden())
        self.assertIn("Thể loại chính",
                      [label.text() for label in page.findChildren(QLabel)])
        self.assertEqual(page.translate_button.text(), "Dịch")
        self.assertEqual(page.qa_button.text(), "QA/QC bản dịch")

    def test_ui_enforces_three_genres_and_describes_style_and_name_rule(self):
        page = build()
        for key in ("modern", "transmigration", "ancient", "cultivation"):
            page.genres[key].setChecked(True)
        self.assertEqual(sum(check.isChecked() for check in page.genres.values()), 3)
        self.assertFalse(page.genres["cultivation"].isChecked())
        self.assertIn("tối đa 3", page.genre_status.text())
        for index in range(page.preset.count()):
            page.preset.setCurrentIndex(index)
            self.assertEqual(page.style_description.text(), STYLES[page.preset.currentData()][1])
        page.proper_name_mode.setCurrentIndex(page.proper_name_mode.findData("preserve_source"))
        self.assertIn("Giữ nguyên tên", page.proper_name_description.text())

    def test_empty_user_context_is_omitted_and_legacy_context_is_not_in_prompt(self):
        project = Project("demo", "video.mp4", translation_genres=["cultivation"],
                          glossary={"顾沉": "Cố Trầm"})
        instruction = build_context_instruction(project)
        self.assertNotIn("PRIORITY 1", instruction)
        self.assertNotIn("None", instruction)
        self.assertNotIn("null", instruction)
        payload = editorial(project)
        self.assertNotIn("approved_context", payload)
        self.assertEqual(payload["user_defined_context"], "")

    def test_translation_prompt_is_natural_context_aware_and_id_scoped(self):
        project = Project(
            "demo", "video.mp4", translation_genres=["cultivation", "ancient"],
            translation_prompt="Không dùng mày/tao.", glossary={"顾沉": "Cố Trầm"},
            story_context={"summary": "顾沉 là sư huynh của SPK_02."},
        )
        prompt = translation_prompt(
            project,
            [{"id": 106, "speaker": "SPK_01", "zh": "你吃下它。"}],
            [{"id": 105, "speaker": "SPK_02", "zh": "这是灵石。"}],
            [{"id": 107, "speaker": "SPK_01", "zh": "也许能突破。"}],
            [{"id": 105, "vi": "Đây là linh thạch."}],
        )
        self.assertIn("Tiếng Việt phải tự nhiên", prompt)
        self.assertIn("không dịch từng chữ", prompt)
        self.assertIn("bê cấu trúc tiếng Trung", prompt)
        self.assertIn("Không dùng mày/tao.", prompt)
        self.assertIn("顾沉 => Cố Trầm", prompt)
        self.assertNotIn("顾沉 là sư huynh", prompt)
        self.assertIn('"reference_before": [{"id": 105', prompt)
        self.assertIn('"reference_after": [{"id": 107', prompt)
        self.assertIn("chỉ dịch targets", prompt)
        self.assertIn("mỗi ID đúng một lần", prompt)

    def test_context_analysis_rules_are_structured_and_do_not_translate(self):
        self.assertIn("transcript tiếng Trung thực tế", CONTEXT_RULES)
        self.assertIn("chưa dịch subtitle", CONTEXT_RULES)
        self.assertIn("Không thay đổi yêu cầu explicit", CONTEXT_RULES)
        self.assertIn("Kết quả phải dễ duyệt", CONTEXT_RULES)


if __name__ == "__main__":
    unittest.main()
