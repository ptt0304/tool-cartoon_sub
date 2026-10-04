"""Allocate presentation timestamps inside one Utterance without touching its source timing."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from cartoon_sub.subtitle.models import DisplaySegment
from cartoon_sub.subtitle.segmentation import SegmentationPlan, SegmentationSettings, normalize_text
from cartoon_sub.syllable.vietnamese import count_syllables


_WORD = re.compile(r"[0-9]+(?:[.,][0-9]+)?|[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class AllocationResult:
    segments: tuple[DisplaySegment, ...]
    timing_source: str


def _finite_time(value):
    return type(value) in (int, float) and math.isfinite(value)


def _part_end_positions(plan):
    position = 0
    ends = []
    for part in plan.parts[:-1]:
        position += len(part)
        ends.append(position)
    return ends


def _valid_boundaries(boundaries, utterance, count):
    if len(boundaries) != count - 1 or any(not _finite_time(value) for value in boundaries):
        return False
    all_times = [utterance.start, *boundaries, utterance.end]
    return all(left < right for left, right in zip(all_times, all_times[1:]))


def _word_timing_boundaries(utterance, plan, word_timestamps):
    if not word_timestamps:
        return None
    source_words = list(_WORD.finditer(plan.source_text))
    if len(source_words) != len(word_timestamps):
        return None
    times = []
    for source, mark in zip(source_words, word_timestamps):
        if not isinstance(mark, dict) or not _finite_time(mark.get("start")) or not _finite_time(mark.get("end")):
            return None
        if mark["start"] < utterance.start or mark["end"] > utterance.end or mark["end"] <= mark["start"]:
            return None
        if "text" in mark and normalize_text(mark["text"]).casefold() != source.group().casefold():
            return None
        times.append(mark)
    if any(left["end"] > right["start"] for left, right in zip(times, times[1:])):
        return None
    boundaries = []
    for position in _part_end_positions(plan):
        previous = [index for index, word in enumerate(source_words) if word.end() <= position]
        if not previous or previous[-1] >= len(times) - 1:
            return None
        boundaries.append(times[previous[-1]]["end"])
    return boundaries if _valid_boundaries(boundaries, utterance, len(plan.parts)) else None


def _clause_position_boundaries(utterance, plan, clause_timestamps):
    if not clause_timestamps:
        return None
    if isinstance(clause_timestamps, dict):
        marks = clause_timestamps
    else:
        if any(not isinstance(mark, dict) for mark in clause_timestamps):
            return None
        marks = {mark.get("position"): mark.get("time") for mark in clause_timestamps}
    boundaries = [marks.get(position) for position in _part_end_positions(plan)]
    return boundaries if _valid_boundaries(boundaries, utterance, len(plan.parts)) else None


def _proportional_boundaries(utterance, plan):
    weights = [count_syllables(part) for part in plan.parts]
    source = "syllable_proportion"
    if not any(weights):
        # Keep the source-clause character offsets, including their original
        # spacing, when syllables are unavailable (for example punctuation).
        weights = [len(part) for part in plan.parts]
        source = "character_proportion"
    if not any(weights):
        weights = [1] * len(plan.parts)
        source = "even_proportion"
    total = sum(weights)
    elapsed = 0.0
    boundaries = []
    for weight in weights[:-1]:
        elapsed += utterance.duration * weight / total
        boundaries.append(utterance.start + elapsed)
    return boundaries, source


def _rebalance(utterance, boundaries, minimum_duration):
    """Keep continuous coverage and meet minimum duration whenever the slot permits it."""
    original = [utterance.start, *boundaries, utterance.end]
    desired = [right - left for left, right in zip(original, original[1:])]
    count, total = len(desired), utterance.duration
    if total < count * minimum_duration:
        weights = desired if any(desired) else [1] * count
        scale = total / sum(weights)
        lengths = [weight * scale for weight in weights]
    else:
        remaining = total - count * minimum_duration
        weights = [max(0.0, value - minimum_duration) for value in desired]
        if not any(weights):
            weights = [1.0] * count
        total_weight = sum(weights)
        lengths = [minimum_duration + remaining * weight / total_weight for weight in weights]
    result = []
    point = utterance.start
    for length in lengths[:-1]:
        point += length
        result.append(point)
    return result


def _qc_flags(part, duration, settings, inherited):
    flags = list(inherited)
    if duration < settings.min_duration:
        flags.append("TOO_SHORT")
    if duration > settings.max_duration:
        flags.append("TOO_LONG")
    if count_syllables(part) > settings.max_syllables:
        flags.append("TOO_MANY_SYLLABLES")
    if len(part.splitlines()) > settings.max_lines:
        flags.append("TOO_MANY_LINES")
    return list(dict.fromkeys(flags)) or ["OK"]


class DisplaySegmentAllocator:
    """Converts a source-preserving plan into timed children without mutating the Utterance."""
    def __init__(self, settings=None):
        self.settings = (settings or SegmentationSettings()).validate()

    def allocate(self, utterance, plan: SegmentationPlan, *, word_timestamps=None, clause_timestamps=None):
        if plan.utterance_id != utterance.id or plan.speaker_id != utterance.speaker_id:
            raise ValueError("Segmentation plan does not belong to this utterance")
        if not plan.parts or not plan.preserves_source_text() or normalize_text(plan.source_text) != normalize_text(utterance.vi_subtitle):
            raise ValueError("Segmentation plan does not preserve utterance subtitle text")
        boundaries = _word_timing_boundaries(utterance, plan, word_timestamps)
        source = "word_timestamps"
        if boundaries is None:
            boundaries = _clause_position_boundaries(utterance, plan, clause_timestamps)
            source = "source_clause_position"
        if boundaries is None:
            boundaries, source = _proportional_boundaries(utterance, plan)
        # Exact word/clause anchors are timing authority. Rebalancing is only
        # allowed for locally estimated proportional timings.
        if source in {"syllable_proportion", "character_proportion", "even_proportion"}:
            boundaries = _rebalance(utterance, boundaries, self.settings.min_duration)
        times = [utterance.start, *boundaries, utterance.end]
        if any(left >= right for left, right in zip(times, times[1:])):
            raise ValueError("Unable to allocate positive display segment durations")
        segments = []
        for index, (text, start, end) in enumerate(zip(plan.parts, times, times[1:]), 1):
            reason = plan.boundary_reasons[index - 1] if index <= len(plan.boundary_reasons) else "utterance_end"
            segments.append(DisplaySegment(f"{utterance.id}.{index}", utterance.id, start, end, text,
                segmentation_reason=reason, qc_flags=_qc_flags(text, end - start, self.settings, plan.qc_flags)))
        for segment in segments:
            segment.inherit_speaker(utterance.speaker_id)
        self._validate(utterance, plan, segments)
        return AllocationResult(tuple(segments), source)

    @staticmethod
    def _validate(utterance, plan, segments):
        if segments[0].start != utterance.start or segments[-1].end != utterance.end:
            raise ValueError("Display timing must cover utterance boundaries")
        if any(left.end != right.start for left, right in zip(segments, segments[1:])):
            raise ValueError("Display timing must not contain gaps")
        if any(segment.utterance_id != utterance.id or segment.speaker_id != utterance.speaker_id for segment in segments):
            raise ValueError("Display speaker mapping changed")
        if normalize_text("".join(segment.vi_text for segment in segments)) != normalize_text(utterance.vi_subtitle):
            raise ValueError("Display timing changed source text")


def allocate_display_segments(utterance, plan, settings=None, *, word_timestamps=None, clause_timestamps=None):
    return DisplaySegmentAllocator(settings).allocate(utterance, plan,
        word_timestamps=word_timestamps, clause_timestamps=clause_timestamps)
