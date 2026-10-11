import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cartoon_sub.subtitle.models import Project
from cartoon_sub.transcription.video.roi import roi_to_pixels
from cartoon_sub.transcription.video.visual_detector import (
    DEFAULT_THRESHOLDS, VideoSubtitleVisualDetector, VideoSubtitleVisualEvent,
    VideoSubtitleVisualEvent as Event, VisualEventStateMachine, VisualSample, _sample_from_raw,
    localized_signature_distance,
    signature_components,
)


def sample(moment, score=.05, signature=.2):
    return VisualSample(moment, score, (signature,) * 8)


def run(samples, duration=2.0):
    machine = VisualEventStateMachine(coarse_fps=5)
    for item in samples:
        machine.consume(item)
    return machine, machine.finish(duration)


class VideoVisualStateMachineTests(unittest.TestCase):
    def test_roi_pixel_crop_conversion(self):
        crop = roi_to_pixels({"x_pct": .1, "y_pct": .7, "w_pct": .8, "h_pct": .22}, 1920, 1080)
        self.assertEqual(crop, {"x": 192, "y": 756, "width": 1536, "height": 238})

    def test_empty_to_empty_has_no_event(self):
        _, events = run([sample(0, 0), sample(.2, 0), sample(.4, 0)])
        self.assertEqual(events, [])

    def test_empty_to_text_starts_and_same_text_continues(self):
        _, events = run([sample(0, 0), sample(.2, .1, .4), sample(.4, .1, .4), sample(.6, .1, .4)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].coarse_start, .2)
        self.assertGreater(events[0].coarse_end, .6)

    def test_text_to_empty_closes_event(self):
        _, events = run([sample(0, .1, .4), sample(.2, .1, .4), sample(.4, 0), sample(.6, 0)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].coarse_end, .4)

    def test_direct_text_change_closes_and_opens_events(self):
        machine, events = run([sample(0, .1, .1), sample(.2, .1, .1), sample(.4, .1, .8), sample(.6, .1, .8)])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].coarse_end, events[1].coarse_start)
        self.assertEqual(machine.direct_changes, 1)

    def test_one_sample_noise_does_not_split_stable_event(self):
        _, events = run([sample(0, .1, .1), sample(.2, .1, .1), sample(.4, .1, .8), sample(.6, .1, .1), sample(.8, .1, .1)])
        self.assertEqual(len(events), 1)

    def test_background_change_without_text_presence_creates_no_event(self):
        _, events = run([sample(0, .01, .1), sample(.2, .01, .9), sample(.4, .01, .2)])
        self.assertEqual(events, [])

    def test_short_subtitle_survives_two_sample_stability_gate(self):
        _, events = run([sample(0, 0), sample(.2, .1, .5), sample(.4, .1, .5), sample(.6, 0), sample(.8, 0)])
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0].coarse_end, events[0].coarse_start)

    def test_thin_short_subtitle_is_detected_from_two_compatible_samples(self):
        _, events = run([sample(0, 0), sample(.2, .011, .5), sample(.4, .012, .5), sample(.6, 0), sample(.8, 0)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].coarse_start, .2)

    def test_small_local_character_change_opens_direct_transition(self):
        first = VisualSample(0, .03, (0,) * 96)
        same_first = VisualSample(.2, .03, (0,) * 96)
        changed = (0,) * 47 + (.30,) + (0,) * 48
        second = VisualSample(.4, .03, changed)
        same_second = VisualSample(.6, .03, changed)
        _, events = run([first, same_first, second, same_second])
        self.assertEqual(len(events), 2)
        self.assertGreater(localized_signature_distance(first.signature, second.signature), DEFAULT_THRESHOLDS["localized_change_distance"])

    def test_spatial_masks_with_equal_occupancy_are_changed(self):
        left = tuple(1 if index % 16 < 8 else 0 for index in range(96))
        right = tuple(0 if index % 16 < 8 else 1 for index in range(96))
        mask, projection, block = signature_components(left, right)
        self.assertGreater(mask, DEFAULT_THRESHOLDS["same_signature_distance"])
        self.assertGreater(projection, DEFAULT_THRESHOLDS["localized_change_distance"])
        self.assertGreater(block, 0)

    def test_short_empty_glitch_does_not_split_stable_event(self):
        _, events = run([sample(0, .1, .1), sample(.2, .1, .1), sample(.4, 0), sample(.6, .1, .1), sample(.8, .1, .1)])
        self.assertEqual(len(events), 1)

    def test_single_blank_frame_between_changed_text_opens_next_event(self):
        _, events = run([sample(0, .1, .1), sample(.2, .1, .1), sample(.4, 0), sample(.6, .1, .8), sample(.8, .1, .8)])
        self.assertEqual(len(events), 2)

    def test_events_are_non_negative_monotonic_and_do_not_overlap(self):
        _, events = run([sample(0, .1, .1), sample(.2, .1, .1), sample(.4, .1, .8), sample(.6, .1, .8)])
        previous = 0
        for event in events:
            self.assertGreater(event.coarse_end, event.coarse_start)
            self.assertGreaterEqual(event.coarse_start, previous)
            previous = event.coarse_end

    def test_event_dict_is_manifest_serializable(self):
        event = Event(1, 1.0, 2.0, refined_start=1.1, refined_end=1.9, representative_time=1.5)
        payload = json.loads(json.dumps(event.as_dict()))
        self.assertEqual(payload["event_id"], 1)
        self.assertIn("visual_signature_id", payload)
        self.assertEqual(payload["refinement_method"], "local_25fps_signature")

    def test_text_luminance_mask_rejects_dim_background(self):
        background = _sample_from_raw(0, bytes([200]) * (128 * 48))
        text = _sample_from_raw(.2, bytes([255]) * 600 + bytes([40]) * (128 * 48 - 600))
        self.assertFalse(background.non_empty)
        self.assertTrue(text.non_empty)


