import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import apply_speaker_review_state
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_service import ContextService
from cartoon_sub.translation.prompts import translation_prompt
from cartoon_sub.translation.qc import local_translation_qa
from cartoon_sub.translation.qc import qa_entry_is_current, store_qa_result
from cartoon_sub.translation.visual_context import VisualContextAnalyzer
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.ui.context_dialog import ContextDialog


def visual_row(uid, spk, speaker_char, addressee, referent=None, visible=None,
               scene="PRESENT", confidence=0.95, status="ANALYZED", notes=""):
    return {
        "id": uid,
        "scene_mode": scene,
        "speaker": {"spk_id": spk, "character_id": speaker_char, "confidence": confidence},
        "addressee": {"character_id": addressee, "confidence": confidence},
        "visible_characters": visible or [],
        "referents": referent or [],
        "visible_objects": [],
        "notes": notes,
        "confidence": confidence,
        "analysis_status": status,
    }


def context(rows):
    return StoryContext(visual_contexts=rows).to_dict()


class FakeVisualClient:
    def __init__(self, scenarios, low_first=None):
        self.scenarios = scenarios
        self.low_first = set(low_first or [])
        self.calls = []

    def generate_video_json(self, system, prompt, video_bytes, mime_type, schema, model,
                            cancel=None, progress=None):
        payload = json.loads(prompt)
        self.calls.append(payload)
        targeted = payload["analysis_pass"] == "targeted_rescan"
        rows = []
        for source in payload["target_transcript_rows"]:
            row = deepcopy(self.scenarios[source["id"]])
            if source["id"] in self.low_first and not targeted:
                row["confidence"] = 0.4
                row["analysis_status"] = "LOW_CONFIDENCE"
            rows.append(row)
        value = context(rows)
        candidates = {}
        for row in rows:
            identities = [
                (row["speaker"].get("character_id"), "speaker"),
                (row["addressee"].get("character_id"), "addressee"),
            ]
            identities.extend((item.get("character_id"), "visible")
                              for item in row.get("visible_characters", []))
            identities.extend((item.get("character_id"), None)
                              for item in row.get("referents", []))
            for temporary_id, role in identities:
                if not temporary_id or temporary_id == "UNKNOWN":
                    continue
                candidate = candidates.setdefault(temporary_id, {
                    "temporary_id": temporary_id,
                    "description": f"visual identity {temporary_id}",
                    "aliases": [temporary_id], "gender_context": "unknown",
                    "confidence": .95, "uncertain": False, "evidence_ids": [], "bindings": [],
                })
                if row["id"] not in candidate["evidence_ids"]:
                    candidate["evidence_ids"].append(row["id"])
                if role in {"speaker", "addressee"}:
                    candidate["bindings"].append({"id": row["id"], "role": role})
        value["new_character_candidates"] = list(candidates.values())
        return value


