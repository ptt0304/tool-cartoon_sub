from dataclasses import asdict
from copy import deepcopy
from difflib import SequenceMatcher
import logging
import unicodedata
from .models import Speaker
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.project.cache import content_hash


MIN_RECONCILED_DURATION = 0.4
UNKNOWN_SPEAKER = "SPK_UNKNOWN"


def _normalized_source_text(text):
    return "".join(character.lower() for character in text.strip()
                   if not character.isspace() and not unicodedata.category(character).startswith("P"))


def duplicate_continuation_candidate(first, second):
    """Cheap deterministic diagnostic; never deletes or merges text."""
    left, right = _normalized_source_text(first), _normalized_source_text(second)
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    if shorter == longer:
        return True
    if len(shorter) >= 4 and shorter in longer and len(shorter) / len(longer) >= 0.45:
        return True
    return len(shorter) >= 4 and SequenceMatcher(None, left, right).ratio() >= 0.72


def _clamp_utterance_end(utterance, new_end):
    """Resize existing presentation timing with its parent; text and segment count stay unchanged."""
    old_start, old_end = utterance.start, utterance.end
    old_duration = old_end - old_start
    new_duration = new_end - old_start
    if utterance.display_segments and old_duration > 0:
        for display in utterance.display_segments:
            start_ratio = max(0.0, min(1.0, (display.start - old_start) / old_duration))
            end_ratio = max(start_ratio, min(1.0, (display.end - old_start) / old_duration))
            display.start = old_start + start_ratio * new_duration
            display.end = old_start + end_ratio * new_duration
            display.recalculate()
    utterance.end = new_end
    utterance.recalculate()


def detect_overlaps(segments):
    """Connected interval components; never change times or merge utterances.

    An overlap group is legitimate dialogue overlap ONLY when there are multiple
    utterances and at least 2 distinct speakers speaking simultaneously.
    Same-speaker overlap is not treated as legitimate dialogue overlap.
    """
    for s in segments:
        preserved = ((s.overlap_type == "SAME_SPEAKER_CONFLICT"
                      and "CLAMP_PREVIOUS_END" in s.overlap_diagnostics)
                     or (s.overlap_type == "TIMING_REVIEW_REQUIRED"
                         and "TIMING_REVIEW_REQUIRED" in s.overlap_diagnostics))
        s.overlap, s.overlap_group = False, None
        if not preserved:
            s.overlap_type, s.overlap_diagnostics = "NONE", []
    group, end, number = [], -1, 0
    def finish(rows, number):
        adjacent = [(left, right) for left, right in zip(rows, rows[1:]) if right.start < left.end]
        same = [(left, right) for left, right in adjacent
                if left.speaker_id == right.speaker_id != UNKNOWN_SPEAKER]
        unknown = [(left, right) for left, right in adjacent
                   if UNKNOWN_SPEAKER in (left.speaker_id, right.speaker_id)]
        distinct_speakers = {row.speaker_id for row in rows if row.speaker_id != UNKNOWN_SPEAKER}
        if same:
            for left, right in same:
                diagnostics = ["SAME_SPEAKER_OVERLAP"]
                if duplicate_continuation_candidate(left.zh, right.zh):
                    diagnostics.append("DUPLICATE_CONTINUATION")
                timing_review = any(row.overlap_type == "TIMING_REVIEW_REQUIRED" for row in (left, right))
                if timing_review:
                    diagnostics.append("TIMING_REVIEW_REQUIRED")
                for row in (left, right):
                    row.overlap_type = "TIMING_REVIEW_REQUIRED" if timing_review else "SAME_SPEAKER_CONFLICT"
                    row.overlap_diagnostics = list(dict.fromkeys(row.overlap_diagnostics + diagnostics))
        if unknown:
            for left, right in unknown:
                for row in (left, right):
                    row.overlap_type = "UNKNOWN_SPEAKER_REVIEW"
                    row.overlap_diagnostics = ["OVERLAP_NEEDS_SPEAKER_REVIEW"]
        if not same and not unknown and len(rows) > 1 and len(distinct_speakers) > 1:
            number += 1
            for row in rows:
                row.overlap = True
                row.overlap_group = f"OVL_{number:03d}"
                row.overlap_type = "LEGITIMATE_OVERLAP"
                row.overlap_diagnostics = []
        return number
    for segment in sorted(segments, key=lambda s:(s.start,s.end,s.id)):
        if group and segment.start >= end:
            number=finish(group,number)
            group=[]
        group.append(segment)
        end=segment.end if len(group)==1 else max(end,segment.end)
    finish(group,number)


