import hashlib
import shutil
import struct
import tempfile
import json
import subprocess
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.subtitle.models import AudioSettings, Project
from cartoon_sub.tts.final_mix_service import FinalAudioMixService
from cartoon_sub.tts.mix_service import MASTER_TTS_FILTER, process_final_tts_audio


def write_wav(path, *, seconds=0.25, rate=8000, channels=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = struct.pack("<h", 1000) * channels
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frame * round(seconds * rate))


def wav_info(path):
    with wave.open(str(path), "rb") as reader:
        return reader.getnframes() / reader.getframerate(), reader.getframerate(), reader.getnchannels(), reader.getsampwidth()


def ffprobe_info(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    return payload["streams"][0], float(payload["format"]["duration"])


class FinalTTSAudioProcessingTests(unittest.TestCase):
    def test_master_processing_uses_exact_filter_preserves_raw_and_outputs_pcm_s24le(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "dubbed_mix.raw.wav"
            final = root / "dubbed_mix.wav"
            segment = root / "segments" / "segment_001.wav"
            write_wav(raw); write_wav(segment)
            raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
            segment_hash = hashlib.sha256(segment.read_bytes()).hexdigest()
            output = process_final_tts_audio(raw, final)
            self.assertEqual(output, final)
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), raw_hash)
            self.assertEqual(hashlib.sha256(segment.read_bytes()).hexdigest(), segment_hash)
            before, rate, channels, _ = wav_info(raw)
            stream, after = ffprobe_info(final)
            self.assertAlmostEqual(after, before, places=3)
            self.assertEqual((int(stream["sample_rate"]), int(stream["channels"])), (rate, channels))
            self.assertEqual(stream["codec_name"], "pcm_s24le")
            self.assertEqual(MASTER_TTS_FILTER, "volume=20dB,alimiter=limit=0.891251:level=false")

    def test_processing_runs_one_master_command_with_exact_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "raw.wav"; final = root / "final.wav"
            write_wav(raw); commands = []
            def fake_run(command, **_kwargs):
                commands.append(command)
                if command[0] == "ffmpeg":
                    shutil.copyfile(raw, Path(command[-1]))
                else:
                    return json.dumps({"streams": [{"sample_rate": "8000", "channels": 1, "duration": "0.25"}], "format": {"duration": "0.25"}})
            with patch("cartoon_sub.tts.mix_service.run_process", side_effect=fake_run):
                process_final_tts_audio(raw, final)
            master_commands = [command for command in commands if command[0] == "ffmpeg"]
            self.assertEqual(len(master_commands), 1)
            self.assertEqual(master_commands[0][master_commands[0].index("-af") + 1], MASTER_TTS_FILTER)
            self.assertEqual(master_commands[0][master_commands[0].index("-c:a") + 1], "pcm_s24le")

    def test_processing_failure_preserves_existing_complete_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "raw.wav"; final = root / "dubbed_mix.wav"
            write_wav(raw); final.write_bytes(b"known-good-existing-mix")
            with patch("cartoon_sub.tts.mix_service.run_process", side_effect=RuntimeError("ffmpeg failed")):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    process_final_tts_audio(raw, final)
            self.assertEqual(final.read_bytes(), b"known-good-existing-mix")
            self.assertFalse((root / "dubbed_mix.boosted.tmp.wav").exists())

    def test_final_audio_mixer_consumes_processed_dubbed_mix_not_raw_intermediate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "audio" / "tts" / "dubbed_mix.wav"
            raw = root / "audio" / "tts" / "dubbed_mix.raw.wav"
            write_wav(processed); write_wav(raw)
            project = Project("demo", "", metadata={"duration": 0.25}, audio_settings=AudioSettings(original_volume=0, dubbed_volume=100))
            captured = []
            def fake_run(command, **_kwargs):
                captured.append(command)
                write_wav(Path(command[-1]))
            with patch("cartoon_sub.tts.final_mix_service.run_process", side_effect=fake_run):
                FinalAudioMixService().mix(project, root)
            self.assertIn(str(processed), captured[0])
            self.assertNotIn(str(raw), captured[0])


if __name__ == "__main__":
    unittest.main()