class RefinementAndDebugTests(unittest.TestCase):
    def test_refined_boundaries_stay_in_local_window_and_rep_is_inside(self):
        event = VideoSubtitleVisualEvent(1, 1.0, 2.0, _signature=(.2,) * 8)
        detector = VideoSubtitleVisualDetector()

        class FakeSampler:
            def __init__(self, *_args): pass
            def samples(self, start, end, **_kwargs):
                return [sample(start + .04, .1, .2), sample(end - .04, .1, .2)]

        with patch("cartoon_sub.transcription.video.visual_detector.FFmpegRoiSampler", FakeSampler):
            detector._refine([event], "video", {"x": 0, "y": 0, "width": 1, "height": 1}, 3)
        window = DEFAULT_THRESHOLDS["refine_window_seconds"]
        self.assertGreaterEqual(event.refined_start, event.coarse_start - window)
        self.assertLessEqual(event.refined_start, event.coarse_start + window)
        self.assertGreaterEqual(event.refined_end, event.coarse_end - window)
        self.assertLessEqual(event.refined_end, event.coarse_end + window)
        self.assertGreater(event.refined_end, event.refined_start)
        self.assertGreaterEqual(event.representative_time, event.refined_start)
        self.assertLessEqual(event.representative_time, event.refined_end)

    def test_debug_cleanup_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            debug = root / "cache" / "transcript" / "video" / "debug"
            debug.mkdir(parents=True)
            stale = debug / "phase3_stale.png"; stale.write_bytes(b"old")
            event = VideoSubtitleVisualEvent(1, 0, 1, representative_time=.5)
            def fake_process(args, *_args, **_kwargs):
                Path(args[-1]).write_bytes(b"new")
            with patch("cartoon_sub.transcription.video.visual_detector.run_process", fake_process):
                count = VideoSubtitleVisualDetector()._save_debug_crops("video", root, {"x": 0, "y": 0, "width": 1, "height": 1}, [event], [.1])
            self.assertEqual(count, 2)
            self.assertFalse(stale.exists())
            self.assertEqual(len(list(debug.glob("phase3_*.png"))), 2)

    def test_detector_writes_manifest_without_frame_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project("p", "video.mp4", metadata={"width": 100, "height": 100, "duration": 1},
                              transcript_source_mode="video",
                              video_subtitle_roi={"x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1})

            class FakeSampler:
                def __init__(self, _source, _crop, fps): self.fps = fps
                def samples(self, start=0, end=None, **_kwargs):
                    if self.fps == 10:
                        return iter([sample(0, .1, .2), sample(.2, .1, .2), sample(.4, 0), sample(.6, 0)])
                    return iter([sample(start + .04, .1, .2), sample(end - .04, .1, .2)])

            with patch("cartoon_sub.transcription.video.visual_detector.FFmpegRoiSampler", FakeSampler), \
                 patch.object(VideoSubtitleVisualDetector, "_save_debug_crops", return_value=0):
                manifest = VideoSubtitleVisualDetector().detect(project, folder)
            path = Path(folder) / "cache" / "transcript" / "video" / "visual_events.json"
            self.assertTrue(path.is_file())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["candidate_count"], len(payload["events"]))
            self.assertEqual(payload["coarse_fps"], 10.0)
            self.assertEqual(manifest["roi"], project.video_subtitle_roi)

    def test_gap_audit_finds_stable_text_activity_but_not_background(self):
        detector = VideoSubtitleVisualDetector()
        region = {"id": "region_001", "start_time": 0.0, "end_time": 10.0}
        activity = [sample(1.0, .011, .4), sample(1.2, .012, .4)]
        self.assertEqual(len(detector._audit_gaps(activity, [], region)), 1)
        self.assertEqual(detector._audit_gaps([sample(1.0, .004, .4), sample(1.2, .004, .4)], [], region), [])


if __name__ == "__main__":
    unittest.main()