def reconcile_overlaps(segments, min_duration=MIN_RECONCILED_DURATION):
    """Clamp safe adjacent same-speaker conflicts and classify every overlap in O(n log n)."""
    if min_duration <= 0:
        raise ValueError("min_duration must be positive")
    rows = sorted(segments, key=lambda s: (s.start, s.end, s.id))
    summary = {"total": 0, "legitimate": 0, "same_speaker_fixed": 0,
               "unknown_review": 0, "timing_review": 0}
    fixed = []
    for previous, current in zip(rows, rows[1:]):
        if current.start >= previous.end:
            continue
        summary["total"] += 1
        overlap_seconds = previous.end - current.start
        if UNKNOWN_SPEAKER in (previous.speaker_id, current.speaker_id):
            summary["unknown_review"] += 1
            continue
        if previous.speaker_id != current.speaker_id:
            summary["legitimate"] += 1
            continue
        diagnostics = ["SAME_SPEAKER_OVERLAP"]
        if duplicate_continuation_candidate(previous.zh, current.zh):
            diagnostics.append("DUPLICATE_CONTINUATION")
        if current.start - previous.start < min_duration:
            diagnostics.append("TIMING_REVIEW_REQUIRED")
            previous.overlap_type = current.overlap_type = "TIMING_REVIEW_REQUIRED"
            previous.overlap_diagnostics = current.overlap_diagnostics = diagnostics
            summary["timing_review"] += 1
            continue
        _clamp_utterance_end(previous, current.start)
        diagnostics.append("CLAMP_PREVIOUS_END")
        fixed.append((previous, current, diagnostics, overlap_seconds))
        summary["same_speaker_fixed"] += 1

    detect_overlaps(rows)
    for previous, current, diagnostics, overlap_seconds in fixed:
        for row in (previous, current):
            row.overlap_type = "SAME_SPEAKER_CONFLICT"
            row.overlap_diagnostics = list(diagnostics)
        logging.getLogger(__name__).info(
            "Overlap reconciliation: utt_%s -> utt_%s speaker=%s overlap=%.3fs action=CLAMP_PREVIOUS_END",
            previous.id, current.id, previous.speaker_id, overlap_seconds,
        )
    logging.getLogger(__name__).info(
        "Overlap reconciliation: total=%d legitimate=%d same-speaker-fixed=%d unknown-review=%d timing-review=%d",
        summary["total"], summary["legitimate"], summary["same_speaker_fixed"],
        summary["unknown_review"], summary["timing_review"],
    )
    return summary


def resolve_subtitle_lanes(utterances):
    """Assign deterministic stable vertical lane index (0, 1, 2...) for each utterance ID.

    Utterances not in legitimate overlap get lane 0.
    In each legitimate overlap group, each distinct speaker receives a stable lane (0, 1, ...)
    based on their first appearance in the group, ensuring that lanes never swap mid-way.
    Returns:
        dict[int, int]: mapping from utterance ID to lane index.
    """
    lane_map = {u.id: 0 for u in utterances}
    groups = {}
    for u in utterances:
        if u.overlap and u.overlap_group:
            groups.setdefault(u.overlap_group, []).append(u)

    for group_id, group_rows in groups.items():
        distinct_speakers = sorted(
            list({r.speaker_id for r in group_rows}),
            key=lambda spk: min(r.start for r in group_rows if r.speaker_id == spk),
        )
        speaker_lanes = {spk: idx for idx, spk in enumerate(distinct_speakers)}
        for r in group_rows:
            lane_map[r.id] = speaker_lanes.get(r.speaker_id, 0)

    return lane_map



def refresh_timeline(project):
    config=DubbingSettings(**project.dubbing_settings).validate()
    project.dubbing_settings=config.to_dict()
    for key, value in project.speakers.items():
        speaker=Speaker(**value)
        if speaker.id != key: raise ValueError("Speaker registry ID mismatch")
    for segment in project.segments:
        segment.validate()
        if segment.speaker_id not in project.speakers:
            project.speakers[segment.speaker_id]=asdict(Speaker(segment.speaker_id,segment.speaker_name))
        segment.speaker_name=project.speakers[segment.speaker_id]["name"]
        segment.recalculate(config)
    detect_overlaps(project.segments)


def review_hash(project):
    return content_hash([{"id":s.id,"start":s.start,"end":s.end,"zh":s.zh,
                         "speaker_id":s.speaker_id,"speaker_name":s.speaker_name} for s in project.segments])


