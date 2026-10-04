import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.speaker.service import approve_review
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.character_registry import CharacterRegistry, UNKNOWN_CHARACTER
from cartoon_sub.translation.context_export import build_context_ai_diagnostic
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.visual_context import VisualContextAnalyzer, validate_merged_visual_contexts


def candidate(temporary_id, description, aliases, evidence_ids, bindings, confidence=.9,
              uncertain=False, gender="unknown"):
    return {
        "temporary_id": temporary_id, "description": description, "aliases": aliases,
        "gender_context": gender, "confidence": confidence, "uncertain": uncertain,
        "evidence_ids": evidence_ids, "bindings": bindings,
    }


def visual(uid, character_id, spk="SPK_01", addressee="UNKNOWN", visible=None):
    return {
        "id": uid, "scene_mode": "PRESENT",
        "speaker": {"spk_id": spk, "character_id": character_id, "confidence": .9},
        "addressee": {"character_id": addressee, "confidence": .8},
        "visible_characters": visible or [], "referents": [], "visible_objects": [],
        "notes": "Bằng chứng hình ảnh nhất quán.", "confidence": .9,
        "analysis_status": "ANALYZED",
    }


def response(rows, candidates=()):
    value = StoryContext(visual_contexts=rows).to_dict()
    value["new_character_candidates"] = list(candidates)
    return value


class QueueClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.last_http_status = 200

    def generate_video_json(self, _system, prompt, *_args, **_kwargs):
        self.calls.append(json.loads(prompt))
        return self.payload


class FakeAnalyzer(VisualContextAnalyzer):
    def _proxy(self, source, start, end, fps, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{start}-{end}.mp4"
        path.write_bytes(b"video")
        return path


class VisualCharacterRegistryTests(unittest.TestCase):
    def test_same_girl_across_three_windows_has_one_canonical_id(self):
        registry = CharacterRegistry()
        ids = [registry.reconcile_candidate(candidate(
            label, "cô gái tóc đen mặc áo xanh", ["cô gái", alias], [index],
            [{"id": index, "role": "speaker"}], gender="female",
        )) for index, (label, alias) in enumerate((
            ("CHAR_GIRL", "小主人"), ("CHAR_1", "cô chủ nhỏ"), ("GIRL_MAIN", "thiếu nữ")
        ), 1)]
        self.assertEqual(ids, ["CHAR_01", "CHAR_01", "CHAR_01"])
        self.assertEqual(len(registry.entries), 1)

    def test_same_cat_different_labels_merges_only_with_visual_evidence(self):
        registry = CharacterRegistry()
        first = registry.reconcile_candidate(candidate(
            "CHAR_CAT", "mèo cam nhỏ đeo vòng cổ đỏ", ["mèo"], [1],
            [{"id": 1, "role": "speaker"}],
        ))
        second = registry.reconcile_candidate(candidate(
            "CAT_ORANGE", "mèo cam nhỏ đeo vòng cổ đỏ", ["臭猫"], [8],
            [{"id": 8, "role": "speaker"}],
        ))
        self.assertEqual((first, second), ("CHAR_01", "CHAR_01"))

    def test_new_and_uncertain_characters_are_conservative(self):
        registry = CharacterRegistry()
        known = registry.reconcile_candidate(candidate(
            "GIRL", "cô gái mặc áo xanh", ["cô gái"], [1],
            [{"id": 1, "role": "speaker"}],
        ))
        new = registry.reconcile_candidate(candidate(
            "SISTER", "người phụ nữ lớn tuổi mặc áo đỏ", ["姐姐"], [9],
            [{"id": 9, "role": "visible"}],
        ))
        uncertain = registry.reconcile_candidate(candidate(
            "SHADOW", "bóng người ở rất xa", [], [10],
            [{"id": 10, "role": "visible"}], confidence=.4, uncertain=True,
        ))
        self.assertEqual((known, new, uncertain), ("CHAR_01", "CHAR_02", UNKNOWN_CHARACTER))

    def test_window_cannot_invent_id_and_all_references_use_registry(self):
        registry = CharacterRegistry()
        payload = response([
            visual(1, "CHAR_GIRL", addressee="CAT_ORANGE", visible=[{
                "character_id": "CHAR_GIRL", "gender_context": "female", "confidence": .9,
            }]),
            visual(2, "UNDECLARED_MODEL_ID"),
        ], [
            candidate("CHAR_GIRL", "cô gái mặc áo xanh", ["cô gái"], [1],
                      [{"id": 1, "role": "speaker"}], gender="female"),
            candidate("CAT_ORANGE", "mèo cam đeo vòng đỏ", ["mèo cam"], [1],
                      [{"id": 1, "role": "addressee"}]),
        ])
        value = registry.reconcile_response(payload, {1, 2})
        first, second = value["visual_contexts"]
        self.assertEqual(first["speaker"]["character_id"], "CHAR_01")
        self.assertEqual(first["addressee"]["character_id"], "CHAR_02")
        self.assertEqual(first["visible_characters"][0]["character_id"], "CHAR_01")
        self.assertEqual(second["speaker"]["character_id"], UNKNOWN_CHARACTER)
        self.assertEqual(second["analysis_status"], "NEED_REVIEW")

    def test_user_confirmed_speaker_wins_and_timeline_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "video.mp4"; video.write_bytes(b"video")
            row = Utterance(1, 1.25, 3.5, "小主人", speaker_id="SPK_01")
            project = Project("p", str(video), segments=[row])
            approve_review(project)
            before = [(item.id, item.start, item.end, item.zh) for item in project.utterances]
            payload = response([visual(1, "TEMP_GIRL", spk="SPK_99")], [
                candidate("TEMP_GIRL", "cô gái mặc áo xanh", ["小主人"], [1],
                          [{"id": 1, "role": "speaker"}], gender="female"),
            ])
            result = FakeAnalyzer(QueueClient(payload), "model").analyze(project, root)
            visual_row = result["visual_contexts"][0]
            self.assertEqual(visual_row["speaker"]["spk_id"], "SPK_01")
            self.assertEqual(visual_row["analysis_status"], "NEED_REVIEW")
            self.assertIn("USER_CONFIRMED", visual_row["notes"])
            self.assertEqual([(item.id, item.start, item.end, item.zh)
                              for item in project.utterances], before)

    def test_recovered_failure_uses_final_coverage_but_keeps_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = Project("p", "video.mp4", segments=[
                Utterance(1, 0, 1, "甲"), Utterance(2, 1, 2, "乙")])
            approve_review(project)
            project.context_proposal = StoryContext(visual_contexts=[
                visual(1, "CHAR_01"), visual(2, "CHAR_01")]).to_dict()
            project.context_status = project.visual_context_status = "proposal_ready"
            diagnostics = root / "cache" / "visual_context" / "diagnostics"
            diagnostics.mkdir(parents=True)
            (diagnostics / "old-failure.json").write_text(json.dumps({
                "status": "validation_failed", "requested_ids": [1, 2],
                "returned_ids": [1], "missing_ids": [2], "extra_ids": [],
                "duplicate_ids": [], "validation_reason": "missing target",
            }), encoding="utf-8")
            exported = build_context_ai_diagnostic(project, root)
            self.assertTrue(exported["validation"]["visual_context_valid"])
            self.assertTrue(exported["diagnostics"]["recovered"])
            self.assertEqual(exported["diagnostics"]["failed_attempts"], 1)

    def test_missing_final_target_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing=\[2\]"):
            validate_merged_visual_contexts([{"id": 1}], [1, 2])


if __name__ == "__main__":
    unittest.main()
