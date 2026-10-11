import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartoon_sub.subtitle.models import Segment
from cartoon_sub.transcription.qa_repair import (
    TranscriptAIRepairService, TranscriptQAResult, TranscriptQAService,
    TranscriptRegion, classify_region_type,
)
from cartoon_sub.transcription.segmentation_normalizer import TranscriptSegmentationNormalizer


class TranscriptQATests(unittest.TestCase):
    def setUp(self):
        self.qa = TranscriptQAService()

    def test_sparse_alignment_falls_back_as_one_safe_weighted_parent(self):
        parent = Segment(1, 0, 51.2, "不可能。集我四人之力，怎会打不下你？然后力扫荡。")
        rows = TranscriptSegmentationNormalizer().normalize([parent], [
            {"word": "不", "start": 4.2, "end": 4.3},
            {"word": "可", "start": 4.3, "end": 4.4},
            {"word": "人", "start": 5.6, "end": 5.7},
            {"word": "然", "start": 50.2, "end": 50.3},
        ])
        self.assertTrue(all(row.transcript_timing_provenance == "ESTIMATED" for row in rows))
        self.assertEqual("".join(row.zh for row in rows), parent.zh)
        self.assertTrue(all(left.end <= right.start for left, right in zip(rows, rows[1:])))

    def test_high_quality_partial_alignment_is_retained(self):
        parent = Segment(1, 0, 4, "小主人，你慢一点。")
        rows = TranscriptSegmentationNormalizer().normalize([parent], [
            {"word": "小主", "start": 1.2, "end": 1.72},
            {"word": "你", "start": 1.82, "end": 2.02},
            {"word": "慢一点", "start": 2.05, "end": 2.7},
        ])
        self.assertEqual(rows[0].transcript_timing_provenance, "PARTIAL_WORD_ALIGNMENT")

    def test_non_monotonic_or_large_gap_alignment_falls_back(self):
        parent = Segment(1, 0, 12, "甲乙。丙丁。")
        rows = TranscriptSegmentationNormalizer().normalize([parent], [
            {"word": "甲乙", "start": 1.0, "end": 2.0},
            {"word": "丙", "start": 8.0, "end": 8.4},
        ])
        self.assertTrue(all(row.transcript_timing_provenance == "ESTIMATED" for row in rows))

    def test_qa_flags_short_long_and_single_character_chain(self):
        rows = [Segment(1, 0, .1, "不"), Segment(2, .1, .2, "可"),
                Segment(3, .2, .3, "能"), Segment(4, .3, 44, "你？")]
        report = self.qa.inspect(rows)
        codes = {item.code for item in report.violations}
        self.assertTrue({"TOO_SHORT_DURATION", "SINGLE_CHAR_CHAIN", "SHORT_TEXT_LONG_DURATION"} <= codes)
        self.assertEqual(len(report.regions), 1)

    def test_density_thresholds_and_vocalization_exception(self):
        rows = [Segment(1, 0, 10, "危险。"), Segment(2, 10, 31, "请到位置。"),
                Segment(3, 31, 94, "集我四人之力，怎会打不下你？"),
                Segment(4, 94, 101.5, "啊"), Segment(5, 102, 104, "正常的八个汉字内容。")]
        by_id = {}
        for item in self.qa.inspect(rows).violations:
            by_id.setdefault(item.segment_id, set()).add(item.code)
        self.assertIn("SHORT_TEXT_LONG_DURATION", by_id[1])
        self.assertIn("EXTREME_TEXT_TIME_RATIO", by_id[2])
        self.assertIn("IMPLAUSIBLY_LONG_SEGMENT", by_id[3])
        self.assertIn("LONG_VOCALIZATION", by_id[4])
        self.assertNotIn("SHORT_TEXT_LONG_DURATION", by_id.get(5, set()))

    def test_punctuation_duplicates_and_weighted_region_are_flagged(self):
        rows = [Segment(1, 0, 1, "。"), Segment(2, 1, 2, "重复。"),
                Segment(3, 2.1, 3.1, "重复。"), Segment(4, 3.1, 28, "短句。")]
        codes = {item.code for item in self.qa.inspect(rows).violations}
        self.assertIn("PUNCTUATION_ONLY", codes)
        self.assertIn("DUPLICATE_NEARBY", codes)
        self.assertIn("REGION_TIMING_SUSPICIOUS", codes)

    def test_normal_density_is_clean_and_micro_chain_is_flagged(self):
        normal = [Segment(1, 0, 2, "这是一句正常的台词。"),
                  Segment(2, 2, 6, "这是一句十五个字左右的正常台词内容。")]
        self.assertEqual(self.qa.inspect(normal).violations, ())
        micro = [Segment(number, (number - 1) * .1, number * .1, "甲") for number in range(1, 6)]
        codes = {item.code for item in self.qa.inspect(micro).violations}
        self.assertTrue({"SINGLE_CHAR_CHAIN", "MICRO_FRAGMENT_CHAIN"} <= codes)

    def test_region_quality_gate_collapses_character_rows(self):
        normalizer = TranscriptSegmentationNormalizer()
        rows = [Segment(index + 1, index, index + 1, text)
                for index, text in enumerate(["不", "可", "能。", "集", "我", "四", "人", "之", "力，", "怎", "会", "打", "不", "下", "你？"])]
        self.assertTrue(normalizer.region_segmentation_untrusted(rows))
        output = normalizer.normalize_region(rows)
        self.assertEqual([item.zh for item in output], ["不可能。", "集我四人之力，怎会打不下你？"])
        self.assertTrue(all(item.start >= 0 and item.end <= 15 for item in output))

    def test_repair_pass_uses_one_request_and_preserves_context(self):
        rows = [Segment(1, 0, .1, "不"), Segment(2, .1, .2, "可"), Segment(3, .2, .3, "能"),
                Segment(4, .3, 1.5, "。"), Segment(5, 2, 3, "上下文")]
        qa = self.qa.inspect(rows)
        payload = {"regions": [{"region_id": "R1", "source_ids": [1, 2, 3, 4], "action": "MERGE", "parts": ["不可能。"], "confidence": .99}]}
        class Client:
            calls = 0
            def generate_json(self, *args, **kwargs): self.calls += 1; return payload
            def close(self): pass
        client = Client()
        class Store:
            def openrouter_key_pool(self): return object()
        with tempfile.TemporaryDirectory() as root, patch(
                "cartoon_sub.ai.model_resolver.AIModelResolver.resolve",
                return_value=SimpleNamespace(model_id="google/gemini-2.5-flash")):
            output, stats = TranscriptAIRepairService(Store(), root, lambda _: client).repair(rows, qa)
        self.assertEqual(client.calls, 1)
        self.assertEqual([row.zh for row in output], ["不可能。", "上下文"])
        self.assertEqual(stats.manual_review, 0)

    def test_failed_repair_stops_without_a_second_request(self):
        rows = [Segment(1, 0, .1, "不"), Segment(2, .1, .2, "可"), Segment(3, .2, .3, "能")]
        qa = self.qa.inspect(rows)
        payload = {"regions": [{"region_id": "R1", "source_ids": [1, 2, 3], "action": "KEEP", "parts": ["不可能"], "confidence": 1.0}]}
        class Client:
            calls = 0
            def generate_json(self, *args, **kwargs): self.calls += 1; return payload
            def close(self): pass
        client = Client()
        class Store:
            def openrouter_key_pool(self): return object()
        with tempfile.TemporaryDirectory() as root, patch(
                "cartoon_sub.ai.model_resolver.AIModelResolver.resolve",
                return_value=SimpleNamespace(model_id="google/gemini-2.5-flash")):
            _, stats = TranscriptAIRepairService(Store(), root, lambda _: client).repair(rows, qa)
        self.assertEqual(client.calls, 1)
        self.assertEqual(stats.manual_review, 1)

    def test_vocalization_policy_keeps_structural_timing_strict(self):
        rows = [Segment(1, 0, 7.5, "啊"), Segment(2, 8, 18, "啊啊啊"),
                Segment(3, 19, 58.2, "别跑了。"), Segment(4, 59, 82, "罗峰"),
                Segment(5, 83, 83.1, "Oh.")]
        report = self.qa.inspect(rows)
        by_id = {}
        for item in report.violations:
            by_id.setdefault(item.segment_id, []).append(item)
        self.assertEqual(classify_region_type([rows[0]]), "VOCALIZATION")
        self.assertEqual(classify_region_type([rows[1]]), "VOCALIZATION")
        self.assertEqual(classify_region_type([Segment(6, 0, 1, "啊，我们走吧。")]), "DIALOGUE")
        self.assertTrue(any(item.code == "LONG_VOCALIZATION" and item.severity == "WARNING" for item in by_id[1]))
        self.assertTrue(any(item.severity == "BLOCKING" for item in by_id[3]))
        self.assertTrue(any(item.severity == "BLOCKING" for item in by_id[4]))
        self.assertIn("TOO_SHORT_DURATION", {item.code for item in by_id[5]})
        self.assertIn("CRITICAL_SHORT_DURATION", {item.code for item in by_id[5]})

    def test_repair_applies_valid_regions_despite_invalid_neighbour(self):
        rows = [Segment(1, 0, .5, "甲"), Segment(2, .5, 1, "乙"),
                Segment(3, 1, 1.5, "丙"), Segment(4, 1.5, 2, "丁"),
                Segment(5, 2, 2.5, "戊"), Segment(6, 2.5, 3, "己")]
        def region(number, first):
            return TranscriptRegion(f"R{number}", (first, first + 1),
                (rows[first].id, rows[first + 1].id), rows[first].start, rows[first + 1].end,
                ("SINGLE_CHAR_CHAIN",), (), (), "DIALOGUE")
        qa = TranscriptQAResult((), (region(1, 0), region(2, 2), region(3, 4)))
        payload = {"regions": [
            {"region_id": "R1", "source_ids": [1, 2], "action": "MERGE", "parts": ["甲乙"], "confidence": 1.0},
            {"region_id": "R2", "source_ids": [3, 4], "action": "MERGE", "parts": ["错误"], "confidence": 1.0},
            {"region_id": "R3", "source_ids": [5, 6], "action": "MERGE", "parts": ["戊己"], "confidence": 1.0},
        ]}
        class Client:
            calls = 0
            def generate_json(self, *args, **kwargs): self.calls += 1; return payload
            def close(self): pass
        client = Client()
        class Store:
            def openrouter_key_pool(self): return object()
        with tempfile.TemporaryDirectory() as root, patch(
                "cartoon_sub.ai.model_resolver.AIModelResolver.resolve",
                return_value=SimpleNamespace(model_id="google/gemini-2.5-flash")):
            output, stats = TranscriptAIRepairService(Store(), root, lambda _: client).repair(rows, qa)
        self.assertEqual(client.calls, 1)
        self.assertEqual([row.zh for row in output], ["甲乙", "丙", "丁", "戊己"])
        self.assertEqual(stats.regions_applied, 2)
        self.assertEqual(stats.regions_rejected, 1)

    def test_repair_rejects_malformed_root_and_cross_region_ids(self):
        row_a, row_b = Segment(1, 0, .5, "甲"), Segment(2, .5, 1, "乙")
        region = TranscriptRegion("R1", (0, 1), (1, 2), 0, 1,
                                  ("SINGLE_CHAR_CHAIN",), (), (), "DIALOGUE")
        with self.assertRaises(ValueError):
            TranscriptAIRepairService._proposal_items({"invalid": []})
        with self.assertRaises(ValueError):
            TranscriptAIRepairService._validate_proposal(
                {"region_id": "R1", "source_ids": [1, 1], "action": "MERGE",
                 "parts": ["甲乙"], "confidence": 1.0}, region)
        with self.assertRaises(ValueError):
            TranscriptAIRepairService._validate_proposal(
                {"region_id": "R1", "source_ids": [1, 99], "action": "MERGE",
                 "parts": ["甲乙"], "confidence": 1.0}, region)


if __name__ == "__main__":
    unittest.main()
