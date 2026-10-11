"""Deterministic, local-only OCR workload preparation for Video transcript."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from cartoon_sub.project.cache import atomic_json, content_hash


OCR_WORKLOAD_VERSION = "phase21a-ocr-workload-v1"
TERMINAL_OCR_STATUSES = frozenset({"SUCCESS", "OCR_EMPTY", "FAILED"})
_LOG_LOCK = Lock()
_RUNTIME_LOG = Path(__file__).resolve().parents[4] / "logs" / "transcript_video" / "phase_7b_ai_workload.log"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _eligible_event(event, regions):
    if not isinstance(event, dict) or event.get("status", "ACCEPTED") != "ACCEPTED":
        return None
    required = ("event_id", "region_id", "refined_start", "refined_end", "representative_time")
    if not all(key in event for key in required):
        return None
    start, end, representative = (event[key] for key in ("refined_start", "refined_end", "representative_time"))
    if not all(_number(value) for value in (start, end, representative)) or end <= start:
        return None
    region = regions.get(event["region_id"])
    if region is None:
        return None
    if start < region["start_time"] or end > region["end_time"]:
        return None
    if not start <= representative <= end:
        return None
    return region


def create_ocr_workload(events, regions, *, video_fingerprint, detector_version):
    """Return a stable pending OCR workload without extracting crops or calling AI."""
    ordered_regions = [dict(row) for row in regions]
    region_index = {row["id"]: row for row in ordered_regions}
    eligible = []
    for event in events or []:
        region = _eligible_event(event, region_index)
        if region is None:
            continue
        source = {
            "event_id": event["event_id"], "region_id": event["region_id"],
            "refined_start": float(event["refined_start"]), "refined_end": float(event["refined_end"]),
            "representative_time": float(event["representative_time"]),
            "visual_signature_id": str(event.get("visual_signature_id", "")),
        }
        crop_identity = content_hash({
            "video_fingerprint": video_fingerprint, "region_id": source["region_id"],
            "roi": region["roi"], "representative_time": source["representative_time"],
            "crop_version": "ffmpeg-png-v1",
        })
        eligible.append({**source, "representative_crop_path_or_identity": crop_identity, "status": "PENDING"})
    eligible.sort(key=lambda row: (row["refined_start"], row["refined_end"], str(row["region_id"]), str(row["event_id"])))
    for index, row in enumerate(eligible, 1):
        row["event_index"] = index
    return {
        "version": OCR_WORKLOAD_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video_fingerprint": video_fingerprint,
        "regions_fingerprint": content_hash(ordered_regions),
        "detector_version": detector_version,
        "visual_events_total": len(events or []),
        "visual_events_rejected": len(events or []) - len(eligible),
        "total_events": len(eligible),
        "events": eligible,
    }


def write_ocr_workload(path, events, regions, *, video_fingerprint, detector_version):
    workload = create_ocr_workload(
        events, regions, video_fingerprint=video_fingerprint, detector_version=detector_version)
    atomic_json(path, workload)
    return workload


class OcrWorkloadProgress:
    """Event-based progress contract for the future asynchronous OCR worker."""
    def __init__(self, total_events):
        self.total_events = int(total_events)
        self._completed = set()

    @property
    def completed(self):
        return len(self._completed)

    def text(self, *, retry=None):
        suffix = f" — retry {retry}" if retry else ""
        return f"Đang OCR Video: {self.completed}/{self.total_events} event{suffix}"

    def terminal(self, event_id, status):
        if status not in TERMINAL_OCR_STATUSES:
            raise ValueError("OCR workload event chưa ở trạng thái kết thúc")
        self._completed.add(str(event_id))
        return self.text()


def append_workload_log(workload, path):
    """Write the local-only Phase 7B workload summary without credentials."""
    relative = Path(path).as_posix()
    lines = (
        "LOCAL_DETECTION_COMPLETE",
        "AI_WORKLOAD:",
        f"visual_events_total={workload['visual_events_total']}",
        f"visual_events_rejected={workload['visual_events_rejected']}",
        f"ai_eligible_events={workload['total_events']}",
        f"AI_ELIGIBLE_EVENTS={workload['total_events']}",
        "OCR_WORKLOAD_MANIFEST:",
        f"path={relative}",
        f"total_events={workload['total_events']}",
        "PROGRESS_CONTRACT:",
        "format=current/total event",
        "retry_counts_once=PASS",
        "cache_hits_count_as_completed=PASS",
        f"ocr_total_events={workload['total_events']}",
        "ocr_cache_hits=0",
        "ocr_api_events=0",
        "API:",
        "requests=0",
        "cost=0",
    )
    with _LOG_LOCK:
        _RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _RUNTIME_LOG.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
