import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cartoon_sub.app.settings import AISettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.translation.qa_service import SEMANTIC_QA_SCHEMA, TranslationQAService
from cartoon_sub.translation.qc import (
    local_translation_qa,
    qa_entry_is_current,
    store_qa_result,
)


SOURCE = "他还有一件中品法器, 流云笠。"
BAD_TARGET = "Hắn còn có một kiện trung phẩm pháp khí là Lưu Vân L笠."
FIXED_TARGET = "Hắn còn có một kiện pháp khí trung phẩm tên là Lưu Vân Lạp."


def settings_store():
    store = Mock()
    store.load.return_value = AISettings(translation_chunk_size=30, retry_count=0)
    store.get_key.return_value = "fake-key"
    return store


def prompt_payload(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]


def fake_qa_client():
    client = Mock()

    def generate(_system, prompt, schema, _model, **_kwargs):
        payload = prompt_payload(prompt)
        if schema == SEMANTIC_QA_SCHEMA:
            if "hoàn chỉnh" in payload["current_vietnamese"]:
                return {"id": payload["target"]["id"], "status": "PASS", "issues": []}
            return {"id": payload["target"]["id"], "status": "FAIL", "issues": [{
                "type": "SUSPECT_OMISSION", "detail": "Bản dịch mới có một phần ý nguồn",
            }]}
        row = payload["targets"][0]
        text = FIXED_TARGET if row["zh"] == SOURCE else "Bản dịch đầy đủ, tự nhiên và hoàn chỉnh."
        return {"translations": [{
            "id": row["id"], "vi": text, "review_note": "",
            "meaning_preservation": "high", "compressed": False,
        }]}

    client.generate_json.side_effect = generate
    return client


