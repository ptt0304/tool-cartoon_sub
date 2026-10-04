import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.transcription.pipeline import (
    invalidate_transcript_dependents,
    transcript_timeline_signature,
)
from cartoon_sub.transcription.semantic_boundary_resolver import (
    ChineseSemanticBoundaryResolver,
    SEMANTIC_BOUNDARY_VERSION,
)


class Store:
    def __init__(self, pool="POOL", model="vendor/text", retries=0):
        self.pool = pool
        self.settings = SimpleNamespace(
            retry_count=retries,
            default_ai_model=model,
            tab_model_overrides={},
        )
    def load(self): return self.settings
    def openrouter_key_pool(self): return self.pool
    def openrouter_catalog_cache(self):
        return {"models": [{"id": self.settings.default_ai_model, "architecture": {
            "input_modalities": ["text"], "output_modalities": ["text"]}}]}


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.models = {"vendor/text": {"id": "vendor/text"}}
    def generate_json(self, *args, **kwargs):
        self.calls += 1
        return self.payload
    def close(self): pass


def ambiguous_fixture(text="好耶出发找姐姐喽小主人"):
    parent = Segment(1, 0, 12, text)
    cut = 3
    children = [Segment(1, 0, 5, text[:cut + 1]), Segment(2, 5, 12, text[cut + 1:])]
    return parent, children, cut


def response(target_id, boundaries):
    return {"targets": [{"target_id": target_id, "boundaries": boundaries}]}


def decision(index, action="ADD", kind="LIKELY_DIALOGUE_TURN", confidence=.95):
    return {"after_index": index, "action": action, "type": kind, "confidence": confidence}


