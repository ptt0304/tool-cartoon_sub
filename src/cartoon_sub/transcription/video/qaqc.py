"""Local, reversible duplicate consolidation for Video Transcript rows."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from cartoon_sub.project.cache import atomic_json, content_hash, file_hash
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.transcription.pipeline import invalidate_transcript_dependents
from cartoon_sub.transcription.video.canonical_integration import validate_video_timeline
from cartoon_sub.transcription.video.regions import regions_for_project, video_fingerprint
from cartoon_sub.transcription.video.visual_detector import DETECTOR_VERSION


MERGE_GAP_THRESHOLD = 0.20
NEAR_SIMILARITY = 0.88
MAX_EDIT_DISTANCE = 2
MAX_LENGTH_DELTA = 2
_TERMINAL_PUNCTUATION = "。！？!?…"


@dataclass(frozen=True)
class VideoQaqcResult:
    applied: bool
    rows_before: int
    rows_after: int
    clusters_merged: int
    rows_removed: int
    exact_duplicate_merges: int
    near_duplicate_merges: int
    groups: list[dict]

    def to_dict(self):
        return {
            "applied": self.applied, "rows_before": self.rows_before,
            "rows_after": self.rows_after, "clusters_merged": self.clusters_merged,
            "rows_removed": self.rows_removed, "exact_duplicate_merges": self.exact_duplicate_merges,
            "near_duplicate_merges": self.near_duplicate_merges, "groups": self.groups,
        }


def normalize_comparison(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", "", text).strip()
    return text.rstrip(_TERMINAL_PUNCTUATION)


def damerau_levenshtein(left: str, right: str) -> int:
    """Optimal string alignment distance; sufficient for bounded local OCR noise."""
    previous_previous, previous = None, list(range(len(right) + 1))
    for i, first in enumerate(left, 1):
        current = [i]
        for j, second in enumerate(right, 1):
            cost = first != second
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
            if i > 1 and j > 1 and first == right[j - 2] and left[i - 2] == second:
                current[-1] = min(current[-1], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current
    return previous[-1]


def _similarity(left: str, right: str) -> float:
    return 1.0 - damerau_levenshtein(left, right) / max(len(left), len(right), 1)


def duplicate_reason(left: str, right: str) -> str | None:
    """Return a deterministic reason only for conservative duplicate candidates."""
    left, right = normalize_comparison(left), normalize_comparison(right)
    if not left or not right:
        return None
    if left == right:
        return "EXACT"
    length = max(len(left), len(right))
    if length <= 4:
        return None
    distance = damerau_levenshtein(left, right)
    similarity = _similarity(left, right)
    if length <= 8:
        return "NEAR" if abs(len(left) - len(right)) <= 1 and distance <= 1 and similarity >= 0.86 else None
    if (abs(len(left) - len(right)) <= MAX_LENGTH_DELTA and distance <= MAX_EDIT_DISTANCE
            and similarity >= NEAR_SIMILARITY):
        return "NEAR"
    return None


def _canonical(cluster: list[Utterance]) -> Utterance:
    variants = {}
    for row in cluster:
        variants.setdefault(normalize_comparison(row.zh), []).append(row)
    highest = max(len(rows) for rows in variants.values())
    candidates = [row for rows in variants.values() if len(rows) == highest for row in rows]
    def score(row):
        value = normalize_comparison(row.zh)
        return (sum(damerau_levenshtein(value, normalize_comparison(other.zh)) for other in cluster), row.start)
    return min(candidates, key=score)


def _clone(row: Utterance) -> Utterance:
    return Utterance.from_dict(row.to_dict())


def _build_candidate(rows: list[Utterance]) -> tuple[list[Utterance], list[dict], int, int]:
    ordered = sorted(rows, key=lambda row: (row.start, row.end, row.id))
    candidate, groups, exact, near = [], [], 0, 0
    index = 0
    while index < len(ordered):
        cluster = [ordered[index]]
        reasons = []
        index += 1
        while index < len(ordered):
            next_row = ordered[index]
            if next_row.start > cluster[-1].end + MERGE_GAP_THRESHOLD:
                break
            reason = duplicate_reason(_canonical(cluster).zh, next_row.zh)
            if not reason:
                break
            cluster.append(next_row); reasons.append(reason); index += 1
        chosen = _clone(_canonical(cluster))
        chosen.start, chosen.end = cluster[0].start, cluster[-1].end
        chosen.transcript_timing_provenance = "VIDEO_SUBTITLE"
        chosen.recalculate()
        candidate.append(chosen)
        if len(cluster) > 1:
            exact += reasons.count("EXACT"); near += reasons.count("NEAR")
            groups.append({"old_ids": [row.id for row in cluster], "start": chosen.start, "end": chosen.end,
                           "canonical_text": chosen.zh, "reason": "NEAR_DUPLICATE_CLUSTER" if "NEAR" in reasons else "EXACT_DUPLICATE_CLUSTER"})
    for identifier, row in enumerate(candidate, 1):
        row.id = identifier
        row.set_display_segments([])
    for group in groups:
        # IDs are deterministic post-merge because groups are left-to-right.
        group["new_id"] = next(row.id for row in candidate if row.start == group["start"] and row.end == group["end"])
    validate_video_timeline(candidate)
    return candidate, groups, exact, near


def _rows_hash(project: Project) -> str:
    return content_hash([row.to_dict() for row in project.segments])


def has_valid_video_timeline(project: Project) -> bool:
    """True only for a complete, persisted Video-owned canonical timeline."""
    if not project.segments:
        return False
    try:
        validate_video_timeline(project.segments, project.metadata.get("duration"))
    except (TypeError, ValueError):
        return False
    return True


class VideoTranscriptQaqcService:
    def _snapshot_path(self, directory) -> Path:
        return ProjectPaths(directory).transcript_video_cache_dir / "qaqc" / "pre_qaqc_snapshot.json"

    @staticmethod
    def _optional_hash(path: Path) -> str:
        return file_hash(path) if path.is_file() else ""

    def generation_identity(self, project: Project, directory) -> dict:
        directory = Path(directory)
        cache = ProjectPaths(directory).transcript_video_cache_dir
        return {
            "video": video_fingerprint(project.source_video_path),
            "regions": content_hash(regions_for_project(project)),
            "detector_version": DETECTOR_VERSION,
            "workload": self._optional_hash(cache / "ocr_workload.json"),
            "ocr_results": self._optional_hash(cache / "ocr_results.json"),
        }

    def invalidate(self, project: Project, directory) -> None:
        self._snapshot_path(directory).unlink(missing_ok=True)
        project.selected_models.pop("video_transcript_qaqc", None)

    def _read_snapshot(self, directory):
        try:
            import json
            return json.loads(self._snapshot_path(directory).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def undo_available(self, project: Project, directory) -> bool:
        snapshot = self._read_snapshot(directory)
        if not snapshot or not snapshot.get("applied_transcript_hash"):
            return False
        return (snapshot.get("generation_identity") == self.generation_identity(project, directory)
                and snapshot.get("applied_transcript_hash") == _rows_hash(project))

    def can_apply(self, project: Project, directory) -> bool:
        return (project.transcript_source_mode == "video_subtitle"
                and has_valid_video_timeline(project)
                and project.selected_models.get("video_transcript_qaqc", {}).get("status") != "APPLIED")

    def can_undo(self, project: Project, directory) -> bool:
        return (project.transcript_source_mode == "video_subtitle"
                and project.selected_models.get("video_transcript_qaqc", {}).get("status") == "APPLIED"
                and self.undo_available(project, directory))

    def apply(self, project: Project, directory) -> VideoQaqcResult:
        if project.selected_models.get("video_transcript_qaqc", {}).get("status") == "APPLIED":
            return VideoQaqcResult(False, len(project.segments), len(project.segments), 0, 0, 0, 0, [])
        if not self.can_apply(project, directory):
            raise ValueError("Chưa có Video Transcript hợp lệ để QA/QC.")
        rows_before = len(project.segments)
        candidate, groups, exact, near = _build_candidate(project.segments)
        if not groups:
            return VideoQaqcResult(False, rows_before, rows_before, 0, 0, 0, 0, [])
        snapshot = {
            "generation_identity": self.generation_identity(project, directory),
            "pre_transcript_hash": _rows_hash(project),
            "project": project.to_dict(),
        }
        # Only a fully validated candidate reaches this point; snapshot and commit
        # are deliberately separate from candidate construction.
        project.segments = candidate
        invalidate_transcript_dependents(project)
        snapshot["applied_transcript_hash"] = _rows_hash(project)
        atomic_json(self._snapshot_path(directory), snapshot)
        result = VideoQaqcResult(True, rows_before, len(candidate), len(groups), rows_before - len(candidate), exact, near, groups)
        project.selected_models["video_transcript_qaqc"] = {"status": "APPLIED", **result.to_dict()}
        return result

    def undo(self, project: Project, directory) -> Project:
        snapshot = self._read_snapshot(directory)
        if not self.undo_available(project, directory):
            raise ValueError("Không có snapshot QA/QC hợp lệ cho Video Transcript hiện tại.")
        restored = Project.from_dict(snapshot["project"])
        restored.selected_models.pop("video_transcript_qaqc", None)
        return restored
