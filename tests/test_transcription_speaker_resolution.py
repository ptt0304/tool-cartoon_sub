import json
import tempfile
import unittest
import wave
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.app.settings import AISettings
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.resolution_service import (
    SpeakerResolutionService,
    SpeakerResolutionState,
    speaker_resolution_state,
)
from cartoon_sub.speaker.service import apply_speaker_review_state, review_complete
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.transcription.contracts import (
    CapabilityAvailability,
    TranscriptionCapability,
    capabilities_for,
)
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber, validate_response
from cartoon_sub.translation.pipeline import mark_stale, translation_fingerprint


def write_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def transcribe_json(self, *args, **kwargs):
        self.calls += 1
        return deepcopy(self.payload)

    def close(self):
        pass


class RecordingClient(FakeClient):
    def __init__(self, payloads):
        super().__init__(None)
        self.payloads = list(payloads)
        self.references_seen = []

    def transcribe_json(self, *args, **kwargs):
        self.calls += 1
        self.references_seen.append(dict(kwargs.get("references") or {}))
        return deepcopy(self.payloads.pop(0))


class TranscriptionSpeakerResolutionTests(unittest.TestCase):
    def test_no_speaker_data_is_successful_and_unresolved(self):
        rows = validate_response({"segments": [
            {"start": 0, "end": .5, "zh": "你好", "transcript_confidence": .9},
        ]}, 1.0)
        project = Project("p", "source.mp4", segments=rows)
        stats = SpeakerResolutionService().resolve(project, "openrouter")
        self.assertEqual(rows[0].speaker_id, "SPK_UNKNOWN")
        self.assertEqual((stats.total, stats.proposed, stats.unresolved), (1, 0, 1))
        self.assertFalse(review_complete(project))
        self.assertIn("không cung cấp thông tin speaker",
                      SpeakerResolutionService.message(stats))

    def test_valid_gemini_hint_is_normalized_and_remains_a_proposal(self):
        rows = validate_response({"segments": [
            {"start": 0, "end": .5, "zh": "你好", "speaker_id": "spk_1",
             "speaker_confidence": .91},
        ]}, 1.0)
        project = Project("p", "source.mp4", segments=rows)
        stats = SpeakerResolutionService().resolve(project, "gemini")
        self.assertEqual(rows[0].speaker_id, "SPK_01")
        self.assertEqual(stats.proposal_ids, ("SPK_01",))
        self.assertEqual(speaker_resolution_state(project, rows[0]),
                         SpeakerResolutionState.PROPOSED)
        self.assertFalse(review_complete(project))

    def test_malformed_hint_becomes_unknown_without_fabrication(self):
        for hint in ("Narrator", "speaker-A", "SPK_00", 7, None):
            with self.subTest(hint=hint):
                row = validate_response({"segments": [
                    {"start": 0, "end": .5, "zh": "你好", "speaker_id": hint,
                     "speaker_confidence": "high"},
                ]}, 1.0)[0]
                self.assertEqual(row.speaker_id, "SPK_UNKNOWN")
                self.assertIsNone(row.speaker_confidence)

    def test_capabilities_describe_adapter_not_model_name(self):
        gemini = capabilities_for("gemini")
        dedicated = capabilities_for("openrouter")
        self.assertEqual(gemini.availability(TranscriptionCapability.SPEAKER_HINTS),
                         CapabilityAvailability.BEST_EFFORT)
        self.assertEqual(dedicated.availability(TranscriptionCapability.SPEAKER_HINTS),
                         CapabilityAvailability.RESPONSE_DEPENDENT)
        self.assertEqual(dedicated.availability(TranscriptionCapability.WORD_TIMESTAMPS),
                         CapabilityAvailability.RESPONSE_DEPENDENT)
        self.assertEqual(capabilities_for("openrouter-any-future-model").values,
                         capabilities_for("unknown").values)

    def test_optional_stt_metadata_survives_chunk_cache(self):
        payload = {
            "segments": [{"start": 0, "end": .5, "zh": "你好", "speaker_id": "SPK_02"}],
            "language": "zh",
            "words": [{"word": "你", "start": 0, "end": .2}],
            "transcription_capabilities": ["TRANSCRIPTION", "WORD_TIMESTAMPS"],
            "transcription_provider": "openrouter_dedicated",
        }
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); audio = root / "audio.wav"; cache = root / "cache"
            write_wav(audio)
            client = FakeClient(payload)
            transcriber = GeminiTranscriber(lambda: "key", "model", cache, 0,
                                             lambda _: client)
            output = transcriber.transcribe(audio)
            self.assertEqual(output[0].speaker_id, "SPK_02")
            saved = json.loads(next(cache.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(saved["response"]["language"], "zh")
            self.assertEqual(saved["response"]["words"][0]["word"], "你")
            transcriber.client_factory = lambda _: self.fail("completed cache was not resumed")
            self.assertEqual(transcriber.transcribe(audio)[0].speaker_id, "SPK_02")

    def test_gemini_proposal_still_feeds_next_chunk_voice_reference(self):
        payloads = [
            {"segments": [{"start": 0, "end": .8, "zh": "一", "speaker_id": "SPK_01",
                            "speaker_confidence": .9}]},
            {"segments": [{"start": 0, "end": .8, "zh": "二", "speaker_id": "SPK_01",
                            "speaker_confidence": .9}]},
        ]
        with tempfile.TemporaryDirectory() as root, patch(
                "cartoon_sub.transcription.gemini_transcriber.CHUNK_SECONDS", 1):
            root = Path(root); audio = root / "audio.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
                stream.writeframes(b"\1\0" * 32000)
            client = RecordingClient(payloads)
            rows = GeminiTranscriber(lambda: "key", "gemini-test", root / "cache", 0,
                                     lambda _: client).transcribe(audio)
        self.assertEqual([row.speaker_id for row in rows], ["SPK_01", "SPK_01"])
        self.assertEqual(client.references_seen[0], {})
        self.assertIn("SPK_01", client.references_seen[1])

    def test_confirmation_reload_and_speaker_edit_invalidation(self):
        project = Project("p", "source.mp4", segments=[
            Utterance(1, 0, 1, "你好", speaker_id="SPK_01", tts_generation_status="generated"),
        ])
        project.speakers = {
            "SPK_01": asdict(Speaker("SPK_01")),
            "SPK_02": asdict(Speaker("SPK_02")),
        }
        SpeakerResolutionService().resolve(project, "gemini")
        apply_speaker_review_state(project, project.speakers, {1: "SPK_01"})
        self.assertEqual(speaker_resolution_state(project, project.utterances[0]),
                         SpeakerResolutionState.USER_CONFIRMED)
        with tempfile.TemporaryDirectory() as root:
            manager = ProjectManager(); manager.save(project, root)
            loaded = manager.load(Path(root) / "project.json")
        self.assertTrue(review_complete(loaded))
        settings = AISettings(translation_model="provider/model")
        loaded.translation_status = "completed"
        loaded.cache_hashes["translation"] = translation_fingerprint(loaded, settings)
        loaded.final_audio_status = "generated"
        apply_speaker_review_state(loaded, loaded.speakers, {1: "SPK_02"})
        mark_stale(loaded, settings)
        self.assertEqual(loaded.translation_status, "stale")
        self.assertEqual(loaded.utterances[0].tts_generation_status, "stale")
        self.assertEqual(loaded.final_audio_status, "stale")

    def test_old_project_without_new_schema_fields_still_loads(self):
        raw = {
            "schema_version": 1,
            "name": "old",
            "source_video_path": "old.mp4",
            "segments": [{"id": 1, "start": 0, "end": 1, "zh": "你好",
                          "speaker_id": "SPK_UNKNOWN"}],
        }
        project = Project.from_dict(raw)
        self.assertEqual(project.utterances[0].speaker_id, "SPK_UNKNOWN")
        self.assertFalse(review_complete(project))


if __name__ == "__main__":
    unittest.main()
