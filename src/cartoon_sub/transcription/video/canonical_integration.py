"""Deterministic Phase 5 bridge from cached video OCR to the master timeline."""
from __future__ import annotations

import math
from dataclasses import dataclass

from cartoon_sub.subtitle.models import Utterance
from cartoon_sub.transcription.language_consistency import assess_text


# A detector split may put two copies of the same hard subtitle a few frames
# apart.  This is deliberately small so ordinary repeated dialogue remains
# separate source events.
EXACT_DUPLICATE_MERGE_GAP_SECONDS = 0.40


@dataclass(frozen=True)
class VideoIntegrationResult:
    utterances: list[Utterance]
    eligible_before_merge: int
    same_text_merges: int
    language_mismatch: int
    manual_review: int


def _eligible(event):
    return (
        isinstance(event, dict)
        and event.get("ocr_status") == "OCR_SUCCESS"
        and event.get("has_subtitle") is True
        and isinstance(event.get("normalized_text"), str)
        and bool(event["normalized_text"].strip())
        and all(type(event.get(key)) in (int, float) and math.isfinite(event[key])
                for key in ("refined_start", "refined_end"))
        and event["refined_start"] >= 0
        and event["refined_start"] < event["refined_end"]
    )


def _reject_invalid_success_event(event):
    """A successful OCR row with invalid detector timing is not safe to hide."""
    if not isinstance(event, dict) or event.get("ocr_status") != "OCR_SUCCESS":
        return
    if event.get("has_subtitle") is True and isinstance(event.get("normalized_text"), str) and event["normalized_text"].strip():
        start, end = event.get("refined_start"), event.get("refined_end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or start < 0 or start >= end):
            raise ValueError("Video OCR success event has invalid duration")


def _merge_exact_duplicates(events):
    # Repeated text may be a separate subtitle event.  Preserve detector
    # events rather than stretching their timestamp through a guessed merge.
    merged = []
    for event in sorted(events, key=lambda item: (item["refined_start"], item["refined_end"], item["event_id"])):
        row = dict(event)
        row["merged_event_ids"] = [event["event_id"]]
        merged.append(row)
    return merged, 0


def validate_video_timeline(rows, project_duration=None):
    previous_end = 0.0
    seen_ids = set()
    for index, row in enumerate(rows):
        row.validate()
        if row.id in seen_ids:
            raise ValueError("Video timeline contains duplicate IDs")
        if not row.zh.strip():
            raise ValueError("Video timeline contains empty Chinese text")
        if row.transcript_timing_provenance != "VIDEO_SUBTITLE":
            raise ValueError("Video timeline must retain VIDEO_SUBTITLE timing")
        if index and row.start < previous_end - 1e-9:
            raise ValueError("Video timeline contains overlapping events")
        if project_duration is not None and row.end > float(project_duration) + 1e-6:
            raise ValueError("Video timeline exceeds project duration")
        seen_ids.add(row.id)
        previous_end = row.end


def build_video_timeline(ocr_events, *, project_duration=None, translation_mode="balanced_dubbing"):
    """Build and validate a new timeline without mutating the project."""
    for event in ocr_events:
        _reject_invalid_success_event(event)
    eligible = [event for event in ocr_events if _eligible(event)]
    merged, merge_count = _merge_exact_duplicates(eligible)
    rows = []
    mismatch = manual_review = 0
    for identifier, event in enumerate(merged, 1):
        assessment = assess_text(event["normalized_text"], project_language="zh")
        status = "PASS"
        if assessment.classification == "LANGUAGE_MISMATCH" and assessment.profile.foreign:
            status = "NEEDS_MANUAL_REVIEW"
            mismatch += 1
            manual_review += 1
        rows.append(Utterance(
            identifier,
            event["refined_start"],
            event["refined_end"],
            event["normalized_text"],
            transcript_raw_stt_text=event.get("raw_text", event["normalized_text"]),
            transcript_timing_provenance="VIDEO_SUBTITLE",
            transcript_timing_confidence=1.0,
            transcript_language_status=status,
            translation_mode=translation_mode,
        ))
    validate_video_timeline(rows, project_duration)
    return VideoIntegrationResult(rows, len(eligible), merge_count, mismatch, manual_review)