class TranslationQATests(unittest.TestCase):
    def test_untranslated_han_and_mixed_script_are_detected(self):
        project = Project("qa", "video.mp4", segments=[Segment(133, 2.5, 5.0, SOURCE, vi=BAD_TARGET)])
        result = local_translation_qa(project, project.segments[0])
        self.assertEqual(result["status"], "FAIL")
        types = [issue["type"] for issue in result["issues"]]
        self.assertIn("UNTRANSLATED_HAN", types)
        self.assertIn("MIXED_SCRIPT", types)
        self.assertIn("笠", result["issues"][0]["detail"])

        root = Segment(134, 5.0, 6.0, "我是三灵根。", vi="Ta là tam linh根.")
        project.segments = [root]
        types = [issue["type"] for issue in local_translation_qa(project, root)["issues"]]
        self.assertIn("UNTRANSLATED_HAN", types)
        self.assertIn("MIXED_SCRIPT", types)

    def test_empty_suspect_omission_and_good_row(self):
        empty = Segment(1, 0, 1, "我不同意。", vi="")
        long_source = "他已经在炼气三层停留八年今天终于得到丹药也许能够直接突破第五层"
        omission = Segment(2, 1, 2, long_source, vi="Hắn nhận đan.")
        good = Segment(3, 2, 3, "我不同意。", vi="Tôi không đồng ý.")
        project = Project("qa", "video.mp4", segments=[empty, omission, good])
        self.assertEqual(local_translation_qa(project, empty)["issues"][0]["type"], "EMPTY_TRANSLATION")
        omission_result = local_translation_qa(project, omission)
        self.assertEqual(omission_result["status"], "SUSPECT")
        self.assertIn("SUSPECT_OMISSION", [issue["type"] for issue in omission_result["issues"]])
        self.assertEqual(local_translation_qa(project, good), {"status": "PASS", "issues": []})

    def test_source_fragment_and_malformed_output_are_detected(self):
        fragment = Segment(1, 0, 1, SOURCE, vi="Hắn có 中品法器 nhưng chưa nói hết.")
        malformed = Segment(2, 1, 2, "你好。", vi=r"Xin chào \\u4f60")
        project = Project("qa", "video.mp4", segments=[fragment, malformed])
        fragment_types = [item["type"] for item in local_translation_qa(project, fragment)["issues"]]
        malformed_types = [item["type"] for item in local_translation_qa(project, malformed)["issues"]]
        self.assertIn("UNTRANSLATED_SOURCE_FRAGMENT", fragment_types)
        self.assertIn("MALFORMED_OUTPUT", malformed_types)

    def test_explicit_han_allowlist_and_preserve_source(self):
        segment = Segment(1, 0, 1, "我回到青云宗。", vi="Tôi trở về 青云宗.")
        mapped = Project("qa", "video.mp4", segments=[segment], glossary={"青云宗": "青云宗"})
        self.assertEqual(local_translation_qa(mapped, segment)["status"], "PASS")
        preserved = Project("qa", "video.mp4", segments=[segment], proper_name_mode="preserve_source")
        self.assertEqual(local_translation_qa(preserved, segment)["status"], "PASS")
        approved = Project("qa", "video.mp4", segments=[segment], story_context={
            "terms": [{"source": "青云宗", "target": "青云宗", "notes": "locked", "evidence_ids": [1]}]
        })
        self.assertEqual(local_translation_qa(approved, segment)["status"], "PASS")

    def test_targeted_retry_fixes_only_bad_row_and_syncs_live_srt(self):
        bad = Segment(133, 12.25, 14.75, SOURCE, vi=BAD_TARGET)
        good = Segment(134, 14.75, 16.0, "我不同意。", vi="Tôi không đồng ý.")
        project = Project("qa", "video.mp4", segments=[bad, good], translation_status="completed")
        client = fake_qa_client()
        original_identity = [(row.id, row.start, row.end, row.vi_subtitle) for row in project.segments]
        with tempfile.TemporaryDirectory() as directory:
            result, _ = TranslationQAService(settings_store(), lambda _key: client).run(project, directory)
            reloaded = ProjectManager().load(directory)
            live_srt = (Path(directory) / "exports" / "translate" / "vi_subtitle.srt").read_text(encoding="utf-8-sig")
        self.assertEqual(client.generate_json.call_count, 1)
        self.assertEqual(result.segments[0].vi_subtitle, FIXED_TARGET)
        self.assertEqual(result.segments[1].vi_subtitle, original_identity[1][3])
        self.assertEqual([(row.id, row.start, row.end) for row in result.segments],
                         [(row[0], row[1], row[2]) for row in original_identity])
        self.assertEqual(result.translation_qa["133"]["status"], "AUTO_FIXED")
        self.assertEqual(result.translation_qa["134"]["status"], "PASS")
        self.assertEqual(reloaded.translation_qa["133"]["status"], "AUTO_FIXED")
        self.assertIn(FIXED_TARGET, live_srt)
        self.assertNotIn("笠", live_srt)

    def test_400_rows_only_send_failed_or_suspect_rows_to_ai(self):
        rows = [Segment(index, index, index + 0.8, f"第{index}句", vi=f"Câu dịch {index}.")
                for index in range(1, 391)]
        rows.extend(Segment(index, index, index + 0.8, SOURCE, vi=BAD_TARGET) for index in range(391, 396))
        rows.extend(Segment(index, index, index + 0.8, "我不同意。", vi="") for index in range(396, 399))
        long_source = "他已经在炼气三层停留八年今天终于得到丹药也许能够直接突破第五层"
        rows.extend(Segment(index, index, index + 0.8, long_source, vi="Hắn nhận đan.")
                    for index in range(399, 401))
        project = Project("qa", "video.mp4", segments=rows, translation_status="completed")
        client = fake_qa_client()
        with tempfile.TemporaryDirectory() as directory:
            result, _ = TranslationQAService(settings_store(), lambda _key: client).run(project, directory)
        self.assertEqual(client.generate_json.call_count, 14)  # hard retries + suspect QA/retry/recheck
        self.assertTrue(all(result.translation_qa[str(index)]["status"] == "PASS" for index in range(1, 391)))
        self.assertTrue(all(result.translation_qa[str(index)]["status"] == "AUTO_FIXED" for index in range(391, 401)))

    def test_failed_retry_stops_after_two_and_keeps_original_for_review(self):
        segment = Segment(1, 0, 1, SOURCE, vi=BAD_TARGET)
        project = Project("qa", "video.mp4", segments=[segment], translation_status="completed")
        client = Mock()
        client.generate_json.return_value = {"translations": [{
            "id": 1, "vi": BAD_TARGET, "review_note": "",
            "meaning_preservation": "unknown", "compressed": False,
        }]}
        with tempfile.TemporaryDirectory() as directory:
            result, _ = TranslationQAService(settings_store(), lambda _key: client).run(project, directory)
        self.assertEqual(client.generate_json.call_count, 2)
        self.assertEqual(result.segments[0].vi_subtitle, BAD_TARGET)
        self.assertEqual(result.translation_qa["1"]["status"], "NEED_REVIEW")
        self.assertEqual(result.translation_qa["1"]["attempts"], 2)

    def test_qa_fingerprint_invalidates_only_changed_text(self):
        segment = Segment(1, 0, 1, "我不同意。", vi="Tôi không đồng ý.")
        project = Project("qa", "video.mp4", segments=[segment])
        entry = store_qa_result(project, segment, "PASS")
        self.assertTrue(qa_entry_is_current(segment, entry))
        segment.vi = "Tôi phản đối."
        self.assertFalse(qa_entry_is_current(segment, entry))


if __name__ == "__main__":
    unittest.main()
