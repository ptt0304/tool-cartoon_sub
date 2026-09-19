import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.tts.cache_identity import build_tts_fingerprint, build_tts_segment_id
from cartoon_sub.tts.mix_service import TTSTimelineMixService


BASE_URL = "http://127.0.0.1:8765"


def write_constant_wav(path, value, seconds=3.0, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(struct.pack("<h", value) * int(seconds * rate))


def sample_at(reader, seconds):
    reader.setpos(round(seconds * reader.getframerate()))
    frame = reader.readframes(1)
    return struct.unpack("<hh", frame)[0]


def mark_current(project, utterance):
    speaker = Speaker(**project.speakers[utterance.speaker_id])
    fingerprint = build_tts_fingerprint(utterance, speaker, BASE_URL)
    utterance.tts_fingerprint = fingerprint
    utterance.tts_segment_id = build_tts_segment_id(utterance, fingerprint)


class TTSTimelineMixTests(unittest.TestCase):
    def make_project(self, root):
        first_path = root / "audio" / "tts" / "segments" / "first.wav"
        second_path = root / "audio" / "tts" / "segments" / "second.wav"
        write_constant_wav(first_path, 1000)
        write_constant_wav(second_path, 2000)
        first = Segment(
            1, 1.0, 4.0, vi_dubbing="Một", speaker_id="SPK_01",
            tts_audio_path=first_path.relative_to(root).as_posix(),
            tts_duration=3.0, tts_generation_status="generated",
        )
        second = Segment(
            2, 2.0, 5.0, vi_dubbing="Hai", speaker_id="SPK_02",
            tts_audio_path=second_path.relative_to(root).as_posix(),
            tts_duration=3.0, tts_generation_status="generated",
        )
        project = Project(
            "mix", "source.mp4", metadata={"duration": 6.0}, segments=[first, second],
            speakers={
                "SPK_01": {"id": "SPK_01", "name": "Một", "tts_voice_id": "voice_a", "tts_speed": 1.0},
                "SPK_02": {"id": "SPK_02", "name": "Hai", "tts_voice_id": "voice_b", "tts_speed": 1.0},
            },
        )
        for utterance in project.utterances:
            mark_current(project, utterance)
        return project

    def test_mix_places_overlapping_utterances_on_master_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            original_times = [(row.start, row.end) for row in project.utterances]

            output = TTSTimelineMixService().mix(project, root, BASE_URL)

            self.assertEqual(output, root / "audio" / "tts" / "dubbed_mix.wav")
            self.assertTrue(output.is_file())
            self.assertFalse((output.parent / "dubbed_mix.tmp.wav").exists())
            with wave.open(str(output), "rb") as reader:
                self.assertEqual(reader.getframerate(), 48000)
                self.assertEqual(reader.getnchannels(), 2)
                self.assertAlmostEqual(reader.getnframes() / reader.getframerate(), 6.0, places=3)
                silence = abs(sample_at(reader, 0.5))
                first_only = abs(sample_at(reader, 1.5))
                overlap = abs(sample_at(reader, 2.5))
                second_only = abs(sample_at(reader, 4.5))
            self.assertLess(silence, 10)
            self.assertGreater(first_only, 500)
            self.assertGreater(second_only, 1000)
            self.assertGreater(overlap, first_only)
            self.assertGreater(overlap, second_only)
            self.assertEqual([(row.start, row.end) for row in project.utterances], original_times)

    def test_stale_status_or_changed_mapping_prevents_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            project.utterances[0].tts_generation_status = "stale"
            with self.assertRaisesRegex(ValueError, "TTS_AUDIO_STALE: Utterance 1"):
                TTSTimelineMixService().mix(project, root, BASE_URL)

            project.utterances[0].tts_generation_status = "generated"
            project.speakers["SPK_01"]["tts_voice_id"] = "changed_voice"
            with self.assertRaisesRegex(ValueError, "TTS_AUDIO_STALE: Utterance 1"):
                TTSTimelineMixService().mix(project, root, BASE_URL)

    def test_failed_remix_preserves_previous_final(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            output = root / "audio" / "tts" / "dubbed_mix.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"previous-complete-mix")
            with patch("cartoon_sub.tts.mix_service.run_process", side_effect=RuntimeError("ffmpeg failed")):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    TTSTimelineMixService().mix(project, root, BASE_URL)
            self.assertEqual(output.read_bytes(), b"previous-complete-mix")
            self.assertFalse((output.parent / "dubbed_mix.tmp.wav").exists())

    def test_failed_utterance_prevents_mix_and_complete_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            project.utterances[0].tts_generation_status = "failed"
            with self.assertRaisesRegex(ValueError, "TTS_AUDIO_STALE: Utterance 1"):
                TTSTimelineMixService().mix(project, root, BASE_URL)
            self.assertFalse((root / "audio" / "tts" / "dubbed_mix.wav").exists())


if __name__ == "__main__":
    unittest.main()
