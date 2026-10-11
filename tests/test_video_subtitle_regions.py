import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.transcription.video.regions import normalize_regions, regions_for_project
from cartoon_sub.transcription.video.visual_detector import VideoSubtitleVisualDetector, VisualSample

ROI = {"x_pct": .2, "y_pct": .8, "w_pct": .6, "h_pct": .1}
def region(identifier, start, end, roi=ROI):
    return {"id": identifier, "start_time": start, "end_time": end, "roi": dict(roi)}
def sample(moment): return VisualSample(moment, .1, (.2,) * 8)

class VideoSubtitleRegionsTests(unittest.TestCase):
    def test_one_region_project_round_trip(self):
        project = Project("p", "v.mp4", metadata={"duration": 30}, video_subtitle_regions=[region("a", 1, 20)])
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.video_subtitle_regions, [region("a", 1.0, 20.0)])

    def test_multiple_regions_project_round_trip_and_sort(self):
        project = Project.from_dict({"schema_version":3, "name":"p", "source_video_path":"v.mp4", "metadata":{"duration":40},
            "video_subtitle_regions":[region("b", 20, 30), region("a", 10, 20)]})
        self.assertEqual([row["id"] for row in project.video_subtitle_regions], ["a", "b"])
        self.assertEqual(Project.from_dict(project.to_dict()).video_subtitle_regions, project.video_subtitle_regions)

    def test_unique_ids_invalid_times_roi_and_overlap_rejected(self):
        cases = [
            [region("a", 10, 10)], [region("a", 0, 20), region("a", 20, 30)],
            [region("a", 0, 25), region("b", 20, 30)],
            [region("a", 0, 1, {**ROI, "x_pct": .9})],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError): normalize_regions(rows, 30)

    def test_touching_regions_allowed(self):
        self.assertEqual([x["id"] for x in normalize_regions([region("b", 20, 30), region("a", 10, 20)], 40)], ["a", "b"])

    def test_legacy_roi_becomes_full_duration_region_without_mutating_input(self):
        p = SimpleNamespace(video_subtitle_regions=[], video_subtitle_roi=ROI, metadata={"duration":50})
        self.assertEqual(regions_for_project(p), [region("region_001", 0.0, 50.0)])
        legacy = {"schema_version":3, "name":"p", "source_video_path":"v.mp4", "metadata":{"duration":50}, "video_subtitle_roi":ROI}
        loaded = Project.from_dict(legacy)
        self.assertEqual(legacy.get("video_subtitle_regions"), None)
        self.assertEqual(loaded.video_subtitle_regions, [region("region_001", 0.0, 50.0)])

    def test_uncovered_time_is_not_a_region(self):
        p = SimpleNamespace(video_subtitle_regions=[region("a", 10, 20)], video_subtitle_roi={}, metadata={"duration":50})
        self.assertEqual(regions_for_project(p), [region("a", 10, 20)])

    def test_controller_confirm_persists_complete_region_list(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(settings_store=SimpleNamespace())
            controller.project = Project("p", "v.mp4", metadata={"duration": 40}, transcript_source_mode="video")
            controller.directory = Path(folder)
            controller.set_video_subtitle_regions([region("a", 0, 10), region("b", 10, 20)])
            self.assertEqual(ProjectManager().load(folder).video_subtitle_regions, [region("a", 0.0, 10.0), region("b", 10.0, 20.0)])

    def test_region_detector_scans_only_bounds_retains_id_and_clamps_refine(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "video.mp4"; source.write_bytes(b"x")
            project = Project("p", str(source), metadata={"width":100,"height":100,"duration":30}, transcript_source_mode="video",
                video_subtitle_regions=[region("a", 5, 10), region("b", 20, 25)])
            calls = []
            class FakeSampler:
                def __init__(self, _source, _crop, fps): self.fps = fps
                def samples(self, start=0, end=None, **_kwargs):
                    calls.append((self.fps, start, end))
                    if self.fps == 5: return iter([sample(start), sample(start + .2), VisualSample(min(end-.1,start+.4), 0, (.2,)*8)])
                    return iter([sample(start + .01), sample(end - .01)])
            with patch("cartoon_sub.transcription.video.visual_detector.FFmpegRoiSampler", FakeSampler):
                manifest = VideoSubtitleVisualDetector().detect(project, folder)
            coarse = [(start, end) for fps, start, end in calls if fps == 5]
            self.assertEqual(coarse, [(5.0, 10.0), (20.0, 25.0)])
            self.assertTrue(all(row["region_id"] in {"a", "b"} for row in manifest["events"]))
            for row in manifest["events"]:
                bounds = (5, 10) if row["region_id"] == "a" else (20, 25)
                self.assertGreaterEqual(row["refined_start"], bounds[0]); self.assertLessEqual(row["refined_end"], bounds[1])

    def test_cache_manifest_identity_changes_when_region_removed(self):
        first = [region("a", 0, 10), region("b", 10, 20)]
        second = [region("a", 0, 10)]
        self.assertNotEqual(first, second)

if __name__ == "__main__": unittest.main()

