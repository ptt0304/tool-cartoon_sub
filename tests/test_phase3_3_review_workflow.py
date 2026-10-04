import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.fusion_service import FusedEvidence, SpeakerEvidenceFusionService
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import apply_speaker_review_state, refresh_timeline, review_complete
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_service import translation_readiness
from cartoon_sub.ui.speaker_dialog import SpeakerDialog


def visual(uid, speaker="SPK_UNKNOWN", character="CHAR_1", addressee="CHAR_2"):
    return {
        "id": uid, "scene_mode": "PRESENT",
        "speaker": {"spk_id": speaker, "character_id": character, "confidence": .8},
        "addressee": {"character_id": addressee, "confidence": .8},
        "visible_characters": [], "referents": [], "visible_objects": [],
        "notes": "evidence", "confidence": .8, "analysis_status": "ANALYZED",
    }


def context_for(project, label="girl"):
    return StoryContext(
        summary=label,
        character_profiles=[{
            "character_id": "CHAR_1", "name": label, "role": "lead",
            "gender_context": "unknown", "relationships": ["nói với CHAR_2"],
            "visual_description": "", "associated_speakers": [],
            "confidence": .8, "evidence_ids": [row.id for row in project.utterances],
        }],
        visual_contexts=[visual(row.id) for row in project.utterances],
    ).to_dict()


def sample(rows=3):
    value = Project("p", "video.mp4", segments=[
        Utterance(index, index - 1, index, f"句{index}") for index in range(1, rows + 1)
    ])
    refresh_timeline(value)
    return value


