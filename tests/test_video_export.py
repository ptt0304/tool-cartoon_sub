import os
import tempfile
import unittest
import wave
from pathlib import Path

from PySide6.QtWidgets import QApplication

from cartoon_sub.media.preview import VideoRenderer, validate_final_audio
from cartoon_sub.media.process import run_process
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.subtitle.models import AudioSettings, Mask, Project, SubtitleStyle
from cartoon_sub.ui.main_window import MainWindow


def make_test_video(path: Path, duration: float = 60.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10",
        "-f", "lavfi", "-i", f"sine=frequency=400:sample_rate=48000",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(path),
    ]
    run_process(cmd)


def make_test_final_audio(path: Path, duration: float = 60.0, rate: int = 48000):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=880:sample_rate={rate}",
        "-t", f"{duration:.2f}",
        "-ar", str(rate), "-ac", "2", "-c:a", "pcm_s16le",
        str(path),
    ]
    run_process(cmd)


class VideoExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tab_order(self):
        window = MainWindow()
        tab_count = window.tabs.count()
        self.assertEqual(tab_count, 7)
        titles = [window.tabs.tabText(i) for i in range(tab_count)]
        expected = ["Video", "Transcript", "Translate", "Subtitle", "Mask", "Audio", "Export"]
        self.assertEqual(titles, expected)

    def test_export_validation_missing_stale_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "audio").mkdir(parents=True, exist_ok=True)
            p = Project("test", "source.mp4", metadata={"duration": 30.0})

            # 1. Missing final_audio.wav
            with self.assertRaisesRegex(ValueError, "FINAL_AUDIO_NOT_FOUND"):
                validate_final_audio(p, root)

            # 2. Invalid final_audio.wav
            final_audio = root / "audio" / "final_audio.wav"
            final_audio.write_bytes(b"corrupt header")
            with self.assertRaisesRegex(ValueError, "FINAL_AUDIO_INVALID"):
                validate_final_audio(p, root)

            # 3. Stale final_audio.wav
            make_test_final_audio(final_audio, duration=10.0)
            p.final_audio_status = "stale"
            with self.assertRaisesRegex(ValueError, "FINAL_AUDIO_STALE"):
                validate_final_audio(p, root)

            # 4. Ready final_audio.wav
            p.final_audio_status = "ready"
            validated = validate_final_audio(p, root)
            self.assertEqual(validated, final_audio)

    def test_test_30s_and_full_export_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_video = root / "source.mp4"
            final_audio = root / "audio" / "final_audio.wav"
            make_test_video(source_video, duration=60.0)
            make_test_final_audio(final_audio, duration=60.0)

            p = Project(
                "export_test",
                str(source_video),
                metadata={"duration": 60.0, "width": 320, "height": 180},
                final_audio_status="ready",
                mask=Mask(enabled=False),
                subtitle_style=SubtitleStyle(),
            )

            renderer = VideoRenderer()

            # 1. Test 30s export from start = 20.0s
            out_30s = renderer.render(
                p, root, start=20.0, preview=False, duration=30.0, use_final_audio=True,
                export_name="test_30s.mp4"
            )
            self.assertEqual(out_30s, root / "test_30s.mp4")
            self.assertTrue(out_30s.is_file())
            info_30s = probe(str(out_30s))
            self.assertAlmostEqual(info_30s["duration"], 30.0, delta=0.5)

            # 2. Test 30s export clamped at end of video: project 40s, start 30s -> expected ~10s
            p.metadata["duration"] = 40.0
            out_clamp = renderer.render(
                p, root, start=30.0, preview=False, duration=30.0, use_final_audio=True
            )
            self.assertTrue(out_clamp.is_file())
            info_clamp = probe(str(out_clamp))
            self.assertAlmostEqual(info_clamp["duration"], 10.0, delta=0.5)

            # 3. Full video export: start = 0, duration = None
            p.metadata["duration"] = 15.0
            make_test_video(source_video, duration=15.0)
            make_test_final_audio(final_audio, duration=15.0)
            out_full = renderer.render(
                p, root, start=0.0, preview=False, duration=None, use_final_audio=True,
                export_name="final.mp4"
            )
            self.assertEqual(out_full, root / "final.mp4")
            self.assertTrue(out_full.is_file())
            info_full = probe(str(out_full))
            self.assertAlmostEqual(info_full["duration"], 15.0, delta=0.5)
            self.assertFalse((root / ".tmp" / "export").exists())

            # Ensure source video was untouched
            self.assertTrue(source_video.is_file())


if __name__ == "__main__":
    unittest.main()
