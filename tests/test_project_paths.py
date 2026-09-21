import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Segment


class ProjectPathsTests(unittest.TestCase):
    def test_all_generated_directories_are_inside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(directory).ensure()
            for value in (paths.project_file, paths.source_dir, paths.audio_dir,
                          paths.transcript_cache_dir, paths.translate_cache_dir,
                          paths.tts_segments_dir, paths.tts_previews_dir,
                          paths.export_dir, paths.logs_dir):
                value.resolve().relative_to(paths.root)

    def test_move_project_keeps_relative_tts_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "first"
            segment = Segment(1, 0, 1, "x", tts_audio_path="audio/tts/segments/one.wav")
            project = Project("first", "C:/external/video.mp4", segments=[segment])
            manager = ProjectManager(); manager.save(project, root)
            moved = Path(directory) / "moved"; root.rename(moved)
            loaded = manager.load(moved)
            self.assertEqual(loaded.utterances[0].tts_audio_path, "audio/tts/segments/one.wav")

    def test_old_absolute_path_inside_project_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "audio" / "tts" / "segments" / "one.wav"
            target.parent.mkdir(parents=True); target.write_bytes(b"wav")
            payload = Project("p", "video.mp4", segments=[Segment(1, 0, 1, "x", tts_audio_path=str(target))]).to_dict()
            (root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(ProjectManager().load(root).utterances[0].tts_audio_path, "audio/tts/segments/one.wav")

