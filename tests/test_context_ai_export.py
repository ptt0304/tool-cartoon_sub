import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from cartoon_sub.speaker.service import approve_review
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_export import (
    build_context_ai_diagnostic,
    export_context_ai_json,
)
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.ui.context_dialog import ContextDialog


def context(setting, note):
    value = StoryContext(
        setting=setting,
        summary="Cô gái và con mèo đang trò chuyện.",
        narration="Lời thoại trực tiếp.",
        uncertainties=["Chưa chắc người nghe có xuất hiện trong khung hình."],
        characters=[{
            "source": "小主人", "target": "cô chủ nhỏ",
            "notes": "Tên gọi của con mèo dành cho cô gái.", "evidence_ids": [1],
        }],
        terms=[{
            "source": "姐姐", "target": "chị gái", "notes": "Cách gọi thân mật.",
            "evidence_ids": [1],
        }],
        address_rules=[{
            "speaker": "CHAR_CAT", "listener": "CHAR_GIRL", "self_term": "tôi",
            "address_term": "cô chủ nhỏ", "condition": "Khi con mèo gọi cô gái.",
            "evidence_ids": [1],
        }],
        character_profiles=[{
            "character_id": "CHAR_GIRL", "name": "cô gái", "role": "nhân vật chính",
            "gender_context": "female", "relationships": ["chủ của con mèo"],
            "visual_description": "Cô gái đứng cạnh bờ sông.",
            "associated_speakers": ["SPK_01"], "confidence": .9, "evidence_ids": [1],
        }],
        speaker_character_mappings=[{
            "spk_id": "SPK_01", "character_id": "CHAR_GIRL", "confidence": .9,
            "evidence_ids": [1], "notes": "Khớp lời thoại và hình ảnh.",
        }],
        visual_contexts=[{
            "id": 1, "scene_mode": "PRESENT",
            "speaker": {"spk_id": "SPK_01", "character_id": "CHAR_GIRL", "confidence": .9},
            "addressee": {"character_id": "CHAR_CAT", "confidence": .8},
            "visible_characters": [{
                "character_id": "CHAR_GIRL", "gender_context": "female", "confidence": .9,
            }],
            "referents": [{
                "source_expression": "小主人", "character_id": "CHAR_GIRL",
                "gender_context": "female", "confidence": .9,
            }],
            "visible_objects": [{
                "source_expression": "河", "description": "Dòng sông phía sau nhân vật.",
                "confidence": .8,
            }],
            "notes": note, "confidence": .9, "analysis_status": "ANALYZED",
        }],
    )
    return value.to_dict()


class ContextAIExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def project(self):
        project = Project(
            "Kiểm tra tiếng Việt", "video.mp4", metadata={"duration": 2.0},
            segments=[Utterance(
                1, 0, 2, "小主人，我们走吧", vi_subtitle="Cô chủ nhỏ, chúng ta đi thôi.",
                speaker_id="SPK_01", canonical_edit_source="manual_split",
                manual_split_parent_id=9,
            )],
        )
        approve_review(project)
        project.context_proposal = context("Cảnh ngoài trời bên bờ sông.", "Cô gái đang nói.")
        project.story_context = context("Bối cảnh đã được người dùng duyệt.", "Ghi chú đã duyệt.")
        project.context_status = project.visual_context_status = "proposal_ready"
        project.selected_models.update({
            "vision_speaker": "qwen/qwen3.7-flash", "vision_input_mode": "frames",
        })
        project.speaker_evidence = [{
            "utterance_id": 1, "source": "VISUAL", "speaker_id": "SPK_01",
            "character_id": "CHAR_GIRL", "confidence": .9, "reason": "Khớp hình ảnh.",
        }]
        project.speaker_proposals = {"utterances": [{
            "utterance_id": 1, "proposed_speaker_id": "SPK_01",
            "character_id": "CHAR_GIRL", "status": "PROPOSED",
        }]}
        return project

    def test_complete_export_has_every_review_section_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            diagnostic_dir = root / "cache" / "visual_context" / "diagnostics"
            diagnostic_dir.mkdir(parents=True)
            (diagnostic_dir / "failed.json").write_text(json.dumps({
                "status": "validation_failed", "model": "qwen/qwen3.7-flash",
                "requested_ids": [1], "returned_ids": [], "missing_ids": [1],
                "extra_ids": [], "duplicate_ids": [], "validation_reason": "missing",
                "Authorization": "Bearer SECRET", "api_key": "SECRET",
                "response": {"summary": "Phản hồi một phần"},
            }, ensure_ascii=False), encoding="utf-8")
            project = self.project()
            before = project.to_dict()
            payload = build_context_ai_diagnostic(project, root, [{
                "target_ids": [1], "window": [0, 2], "frames": 4, "cache": "MISS",
                "returned_ids": [1], "validation": "PASS",
            }])
            self.assertEqual(project.to_dict(), before)
            for key in (
                "export_meta", "project", "timeline", "analysis_status", "story_context",
                "characters", "terms", "addressing_rules", "relationships",
                "speaker_character_mapping", "visual_characters", "visual_context_by_id",
                "uncertainties", "speaker_evidence", "validation", "diagnostics",
            ):
                self.assertIn(key, payload)
            self.assertEqual(payload["timeline"]["utterances"][0]["source_text"], "小主人，我们走吧")
            self.assertEqual(payload["timeline"]["utterances"][0]["canonical_edit_source"], "manual_split")
            self.assertNotEqual(
                payload["story_context"]["candidate_story_context"]["setting"],
                payload["story_context"]["approved_story_context"]["setting"],
            )
            self.assertEqual(payload["visual_context_by_id"][0]["id"], 1)
            self.assertTrue(payload["diagnostics"]["visual_chunks"])
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("SECRET", encoded)
            self.assertNotIn("Authorization", encoded)

            destination = root / "context_ai.json"
            export_context_ai_json(payload, destination)
            raw = destination.read_text(encoding="utf-8")
            self.assertIn("Kiểm tra tiếng Việt", raw)
            self.assertIn("小主人，我们走吧", raw)
            self.assertIn("\n  \"export_meta\"", raw)

    def test_failed_partial_state_exports_machine_readable_blockers(self):
        with tempfile.TemporaryDirectory() as temp:
            project = self.project()
            project.context_proposal = {}
            project.context_status = project.visual_context_status = "VISUAL_CONTEXT_FAILED"
            project.visual_context_error = "Visual AI context thiếu target ID"
            project.speaker_review_hash = ""
            payload = build_context_ai_diagnostic(project, temp)
            self.assertFalse(payload["validation"]["success"])
            self.assertIn("SPEAKER_REVIEW_INCOMPLETE", payload["validation"]["blocking_reasons"])
            self.assertIn("VISUAL_CONTEXT_FAILED", payload["validation"]["blocking_reasons"])
            self.assertFalse(payload["analysis_status"]["candidate_available"])
            self.assertFalse(payload["diagnostics"]["raw_response_available"])

            dialog = ContextDialog(
                StoryContext().to_dict(), {1}, proposal=True, visual_ready=False,
                speaker_reviewed=False, diagnostic_payload=payload, export_directory=temp,
            )
            self.assertTrue(dialog.export_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertEqual(dialog.export_button.text(), "Export context_ai.json")
            destination = Path(temp) / "failed-context.json"
            with patch(
                "cartoon_sub.ui.context_dialog.QFileDialog.getSaveFileName",
                return_value=(str(destination), "JSON (*.json)"),
            ) as chooser, patch("cartoon_sub.ui.context_dialog.QMessageBox.information"):
                dialog.export_diagnostic()
            self.assertTrue(chooser.call_args.args[2].endswith("context_ai.json"))
            self.assertTrue(destination.is_file())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
