from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import AudioSettings, Project
from cartoon_sub.ui.tabs.audio_tab import AudioPage
from cartoon_sub.tts.final_mix_service import FinalAudioMixService


DUMMY_WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


class FinalAudioPlaybackAndBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_play_stop_toggle_and_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audio_dir = root / "audio"
            audio_dir.mkdir(parents=True)
            final_wav = audio_dir / "final_audio.wav"
            final_wav.write_bytes(DUMMY_WAV)

            page = AudioPage()
            self.assertEqual(page.final_play_btn.text(), "Play Final Audio")

            proj = Project("test", "test.mp4", metadata={"duration": 10.0})
            page.populate(proj, project_dir=root)
            self.assertTrue(page.final_play_btn.isEnabled())

            # 1. Play
            page._play_final_audio()
            self.assertTrue(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Stop Final Audio")
            self.assertTrue(page.player.source().toString().endswith("final_audio.wav"))

            # 2. Stop toggle -> source must be cleared
            page._play_final_audio()
            self.assertFalse(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Play Final Audio")
            self.assertEqual(page.player.source().toString(), "")

            # 3. Play again
            page._play_final_audio()
            self.assertTrue(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Stop Final Audio")

            # 4. StoppedState / EndOfMedia -> source must be cleared
            page._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertFalse(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Play Final Audio")
            self.assertEqual(page.player.source().toString(), "")

            # 5. Play final audio then preview another sound
            page._play_final_audio()
            self.assertTrue(page._is_playing_final)
            other_wav = audio_dir / "other.wav"
            other_wav.write_bytes(DUMMY_WAV)
            page.play_audio(other_wav)
            self.assertFalse(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Play Final Audio")

            page.reset_media()
            self.app.processEvents()

    def test_build_final_audio_while_playing_clears_source_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            audio_dir = root / "audio"
            audio_dir.mkdir(parents=True)
            final_wav = audio_dir / "final_audio.wav"
            final_wav.write_bytes(DUMMY_WAV)

            page = AudioPage()
            proj = Project(
                "test", "test.mp4", metadata={"duration": 2.0},
                audio_settings=AudioSettings(original_volume=0, dubbed_volume=0),
            )
            page.populate(proj, project_dir=root)

            # Start playing final audio
            page._play_final_audio()
            self.assertTrue(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Stop Final Audio")
            self.assertTrue(bool(page.player.source().toString()))

            # User clicks "Build Final Audio" -> should stop playback and clear source
            build_requested = []
            page.mix_final_requested.connect(lambda: build_requested.append(True))
            page._on_final_mix_clicked()

            self.assertEqual(len(build_requested), 1)
            self.assertFalse(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Play Final Audio")
            self.assertEqual(page.player.source().toString(), "")
            self.app.processEvents()

            # Now atomic replace should succeed without WinError 5
            tmp_wav = audio_dir / "final_audio.tmp.wav"
            new_content = DUMMY_WAV + b"\x01\x02\x03\x04"
            tmp_wav.write_bytes(new_content)

            # Perform atomic replace as in FinalAudioMixService
            tmp_wav.replace(final_wav)
            self.assertEqual(final_wav.read_bytes(), new_content)

            # User clicks Play Final Audio after rebuild -> plays new file
            page._play_final_audio()
            self.assertTrue(page._is_playing_final)
            self.assertEqual(page.final_play_btn.text(), "Stop Final Audio")
            self.assertTrue(page.player.source().toString().endswith("final_audio.wav"))

            page.reset_media()
            self.app.processEvents()

    def test_atomic_replace_retry_and_locked_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dest = root / "final_audio.wav"
            tmp = root / "final_audio.tmp.wav"
            dest.write_bytes(b"initial")
            tmp.write_bytes(b"replacement")

            # Open file exclusively to simulate an external app lock (VLC / editor)
            with open(dest, "r+b") as locked_handle:
                service = FinalAudioMixService()
                with self.assertRaises(ValueError) as caught:
                    # simulate attempt in final_mix_service
                    for attempt in range(5):
                        try:
                            import os
                            os.replace(tmp, dest)
                            break
                        except PermissionError:
                            if attempt < 4:
                                time.sleep(0.01)
                            else:
                                raise ValueError(
                                    f"FINAL_AUDIO_FILE_LOCKED: {dest.name} đang được một ứng dụng khác sử dụng "
                                    "(ví dụ media player hoặc trình chỉnh sửa ngoài). Hãy đóng ứng dụng đang mở file và thử lại."
                                )
                self.assertIn("FINAL_AUDIO_FILE_LOCKED", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
