import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.prompts import translation_prompt
from cartoon_sub.translation.qc import local_translation_qa
from cartoon_sub.translation.qc import qa_entry_is_current, store_qa_result
from cartoon_sub.translation.visual_context import VisualContextAnalyzer


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
            row = dict(self.scenarios[source["id"]])
            if source["id"] in self.low_first and not targeted:
                row["confidence"] = 0.4
                row["analysis_status"] = "LOW_CONFIDENCE"
            rows.append(row)
        return context(rows)


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
            self.assertEqual(by_id[1]["speaker"]["character_id"], "CHAR_A")
            self.assertEqual(by_id[2]["speaker"]["character_id"], "CHAR_B")
            self.assertTrue(all(row["visible_characters"][0]["character_id"] == "CHAR_C" for row in by_id.values()))
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
            self.assertEqual(values[1]["speaker"]["character_id"], "CHAR_A")
            self.assertEqual(values[2]["speaker"]["character_id"], "CHAR_D")
            self.assertEqual(values[3]["speaker"]["character_id"], "NARRATOR")
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

    def test_cache_is_partial_per_60_second_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "v.mp4"; video.write_bytes(b"source")
            rows = [Utterance(1, 5, 7, "甲", speaker_id="SPK_01"),
                    Utterance(2, 65, 67, "乙", speaker_id="SPK_02")]
            scenarios = {1: visual_row(1, "SPK_01", "CHAR_A", ""), 2: visual_row(2, "SPK_02", "CHAR_B", "")}
            client = FakeVisualClient(scenarios)
            FakeProxyAnalyzer(client, "model").analyze(make_project(video, rows), root)
            self.assertEqual(len(client.calls), 2)
            rows[1].zh = "乙已修改"
            FakeProxyAnalyzer(client, "model").analyze(make_project(video, rows), root)
            self.assertEqual(len(client.calls), 3)
            self.assertEqual(client.calls[-1]["target_transcript_rows"][0]["id"], 2)

    def test_qa_detects_female_referent_translated_with_male_pronoun(self):
        row = Utterance(1, 0, 2, "他来了。", vi_subtitle="Hắn đến rồi.", speaker_id="SPK_01")
        visual = visual_row(1, "SPK_01", "CHAR_A", "", [
            {"source_expression": "他", "character_id": "CHAR_C", "gender_context": "female", "confidence": 0.97}
        ])
        p = make_project(Path("missing.mp4"), [row])
        p.story_context = context([visual])
        p.visual_context_status = "applied"
        issues = local_translation_qa(p, row)["issues"]
        self.assertIn("PRONOUN_CONTEXT_MISMATCH", {item["type"] for item in issues})

    def test_unknown_visual_context_never_defaults_male(self):
        row = visual_row(1, "SPK_01", "", "", confidence=0.4, status="LOW_CONFIDENCE")
        self.assertEqual(row["speaker"]["character_id"], "")
        self.assertNotIn("gender", row["speaker"])

    def test_visual_context_change_invalidates_saved_qa(self):
        segment = Utterance(1, 0, 2, "他来了。", vi_subtitle="Người đó đến rồi.", speaker_id="SPK_01")
        project = make_project(Path("missing.mp4"), [segment])
        project.story_context = context([visual_row(1, "SPK_01", "CHAR_A", "")])
        project.visual_context_status = "applied"
        entry = store_qa_result(project, segment, "PASS")
        self.assertTrue(qa_entry_is_current(segment, entry, project))
        project.story_context["visual_contexts"][0]["scene_mode"] = "FLASHBACK"
        self.assertFalse(qa_entry_is_current(segment, entry, project))

    def test_translation_prompt_prioritizes_user_and_passes_only_relevant_visual_rows(self):
        rows = [Utterance(1, 0, 1, "他", speaker_id="SPK_01"), Utterance(2, 1, 2, "她", speaker_id="SPK_02")]
        p = make_project(Path("missing.mp4"), rows)
        p.translation_prompt = "SPK_01 là A; ưu tiên cách xưng hô do user duyệt."
        p.story_context = context([visual_row(1, "SPK_01", "CHAR_A", "") , visual_row(2, "SPK_02", "CHAR_B", "")])
        text = translation_prompt(p, [{"id": 1}], [], [], [])
        payload = json.loads(text[text.index("{"):])
        self.assertIn("PRIORITY 1", payload["editorial"]["context_instruction"])
        self.assertEqual([item["id"] for item in payload["editorial"]["approved_context"]["visual_contexts"]], [1])


if __name__ == "__main__":
    unittest.main()
