"""Deterministic presentation-subtitle QC."""
import re

from cartoon_sub.subtitle.segmentation import hard_limit_failures, part_is_hard_valid
from cartoon_sub.syllable.vietnamese import count_syllables


MAX_SYLLABLES_PER_SECOND = 5.0
DYNAMIC_QC_FLAGS = {
    "OK", "TOO_SHORT", "TOO_LONG", "TOO_MANY_SYLLABLES", "TOO_MANY_LINES",
    "TOO_MANY_CHARS_PER_LINE", "HIGH_READING_SPEED", "BAD_SPLIT",
}


def review_display_segment(segment, settings, source_text=None):
    flags = [flag for flag in segment.qc_flags if flag not in DYNAMIC_QC_FLAGS]
    if segment.duration < settings.min_duration:
        flags.append("TOO_SHORT")
    hard_failures = hard_limit_failures(segment.vi_text, segment.duration, settings)
    if "max_duration" in hard_failures:
        flags.append("TOO_LONG")
    if "max_syllables" in hard_failures:
        flags.append("TOO_MANY_SYLLABLES")
    if "max_lines" in hard_failures:
        flags.append("TOO_MANY_LINES")
    if "hard_chars_per_line" in hard_failures:
        flags.append("TOO_MANY_CHARS_PER_LINE")
    if count_syllables(segment.vi_text) / segment.duration > MAX_SYLLABLES_PER_SECOND:
        flags.append("HIGH_READING_SPEED")
    words = re.findall(r"[^\W\d_]+", segment.vi_text, re.UNICODE)
    if source_text and len(words) <= 1 and len(re.findall(r"[^\W\d_]+", source_text, re.UNICODE)) > 1:
        flags.append("BAD_SPLIT")
    if re.fullmatch(r"\s*[.,;:!?…。，；：！？]+\s*", segment.vi_text):
        flags.append("BAD_SPLIT")
    if segment.manual or segment.segmentation_reason == "manual":
        flags.append("MANUAL_REVIEW")
    return list(dict.fromkeys(flags)) or ["OK"]


def apply_display_qc(segment, settings, source_text=None):
    segment.qc_flags = review_display_segment(segment, settings, source_text)
    return segment.qc_flags


def review_display_segments(segments, settings, source_text, source_duration):
    """Review sibling-aware split quality without changing canonical text/timing."""
    reviewed = [review_display_segment(segment, settings, source_text) for segment in segments]
    if len(segments) <= 1:
        return reviewed
    bad = set()
    if part_is_hard_valid(source_text, source_duration, settings):
        bad.update(range(len(segments)))
    for index, segment in enumerate(segments):
        words = re.findall(r"[^\W\d_]+", segment.vi_text, re.UNICODE)
        if len(words) <= 1:
            bad.add(index)
        if re.match(r"^\s*[,;:!?…。，；：！？]", segment.vi_text):
            bad.add(index)
    # A whitespace/semantic cut is poor when a usable punctuation boundary
    # could have produced two non-orphan, hard-valid children.
    punctuation_positions = [match.end() for match in re.finditer(r"[.!?…。！？;；:：,，]+", source_text)]
    usable_punctuation = False
    for position in punctuation_positions:
        left, right = source_text[:position], source_text[position:]
        left_words = re.findall(r"[^\W\d_]+", left, re.UNICODE)
        right_words = re.findall(r"[^\W\d_]+", right, re.UNICODE)
        ratio = len(left) / max(1, len(source_text))
        if (len(left_words) > 1 and len(right_words) > 1
                and part_is_hard_valid(left, source_duration * ratio, settings)
                and part_is_hard_valid(right, source_duration * (1 - ratio), settings)):
            usable_punctuation = True
            break
    if usable_punctuation:
        for index, segment in enumerate(segments[:-1]):
            if segment.segmentation_reason in {"whitespace_boundary", "semantic_boundary"}:
                bad.update((index, index + 1))
    for index in bad:
        if "BAD_SPLIT" not in reviewed[index]:
            reviewed[index] = [flag for flag in reviewed[index] if flag != "OK"] + ["BAD_SPLIT"]
    return reviewed


def apply_display_group_qc(segments, settings, source_text, source_duration):
    reviewed = review_display_segments(segments, settings, source_text, source_duration)
    for segment, flags in zip(segments, reviewed):
        segment.qc_flags = flags
    return reviewed
