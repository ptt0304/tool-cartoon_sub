import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.media.preview import VideoRenderer, _argv_length
from cartoon_sub.subtitle.models import Mask, Project, Utterance


class RenderFilterScriptTests(unittest.TestCase):
    def _project(self, source, rows):
        return Project(
            "render", str(source), metadata={"duration": 1200.0, "width": 1280, "height": 720},
            segments=rows, mask=Mask(enabled=True, kind="solid", x=100, y=500, width=900, height=120),
        )

    def _capture_render(self, project, root, *, start=0, duration=None):
        captured = {}

        def fake_run(args, *_args, **kwargs):
            captured["args"] = list(args)
            script = Path(args[args.index("-filter_complex_script") + 1])
            captured["script"] = script.read_text(encoding="utf-8")
            captured["script_bytes"] = script.stat().st_size
            Path(args[-1]).write_bytes(b"rendered")

        with patch("cartoon_sub.media.preview.run_process", side_effect=fake_run):
            output = VideoRenderer().render(project, root, start=start, preview=False, duration=duration)
        self.assertTrue(output.is_file())
        return captured

    def test_filter_graph_is_external_and_unicode_ass_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.mp4"; source.write_bytes(b"source")
            rows = [Utterance(1, 0, 2, "中文", vi_subtitle="Tiếng Việt: chẳng, không — 你好")]
            captured = self._capture_render(self._project(source, rows), root)
            args = captured["args"]
            self.assertIn("-filter_complex_script", args)
            self.assertNotIn(captured["script"], args)
            self.assertIn("drawbox=", captured["script"])
            ass = next((root / "output").rglob("subtitle.ass"))
            self.assertIn("Tiếng Việt", ass.read_text(encoding="utf-8"))

    def test_test_range_includes_only_intersecting_events_with_relative_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.mp4"; source.write_bytes(b"source")
            rows = [
                Utterance(1, 10, 12, "旧", vi_subtitle="cũ"),
                Utterance(2, 305, 307, "中", vi_subtitle="giữa"),
                Utterance(3, 329, 332, "末", vi_subtitle="cuối"),
            ]
            captured = self._capture_render(self._project(source, rows), root, start=300, duration=30)
            ass = next((root / "output").rglob("subtitle.ass")).read_text(encoding="utf-8")
            self.assertNotIn("cũ", ass)
            self.assertIn("giữa", ass)
            self.assertIn("cuối", ass)
            self.assertEqual(captured["script"].count("drawbox="), 2)

    def test_large_event_graph_keeps_windows_argv_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.mp4"; source.write_bytes(b"source")
            rows = [Utterance(index + 1, float(index), float(index) + .8, "字", vi_subtitle="Nội dung phụ đề")
                    for index in range(1000)]
            captured = self._capture_render(self._project(source, rows), root)
            self.assertGreater(captured["script_bytes"], 50_000)
            self.assertLess(_argv_length(captured["args"]), 2_000)
            self.assertEqual(captured["script"].count("drawbox="), 1000)


if __name__ == "__main__":
    unittest.main()
