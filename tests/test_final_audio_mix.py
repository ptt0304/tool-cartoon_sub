import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from cartoon_sub.subtitle.models import AudioSettings, Project
from cartoon_sub.tts.final_mix_service import FinalAudioMixService, compute_final_audio_fingerprint


def make_sine_wav(path: Path, freq: float, duration: float = 4.0, rate: int = 48000, amp: int = 12000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        total_frames = int(duration * rate)
        frames = bytearray()
        for i in range(total_frames):
            val = int(amp * math.sin(2 * math.pi * freq * i / rate))
            frames.extend(struct.pack("<hh", val, val))
        writer.writeframes(frames)


def read_channel_samples(path: Path, start_sec: float = 0.0, duration_sec: float = 1.0):
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        reader.setpos(int(start_sec * rate))
        num_frames = int(duration_sec * rate)
        raw = reader.readframes(num_frames)
        samples = [struct.unpack("<hh", raw[i * 4:(i + 1) * 4])[0] for i in range(len(raw) // 4)]
        return samples, rate


def spectral_energy(samples: list[int], freq: float, rate: int):
    # DFT correlation at target frequency
    cos_sum = sum(s * math.cos(2 * math.pi * freq * i / rate) for i, s in enumerate(samples))
    sin_sum = sum(s * math.sin(2 * math.pi * freq * i / rate) for i, s in enumerate(samples))
    return math.hypot(cos_sum, sin_sum) / len(samples)


class FinalAudioMixServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "audio").mkdir(parents=True, exist_ok=True)
        (self.root / "audio" / "tts").mkdir(parents=True, exist_ok=True)

        self.orig_wav = self.root / "audio" / "source.wav"
        self.dub_wav = self.root / "audio" / "tts" / "dubbed_mix.wav"
        self.add_wav = self.root / "audio" / "additional.wav"

        # Synthetic tones
        make_sine_wav(self.orig_wav, freq=440.0, duration=4.0)
        make_sine_wav(self.dub_wav, freq=880.0, duration=4.0)
        make_sine_wav(self.add_wav, freq=660.0, duration=4.0)

        self.service = FinalAudioMixService()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_final_audio_two_sources(self):
        # 1. Original 100, Dubbed 100
        p = Project("test", "source.mp4", metadata={"duration": 4.0},
                    audio_settings=AudioSettings(original_volume=100, dubbed_volume=100, additional_audio_path=None))
        out = self.service.mix(p, self.root)
        self.assertTrue(out.is_file())
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        e440 = spectral_energy(samples, 440.0, rate)
        e880 = spectral_energy(samples, 880.0, rate)
        self.assertGreater(e440, 2000)
        self.assertGreater(e880, 2000)

        # 2. Original 0, Dubbed 100
        p.audio_settings = AudioSettings(original_volume=0, dubbed_volume=100)
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertLess(spectral_energy(samples, 440.0, rate), 200)
        self.assertGreater(spectral_energy(samples, 880.0, rate), 2000)

        # 3. Original 100, Dubbed 0
        p.audio_settings = AudioSettings(original_volume=100, dubbed_volume=0)
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertGreater(spectral_energy(samples, 440.0, rate), 2000)
        self.assertLess(spectral_energy(samples, 880.0, rate), 200)

        # 4. Original 0, Dubbed 0 -> silence
        p.audio_settings = AudioSettings(original_volume=0, dubbed_volume=0)
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertLess(spectral_energy(samples, 440.0, rate), 50)
        self.assertLess(spectral_energy(samples, 880.0, rate), 50)

    def test_final_audio_three_sources(self):
        # All 100
        p = Project("test", "source.mp4", metadata={"duration": 4.0},
                    audio_settings=AudioSettings(
                        original_volume=100,
                        dubbed_volume=100,
                        additional_audio_path=str(self.add_wav),
                        additional_audio_volume=100,
                        additional_audio_start=0.0,
                    ))
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertGreater(spectral_energy(samples, 440.0, rate), 1500)
        self.assertGreater(spectral_energy(samples, 880.0, rate), 1500)
        self.assertGreater(spectral_energy(samples, 660.0, rate), 1500)

        # Additional 0 -> 660 muted
        p.audio_settings.additional_audio_volume = 0
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertGreater(spectral_energy(samples, 440.0, rate), 1500)
        self.assertGreater(spectral_energy(samples, 880.0, rate), 1500)
        self.assertLess(spectral_energy(samples, 660.0, rate), 200)

        # Original 0, Dubbed 0, Additional 100 -> only 660
        p.audio_settings.original_volume = 0
        p.audio_settings.dubbed_volume = 0
        p.audio_settings.additional_audio_volume = 100
        out = self.service.mix(p, self.root)
        samples, rate = read_channel_samples(out, 1.0, 1.0)
        self.assertLess(spectral_energy(samples, 440.0, rate), 200)
        self.assertLess(spectral_energy(samples, 880.0, rate), 200)
        self.assertGreater(spectral_energy(samples, 660.0, rate), 2000)

    def test_additional_audio_offset(self):
        # Project 5s, Additional starts at 2.0s
        make_sine_wav(self.add_wav, freq=660.0, duration=3.0)
        p = Project("test", "source.mp4", metadata={"duration": 5.0},
                    audio_settings=AudioSettings(
                        original_volume=0,
                        dubbed_volume=0,
                        additional_audio_path=str(self.add_wav),
                        additional_audio_volume=100,
                        additional_audio_start=2.0,
                    ))
        out = self.service.mix(p, self.root)
        # 0.5s -> 1.5s: before start offset -> silence
        samples_before, rate = read_channel_samples(out, 0.5, 1.0)
        self.assertLess(spectral_energy(samples_before, 660.0, rate), 50)

        # 2.5s -> 3.5s: after start offset -> 660Hz present
        samples_after, rate = read_channel_samples(out, 2.5, 1.0)
        self.assertGreater(spectral_energy(samples_after, 660.0, rate), 2000)

    def test_additional_audio_validation(self):
        # Missing file
        p = Project("test", "source.mp4", metadata={"duration": 4.0},
                    audio_settings=AudioSettings(additional_audio_path="nonexistent.mp3"))
        with self.assertRaisesRegex(ValueError, "ADDITIONAL_AUDIO_NOT_FOUND"):
            self.service.mix(p, self.root)

        # Invalid file (not an audio file)
        fake = self.root / "fake.txt"
        fake.write_text("not audio", encoding="utf-8")
        p.audio_settings.additional_audio_path = str(fake)
        with self.assertRaisesRegex(ValueError, "ADDITIONAL_AUDIO_INVALID"):
            self.service.mix(p, self.root)

    def test_audio_settings_invalidation(self):
        settings = AudioSettings(original_volume=100, dubbed_volume=100)
        fp1 = compute_final_audio_fingerprint(10.0, settings, "hash1")

        # Changing volume changes fingerprint
        settings2 = AudioSettings(original_volume=50, dubbed_volume=100)
        fp2 = compute_final_audio_fingerprint(10.0, settings2, "hash1")
        self.assertNotEqual(fp1, fp2)

        # Changing additional settings changes fingerprint
        settings3 = AudioSettings(original_volume=100, dubbed_volume=100, additional_audio_path="bgm.mp3")
        fp3 = compute_final_audio_fingerprint(10.0, settings3, "hash1")
        self.assertNotEqual(fp1, fp3)


if __name__ == "__main__":
    unittest.main()
