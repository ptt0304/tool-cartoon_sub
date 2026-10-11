"""Local visual-event detection for hardcoded subtitles.

This module deliberately has no OCR, model, or network dependency.  FFmpeg
streams a small edge-filtered ROI to memory; only a few optional debug crops
are written after detection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import time
import re
import hashlib
from threading import Thread

from cartoon_sub.media.process import CancelledError, run_process
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.transcription.video.roi import roi_to_pixels


DETECTOR_VERSION = "phase7b3-high-recall-spatial-v1"
SIGNATURE_VERSION = "text-mask-grid-projection-v1"
DEFAULT_THRESHOLDS = {
    "edge_density": 0.018,
    # A thin, short hard subtitle can have fewer bright pixels than the old
    # broad-presence gate.  It is still required to persist compatibly for
    # two coarse frames, so this does not turn a bright flash into an event.
    "candidate_edge_density": 0.0105,
    "strong_edge_density": 0.060,
    "same_signature_distance": 0.012,
    "localized_change_distance": 0.040,
    "empty_boundary_change_distance": 0.020,
    "appearance_samples": 2,
    "disappearance_samples": 2,
    "change_samples": 2,
    "minimum_stability_seconds": 0.12,
    "refine_window_seconds": 0.24,
    "suspicious_gap_seconds": 5.0,
    "long_event_rescan_seconds": 5.0,
}
COARSE_FPS = 10
REFINE_FPS = 25
_SAMPLE_WIDTH = 128
_SAMPLE_HEIGHT = 48
_TEXT_LUMA_THRESHOLD = 220


@dataclass(frozen=True)
class VisualSample:
    time: float
    visual_score: float
    signature: tuple[float, ...]

    @property
    def non_empty(self):
        return self.visual_score >= DEFAULT_THRESHOLDS["candidate_edge_density"]


@dataclass
class VideoSubtitleVisualEvent:
    event_id: int
    coarse_start: float
    coarse_end: float
    region_id: str = "region_001"
    representative_time: float = 0.0
    visual_score: float = 0.0
    stability_score: float = 0.0
    same_score: float = 0.0
    change_score: float = 0.0
    mask_difference: float = 0.0
    projection_difference: float = 0.0
    block_difference: float = 0.0
    detection_status: str = "ACCEPTED"
    refined_start: float = 0.0
    refined_end: float = 0.0
    _signature: tuple[float, ...] = field(default_factory=tuple, repr=False)
    _sample_count: int = field(default=0, repr=False)

    def _signature_id(self):
        payload = ",".join(f"{value:.4f}" for value in self._signature).encode("ascii")
        return hashlib.sha256(payload).hexdigest()[:16]

    def as_dict(self):
        duration = max(0.0, self.refined_end - self.refined_start)
        flags = []
        if duration > 10: flags.append("LONG_EVENT")
        if duration > 15: flags.append("VERY_LONG_EVENT")
        if self.stability_score < 2: flags.append("LOW_STABILITY")
        if self.visual_score < DEFAULT_THRESHOLDS["edge_density"] * 1.5: flags.append("LOW_PRESENCE")
        return {
            "event_id": self.event_id,
            "region_id": self.region_id,
            "coarse_start": round(self.coarse_start, 6),
            "coarse_end": round(self.coarse_end, 6),
            "refined_start": round(self.refined_start, 6),
            "refined_end": round(self.refined_end, 6),
            "representative_time": round(self.representative_time, 6),
            "visual_score": round(self.visual_score, 6),
            "presence_score": round(self.visual_score, 6),
            "stability_score": round(self.stability_score, 6),
            "same_score": round(self.same_score, 6),
            "change_score": round(self.change_score, 6),
            "mask_difference": round(self.mask_difference, 6),
            "projection_difference": round(self.projection_difference, 6),
            "block_difference": round(self.block_difference, 6),
            "visual_signature_id": self._signature_id(),
            "signature_version": SIGNATURE_VERSION,
            "refinement_method": "local_25fps_signature",
            "suspicious_flags": flags,
            "status": self.detection_status,
        }


def signature_distance(first, second):
    if not first or not second or len(first) != len(second):
        return 1.0
    return sum(abs(a - b) for a, b in zip(first, second)) / len(first)


def localized_signature_distance(first, second):
    """Detect a small character replacement without reacting to ROI-wide noise."""
    if not first or not second or len(first) != len(second):
        return 1.0
    differences = [abs(a - b) for a, b in zip(first, second)]
    return max(sum(differences) / len(differences), max(differences) * 0.15)


def signature_components(first, second):
    """Deterministic text-mask spatial differences for diagnostics/change voting."""
    if not first or not second or len(first) != len(second):
        return 1.0, 1.0, 1.0
    mask = signature_distance(first, second)
    blocks = max(abs(a - b) for a, b in zip(first, second))
    columns = 16
    projection = sum(abs(sum(first[i::columns]) - sum(second[i::columns])) for i in range(columns)) / columns
    return mask, projection, blocks


def _mean_signature(samples):
    if not samples:
        return ()
    return tuple(sum(sample.signature[index] for sample in samples) / len(samples)
                 for index in range(len(samples[0].signature)))


class VisualEventStateMachine:
    """Hysteresis state machine for EMPTY, stable text, and direct changes."""
    def __init__(self, coarse_fps=COARSE_FPS, thresholds=None):
        self.coarse_fps = float(coarse_fps)
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.events = []
        self._current = None
        self._pending = []
        self._pending_kind = None
        self._last_non_empty = None
        self.empty_intervals = 0
        self.direct_changes = 0

    def _compatible(self, samples, sample):
        return bool(samples) and signature_distance(_mean_signature(samples), sample.signature) <= self.thresholds["same_signature_distance"]

    def _non_empty(self, sample):
        return sample.visual_score >= self.thresholds["candidate_edge_density"]

    def _changed(self, first, second):
        mask, projection, blocks = signature_components(first, second)
        return (mask > self.thresholds["same_signature_distance"]
                or projection > self.thresholds["localized_change_distance"]
                or blocks * .15 > self.thresholds["localized_change_distance"]
                )

    def _new_event(self, samples):
        first = samples[0]
        event = VideoSubtitleVisualEvent(
            event_id=len(self.events) + 1,
            coarse_start=first.time,
            coarse_end=first.time + 1 / self.coarse_fps,
            visual_score=sum(item.visual_score for item in samples) / len(samples),
            stability_score=float(len(samples)),
            same_score=1.0,
            _signature=_mean_signature(samples),
            _sample_count=len(samples),
        )
        self._current = event

    def _close_current(self, end):
        if self._current is None:
            return
        self._current.coarse_end = max(self._current.coarse_start + 1 / self.coarse_fps, end)
        self.events.append(self._current)
        self._current = None

    def _pending_sample(self, kind, sample):
        if self._pending_kind != kind or (kind != "empty" and not self._compatible(self._pending, sample)):
            self._pending_kind, self._pending = kind, [sample]
        else:
            self._pending.append(sample)

    def consume(self, sample):
        if self._current is None:
            if not self._non_empty(sample):
                self._pending_kind, self._pending = None, []
                self.empty_intervals += 1
                return
            self._pending_sample("appear", sample)
            if len(self._pending) >= self.thresholds["appearance_samples"]:
                self._new_event(self._pending)
                self._pending_kind, self._pending = None, []
                self._last_non_empty = sample
            return

        if not self._non_empty(sample):
            self._pending_sample("empty", sample)
            if len(self._pending) >= self.thresholds["disappearance_samples"]:
                self._close_current(self._pending[0].time)
                self.empty_intervals += 1
                self._pending_kind, self._pending = None, []
            return

        # A single blank coarse frame can be the boundary between two hard
        # subtitles.  If the next text signature differs from the last real
        # subtitle frame, preserve that A→B transition instead of treating it
        # as a harmless empty glitch.
        if (self._pending_kind == "empty" and len(self._pending) == 1 and self._last_non_empty is not None
                and (self._changed(self._last_non_empty.signature, sample.signature)
                     or localized_signature_distance(self._last_non_empty.signature, sample.signature)
                     > self.thresholds["empty_boundary_change_distance"])):
            self._close_current(self._pending[0].time)
            self.events[-1].change_score = localized_signature_distance(self._last_non_empty.signature, sample.signature)
            self.events[-1].mask_difference, self.events[-1].projection_difference, self.events[-1].block_difference = signature_components(self._last_non_empty.signature, sample.signature)
            self._new_event([sample])
            self.direct_changes += 1
            self._pending_kind, self._pending = None, []
            self._last_non_empty = sample
            return

        if not self._changed(self._current._signature, sample.signature):
            current = self._current
            count = current._sample_count
            current._signature = tuple((old * count + new) / (count + 1)
                                       for old, new in zip(current._signature, sample.signature))
            current.visual_score = (current.visual_score * count + sample.visual_score) / (count + 1)
            current.same_score = 1.0 - signature_distance(current._signature, sample.signature)
            current.change_score = 1.0 - current.same_score
            current._sample_count += 1
            current.stability_score = float(current._sample_count)
            current.coarse_end = sample.time + 1 / self.coarse_fps
            self._pending_kind, self._pending = None, []
            self._last_non_empty = sample
            return

        self._pending_sample("change", sample)
        if len(self._pending) >= self.thresholds["change_samples"]:
            start = self._pending[0].time
            self._close_current(start)
            self.events[-1].change_score = localized_signature_distance(self.events[-1]._signature, sample.signature)
            self.events[-1].mask_difference, self.events[-1].projection_difference, self.events[-1].block_difference = signature_components(self.events[-1]._signature, sample.signature)
            self._new_event(self._pending)
            self.direct_changes += 1
            self._pending_kind, self._pending = None, []
            self._last_non_empty = sample

    def finish(self, duration):
        if self._current is not None:
            self._close_current(min(float(duration), self._current.coarse_end))
        minimum = self.thresholds["minimum_stability_seconds"]
        for event in self.events:
            if event.coarse_end - event.coarse_start < minimum:
                event.detection_status = "VERY_SHORT"
        return self.events


class FFmpegRoiSampler:
    """Stream edge-filtered grayscale ROI frames; never writes a frame sequence."""
    def __init__(self, source, crop, fps):
        self.source, self.crop, self.fps = Path(source), dict(crop), float(fps)

    def samples(self, start=0.0, end=None, *, cancel=None, progress=None):
        duration = None if end is None else max(0.0, float(end) - float(start))
        filter_graph = (
            f"fps={self.fps},crop={self.crop['width']}:{self.crop['height']}:{self.crop['x']}:{self.crop['y']},"
            f"scale={_SAMPLE_WIDTH}:{_SAMPLE_HEIGHT}:flags=area,format=gray"
        )
        args = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{float(start):.6f}", "-i", str(self.source)]
        if duration is not None:
            args += ["-t", f"{duration:.6f}"]
        args += ["-vf", filter_graph, "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        frame_size, index = _SAMPLE_WIDTH * _SAMPLE_HEIGHT, 0
        try:
            while True:
                if cancel and cancel.is_set():
                    process.terminate()
                    raise CancelledError("Job cancelled")
                raw = process.stdout.read(frame_size)
                if len(raw) != frame_size:
                    break
                yield _sample_from_raw(float(start) + index / self.fps, raw)
                index += 1
                if progress and index % 100 == 0:
                    progress(f"Video local scan: {index} samples")
            if process.wait() != 0:
                raise RuntimeError("FFmpeg không thể đọc vùng phụ đề Video.")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def samples_for_windows(self, windows, *, cancel=None):
        """Sample disjoint local windows in one FFmpeg process.

        ``select`` preserves source timestamps for ``showinfo`` while the
        expensive crop/edge filters run only for the requested windows.
        """
        windows = [(float(start), float(end)) for start, end in windows if end > start]
        if not windows:
            return []
        select = "+".join(f"between(t\\,{start:.6f}\\,{end:.6f})" for start, end in windows)
        graph = (
            f"select='{select}',fps={self.fps},crop={self.crop['width']}:{self.crop['height']}:{self.crop['x']}:{self.crop['y']},"
            f"scale={_SAMPLE_WIDTH}:{_SAMPLE_HEIGHT}:flags=area,format=gray,showinfo"
        )
        args = ["ffmpeg", "-nostdin", "-v", "info", "-i", str(self.source), "-vf", graph,
                "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        diagnostics = []
        reader = Thread(target=lambda: diagnostics.extend(process.stderr.read().decode("utf-8", errors="replace").splitlines()), daemon=True)
        reader.start()
        raw_frames, frame_size = [], _SAMPLE_WIDTH * _SAMPLE_HEIGHT
        try:
            while True:
                if cancel and cancel.is_set():
                    process.terminate()
                    raise CancelledError("Job cancelled")
                raw = process.stdout.read(frame_size)
                if len(raw) != frame_size:
                    break
                raw_frames.append(raw)
            if process.wait() != 0:
                raise RuntimeError("FFmpeg không thể refine vùng phụ đề Video.")
            reader.join(timeout=1)
            times = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", "\n".join(diagnostics))]
            if len(times) != len(raw_frames):
                raise RuntimeError("FFmpeg không trả đủ timestamp refine cho vùng phụ đề Video.")
            return [_sample_from_raw(moment, raw) for moment, raw in zip(times, raw_frames)]
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _sample_from_raw(moment, raw):
    values = tuple(raw)
    mask = tuple(value >= _TEXT_LUMA_THRESHOLD for value in values)
    score = sum(mask) / len(mask)
    return VisualSample(moment, score, _grid_signature(mask))


def _grid_signature(values, columns=16, rows=6):
    cell_width, cell_height = _SAMPLE_WIDTH // columns, _SAMPLE_HEIGHT // rows
    result = []
    for row in range(rows):
        for column in range(columns):
            total = 0
            for y in range(row * cell_height, (row + 1) * cell_height):
                offset = y * _SAMPLE_WIDTH + column * cell_width
                total += sum(values[offset:offset + cell_width])
            result.append(total / (cell_width * cell_height))
    return tuple(result)


class VideoSubtitleVisualDetector:
    def __init__(self, coarse_fps=COARSE_FPS, refine_fps=REFINE_FPS, thresholds=None):
        self.coarse_fps, self.refine_fps = float(coarse_fps), float(refine_fps)
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def _audit_gaps(self, samples, events, region):
        """Expose stable text-like activity missed inside long accepted-event gaps."""
        if not samples:
            return []
        boundaries = [(region["start_time"], region["end_time"])]
        accepted = sorted(events, key=lambda event: event.coarse_start)
        gaps, cursor = [], region["start_time"]
        for event in accepted:
            if event.coarse_start - cursor > self.thresholds["suspicious_gap_seconds"]:
                gaps.append((cursor, event.coarse_start))
            cursor = max(cursor, event.coarse_end)
        if region["end_time"] - cursor > self.thresholds["suspicious_gap_seconds"]:
            gaps.append((cursor, region["end_time"]))
        findings = []
        for gap_start, gap_end in gaps:
            pending = []
            for sample in (item for item in samples if gap_start <= item.time <= gap_end):
                if (sample.visual_score >= self.thresholds["candidate_edge_density"]
                        and (not pending or signature_distance(_mean_signature(pending), sample.signature)
                             <= self.thresholds["same_signature_distance"])):
                    pending.append(sample)
                    continue
                if len(pending) >= self.thresholds["appearance_samples"]:
                    findings.append({"status": "SUSPICIOUS_MISSED_SUBTITLE_GAP", "region_id": region["id"],
                                     "timestamp_window": [round(pending[0].time, 6), round(pending[-1].time + 1 / self.coarse_fps, 6)],
                                     "presence_score": round(max(item.visual_score for item in pending), 6),
                                     "stability_score": len(pending), "coarse_candidate": True})
                pending = []
            if len(pending) >= self.thresholds["appearance_samples"]:
                findings.append({"status": "SUSPICIOUS_MISSED_SUBTITLE_GAP", "region_id": region["id"],
                                 "timestamp_window": [round(pending[0].time, 6), round(pending[-1].time + 1 / self.coarse_fps, 6)],
                                 "presence_score": round(max(item.visual_score for item in pending), 6),
                                 "stability_score": len(pending), "coarse_candidate": True})
        return findings

    def detect(self, project, directory, *, cancel=None, progress=None):
        metadata = project.metadata
        duration = float(metadata.get("duration", 0) or 0)
        from cartoon_sub.transcription.video.regions import regions_for_project, video_fingerprint
        regions = regions_for_project(project)
        if duration <= 0:
            raise ValueError("Video phải có thời lượng hợp lệ")
        started = time.monotonic()
        empty_times, sample_count = [], 0
        events=[]; empty_intervals=direct_changes=0; suspicious_gaps=[]
        for region in regions:
            crop = roi_to_pixels(region["roi"], metadata.get("width"), metadata.get("height"))
            machine = VisualEventStateMachine(self.coarse_fps, self.thresholds)
            regional_samples = []
            for sample in FFmpegRoiSampler(project.source_video_path, crop, self.coarse_fps).samples(region["start_time"], region["end_time"], cancel=cancel, progress=progress):
                regional_samples.append(sample)
                machine.consume(sample)
                if not sample.non_empty and len(empty_times) < 5: empty_times.append(sample.time)
                sample_count += 1
            regional=machine.finish(region["end_time"])
            for event in regional: event.region_id=region["id"]
            self._refine(regional, project.source_video_path, crop, region["end_time"], lower_bound=region["start_time"], cancel=cancel)
            suspicious_gaps.extend(self._audit_gaps(regional_samples, regional, region))
            events.extend(regional); empty_intervals+=machine.empty_intervals; direct_changes+=machine.direct_changes
        events.sort(key=lambda event:(event.refined_start,event.refined_end,event.region_id))
        for identifier,event in enumerate(events,1): event.event_id=identifier
        debug_count = 0
        runtime = time.monotonic() - started
        manifest = {
            "video": str(Path(project.source_video_path).resolve()),
            "video_fingerprint": video_fingerprint(project.source_video_path),
            "video_width": int(metadata.get("width", 0)),
            "video_height": int(metadata.get("height", 0)),
            "duration": duration,
            "regions": regions,
            "roi": dict(project.video_subtitle_roi),
            "coarse_fps": self.coarse_fps,
            "refine_fps": self.refine_fps,
            "detector_version": DETECTOR_VERSION,
            "thresholds": self.thresholds,
            "sample_count": sample_count,
            "candidate_count": len(events),
            "scan_duration_seconds": runtime,
            "samples_per_second": sample_count / runtime if runtime else 0.0,
            "empty_intervals": empty_intervals,
            "direct_changes": direct_changes,
            "suspicious_missed_subtitle_gaps": suspicious_gaps,
            "debug_samples_saved": debug_count,
            "events": [event.as_dict() for event in events],
        }
        target = ProjectPaths(directory).transcript_video_cache_dir / "visual_events.json"
        atomic_json(target, manifest)
        from cartoon_sub.transcription.video.ocr_workload import write_ocr_workload
        workload = write_ocr_workload(
            target.with_name("ocr_workload.json"), manifest["events"], regions,
            video_fingerprint=manifest["video_fingerprint"], detector_version=DETECTOR_VERSION)
        manifest["ai_eligible_events"] = workload["total_events"]
        manifest["visual_events_rejected"] = workload["visual_events_rejected"]
        atomic_json(target, manifest)
        return manifest

    def _refine(self, events, source, crop, duration, *, lower_bound=0.0, cancel=None):
        window = float(self.thresholds["refine_window_seconds"])
        minimum = 1 / self.refine_fps
        # Boundaries of adjacent events usually share a window.  Merge those
        # windows before invoking FFmpeg so a busy scene does not spawn one
        # decoder process per event; every output sample is still within at
        # least one boundary's local refinement window.
        requests = []
        for index, event in enumerate(events):
            requests.append((max(lower_bound, event.coarse_start - window),
                             min(duration, event.coarse_start + window), index, "start"))
            requests.append((max(lower_bound, event.coarse_end - window),
                             min(duration, event.coarse_end + window), index, "end"))
        groups = []
        for request in sorted(requests):
            if groups and request[0] <= groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], request[1])
                groups[-1][2].append(request)
            else:
                groups.append([request[0], request[1], [request]])
        refined_samples = {}
        for start, end, group_requests in groups:
            samples = list(FFmpegRoiSampler(source, crop, self.refine_fps).samples(
                start, end, cancel=cancel))
            for request_start, request_end, index, kind in group_requests:
                refined_samples[index, kind] = [sample for sample in samples
                                                 if request_start <= sample.time <= request_end]
        previous_end = lower_bound
        for index, event in enumerate(events):
            start_samples = refined_samples[index, "start"]
            end_samples = refined_samples[index, "end"]
            matching_start = [sample for sample in start_samples if sample.visual_score >= self.thresholds["candidate_edge_density"] and signature_distance(event._signature, sample.signature) <= self.thresholds["same_signature_distance"]]
            matching_end = [sample for sample in end_samples if sample.visual_score >= self.thresholds["candidate_edge_density"] and signature_distance(event._signature, sample.signature) <= self.thresholds["same_signature_distance"]]
            start = matching_start[0].time if matching_start else event.coarse_start
            end = (matching_end[-1].time + minimum) if matching_end else event.coarse_end
            event.refined_start = max(previous_end, min(event.coarse_start + window, start))
            event.refined_end = min(duration, max(event.refined_start + minimum, event.coarse_end - window, end))
            event.representative_time = max(event.refined_start, min(event.refined_end, (event.refined_start + event.refined_end) / 2))
            previous_end = event.refined_end

    def _save_debug_crops(self, source, directory, crop, events, empty_times, *, cancel=None):
        debug = ProjectPaths(directory).transcript_video_cache_dir / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        for old in debug.glob("phase3_*.png"):
            old.unlink(missing_ok=True)
        times = [(f"event_{event.event_id:03d}", event.representative_time) for event in events[:10]]
        times += [(f"empty_{index:02d}", moment) for index, moment in enumerate(empty_times[:5], 1)]
        for label, moment in times:
            target = debug / f"phase3_{label}_{moment:.3f}.png"
            run_process(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{moment:.6f}", "-i", str(source),
                         "-vf", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}",
                         "-frames:v", "1", str(target)], cancel)
        return len(times)
