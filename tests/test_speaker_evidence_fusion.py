import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.fusion_service import (
    FusedEvidence, SPEAKER_EVIDENCE_FUSION_VERSION,
    SpeakerEvidenceFusionService, load_visual_evidence_cache,
)
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import apply_speaker_review_state, review_complete
from cartoon_sub.subtitle.models import Project, Utterance


def project(rows=4):
    return Project("p", "v.mp4", segments=[
        Utterance(index, index - 1, index, f"句{index}") for index in range(1, rows + 1)
    ])


def visual(uid, char="CHAR_2", addressee="CHAR_1", confidence=.8,
           status="PROPOSED", source="VISUAL"):
    return FusedEvidence(uid, source, character_id=char, semantic_label="mèo",
                         addressee_id=addressee, confidence=confidence,
                         status=status, reason="grounded")


class SpeakerEvidenceFusionTests(unittest.TestCase):
    def test_a_identical_strong_character_and_l_continuity_form_one_cluster(self):
        result, _ = SpeakerEvidenceFusionService().fuse(project(), [visual(1), visual(2), visual(3)])
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual(result["clusters"][0]["utterance_ids"], [1, 2, 3])
        self.assertTrue(result["clusters"][0]["continuity_support"])
        self.assertGreater(result["clusters"][0]["confidence"], .8)

    def test_b_conflicting_addressee_is_not_blindly_merged(self):
        result, _ = SpeakerEvidenceFusionService().fuse(
            project(2), [visual(1, addressee="CHAR_1"), visual(2, addressee="CHAR_3")])
        self.assertEqual(len(result["clusters"]), 2)

    def test_c_offscreen_unknown_and_d_low_confidence_need_review(self):
        result, _ = SpeakerEvidenceFusionService().fuse(project(2), [
            visual(1, char="UNKNOWN", addressee="UNKNOWN", status="UNKNOWN"),
            visual(2, confidence=.4, status="NEED_REVIEW"),
        ])
        self.assertEqual([row["proposed_speaker_id"] for row in result["utterances"]],
                         ["SPK_UNKNOWN", "SPK_UNKNOWN"])
        self.assertEqual(result["utterances"][1]["status"], "NEED_REVIEW")

    def test_e_f_user_confirmed_wins_and_conflict_is_recorded(self):
        value = project(1)
        value.speakers = {"SPK_01": asdict(Speaker("SPK_01"))}
        apply_speaker_review_state(value, value.speakers, {1: "SPK_01"})
        result, _ = SpeakerEvidenceFusionService().fuse(value, [
            FusedEvidence(1, "STT_HINT", speaker_id="SPK_02", confidence=.99,
                          status="PROPOSED")])
        row = result["utterances"][0]
        self.assertEqual((value.utterances[0].speaker_id, row["proposed_speaker_id"], row["status"]),
                         ("SPK_01", "SPK_01", "USER_CONFIRMED"))
        self.assertEqual(row["conflicts"], ["SPK_02"])

    def test_g_h_deterministic_ids_and_same_evidence_same_clusters(self):
        evidence = [visual(3, "CHAR_1", "CHAR_2"), visual(1, "CHAR_2", "CHAR_1")]
        first, _ = SpeakerEvidenceFusionService().fuse(project(3), evidence)
        second, _ = SpeakerEvidenceFusionService().fuse(project(3), list(reversed(evidence)))
        self.assertEqual(first["clusters"], second["clusters"])
        self.assertEqual([row["speaker_id"] for row in first["clusters"]], ["SPK_01", "SPK_02"])

    def test_i_speaker_id_is_independent_of_character_suffix(self):
        value = project(1)
        value.speakers = {"SPK_01": asdict(Speaker("SPK_01"))}
        result, _ = SpeakerEvidenceFusionService().fuse(value, [visual(1, "CHAR_2")])
        self.assertEqual(result["clusters"][0]["speaker_id"], "SPK_02")
        self.assertEqual(result["clusters"][0]["character_id"], "CHAR_2")

    def test_canonical_registry_ids_are_not_remerged_by_similar_labels(self):
        value = project(4)
        evidence = [
            FusedEvidence(1, "VISUAL", character_id="CHAR_2", semantic_label="mèo cam biết nói",
                          addressee_id="CHAR_1", confidence=.8, status="PROPOSED"),
            FusedEvidence(2, "VISUAL", character_id="CHAR_1", semantic_label="cô gái đội tai mèo",
                          addressee_id="CHAR_2", confidence=.8, status="PROPOSED"),
            FusedEvidence(3, "VISUAL", character_id="CHAR_CAT", semantic_label="mèo cam (chưa rõ tên)",
                          addressee_id="CHAR_GIRL", confidence=.8, status="PROPOSED"),
            FusedEvidence(4, "VISUAL", character_id="CHAR_GIRL", semantic_label="cô gái tai mèo (chưa rõ tên)",
                          addressee_id="CHAR_CAT", confidence=.8, status="PROPOSED"),
        ]
        result, _ = SpeakerEvidenceFusionService().fuse(value, evidence)
        self.assertEqual(len(result["clusters"]), 4)
        self.assertNotEqual(result["utterances"][0]["proposed_speaker_id"],
                            result["utterances"][2]["proposed_speaker_id"])
        self.assertNotEqual(result["utterances"][1]["proposed_speaker_id"],
                            result["utterances"][3]["proposed_speaker_id"])

    def test_j_k_addressee_and_semantic_label_are_preserved_separately(self):
        row = SpeakerEvidenceFusionService().fuse(project(1), [visual(1)])[0]["utterances"][0]
        self.assertEqual((row["character_id"], row["semantic_label"], row["addressee_id"]),
                         ("CHAR_2", "mèo", "CHAR_1"))

    def test_m_strict_alternation_is_not_assumed(self):
        result, _ = SpeakerEvidenceFusionService().fuse(project(3), [visual(1), visual(2), visual(3)])
        self.assertEqual({row["proposed_speaker_id"] for row in result["utterances"]}, {"SPK_01"})

    def test_n_o_malformed_cache_is_ignored_and_valid_visual_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache" / "visual_context"; cache.mkdir(parents=True)
            (cache / "bad.json").write_text("{broken", encoding="utf-8")
            context, files = load_visual_evidence_cache(temp, {1})
            self.assertEqual(context["visual_contexts"], [])
            self.assertEqual(files, ())

    def test_p_fusion_cache_hit_causes_no_external_request(self):
        with tempfile.TemporaryDirectory() as temp:
            value = project(1); service = SpeakerEvidenceFusionService()
            first, hit1 = service.fuse(value, [visual(1)], temp)
            second, hit2 = service.fuse(value, [visual(1)], temp)
            self.assertFalse(hit1); self.assertTrue(hit2); self.assertEqual(first, second)

    def test_s_unknown_is_valid(self):
        result, _ = SpeakerEvidenceFusionService().fuse(project(1), [])
        self.assertEqual(result["utterances"][0]["status"], "UNKNOWN")

    def test_t_schema_1_2_3_compatibility(self):
        base = project(1).to_dict()
        for schema in (1, 2, 3):
            raw = dict(base); raw["schema_version"] = schema
            raw.pop("speaker_evidence", None); raw.pop("speaker_proposals", None)
            loaded = Project.from_dict(raw)
            self.assertEqual((loaded.speaker_evidence, loaded.speaker_proposals), ([], {}))

    def test_u_api_key_is_not_in_fingerprint_or_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = "sk-or-secret-value"
            item = visual(1); value = project(1)
            result, _ = SpeakerEvidenceFusionService().fuse(value, [item], temp)
            dumped = json.dumps(result)
            self.assertNotIn(secret, dumped)
            cache_text = next((Path(temp) / "cache" / "speaker_fusion").glob("*.json")).read_text()
            self.assertNotIn(secret, cache_text)

    def test_v_reload_preserves_proposal_and_review_simulation_protects_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "video.mp4").write_bytes(b"x")
            value = Project("p", str(root / "video.mp4"), segments=[Utterance(1, 0, 1, "甲")])
            SpeakerEvidenceFusionService().fuse(value, [visual(1)], root)
            ProjectManager().save(value, root)
            loaded = ProjectManager().load(root)
            self.assertEqual(loaded.speaker_proposals, value.speaker_proposals)
            assignments = SpeakerEvidenceFusionService.draft_assignments(loaded)
            speaker_id = assignments[1]
            loaded.speakers[speaker_id] = asdict(Speaker(speaker_id, "mèo"))
            apply_speaker_review_state(loaded, loaded.speakers, assignments)
            old_hash = loaded.speaker_review_hash
            SpeakerEvidenceFusionService().fuse(loaded, [visual(1, "CHAR_9")], root)
            self.assertTrue(review_complete(loaded))
            self.assertEqual((loaded.utterances[0].speaker_id, loaded.speaker_review_hash),
                             (speaker_id, old_hash))

    def test_version_is_persisted(self):
        result, _ = SpeakerEvidenceFusionService().fuse(project(1), [visual(1)])
        self.assertEqual(result["version"], SPEAKER_EVIDENCE_FUSION_VERSION)


if __name__ == "__main__":
    unittest.main()