class Phase33ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_cluster_level_confirmation_is_working_state_until_final_commit(self):
        value = sample()
        evidence = [FusedEvidence(index, "VISUAL", character_id="CHAR_2",
                                  semantic_label="orange cat", addressee_id="CHAR_1",
                                  confidence=.8, status="PROPOSED") for index in (1, 2)]
        SpeakerEvidenceFusionService().fuse(value, evidence)
        dialog = SpeakerDialog(value, ".")
        dialog.cluster_table.selectRow(0)
        dialog.apply_selected_cluster()
        self.assertEqual([dialog.assignments[i] for i in (1, 2)], ["SPK_01", "SPK_01"])
        self.assertEqual(value.utterances[0].speaker_id, "SPK_UNKNOWN")
        dialog.reject()

    def test_b_c_individual_assignment_and_reviewed_unknown_are_accepted(self):
        value = sample(2)
        value.speakers["SPK_01"] = asdict(Speaker("SPK_01"))
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01", 2: "SPK_UNKNOWN"})
        self.assertTrue(review_complete(value))
        self.assertEqual(value.utterances[1].speaker_id, "SPK_UNKNOWN")

    def test_d_f_review_signature_persists_and_changes_with_assignment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "video.mp4").write_bytes(b"video")
            value = sample(1); value.source_video_path = str(root / "video.mp4")
            apply_speaker_review_state(value, value.speakers, {1: "SPK_UNKNOWN"})
            first = value.speaker_review_hash
            ProjectManager().save(value, root)
            loaded = ProjectManager().load(root)
            self.assertTrue(review_complete(loaded))
            loaded.speakers["SPK_01"] = asdict(Speaker("SPK_01"))
            apply_speaker_review_state(loaded, loaded.speakers, {1: "SPK_01"})
            self.assertNotEqual(first, loaded.speaker_review_hash)

    def test_e_user_confirmed_survives_new_visual_evidence(self):
        value = sample(1); value.speakers["SPK_01"] = asdict(Speaker("SPK_01"))
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01"})
        SpeakerEvidenceFusionService().fuse(value, [FusedEvidence(
            1, "VISUAL", speaker_id="SPK_02", character_id="CHAR_9",
            confidence=.99, status="PROPOSED")])
        self.assertEqual(value.utterances[0].speaker_id, "SPK_01")

    def test_g_unchanged_review_does_not_invalidate_downstream(self):
        value = sample(1); value.speakers["SPK_01"] = asdict(Speaker("SPK_01"))
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01"})
        value.translation_status = "completed"; value.final_audio_status = "generated"
        apply_speaker_review_state(value, deepcopy(value.speakers), {1: "SPK_01"})
        self.assertEqual((value.translation_status, value.final_audio_status),
                         ("completed", "generated"))

    def test_h_changed_speaker_invalidates_only_dependents(self):
        value = sample(1)
        value.speakers.update({key: asdict(Speaker(key)) for key in ("SPK_01", "SPK_02")})
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01"})
        value.context_source_hash = "approved-old"; value.context_status = "applied"
        value.translation_status = "completed"; value.translation_qa = {"1": {"status": "PASS"}}
        row = value.utterances[0]; row.dubbing_optimized = True; row.dubbing_status = "completed"
        row.tts_generation_status = "generated"; value.final_audio_status = "generated"
        apply_speaker_review_state(value, value.speakers, {1: "SPK_02"})
        self.assertEqual((value.context_status, value.translation_status), ("stale", "stale"))
        self.assertEqual(value.translation_qa, {})
        self.assertEqual((row.dubbing_status, row.tts_generation_status, value.final_audio_status),
                         ("stale", "stale", "stale"))

    def test_i_j_k_l_candidate_approve_edit_and_context_invalidation(self):
        value = sample(1)
        apply_speaker_review_state(value, value.speakers, {1: "SPK_UNKNOWN"})
        candidate = context_for(value, "girl")
        value.context_proposal = candidate; value.visual_context_status = "proposal_ready"
        self.assertNotEqual(value.context_proposal, value.story_context)
        controller = Controller.__new__(Controller)
        controller.project = value; controller.directory = Path("."); controller.save = Mock()
        controller.apply_context(candidate)
        self.assertEqual(value.context_status, "applied")
        value.translation_status = "completed"; value.translation_qa = {"1": {}}
        edited = context_for(value, "young girl")
        value.visual_context_status = "applied"
        controller.apply_context(edited)
        self.assertEqual(value.translation_status, "stale")
        self.assertEqual(value.translation_qa, {})

    def test_m_n_o_legacy_reviews_do_not_gate_translation(self):
        value = sample(1)
        ready, message = translation_readiness(value)
        self.assertTrue(ready); self.assertIn("Transcript sẵn sàng", message)
        apply_speaker_review_state(value, value.speakers, {1: "SPK_UNKNOWN"})
        ready, message = translation_readiness(value)
        self.assertTrue(ready)
        candidate = context_for(value)
        value.context_proposal = candidate; value.visual_context_status = "proposal_ready"
        controller = Controller.__new__(Controller)
        controller.project = value; controller.directory = Path("."); controller.save = Mock()
        controller.apply_context(candidate)
        self.assertTrue(translation_readiness(value)[0])

    def test_p_q_spk_character_addressee_remain_separate(self):
        value = sample(1)
        result, _ = SpeakerEvidenceFusionService().fuse(value, [FusedEvidence(
            1, "VISUAL", character_id="CHAR_2", semantic_label="orange cat",
            addressee_id="CHAR_1", confidence=.8, status="PROPOSED")])
        row = result["utterances"][0]
        self.assertEqual((row["proposed_speaker_id"], row["character_id"], row["addressee_id"]),
                         ("SPK_01", "CHAR_2", "CHAR_1"))

    def test_r_reopen_review_preserves_authoritative_assignment(self):
        value = sample(1); value.speakers["SPK_01"] = asdict(Speaker("SPK_01", "Cat"))
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01"})
        dialog = SpeakerDialog(value, ".")
        self.assertEqual(dialog.assignments[1], "SPK_01")
        self.assertEqual(dialog.speakers["SPK_01"]["name"], "Cat")
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