def review_complete(project):
    return bool(project.segments) and all(s.speaker_id != "SPK_UNKNOWN" for s in project.segments) and project.speaker_review_hash==review_hash(project)


def approve_review(project):
    refresh_timeline(project)
    if any(s.speaker_id=="SPK_UNKNOWN" for s in project.segments):
        raise ValueError("Còn dòng chưa gán speaker. Chọn các dòng và gán SPK_01… trước khi xác nhận.")
    summary = reconcile_overlaps(project.segments)
    if summary["same_speaker_fixed"]:
        for segment in project.segments:
            if "CLAMP_PREVIOUS_END" in segment.overlap_diagnostics:
                if segment.tts_generation_status in {"generated", "cached"}:
                    segment.tts_generation_status = "stale"
                    segment.tts_error = ""
        project.final_audio_status = "stale"
    project.speaker_review_hash=review_hash(project)


def speaker_counts(project):
    return {key:sum(s.speaker_id==key for s in project.segments) for key in project.speakers}


def capture_initial_speaker_state(project, replace=False):
    """Persist the minimal immutable reset baseline for the current transcript."""
    if project.speaker_review_initial_state and not replace:
        return False
    refresh_timeline(project)
    project.speaker_review_initial_state = {
        "speakers": deepcopy(project.speakers),
        "assignments": {str(row.id): row.speaker_id for row in project.utterances},
        "speaker_review_hash": project.speaker_review_hash,
    }
    return True


def _validated_speaker_state(project, speakers, assignments, require_complete):
    if not isinstance(speakers, dict) or not isinstance(assignments, dict):
        raise ValueError("Speaker state không hợp lệ")
    registry = {}
    for key, raw in speakers.items():
        speaker = Speaker(**raw)
        if speaker.id != key:
            raise ValueError("Speaker registry ID mismatch")
        registry[key] = asdict(speaker)
    utterance_ids = {row.id for row in project.utterances}
    normalized = {int(key): value for key, value in assignments.items()}
    if set(normalized) != utterance_ids:
        raise ValueError("Speaker assignment không khớp timeline hiện tại")
    if any(speaker_id not in registry for speaker_id in normalized.values()):
        raise ValueError("Speaker assignment tham chiếu SPK không tồn tại")
    if require_complete and any(speaker_id == UNKNOWN_SPEAKER for speaker_id in normalized.values()):
        raise ValueError("Còn dòng chưa gán speaker. Chọn các dòng và gán SPK_01… trước khi xác nhận.")
    return registry, normalized


def apply_speaker_review_state(project, speakers, assignments):
    """Atomically commit one confirmed registry + batch assignment session."""
    registry, normalized = _validated_speaker_state(project, speakers, assignments, True)
    old_registry = project.speakers
    old_ids = {row.id: row.speaker_id for row in project.utterances}
    old_hash = project.speaker_review_hash
    try:
        project.speakers = registry
        changed = []
        for row in project.utterances:
            new_id = normalized[row.id]
            if row.speaker_id != new_id:
                changed.append(row)
            row.speaker_id = new_id
            row.speaker_name = registry[new_id]["name"]
        approve_review(project)
        for row in changed:
            if row.tts_generation_status in {"generated", "cached"}:
                row.tts_generation_status = "stale"
                row.tts_error = ""
        if changed:
            project.final_audio_status = "stale"
        return project
    except Exception:
        project.speakers = old_registry
        project.speaker_review_hash = old_hash
        for row in project.utterances:
            row.speaker_id = old_ids[row.id]
        refresh_timeline(project)
        raise


def restore_initial_speaker_state(project):
    baseline = project.speaker_review_initial_state
    if not baseline:
        capture_initial_speaker_state(project)
        baseline = project.speaker_review_initial_state
    registry, normalized = _validated_speaker_state(
        project, baseline.get("speakers", {}), baseline.get("assignments", {}), False,
    )
    changed = []
    project.speakers = registry
    for row in project.utterances:
        if row.speaker_id != normalized[row.id]:
            changed.append(row)
        row.speaker_id = normalized[row.id]
        row.speaker_name = registry[row.speaker_id]["name"]
    project.speaker_review_hash = str(baseline.get("speaker_review_hash", ""))
    refresh_timeline(project)
    for row in changed:
        if row.tts_generation_status in {"generated", "cached"}:
            row.tts_generation_status = "stale"
            row.tts_error = ""
    if changed:
        project.final_audio_status = "stale"
    return project
