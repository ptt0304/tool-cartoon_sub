import io
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.service import approve_review
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.tts.generation_service import LocalTTSGenerationService
from cartoon_sub.tts.mix_service import TTSTimelineMixService


BASE_URL = "http://127.0.0.1:8765"


def constant_wav(value, seconds=3.0, rate=8000):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(struct.pack("<h", value) * int(seconds * rate))
    return output.getvalue()


def sample_at(reader, seconds):
    reader.setpos(round(seconds * reader.getframerate()))
    return abs(struct.unpack("<hh", reader.readframes(1))[0])


class MockHTTPStyleLocalTTSClient:
    def __init__(self):
        self.settings = SimpleNamespace(base_url=BASE_URL)
        self.generate_calls = []
        self.audio_by_url = {}

    def health(self):
        return {"status": "READY", "voices": 3}

    def list_voices(self):
        return [
            {"voice_id": "voice_a", "status": "READY"},
            {"voice_id": "voice_a_changed", "status": "READY"},
            {"voice_id": "voice_b", "status": "READY"},
        ]

    def generate(self, segment_id, speaker_id, voice_id, text, speed):
        self.generate_calls.append({
            "segment_id": segment_id,
            "speaker_id": speaker_id,
            "voice_id": voice_id,
            "text": text,
            "speed": speed,
        })
        audio_url = f"/api/tts/audio/{segment_id}"
        value = 1000 if speaker_id == "SPK_01" else 2000
        self.audio_by_url[audio_url] = constant_wav(value)
        return {"audio_url": audio_url, "duration": 99.0}

    def download_audio(self, audio_url):
        return self.audio_by_url[audio_url]


class Phase1EndToEndTests(unittest.TestCase):
    def test_mapping_generate_cache_overlap_mix_and_stale_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Project(
                "phase1", "source.mp4", metadata={"duration": 6.0},
                segments=[
                    Segment(1, 1.0, 4.0, vi_subtitle="Phụ đề một", vi_dubbing="Lồng tiếng một", speaker_id="SPK_01", speaker_name="Một"),
                    Segment(2, 2.0, 5.0, vi_subtitle="Phụ đề hai", vi_dubbing="Lồng tiếng hai", speaker_id="SPK_02", speaker_name="Hai"),
                ],
                speakers={
                    "SPK_01": {"id": "SPK_01", "name": "Một", "tts_voice_id": "voice_a", "tts_speed": 1.0},
                    "SPK_02": {"id": "SPK_02", "name": "Hai", "tts_voice_id": "voice_b", "tts_speed": 0.95},
                },
            )
            approve_review(project)
            manager = ProjectManager()
            manager.save(project, root)
            client = MockHTTPStyleLocalTTSClient()
            generator = LocalTTSGenerationService(client, manager)

            generated = generator.generate(project, root)
            self.assertEqual(generated.generated, 2)
            self.assertEqual(len(client.generate_calls), 2)
            self.assertEqual(
                [(call["speaker_id"], call["voice_id"], call["text"]) for call in client.generate_calls],
                [
                    ("SPK_01", "voice_a", "Lồng tiếng một"),
                    ("SPK_02", "voice_b", "Lồng tiếng hai"),
                ],
            )
            self.assertEqual(len(list((root / "audio" / "tts" / "segments").glob("*.wav"))), 2)
            persisted = manager.load(root)
            self.assertTrue(all(row.tts_generation_status == "generated" for row in persisted.utterances))
            self.assertTrue(all(row.tts_fingerprint and row.tts_segment_id for row in persisted.utterances))
            self.assertTrue(all(row.tts_duration == 3.0 for row in persisted.utterances))

            mix = TTSTimelineMixService().mix(project, root, BASE_URL)
            self.assertTrue(mix.is_file())
            with wave.open(str(mix), "rb") as reader:
                self.assertAlmostEqual(reader.getnframes() / reader.getframerate(), 6.0, places=3)
                first_only = sample_at(reader, 1.5)
                overlap = sample_at(reader, 2.5)
                second_only = sample_at(reader, 4.5)
            self.assertGreater(overlap, first_only)
            self.assertGreater(overlap, second_only)

            project.speakers["SPK_01"]["tts_voice_id"] = "voice_a_changed"
            with self.assertRaisesRegex(ValueError, "TTS_AUDIO_STALE: Utterance 1"):
                TTSTimelineMixService().mix(project, root, BASE_URL)

            resumed = generator.generate(project, root)
            self.assertEqual((resumed.generated, resumed.cached), (1, 1))
            self.assertEqual(len(client.generate_calls), 3)
            self.assertEqual(client.generate_calls[-1]["voice_id"], "voice_a_changed")
            remixed = TTSTimelineMixService().mix(project, root, BASE_URL)
            self.assertTrue(remixed.is_file())


if __name__ == "__main__":
    unittest.main()
