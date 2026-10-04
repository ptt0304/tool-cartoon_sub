import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.service import approve_review
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.tts.generation_service import LocalTTSGenerationService
from cartoon_sub.tts.local_tts_client import LocalTTSError
from cartoon_sub.tts.cache_manifest import segment_manifest_key


def wav_bytes(seconds=0.1, rate=8000):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\0\0" * int(rate * seconds))
    return output.getvalue()


class FakeLocalTTSClient:
    def __init__(self):
        self.generate_calls = []
        self.download_calls = []
        self.fail_texts = set()
        self.settings = SimpleNamespace(base_url="http://127.0.0.1:8765")
        self.metadata_duration = 1.25
        self.wav_seconds = 0.1
        self.voices = [
            {"voice_id": "voice_a", "display_name": "A", "engine": "piper", "status": "READY"},
            {"voice_id": "voice_b", "display_name": "B", "engine": "vieneu", "status": "READY"},
            {"voice_id": "pending", "display_name": "Pending", "engine": "vieneu", "status": "REQUIRES_REFERENCE"},
        ]

    def health(self):
        return {"status": "READY", "voices": 2}

    def list_voices(self):
        return self.voices

    def generate(self, segment_id, speaker_id, voice_id, text, speed):
        self.generate_calls.append({
            "segment_id": segment_id, "speaker_id": speaker_id, "voice_id": voice_id,
            "text": text, "speed": speed,
        })
        if text in self.fail_texts:
            raise LocalTTSError("GENERATION_FAILED", "mock generation failed", 500)
        return {"audio_url": f"/api/tts/audio/{segment_id}", "duration": self.metadata_duration}

    def download_audio(self, audio_url):
        self.download_calls.append(audio_url)
        return wav_bytes(self.wav_seconds)


