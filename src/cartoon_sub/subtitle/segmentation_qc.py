"""Deterministic presentation-subtitle QC."""
import re

from cartoon_sub.syllable.vietnamese import count_syllables


MAX_SYLLABLES_PER_SECOND = 5.0
DYNAMIC_QC_FLAGS = {
    "OK", "TOO_SHORT", "TOO_LONG", "TOO_MANY_SYLLABLES", "TOO_MANY_LINES",
    "TOO_MANY_CHARS_PER_LINE", "HIGH_READING_SPEED", "BAD_SPLIT", "MANUAL_REVIEW",
}


def review_display_segment(segment, settings, source_text=None):
    flags = [flag for flag in segment.qc_flags if flag not in DYNAMIC_QC_FLAGS]
    if segment.duration < settings.min_duration:
        flags.append("TOO_SHORT")
    if segment.duration > settings.max_duration:
        flags.append("TOO_LONG")
    if count_syllables(segment.vi_text) > settings.max_syllables:
        flags.append("TOO_MANY_SYLLABLES")
    if segment.line_count > settings.max_lines:
        flags.append("TOO_MANY_LINES")
    if any(len(re.sub(r"\s+", " ", line).strip()) > settings.hard_max_chars_per_line
           for line in segment.vi_text.splitlines() or [segment.vi_text]):
        flags.append("TOO_MANY_CHARS_PER_LINE")
    if count_syllables(segment.vi_text) / segment.duration > MAX_SYLLABLES_PER_SECOND:
        flags.append("HIGH_READING_SPEED")
    words = re.findall(r"[^\W\d_]+", segment.vi_text, re.UNICODE)
    if source_text and len(words) <= 1 and len(re.findall(r"[^\W\d_]+", source_text, re.UNICODE)) > 1:
        flags.append("BAD_SPLIT")
    if segment.manual or segment.segmentation_reason == "manual":
        flags.append("MANUAL_REVIEW")
    return list(dict.fromkeys(flags)) or ["OK"]


def apply_display_qc(segment, settings, source_text=None):
    segment.qc_flags = review_display_segment(segment, settings, source_text)
    return segment.qc_flags
