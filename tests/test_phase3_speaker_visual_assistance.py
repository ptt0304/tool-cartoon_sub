import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import httpx

from cartoon_sub.ai.openrouter_client import OpenRouterClient, OpenRouterKeyPool
from cartoon_sub.app.controller import Controller
from cartoon_sub.app.settings import AISettings
from cartoon_sub.speaker.resolution_service import (
    SpeakerEvidence, SpeakerEvidenceSource, SpeakerResolutionService,
)
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import apply_speaker_review_state
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.visual_context import VisualContextAnalyzer
from cartoon_sub.translation.context_service import ContextService
from cartoon_sub.translation.visual_context import visual_source_signature


def visual_row(uid, speaker="SPK_01", confidence=.95):
    return {
        "id": uid, "scene_mode": "PRESENT",
        "speaker": {"spk_id": speaker, "character_id": "CHAR_A", "confidence": confidence},
        "addressee": {"character_id": "", "confidence": confidence},
        "visible_characters": [], "referents": [], "visible_objects": [], "notes": "",
        "confidence": confidence, "analysis_status": "ANALYZED",
    }


class FakeMultimodalClient:
    def __init__(self):
        self.calls = []

    def generate_multimodal_json(self, system, prompt, media, schema, model, **kwargs):
        del system, schema, kwargs
        request = json.loads(prompt)
        self.calls.append((request, media, model))
        ids = [row["id"] for row in request["target_transcript_rows"]]
        value = StoryContext(visual_contexts=[
            visual_row(row["id"], row["speaker_id"] if row["speaker_id"] != "SPK_UNKNOWN" else "SPK_01")
            for row in request["target_transcript_rows"]
        ]).to_dict()
        value["new_character_candidates"] = [{
            "temporary_id": "CHAR_A", "description": "cùng một nhân vật mặc áo xanh",
            "aliases": ["nhân vật A"], "gender_context": "unknown", "confidence": .95,
            "uncertain": False, "evidence_ids": ids,
            "bindings": [{"id": uid, "role": "speaker"} for uid in ids],
        }]
        return value


