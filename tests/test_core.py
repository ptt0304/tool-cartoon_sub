import tempfile
import unittest
from pathlib import Path
from threading import Event
from cartoon_sub.subtitle.models import Segment, Project
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.parser import import_srt, export_srt
from cartoon_sub.translation.service import MockTranslationService, TranslationOptions
from cartoon_sub.media.process import run_process, CancelledError
import sys

class CoreTests(unittest.TestCase):
    def test_project_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Project("中文", "missing.mp4", segments=[Segment(1, 0, 2, "小美", "Tiểu Mỹ")])
            p.glossary = {"小美": "Tiểu Mỹ"}
            manager = ProjectManager()
            manager.save(p, d)
            loaded = manager.load(d)
            self.assertEqual(loaded.segments, p.segments)
            self.assertEqual(loaded.glossary, p.glossary)
            self.assertTrue((Path(d) / "output").is_dir())
            with self.assertRaises(ValueError):
                manager.create(d, "missing.mp4", {})

    def test_srt_unicode_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "zh.srt"
            segments = [Segment(1, 1.25, 3.8, "中文\n第二行")]
            export_srt(segments, path)
            imported = import_srt(path)
            self.assertEqual(
                [(row.id, row.start, row.end, row.zh) for row in imported],
                [(row.id, row.start, row.end, row.zh) for row in segments],
            )

    def test_mock_does_not_mutate_source(self):
        source = [Segment(7, 1, 4, "你好")]
        result = MockTranslationService().translate(source, TranslationOptions())
        self.assertEqual((result[0].id, result[0].start, result[0].end), (7, 1, 4))
        self.assertEqual(source[0].vi, "")
        self.assertIn("MOCK", result[0].vi)

    def test_invalid_models(self):
        with self.assertRaises(ValueError):
            Segment(1, 3, 2)
        with self.assertRaises(ValueError):
            Segment(1, float("nan"), 2)
        with self.assertRaises(ValueError):
            Project.from_dict({"schema_version": 99})

    def test_process_failure_and_cancel(self):
        with self.assertRaises(RuntimeError):
            run_process([sys.executable, "-c", "raise SystemExit(2)"])
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            run_process([sys.executable, "-c", "pass"], cancel)

if __name__ == "__main__":
    unittest.main()