class TTSGenerationTests(unittest.TestCase):
    def make_project(self, root, *, two=False):
        rows = [Segment(
            31, 1.0, 3.0, "中文", vi_subtitle="Phụ đề khác",
            vi_dubbing="Lời lồng tiếng", speaker_id="SPK_01", speaker_name="Một",
        )]
        speakers = {
            "SPK_01": {
                "id": "SPK_01", "name": "Một", "voice_notes": "",
                "tts_voice_id": "voice_a", "tts_speed": 1.0,
            }
        }
        if two:
            rows.append(Segment(
                32, 2.0, 4.0, "失败", vi_subtitle="Phụ đề lỗi",
                vi_dubbing="Lỗi tổng hợp", speaker_id="SPK_02", speaker_name="Hai",
            ))
            speakers["SPK_02"] = {
                "id": "SPK_02", "name": "Hai", "voice_notes": "",
                "tts_voice_id": "voice_b", "tts_speed": 0.95,
            }
        project = Project("tts", "source.mp4", metadata={"duration": 6.0}, segments=rows, speakers=speakers)
        approve_review(project)
        ProjectManager().save(project, root)
        return project

    def test_uses_vi_dubbing_once_with_speaker_voice_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            messages = []
            result = LocalTTSGenerationService(client).generate(project, directory, progress=messages.append)
            self.assertEqual(result.generated, 1)
            self.assertEqual(len(client.generate_calls), 1)
            self.assertEqual(client.generate_calls[0]["text"], "Lời lồng tiếng")
            self.assertNotEqual(client.generate_calls[0]["text"], project.utterances[0].vi_subtitle)
            self.assertEqual(client.generate_calls[0]["voice_id"], "voice_a")
            self.assertIn("TTS 1/1 | VI Dubbing | SPK_01 | Utterance 31 | A", messages)
            row = ProjectManager().load(directory).utterances[0]
            self.assertEqual(row.tts_generation_status, "generated")
            self.assertTrue(row.tts_fingerprint)
            self.assertEqual(row.tts_segment_id, f"utt_{row.tts_cache_key}_vi_dubbing")
            self.assertFalse(Path(row.tts_audio_path).is_absolute())
            self.assertTrue((Path(directory) / row.tts_audio_path).is_file())
            self.assertFalse((Path(directory) / "audio" / "tts" / "dubbed_mix.wav").exists())
            self.assertAlmostEqual(row.tts_duration, 0.1, places=3)
            self.assertEqual(row.tts_alignment_status, "fits")

    def test_subtitle_and_dubbing_sources_use_independent_cache_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            dubbing = LocalTTSGenerationService(client, text_source="vi_dubbing")
            dubbing.generate(project, directory)
            dubbing_path = project.utterances[0].tts_audio_path
            self.assertEqual(client.generate_calls[-1]["text"], "Lời lồng tiếng")

            subtitle = LocalTTSGenerationService(client, text_source="vi_subtitle")
            subtitle.generate(project, directory)
            subtitle_path = project.utterances[0].tts_audio_path
            self.assertEqual(client.generate_calls[-1]["text"], "Phụ đề khác")
            self.assertNotEqual(subtitle_path, dubbing_path)
            self.assertTrue((Path(directory) / subtitle_path).is_file())
            self.assertTrue((Path(directory) / dubbing_path).is_file())

            client.generate_calls.clear()
            resumed = dubbing.generate(project, directory)
            self.assertEqual((resumed.cached, resumed.generated), (1, 0))
            self.assertEqual(client.generate_calls, [])
            self.assertEqual(project.utterances[0].tts_audio_path, dubbing_path)
            manifest = json.loads(
                (Path(directory) / "audio" / "tts" / "tts_cache.json").read_text(encoding="utf-8")
            )
            key = project.utterances[0].tts_cache_key
            self.assertIn(segment_manifest_key(key, "vi_dubbing"), manifest["segments"])
            self.assertIn(segment_manifest_key(key, "vi_subtitle"), manifest["segments"])

    def test_progress_uses_friendly_name_while_request_uses_canonical_voice_id(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            voice_id = "vieneu_user_capcut_co_gai_hoat_ngon"
            project.speakers["SPK_01"]["tts_voice_id"] = voice_id
            client = FakeLocalTTSClient()
            client.voices = [{
                "voice_id": voice_id,
                "display_name": "capcut_cô gái hoạt ngôn",
                "engine": "vieneu_v3",
                "status": "READY",
            }]
            progress = []

            LocalTTSGenerationService(client).generate(project, directory, progress=progress.append)

            self.assertEqual(client.generate_calls[0]["voice_id"], voice_id)
            self.assertTrue(any(message.endswith("capcut_cô gái hoạt ngôn") for message in progress))

    def test_unchanged_fingerprint_reuses_valid_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            client.generate_calls.clear()
            result = service.generate(project, directory)
            self.assertEqual(result.cached, 1)
            self.assertEqual(client.generate_calls, [])
            self.assertEqual(project.utterances[0].tts_generation_status, "cached")

    def test_text_voice_and_speed_changes_each_invalidate_cache(self):
        mutations = (
            lambda project: setattr(project.utterances[0], "vi_dubbing", "Lời mới"),
            lambda project: project.speakers["SPK_01"].update(tts_voice_id="voice_b"),
            lambda project: project.speakers["SPK_01"].update(tts_speed=1.2),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as directory:
                project = self.make_project(directory)
                client = FakeLocalTTSClient()
                service = LocalTTSGenerationService(client)
                service.generate(project, directory)
                first_id = project.utterances[0].tts_segment_id
                first_fingerprint = project.utterances[0].tts_fingerprint
                client.generate_calls.clear()
                mutate(project)
                service.generate(project, directory)
                self.assertEqual(len(client.generate_calls), 1)
                self.assertEqual(project.utterances[0].tts_segment_id, first_id)
                self.assertNotEqual(project.utterances[0].tts_fingerprint, first_fingerprint)

    def test_manifest_uses_stable_cache_key_and_renumbering_is_a_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            row = project.utterances[0]
            stable_key = row.tts_cache_key
            original_path = row.tts_audio_path
            manifest_path = Path(directory) / "audio" / "tts" / "tts_cache.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], 1)
            self.assertIn(segment_manifest_key(stable_key, "vi_dubbing"), manifest["segments"])

            row.id = 99
            approve_review(project)
            client.generate_calls.clear()
            result = service.generate(project, directory)

            self.assertEqual(result.cached, 1)
            self.assertEqual(client.generate_calls, [])
            self.assertEqual(row.tts_cache_key, stable_key)
            self.assertEqual(row.tts_audio_path, original_path)

    def test_new_and_deleted_utterance_only_touch_owned_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            original = project.utterances[0]
            original_key = original.tts_cache_key
            original_path = Path(directory) / original.tts_audio_path

            project.utterances.append(Segment(
                32, 3.1, 4.5, "新增", vi_subtitle="Câu mới",
                vi_dubbing="Câu mới", speaker_id="SPK_01", speaker_name="Một",
            ))
            approve_review(project)
            client.generate_calls.clear()
            added = service.generate(project, directory)
            self.assertEqual(added.new, 1)
            self.assertEqual(added.cached, 1)
            self.assertEqual(len(client.generate_calls), 1)
            remaining_path = Path(directory) / project.utterances[1].tts_audio_path

            project.utterances.pop(0)
            approve_review(project)
            client.generate_calls.clear()
            deleted = service.generate(project, directory)
            manifest = json.loads(
                (Path(directory) / "audio" / "tts" / "tts_cache.json").read_text(encoding="utf-8")
            )
            self.assertEqual(deleted.deleted, 1)
            self.assertFalse(original_path.exists())
            self.assertTrue(remaining_path.exists())
            self.assertNotIn(segment_manifest_key(original_key, "vi_dubbing"), manifest["segments"])
            self.assertEqual(client.generate_calls, [])

    def test_voice_change_regenerates_only_affected_speaker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory, two=True)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            client.generate_calls.clear()

            project.speakers["SPK_01"]["tts_voice_id"] = "voice_b"
            result = service.generate(project, directory)

            self.assertEqual(result.changed, 1)
            self.assertEqual(result.cached, 1)
            self.assertEqual(
                [call["speaker_id"] for call in client.generate_calls], ["SPK_01"],
            )

    def test_missing_file_regenerates_only_that_utterance(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory, two=True)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            (Path(directory) / project.utterances[0].tts_audio_path).unlink()
            client.generate_calls.clear()

            result = service.generate(project, directory)

            self.assertEqual(result.missing_file, 1)
            self.assertEqual(result.cached, 1)
            self.assertEqual(len(client.generate_calls), 1)
            self.assertEqual(client.generate_calls[0]["speaker_id"], "SPK_01")

    def test_cancel_resume_reuses_every_successfully_persisted_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory, two=True)
            client = FakeLocalTTSClient()
            cancel = Event()

            def cancel_after_first_success(message):
                if str(message).startswith("TTS saved 1/"):
                    cancel.set()

            with self.assertRaises(CancelledError):
                LocalTTSGenerationService(client).generate(
                    project, directory, cancel=cancel, progress=cancel_after_first_success,
                )
            self.assertEqual(len(client.generate_calls), 1)

            client.generate_calls.clear()
            resumed = LocalTTSGenerationService(client).generate(project, directory)
            self.assertEqual(resumed.cached, 1)
            self.assertEqual(resumed.generated, 1)
            self.assertEqual(len(client.generate_calls), 1)

    def test_failure_persists_and_resume_only_regenerates_failed_cue(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory, two=True)
            client = FakeLocalTTSClient()
            client.fail_texts.add("Lỗi tổng hợp")
            service = LocalTTSGenerationService(client)
            first = service.generate(project, directory)
            self.assertEqual(first.failed_ids, [32])
            persisted = ProjectManager().load(directory)
            self.assertEqual(persisted.utterances[0].tts_generation_status, "generated")
            self.assertEqual(persisted.utterances[1].tts_generation_status, "failed")
            self.assertIn("GENERATION_FAILED", persisted.utterances[1].tts_error)
            self.assertIsNone(persisted.utterances[1].tts_audio_path)

            client.fail_texts.clear()
            client.generate_calls.clear()
            second = service.generate(project, directory)
            self.assertEqual(second.cached, 1)
            self.assertEqual(second.generated, 1)
            self.assertEqual(second.failed_ids, [])
            self.assertEqual([call["speaker_id"] for call in client.generate_calls], ["SPK_02"])

    def test_server_change_invalidates_cache_and_changes_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            local = LocalTTSGenerationService(client)
            local.generate(project, directory)
            local_fingerprint = project.utterances[0].tts_fingerprint
            client.generate_calls.clear()
            remote = LocalTTSGenerationService(
                client, server_base_url=" https://ABC.trycloudflare.com/ "
            )
            remote.generate(project, directory)
            self.assertEqual(len(client.generate_calls), 1)
            self.assertNotEqual(project.utterances[0].tts_fingerprint, local_fingerprint)

    def test_timing_change_does_not_invalidate_raw_tts_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            project.utterances[0].start += 0.25
            project.utterances[0].end += 0.25
            approve_review(project)
            client.generate_calls.clear()

            result = service.generate(project, directory)

            self.assertEqual(result.cached, 1)
            self.assertEqual(client.generate_calls, [])

    def test_engine_change_with_same_voice_id_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            service = LocalTTSGenerationService(client)
            service.generate(project, directory)
            client.voices[0]["engine"] = "piper_v2"
            client.generate_calls.clear()

            result = service.generate(project, directory)

            self.assertEqual(result.changed, 1)
            self.assertEqual(len(client.generate_calls), 1)

    def test_downloaded_wav_duration_is_source_of_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            client = FakeLocalTTSClient()
            client.metadata_duration = 1.0
            client.wav_seconds = 2.0
            LocalTTSGenerationService(client).generate(project, directory)
            self.assertAlmostEqual(project.utterances[0].tts_duration, 2.0, places=3)
            self.assertEqual(project.utterances[0].tts_alignment_status, "fits")

    def test_missing_or_non_ready_speaker_voice_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            project.speakers["SPK_01"]["tts_voice_id"] = None
            with self.assertRaisesRegex(ValueError, "chưa chọn"):
                LocalTTSGenerationService(FakeLocalTTSClient()).generate(project, directory)
            project.speakers["SPK_01"]["tts_voice_id"] = "pending"
            with self.assertRaisesRegex(ValueError, "chưa READY"):
                LocalTTSGenerationService(FakeLocalTTSClient()).generate(project, directory)

    def test_missing_vi_dubbing_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            project.utterances[0].vi_dubbing = " "
            with self.assertRaisesRegex(ValueError, "vi_dubbing"):
                LocalTTSGenerationService(FakeLocalTTSClient()).generate(project, directory)

    def test_cancel_uses_shared_cancel_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            cancel = Event()
            cancel.set()
            with self.assertRaises(CancelledError):
                LocalTTSGenerationService(FakeLocalTTSClient()).generate(project, directory, cancel=cancel)


if __name__ == "__main__":
    unittest.main()