class FakeFramesAnalyzer(VisualContextAnalyzer):
    def _frame(self, source, timestamp, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{timestamp:.2f}.jpg"
        path.write_bytes(b"f" * 600)
        return path


class FakeVideoAnalyzer(VisualContextAnalyzer):
    def _proxy(self, source, start, end, fps, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "window.mp4"
        path.write_bytes(b"v" * 1200)
        return path


class Phase3SpeakerVisualTests(unittest.TestCase):
    def test_imported_srt_remains_authoritative_and_does_not_call_stt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source.srt"
            source.write_text("1\n00:00:01,100 --> 00:00:02,300\n\u4f60\u597d\n", encoding="utf-8")
            controller = Controller.__new__(Controller)
            controller.project = Project("p", str(root / "video.mp4"))
            controller.directory = root
            controller.pipeline = Mock()
            controller.segmentation_service = Mock(); controller.segmentation_service.sync_stale.return_value = False
            controller.save = Mock()
            controller.import_subtitles(source)
            controller.pipeline.transcribe.assert_not_called()
            row = controller.project.utterances[0]
            self.assertEqual((row.zh, row.start, row.end, row.speaker_id),
                             ("\u4f60\u597d", 1.1, 2.3, "SPK_UNKNOWN"))

    def test_frames_are_grounded_and_one_window_is_one_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"video")
            rows = [Utterance(1, 10, 12, "甲"), Utterance(2, 13, 15, "乙")]
            project = Project("p", str(video), segments=rows)
            client = FakeMultimodalClient()
            result = FakeFramesAnalyzer(client, "google/vision", input_mode="frames").analyze(project, root)
            self.assertEqual(len(client.calls), 1)
            prompt, media, _ = client.calls[0]
            self.assertEqual([row["id"] for row in prompt["target_transcript_rows"]], [1, 2])
            self.assertTrue(all(mime == "image/jpeg" for mime, _ in media))
            self.assertEqual({row["id"] for row in result["visual_contexts"]}, {1, 2})

    def test_visual_cache_is_independent_of_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"video")
            project = Project("p", str(video), segments=[Utterance(1, 1, 2, "甲")])
            first, second = FakeMultimodalClient(), FakeMultimodalClient()
            FakeFramesAnalyzer(first, "google/vision", input_mode="frames").analyze(project, root)
            FakeFramesAnalyzer(second, "google/vision", input_mode="frames").analyze(project, root)
            self.assertEqual(len(first.calls), 1)
            self.assertEqual(len(second.calls), 0)

    def test_direct_video_sends_one_bounded_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"video")
            project = Project("p", str(video), segments=[Utterance(1, 4, 6, "\u7532")])
            client = FakeMultimodalClient()
            FakeVideoAnalyzer(client, "vendor/video", input_mode="video").analyze(project, root)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0][1][0][0], "video/mp4")
            self.assertEqual(client.calls[0][0]["video_range_seconds"], {"start": 4, "end": 6})

    def test_model_capability_rejections_are_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video.mp4"; video.write_bytes(b"video")
            project = Project("p", str(video), segments=[Utterance(1, 0, 1, "\u7532")])
            store = Mock()
            store.openrouter_catalog_cache.return_value = {"models": [{
                "id": "vendor/text", "architecture": {
                    "input_modalities": ["text"], "output_modalities": ["text"]}}]}
            store.load.return_value = AISettings(default_ai_model="vendor/text",
                                                  vision_input_mode="frames")
            with self.assertRaisesRegex(ValueError, "không hỗ trợ hình ảnh"):
                ContextService(store).analyze(project, root)
            store.load.return_value = AISettings(default_ai_model="vendor/text",
                                                  vision_input_mode="video")
            with self.assertRaisesRegex(ValueError, "không hỗ trợ video"):
                ContextService(store).analyze(project, root)

    def test_timestamp_or_text_change_invalidates_visual_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "video.mp4"; video.write_bytes(b"video")
            project = Project("p", str(video), segments=[Utterance(1, 1, 2, "\u7532")])
            original = visual_source_signature(project)
            project.utterances[0].start = 1.25
            self.assertNotEqual(original, visual_source_signature(project))
            changed_time = visual_source_signature(project)
            project.utterances[0].zh = "\u4e59"
            self.assertNotEqual(changed_time, visual_source_signature(project))

    def test_high_confidence_visual_proposes_but_low_confidence_does_not(self):
        project = Project("p", "v.mp4", segments=[
            Utterance(1, 0, 1, "甲"), Utterance(2, 1, 2, "乙")])
        service = SpeakerResolutionService()
        result = service.apply_evidence(project, [
            SpeakerEvidence(1, "SPK_02", .95, SpeakerEvidenceSource.VISUAL),
            SpeakerEvidence(2, "SPK_03", .4, SpeakerEvidenceSource.VISUAL),
        ])
        self.assertEqual(project.utterances[0].speaker_id, "SPK_02")
        self.assertEqual(project.utterances[1].speaker_id, "SPK_UNKNOWN")
        self.assertEqual(result.applied_ids, (1,))
        self.assertEqual(result.unresolved_ids, (2,))

    def test_visual_agreement_preserves_stt_proposal(self):
        row = Utterance(1, 0, 1, "甲", speaker_id="SPK_01", speaker_confidence=.8)
        project = Project("p", "v.mp4", segments=[row])
        result = SpeakerResolutionService().apply_evidence(project, [
            SpeakerEvidence(1, "SPK_01", .95, SpeakerEvidenceSource.VISUAL)])
        self.assertEqual(row.speaker_id, "SPK_01")
        self.assertEqual(result.conflict_ids, ())
        self.assertEqual(result.applied_ids, (1,))

    def test_stt_visual_conflict_becomes_review_item(self):
        row = Utterance(1, 0, 1, "甲", speaker_id="SPK_01", speaker_confidence=.8)
        project = Project("p", "v.mp4", segments=[row])
        result = SpeakerResolutionService().apply_evidence(project, [
            SpeakerEvidence(1, "SPK_02", .95, SpeakerEvidenceSource.VISUAL)])
        self.assertEqual(row.speaker_id, "SPK_01")
        self.assertEqual(result.conflict_ids, (1,))

    def test_story_context_mapping_is_dialogue_evidence_not_character_identity(self):
        evidence = SpeakerResolutionService.evidence_from_context({
            "visual_contexts": [],
            "speaker_character_mappings": [{
                "spk_id": "SPK_03", "character_id": "CHAR_X", "confidence": .9,
                "evidence_ids": [7], "notes": "dialogue continuity",
            }],
        })
        self.assertEqual(evidence[0].speaker_id, "SPK_03")
        self.assertEqual(evidence[0].source, SpeakerEvidenceSource.DIALOGUE_CONTEXT)
        self.assertNotEqual(evidence[0].speaker_id, "CHAR_X")

    def test_visual_evidence_does_not_change_voice_registry_mapping(self):
        row = Utterance(1, 0, 1, "\u7532", speaker_id="SPK_01")
        project = Project("p", "v.mp4", segments=[row], speakers={
            "SPK_01": asdict(Speaker("SPK_01", "A", tts_voice_id="voice-a")),
        })
        before = json.loads(json.dumps(project.speakers))
        SpeakerResolutionService().apply_evidence(project, [
            SpeakerEvidence(1, "SPK_01", .99, SpeakerEvidenceSource.VISUAL)])
        self.assertEqual(project.speakers, before)

    def test_user_confirmed_speaker_is_never_overwritten(self):
        row = Utterance(1, 0, 1, "甲", speaker_id="SPK_01")
        project = Project("p", "v.mp4", segments=[row],
                          speakers={"SPK_01": asdict(Speaker("SPK_01"))})
        apply_speaker_review_state(project, project.speakers, {1: "SPK_01"})
        result = SpeakerResolutionService().apply_evidence(project, [
            SpeakerEvidence(1, "SPK_02", .99, SpeakerEvidenceSource.VISUAL)])
        self.assertEqual(row.speaker_id, "SPK_01")
        self.assertEqual(result.protected_ids, (1,))
        self.assertEqual(result.conflict_ids, (1,))

    def test_multimodal_openrouter_uses_shared_pool_failover(self):
        seen = []

        def post(url, headers, json):
            del url
            seen.append((headers["Authorization"], json))
            status = 429 if len(seen) == 1 else 200
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = Mock(status_code=status, headers={}, request=request)
            response.raise_for_status.side_effect = (
                httpx.HTTPStatusError("rate", request=request, response=response) if status == 429 else None)
            response.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
            return response

        http = Mock(); http.post.side_effect = post
        pool = OpenRouterKeyPool(["key-one", "key-two"])
        client = OpenRouterClient(pool, models=[{"id": "google/vision", "supported_parameters": []}], client=http)
        self.assertEqual(client.generate_multimodal_json(
            "system", "prompt", [("image/jpeg", b"image")], {}, "google/vision"), {})
        self.assertEqual([item[0] for item in seen], ["Bearer key-one", "Bearer key-two"])
        content = seen[-1][1]["messages"][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