class FakeProxyAnalyzer(VisualContextAnalyzer):
    def _proxy(self, source, start, end, fps, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"proxy_{start:.0f}_{end:.0f}_{fps}.mp4"
        path.write_bytes(b"fake-video")
        return path


def make_project(video, rows):
    speakers = {row.speaker_id: asdict(Speaker(row.speaker_id, row.speaker_id)) for row in rows}
    return Project("visual", str(video), metadata={"duration": max(row.end for row in rows)},
                   segments=rows, speakers=speakers)


class VisualTranslationContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_flashback_two_men_keep_female_visible_as_referent_not_speaker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "video.mp4"; video.write_bytes(b"source")
            rows = [
                Utterance(1, 10.0, 12.0, "你还记得她吗？", speaker_id="SPK_01"),
                Utterance(2, 12.1, 14.0, "我以为她已经死了。", speaker_id="SPK_02"),
            ]
            female = [{"character_id": "CHAR_C", "gender_context": "female", "confidence": 0.98}]
            scenarios = {
                1: visual_row(1, "SPK_01", "CHAR_A", "CHAR_B",
                    [{"source_expression": "她", "character_id": "CHAR_C", "gender_context": "female", "confidence": 0.98}],
                    female, "FLASHBACK", notes="Two men talk while female memory is shown."),
                2: visual_row(2, "SPK_02", "CHAR_B", "CHAR_A",
                    [{"source_expression": "她", "character_id": "CHAR_C", "gender_context": "female", "confidence": 0.98}],
                    female, "FLASHBACK"),
            }
            client = FakeVisualClient(scenarios)
            result = FakeProxyAnalyzer(client, "model").analyze(make_project(video, rows), root)
            by_id = {row["id"]: row for row in result["visual_contexts"]}
            self.assertEqual(by_id[1]["speaker"]["character_id"], "CHAR_01")
            self.assertEqual(by_id[2]["speaker"]["character_id"], "CHAR_02")
            self.assertTrue(all(row["visible_characters"][0]["character_id"] == "CHAR_03" for row in by_id.values()))
            self.assertTrue(all(row["scene_mode"] == "FLASHBACK" for row in by_id.values()))

    def test_listener_camera_offscreen_and_narrator_remain_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "v.mp4"; video.write_bytes(b"source")
            rows = [
                Utterance(1, 0, 2, "你听我说。", speaker_id="SPK_01"),
                Utterance(2, 2, 4, "门外传来声音。", speaker_id="SPK_02"),
                Utterance(3, 4, 6, "多年以后，他们再次相见。", speaker_id="SPK_03"),
            ]
            scenarios = {
                1: visual_row(1, "SPK_01", "CHAR_A", "CHAR_B", visible=[{"character_id": "CHAR_B", "gender_context": "female", "confidence": 0.96}], notes="Camera is on listener B."),
                2: visual_row(2, "SPK_02", "CHAR_D", "", visible=[{"character_id": "CHAR_B", "gender_context": "female", "confidence": 0.9}], notes="CHAR_D speaks offscreen."),
                3: visual_row(3, "SPK_03", "NARRATOR", "", visible=[{"character_id": "CHAR_A", "gender_context": "male", "confidence": 0.9}], scene="NARRATION_VISUAL"),
            }
            result = FakeProxyAnalyzer(FakeVisualClient(scenarios), "model").analyze(make_project(video, rows), root)
            values = {row["id"]: row for row in result["visual_contexts"]}
            self.assertEqual(values[1]["speaker"]["character_id"], "CHAR_01")
            self.assertEqual(values[2]["speaker"]["character_id"], "CHAR_03")
            self.assertEqual(values[3]["speaker"]["character_id"], "CHAR_04")
            self.assertEqual(values[3]["scene_mode"], "NARRATION_VISUAL")

    def test_adaptive_rescan_only_low_confidence_and_cache_reuses_both_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "v.mp4"; video.write_bytes(b"source")
            rows = [Utterance(1, 5, 7, "他来了。", speaker_id="SPK_01")]
            scenarios = {1: visual_row(1, "SPK_01", "CHAR_A", "", scene="PRESENT")}
            client = FakeVisualClient(scenarios, low_first={1})
            analyzer = FakeProxyAnalyzer(client, "model")
            first = analyzer.analyze(make_project(video, rows), root)
            self.assertEqual([call["analysis_pass"] for call in client.calls], ["scene_chunk", "targeted_rescan"])
            self.assertEqual(first["visual_contexts"][0]["confidence"], 0.95)
            analyzer.analyze(make_project(video, rows), root)
            self.assertEqual(len(client.calls), 2)

    def test_model_approved_status_is_normalized_to_analyzed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "v.mp4"; video.write_bytes(b"source")
            rows = [Utterance(1, 1, 2, "他说。", speaker_id="SPK_01")]
            scenario = visual_row(1, "SPK_01", "CHAR_A", "")
            scenario["analysis_status"] = "approved"
            result = FakeProxyAnalyzer(FakeVisualClient({1: scenario}), "model").analyze(
                make_project(video, rows), root,
            )
            self.assertEqual(result["visual_contexts"][0]["analysis_status"], "ANALYZED")

    def test_cache_is_partial_per_bounded_target_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "v.mp4"; video.write_bytes(b"source")
            rows = [Utterance(index, index, index + .5, f"句{index}", speaker_id="SPK_01")
                    for index in range(1, 18)]
            scenarios = {row.id: visual_row(row.id, "SPK_01", "CHAR_A", "") for row in rows}
            client = FakeVisualClient(scenarios)
            FakeProxyAnalyzer(client, "model").analyze(make_project(video, rows), root)
            self.assertEqual(len(client.calls), 2)
            rows[-1].zh = "末句已修改"
            FakeProxyAnalyzer(client, "model").analyze(make_project(video, rows), root)
            # ID 17 is both the second chunk target and forward dialogue
            # context for chunk 1, so both affected cache entries refresh.
            self.assertEqual(len(client.calls), 4)
            self.assertEqual(client.calls[-1]["target_transcript_rows"][0]["id"], 17)

    def test_qa_no_longer_depends_on_legacy_visual_referent(self):
        row = Utterance(1, 0, 2, "他来了。", vi_subtitle="Hắn đến rồi.", speaker_id="SPK_01")
        visual = visual_row(1, "SPK_01", "CHAR_A", "", [
            {"source_expression": "他", "character_id": "CHAR_C", "gender_context": "female", "confidence": 0.97}
        ])
        p = make_project(Path("missing.mp4"), [row])
        p.story_context = context([visual])
        p.visual_context_status = "applied"
        issues = local_translation_qa(p, row)["issues"]
        self.assertNotIn("PRONOUN_CONTEXT_MISMATCH", {item["type"] for item in issues})

    def test_unknown_visual_context_never_defaults_male(self):
        row = visual_row(1, "SPK_01", "", "", confidence=0.4, status="LOW_CONFIDENCE")
        self.assertEqual(row["speaker"]["character_id"], "")
        self.assertNotIn("gender", row["speaker"])

    def test_legacy_visual_context_change_does_not_invalidate_translation_qa(self):
        segment = Utterance(1, 0, 2, "他来了。", vi_subtitle="Người đó đến rồi.", speaker_id="SPK_01")
        project = make_project(Path("missing.mp4"), [segment])
        project.story_context = context([visual_row(1, "SPK_01", "CHAR_A", "")])
        project.visual_context_status = "applied"
        entry = store_qa_result(project, segment, "PASS")
        self.assertTrue(qa_entry_is_current(segment, entry, project))
        project.story_context["visual_contexts"][0]["scene_mode"] = "FLASHBACK"
        self.assertTrue(qa_entry_is_current(segment, entry, project))

    def test_translation_prompt_prioritizes_user_and_ignores_legacy_visual_rows(self):
        rows = [Utterance(1, 0, 1, "他", speaker_id="SPK_01"), Utterance(2, 1, 2, "她", speaker_id="SPK_02")]
        p = make_project(Path("missing.mp4"), rows)
        p.translation_prompt = "SPK_01 là A; ưu tiên cách xưng hô do user duyệt."
        p.story_context = context([visual_row(1, "SPK_01", "CHAR_A", "") , visual_row(2, "SPK_02", "CHAR_B", "")])
        text = translation_prompt(p, [{"id": 1}], [], [], [])
        payload = json.loads(text[text.index("{"):])
        self.assertIn("PRIORITY 1", payload["editorial"]["context_instruction"])
        self.assertNotIn("approved_context", payload["editorial"])

    def test_visual_failure_is_explicit_and_never_creates_text_only_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"source")
            project = make_project(video, [Utterance(1, 0, 1, "你好", speaker_id="SPK_01")])
            config = Mock()
            config.load.return_value = AISettings(default_ai_model="vendor/vision")
            config.openrouter_catalog_cache.return_value = {"models": [{
                "id": "vendor/vision",
                "architecture": {"input_modalities": ["text", "image"],
                                 "output_modalities": ["text"]},
            }]}
            config.openrouter_key_pool.return_value = Mock()
            client = Mock()
            factory = Mock(return_value=client)
            with patch("cartoon_sub.translation.context_service.VisualContextAnalyzer.analyze",
                       side_effect=ValueError("response parse lỗi")):
                with self.assertRaisesRegex(ValueError, "chưa đối chiếu được video"):
                    ContextService(config, openrouter_factory=factory).analyze(project, root)
            saved = ProjectManager().load(root)
            self.assertEqual([(row.id, row.zh, row.start, row.end) for row in saved.utterances],
                             [(1, "你好", 0.0, 1.0)])
            self.assertEqual(saved.visual_context_status, "VISUAL_CONTEXT_FAILED")
            self.assertEqual(saved.context_status, "VISUAL_CONTEXT_FAILED")
            self.assertEqual(saved.context_proposal, {})
            self.assertIn("response parse lỗi", saved.visual_context_error)

    def test_missing_video_and_non_vision_model_fail_before_gemini_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = make_project(root / "missing.mp4", [Utterance(1, 0, 1, "你好", speaker_id="SPK_01")])
            config = Mock()
            config.load.return_value = AISettings(default_ai_model="vendor/vision")
            config.openrouter_catalog_cache.return_value = {"models": [{
                "id": "vendor/vision",
                "architecture": {"input_modalities": ["text", "image"],
                                 "output_modalities": ["text"]},
            }]}
            factory = Mock()
            with self.assertRaisesRegex(ValueError, "Không tìm thấy video"):
                ContextService(config, factory).analyze(project, root)
            factory.assert_not_called()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"source")
            project = make_project(video, [Utterance(1, 0, 1, "你好", speaker_id="SPK_01")])
            config = Mock()
            config.load.return_value = AISettings(default_ai_model="vendor/transcribe")
            config.openrouter_catalog_cache.return_value = {"models": [{
                "id": "vendor/transcribe",
                "architecture": {"input_modalities": ["audio"],
                                 "output_modalities": ["transcription"]},
            }]}
            factory = Mock()
            with self.assertRaisesRegex(ValueError, "không hỗ trợ hình ảnh"):
                ContextService(config, factory).analyze(project, root)
            factory.assert_not_called()

    def test_review_dialog_invalid_json_is_visible_and_valid_save_persists(self):
        row = Utterance(1, 0, 1, "他", speaker_id="SPK_01")
        visual = visual_row(1, "SPK_01", "CHAR_A", "")
        candidate = context([visual])
        candidate["character_profiles"] = [{
            "character_id": "CHAR_A", "name": "A", "role": "lead", "gender_context": "male",
            "relationships": [], "visual_description": "áo xanh", "associated_speakers": ["SPK_01"],
            "confidence": 0.9, "evidence_ids": [1],
        }]
        dialog = ContextDialog(candidate, {1}, proposal=True, visual_ready=True)
        self.assertEqual(dialog.apply_button.text(), "Phê duyệt & lưu ngữ cảnh")
        self.assertEqual(dialog.tables["character_profiles"][0].item(0, 7).text(), "0.9")
        dialog.visual_json.setPlainText("[{broken]")
        with patch("cartoon_sub.ui.context_dialog.QMessageBox.warning") as warning:
            dialog.apply_button.click()
            self.application.processEvents()
        self.assertTrue(warning.called)
        self.assertIn("dòng 1", warning.call_args.args[2])
        self.assertIsNone(dialog.result_context)

        dialog.visual_json.setPlainText(json.dumps([visual], ensure_ascii=False))
        dialog.apply_button.click()
        self.application.processEvents()
        self.assertIsNotNone(dialog.result_context)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"source")
            project = make_project(video, [row])
            project.visual_context_status = "proposal_ready"
            apply_speaker_review_state(project, project.speakers, {1: "SPK_01"})
            ProjectManager().save(project, root)
            controller = Controller()
            controller.accept((project, root))
            controller.apply_context(dialog.result_context)
            reopened = ProjectManager().load(root)
            self.assertEqual(reopened.visual_context_status, "applied")
            self.assertEqual(reopened.story_context["visual_contexts"][0]["id"], 1)

    def test_all_review_tables_serialize_and_localize_without_changing_schema(self):
        visual = visual_row(1, "SPK_01", "CHAR_01", "", status="NEED_REVIEW")
        candidate = context([visual])
        candidate.update({
            "characters": [{"source": "妖女", "target": "CHAR_03", "notes": "Nữ yêu trong cảnh", "evidence_ids": [1]}],
            "terms": [{"source": "中品灵石", "target": "TERM_01", "notes": "Thuật ngữ tu luyện", "evidence_ids": [1]}],
            "address_rules": [{"speaker": "SPK_01", "listener": "CHAR_03", "self_term": "ta",
                               "address_term": "nàng", "condition": "Khi đối thoại trực tiếp", "evidence_ids": [1]}],
            "character_profiles": [{"character_id": "CHAR_01", "name": "", "role": "Nhân vật chính",
                                    "gender_context": "male", "relationships": ["Đối thủ CHAR_03"],
                                    "visual_description": "Nam tu mặc áo tối màu", "associated_speakers": ["SPK_01"],
                                    "confidence": 0.8, "evidence_ids": [1]}],
            "speaker_character_mappings": [{"spk_id": "SPK_01", "character_id": "CHAR_01",
                                            "confidence": 0.8, "evidence_ids": [1], "notes": "Khớp khẩu hình"}],
        })
        dialog = ContextDialog(candidate, {1}, proposal=True, visual_ready=True)
        self.assertEqual(dialog.tables["characters"][0].item(0, 1).text(), "Chưa xác định")
        self.assertEqual(dialog.tables["terms"][0].item(0, 1).text(), "Chưa xác định")
        self.assertEqual(dialog.tables["character_profiles"][0].item(0, 3).text(), "Nam")
        saved = dialog.values()
        self.assertEqual(saved["character_profiles"][0]["gender_context"], "male")
        self.assertEqual(saved["visual_contexts"][0]["analysis_status"], "NEED_REVIEW")
        self.assertEqual(saved["characters"][0]["source"], "妖女")

    def test_visual_prompt_requires_vietnamese_review_text_and_preserves_machine_fields(self):
        from cartoon_sub.translation.visual_context import VISUAL_CONTEXT_SYSTEM
        self.assertIn("natural Vietnamese", VISUAL_CONTEXT_SYSTEM)
        self.assertIn("Keep Chinese source names and terms unchanged", VISUAL_CONTEXT_SYSTEM)
        self.assertIn("Keep machine schema keys", VISUAL_CONTEXT_SYSTEM)

    def test_all_review_statuses_can_be_saved(self):
        for status in ("ANALYZED", "LOW_CONFIDENCE", "NEED_REVIEW"):
            with self.subTest(status=status):
                candidate = context([visual_row(1, "SPK_01", "", "", status=status)])
                dialog = ContextDialog(candidate, {1}, proposal=True, visual_ready=True)
                self.assertEqual(dialog.values()["visual_contexts"][0]["analysis_status"], status)


if __name__ == "__main__":
    unittest.main()
