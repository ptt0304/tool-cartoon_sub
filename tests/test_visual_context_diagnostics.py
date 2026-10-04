import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.visual_context import (
    VisualContextAnalyzer,
    bounded_target_chunks,
    sanitize_visual_diagnostic,
    validate_merged_visual_contexts,
    visual_id_diagnostics,
)


def visual_row(uid):
    return {
        "id": uid, "scene_mode": "PRESENT",
        "speaker": {"spk_id": "SPK_01", "character_id": "CHAR_A", "confidence": .9},
        "addressee": {"character_id": "", "confidence": .8},
        "visible_characters": [], "referents": [], "visible_objects": [],
        "notes": "", "confidence": .9, "analysis_status": "ANALYZED",
    }


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_http_status = 200

    def generate_multimodal_json(self, *_args, **_kwargs):
        return self.payload


class FakeAnalyzer(VisualContextAnalyzer):
    def _frame(self, source, timestamp, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{timestamp:.2f}.jpg"
        path.write_bytes(b"x" * 600)
        return path


class ChunkClient:
    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.calls = []
        self.last_http_status = 200

    def generate_multimodal_json(self, _system, prompt, _media, _schema, _model, **_kwargs):
        payload = json.loads(prompt)
        self.calls.append(payload)
        ids = [row["id"] for row in payload["target_transcript_rows"]]
        if self.fail_ids.intersection(ids):
            ids = ids[:-1]
        value = StoryContext(visual_contexts=[visual_row(uid) for uid in ids]).to_dict()
        if ids:
            value["new_character_candidates"] = [{
                "temporary_id": "CHAR_A", "description": "cùng một cô gái mặc áo xanh",
                "aliases": ["cô gái"], "gender_context": "female", "confidence": .9,
                "uncertain": False, "evidence_ids": ids,
                "bindings": [{"id": uid, "role": "speaker"} for uid in ids],
            }]
        return value


def project_rows(root, count):
    video = root / "video.mp4"; video.write_bytes(b"video")
    rows = [Utterance(index, float(index - 1), float(index) - .1, f"句{index}")
            for index in range(1, count + 1)]
    return Project("p", str(video), segments=rows), rows


class VisualContextDiagnosticsTests(unittest.TestCase):
    def test_bounded_chunk_sizes(self):
        for count, expected in ((16, [16]), (17, [16, 1]), (24, [16, 8])):
            with self.subTest(count=count):
                rows = [Utterance(index, index, index + .5, str(index))
                        for index in range(1, count + 1)]
                chunks = bounded_target_chunks(rows)
                self.assertEqual([len(chunk) for chunk in chunks], expected)
                self.assertEqual([row.id for chunk in chunks for row in chunk],
                                 list(range(1, count + 1)))

    def test_chunk_windows_ids_and_context_only_neighbors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, _ = project_rows(root, 24)
            client = ChunkClient()
            analyzer = FakeAnalyzer(client, "model", input_mode="frames")
            result = analyzer.analyze(project, root)
            self.assertEqual(len(client.calls), 2)
            first, second = client.calls
            self.assertEqual(first["expected_target_ids"], list(range(1, 17)))
            self.assertEqual(second["expected_target_ids"], list(range(17, 25)))
            self.assertEqual(first["video_range_seconds"], {"start": 0.0, "end": 15.9})
            self.assertEqual(second["video_range_seconds"], {"start": 16.0, "end": 23.9})
            self.assertEqual([row["id"] for row in second["neighboring_transcript_rows"]],
                             [15, 16])
            self.assertEqual([row["id"] for row in second["target_transcript_rows"]],
                             list(range(17, 25)))
            self.assertEqual([row["id"] for row in result["visual_contexts"]],
                             list(range(1, 25)))

    def test_partial_failure_keeps_success_cache_and_retry_only_calls_failed_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, _ = project_rows(root, 17)
            first_client = ChunkClient(fail_ids={17})
            with self.assertRaisesRegex(ValueError, "target ID"):
                FakeAnalyzer(first_client, "model", input_mode="frames").analyze(project, root)
            self.assertEqual(len(first_client.calls), 2)
            second_client = ChunkClient()
            analyzer = FakeAnalyzer(second_client, "model", input_mode="frames")
            result = analyzer.analyze(project, root)
            self.assertEqual(len(second_client.calls), 1)
            self.assertEqual(second_client.calls[0]["expected_target_ids"], [17])
            self.assertEqual(analyzer.cache_hits, 1)
            self.assertEqual([row["id"] for row in result["visual_contexts"]],
                             list(range(1, 18)))

    def test_merge_rejects_duplicates_and_incomplete_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicates=\[1\]"):
            validate_merged_visual_contexts([{"id": 1}, {"id": 1}], [1])
        with self.assertRaisesRegex(ValueError, "missing=\[2\]"):
            validate_merged_visual_contexts([{"id": 1}], [1, 2])

    def test_missing_id_diagnostics(self):
        result = visual_id_diagnostics({"visual_contexts": [{"id": 1}]}, [1, 2])
        self.assertEqual(result["missing_ids"], [2])
        self.assertFalse(result["exact_set"])

    def test_extra_id_diagnostics(self):
        result = visual_id_diagnostics(
            {"visual_contexts": [{"id": 1}, {"id": 3}]}, [1, 2])
        self.assertEqual(result["missing_ids"], [2])
        self.assertEqual(result["extra_ids"], [3])

    def test_duplicate_id_diagnostics(self):
        result = visual_id_diagnostics(
            {"visual_contexts": [{"id": 1}, {"id": 1}]}, [1])
        self.assertEqual(result["duplicate_ids"], [1])
        self.assertFalse(result["exact_set"])

    def test_exact_set_passes(self):
        result = visual_id_diagnostics(
            {"visual_contexts": [{"id": 2}, {"id": 1}]}, [1, 2])
        self.assertTrue(result["exact_set"])
        self.assertEqual(result["count"], 2)

    def test_failed_response_is_retained_outside_production_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "video.mp4"; video.write_bytes(b"video")
            rows = [Utterance(1, 0, 1, "甲"), Utterance(2, 1, 2, "乙")]
            project = Project("p", str(video), segments=rows)
            analyzer = FakeAnalyzer(
                FakeClient(StoryContext(visual_contexts=[visual_row(1)]).to_dict()),
                "qwen/test", input_mode="frames")
            with self.assertRaisesRegex(ValueError, "target ID"):
                analyzer._request(project, root / "cache", rows, [], 0, 2, 2, "scene_chunk")
            files = list((root / "cache" / "diagnostics").glob("*.json"))
            self.assertEqual(len(files), 1)
            diagnostic = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["http_status"], 200)
            self.assertEqual(diagnostic["missing_ids"], [2])
            self.assertFalse(any((root / "cache").glob("*.json")))

    def test_secret_fields_are_excluded(self):
        sanitized = sanitize_visual_diagnostic({
            "Authorization": "Bearer secret", "nested": {"api_key": "secret", "id": 1}})
        encoded = json.dumps(sanitized)
        self.assertNotIn("Bearer secret", encoded)
        self.assertNotIn('"secret"', encoded)
        self.assertEqual(sanitized["nested"]["id"], 1)


if __name__ == "__main__":
    unittest.main()