class ChineseSemanticBoundaryResolverTests(unittest.TestCase):
    def test_high_confidence_deterministic_and_normal_srt_make_no_ai_call(self):
        with tempfile.TemporaryDirectory() as root:
            store = Mock()
            resolver = ChineseSemanticBoundaryResolver(store, root,
                lambda _: self.fail("AI client must not be created"))
            clear = Segment(1, 0, 4, "你好吗？")
            result = resolver.resolve([clear], [clear])
            self.assertEqual(result.utterances, [clear])
            self.assertEqual(result.stats.requests, 0)
            srt = Segment(1, 0, 5, "第一句。第二句。")
            result = resolver.resolve([srt], [srt], source="srt")
            self.assertEqual(result.stats.requests, 0)
            store.load.assert_not_called()

    def test_ambiguous_region_adds_boundary_without_assigning_speaker(self):
        parent, children, existing = ambiguous_fixture()
        payload = response(1, [decision(existing, "KEEP"), decision(7, "ADD")])
        client = Client(payload)
        with tempfile.TemporaryDirectory() as root:
            result = ChineseSemanticBoundaryResolver(Store(), root, lambda pool: client).resolve(
                [parent], children)
        self.assertEqual(result.stats.ambiguous_regions, 1)
        self.assertEqual((result.stats.requests, result.stats.added), (1, 1))
        self.assertEqual([row.zh for row in result.utterances], ["好耶出发", "找姐姐喽", "小主人"])
        self.assertTrue(all(row.speaker_id == "SPK_UNKNOWN" for row in result.utterances))
        self.assertEqual("".join(row.zh for row in result.utterances), parent.zh)

    def test_resolver_can_remove_bad_phase21_boundary(self):
        parent, children, existing = ambiguous_fixture()
        client = Client(response(1, [decision(existing, "REMOVE", "CLAUSE")]))
        with tempfile.TemporaryDirectory() as root:
            result = ChineseSemanticBoundaryResolver(Store(), root, lambda _: client).resolve(
                [parent], children)
        self.assertEqual(result.stats.removed, 1)
        self.assertEqual([row.zh for row in result.utterances], [parent.zh])

    def test_vocative_and_question_answer_positions_slice_original_only(self):
        for text, add_at in (("小主人我们现在出发", 2), ("你去吗我不去", 2)):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as root:
                parent = Segment(1, 0, 12, text)
                children = [Segment(1, 0, 5, text[:5]), Segment(2, 5, 12, text[5:])]
                targets = ChineseSemanticBoundaryResolver(Store(), root)._targets(
                    [parent], children, None, "stt")
                existing = targets[0]["existing_boundaries"]
                choices = sorted([decision(index, "REMOVE") for index in existing]
                                 + [decision(add_at, "ADD")], key=lambda row: row["after_index"])
                client = Client(response(1, choices))
                result = ChineseSemanticBoundaryResolver(Store(), root, lambda _: client).resolve(
                    [parent], children)
                self.assertEqual(result.utterances[0].zh, text[:add_at + 1])
                self.assertEqual("".join(row.zh for row in result.utterances), text)

    def test_invalid_duplicate_unknown_and_rewritten_outputs_fall_back(self):
        parent, children, existing = ambiguous_fixture()
        bad_payloads = [
            response(1, [decision(999)]),
            response(1, [decision(existing), decision(existing)]),
            response(99, []),
            {"targets": [{"target_id": 1, "boundaries": [], "text": "改写"}]},
            "not-json",
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as root:
                result = ChineseSemanticBoundaryResolver(
                    Store(), root, lambda _: Client(payload)).resolve([parent], children)
                self.assertEqual([(r.start, r.end, r.zh) for r in result.utterances],
                                 [(r.start, r.end, r.zh) for r in children])

    def test_low_confidence_boundary_is_ignored(self):
        parent, children, existing = ambiguous_fixture()
        payload = response(1, [decision(existing, "KEEP"), decision(7, confidence=.4)])
        with tempfile.TemporaryDirectory() as root:
            result = ChineseSemanticBoundaryResolver(
                Store(), root, lambda _: Client(payload)).resolve([parent], children)
        self.assertEqual(result.utterances, children)
        self.assertFalse(result.stats.changed)

    def test_unsafe_function_word_cut_and_adjacent_replacement_are_ignored(self):
        text = "鲸鱼等会儿烤给你吃还有螃蟹"
        parent = Segment(1, 0, 12, text)
        existing = text.index("有")
        children = [Segment(1, 0, 6, text[:existing + 1]),
                    Segment(2, 6, 12, text[existing + 1:])]
        unsafe = text.index("还")
        payload = response(1, [decision(unsafe, "ADD"), decision(existing, "REMOVE")])
        with tempfile.TemporaryDirectory() as root:
            result = ChineseSemanticBoundaryResolver(
                Store(), root, lambda _: Client(payload)).resolve([parent], children)
        self.assertEqual(result.utterances, children)
        self.assertEqual((result.stats.added, result.stats.removed), (0, 0))

    def test_timestamps_are_deterministic_monotonic_and_inside_parent(self):
        parent, children, existing = ambiguous_fixture()
        payload = response(1, [decision(existing, "KEEP"), decision(7)])
        outputs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as root:
                result = ChineseSemanticBoundaryResolver(
                    Store(), root, lambda _: Client(payload)).resolve([parent], children)
                outputs.append([(r.start, r.end, r.zh) for r in result.utterances])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual((outputs[0][0][0], outputs[0][-1][1]), (0, 12))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(outputs[0], outputs[0][1:])))

    def test_cache_hit_has_zero_request_and_key_is_not_in_identity_or_cache(self):
        parent, children, existing = ambiguous_fixture()
        payload = response(1, [decision(existing, "KEEP"), decision(7)])
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first_client = Client(payload)
            first = ChineseSemanticBoundaryResolver(
                Store(pool="SECRET_A"), root, lambda pool: first_client).resolve([parent], children)
            second = ChineseSemanticBoundaryResolver(
                Store(pool="SECRET_B"), root,
                lambda pool: self.fail("cache hit must not create client")).resolve([parent], children)
            self.assertEqual(first.stats.requests, 1)
            self.assertEqual((second.stats.requests, second.stats.cache_hits), (0, 1))
            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            cache_text = files[0].read_text(encoding="utf-8")
            self.assertNotIn("SECRET_A", cache_text)
            self.assertNotIn("SECRET_B", cache_text)
            self.assertIsInstance(SEMANTIC_BOUNDARY_VERSION, int)

    def test_shared_openrouter_pool_is_passed_to_client_factory(self):
        parent, children, existing = ambiguous_fixture()
        pool = object(); seen = []
        client = Client(response(1, [decision(existing, "KEEP")]))
        with tempfile.TemporaryDirectory() as root:
            ChineseSemanticBoundaryResolver(
                Store(pool=pool), root,
                lambda actual: seen.append(actual) or client).resolve([parent], children)
        self.assertEqual(seen, [pool])

    def test_downstream_invalidation_is_needed_only_for_changed_canonical_timeline(self):
        parent, children, existing = ambiguous_fixture()
        unchanged = list(children)
        changed_client = Client(response(1, [decision(existing, "REMOVE")]))
        with tempfile.TemporaryDirectory() as root:
            changed = ChineseSemanticBoundaryResolver(
                Store(), root, lambda _: changed_client).resolve([parent], children).utterances
        project = Project("p", "video.mp4", segments=children)
        project.speaker_review_hash = "approved"
        self.assertEqual(transcript_timeline_signature(children), transcript_timeline_signature(unchanged))
        self.assertNotEqual(transcript_timeline_signature(children), transcript_timeline_signature(changed))
        self.assertEqual(project.speaker_review_hash, "approved")
        invalidate_transcript_dependents(project)
        self.assertEqual(project.speaker_review_hash, "")


if __name__ == "__main__":
    unittest.main()
