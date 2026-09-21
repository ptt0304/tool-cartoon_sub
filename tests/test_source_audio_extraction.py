import tempfile
import unittest
import wave
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from cartoon_sub.media.ffmpeg import FFmpeg, NoAudioStreamError
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.app.controller import Controller
from cartoon_sub.subtitle.models import Project
from cartoon_sub.transcription.pipeline import TranscriptionPipeline
from cartoon_sub.ui.main_window import MainWindow


def write_wav(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 160)


class SourceAudioExtractionTests(unittest.TestCase):
    def test_probe_accepts_video_with_audio_and_unicode_path(self):
        payload = {"format": {"duration": "1.5"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "width": 640, "height": 360, "avg_frame_rate": "25/1"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ]}
        source = Path("D:/project có spaces/动画.mp4")
        with patch("cartoon_sub.media.ffprobe.run_process", return_value=json.dumps(payload)) as run:
            metadata = probe(source, require_audio=True)
        self.assertEqual(metadata["audio_codec"], "aac")
        self.assertEqual(run.call_args.args[0][-1], source)

    def test_probe_distinguishes_video_only_from_invalid_media(self):
        video_only = {"format": {"duration": "1"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "av1",
             "width": 640, "height": 360, "avg_frame_rate": "24/1"},
        ]}
        with patch("cartoon_sub.media.ffprobe.run_process", return_value=json.dumps(video_only)):
            with self.assertRaisesRegex(NoAudioStreamError, "NO_AUDIO_STREAM"):
                probe("abc.mp4", require_audio=True)
        with patch("cartoon_sub.media.ffprobe.run_process", side_effect=RuntimeError("invalid data")):
            with self.assertRaisesRegex(RuntimeError, "invalid data"):
                probe("broken.mp4", require_audio=True)

    def test_video_only_pipeline_skips_extract_and_transcriber(self):
        project = Project("old", "abc.mp4")
        media = Mock()
        transcriber_factory = Mock()
        pipeline = TranscriptionPipeline(Mock(), media, transcriber_factory)
        with patch("cartoon_sub.transcription.pipeline.probe",
                   side_effect=NoAudioStreamError("abc.mp4")):
            with self.assertRaises(NoAudioStreamError):
                pipeline.run(project, "unused")
        media.extract_audio.assert_not_called()
        transcriber_factory.assert_not_called()

    def test_video_only_create_does_not_create_project(self):
        controller = Controller.__new__(Controller)
        controller.manager = Mock()
        controller.settings_store = Mock()
        with patch("cartoon_sub.app.controller.probe",
                   side_effect=NoAudioStreamError("abc.mp4")):
            with self.assertRaises(NoAudioStreamError):
                controller.create("abc.mp4", "new-project")
        controller.manager.create.assert_not_called()

    def test_video_only_popup_is_concise_and_has_specific_title(self):
        window = SimpleNamespace(controller=SimpleNamespace(directory=None), worker=None, pages=[])
        with patch("cartoon_sub.ui.main_window.QMessageBox.critical") as critical:
            MainWindow.error(window, NoAudioStreamError("abc.mp4"))
        _, title, message = critical.call_args.args
        self.assertEqual(title, "Video không có âm thanh")
        self.assertIn("Tên file: abc.mp4", message)
        self.assertNotIn("ffmpeg", message.lower())

    def _assert_extract(self, root, source_name, create_audio_dir):
        root = Path(root)
        source = root / source_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        audio_dir = root / "audio"
        if create_audio_dir:
            audio_dir.mkdir(parents=True)
        output = audio_dir / "source-0123456789abcdef.wav"

        def fake_run(args, cancel=None, progress=None):
            self.assertTrue(all(isinstance(arg, str) for arg in args))
            self.assertEqual(args[4], str(source))
            self.assertEqual(args[-1], str(output))
            self.assertFalse(args[-1].startswith('"'))
            self.assertTrue(output.parent.is_dir())
            write_wav(output)

        with patch("cartoon_sub.media.ffmpeg.run_process", side_effect=fake_run):
            result = FFmpeg().extract_audio(source, output)
        self.assertEqual(result, output)
        self.assertGreater(output.stat().st_size, 0)

    def test_missing_audio_directory_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            self._assert_extract(Path(folder) / "project with spaces", "video.mp4", False)

    def test_existing_audio_directory_still_works(self):
        with tempfile.TemporaryDirectory() as folder:
            self._assert_extract(Path(folder) / "existing project", "video.mp4", True)

    def test_unicode_input_filename_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            self._assert_extract(Path(folder) / "dự án Unicode", "动画 中文.mp4", False)

    def test_no_audio_stream_has_concise_error_and_no_partial_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            output = root / "audio" / "source-id.wav"
            diagnostic = RuntimeError(
                "ffmpeg exited: Output file does not contain any stream; "
                "Error opening output files: Invalid argument"
            )
            with patch("cartoon_sub.media.ffmpeg.run_process", side_effect=diagnostic):
                with self.assertRaisesRegex(NoAudioStreamError, "không có luồng âm thanh"):
                    FFmpeg().extract_audio(source, output)
            self.assertFalse(output.exists())
            self.assertTrue(output.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
