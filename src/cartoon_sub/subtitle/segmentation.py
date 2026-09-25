"""Deterministic presentation-text splitting; timing allocation belongs to a later phase."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from cartoon_sub.syllable.vietnamese import count_syllables


class SegmentationProfile(StrEnum):
    BALANCED = "BALANCED"
    READING_COMFORT = "READING_COMFORT"
    FAST_DIALOGUE = "FAST_DIALOGUE"
    PRESERVE_SENTENCES = "PRESERVE_SENTENCES"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class SegmentationSettings:
    min_duration: float = 1.0
    preferred_duration_min: float = 2.0
    preferred_duration_max: float = 4.0
    max_duration: float = 5.0
    preferred_syllables_min: int = 8
    preferred_syllables_max: int = 16
    max_syllables: int = 18
    max_lines: int = 2
    preferred_chars_per_line: int = 36
    hard_max_chars_per_line: int = 44

    def validate(self):
        if not (0 < self.min_duration <= self.preferred_duration_min <= self.preferred_duration_max <= self.max_duration):
            raise ValueError("Invalid segmentation duration limits")
        if not (1 <= self.preferred_syllables_min <= self.preferred_syllables_max <= self.max_syllables):
            raise ValueError("Invalid segmentation syllable limits")
        if self.max_lines < 1 or self.preferred_chars_per_line < 1 or self.hard_max_chars_per_line < self.preferred_chars_per_line:
            raise ValueError("Invalid segmentation visual limits")
        return self


PROFILE_SETTINGS = {
    SegmentationProfile.BALANCED: SegmentationSettings(),
    SegmentationProfile.READING_COMFORT: SegmentationSettings(preferred_duration_min=2.5, preferred_duration_max=4.5,
        preferred_syllables_min=7, preferred_syllables_max=14, max_syllables=16),
    SegmentationProfile.FAST_DIALOGUE: SegmentationSettings(min_duration=.75, preferred_duration_min=1.2,
        preferred_duration_max=2.8, max_duration=4.0, preferred_syllables_min=5, preferred_syllables_max=11,
        max_syllables=14),
    SegmentationProfile.PRESERVE_SENTENCES: SegmentationSettings(max_duration=6.0, preferred_syllables_min=8,
        preferred_syllables_max=18, max_syllables=20),
}


def settings_for(profile=SegmentationProfile.BALANCED, custom=None):
    profile = SegmentationProfile(profile)
    if profile is SegmentationProfile.CUSTOM:
        if custom is None:
            raise ValueError("CUSTOM segmentation requires settings")
        return custom.validate()
    if custom is not None:
        raise ValueError("Custom segmentation settings require CUSTOM profile")
    return PROFILE_SETTINGS[profile].validate()


@dataclass(frozen=True)
class BoundaryCandidate:
    position: int
    kind: str
    score: float


@dataclass(frozen=True)
class SegmentationPlan:
    utterance_id: int
    speaker_id: str
    source_text: str
    parts: tuple[str, ...]
    boundary_reasons: tuple[str, ...]
    candidates: tuple[BoundaryCandidate, ...]
    accepted_as_one: bool
    requires_timestamp_allocation: bool
    qc_flags: tuple[str, ...] = ()

    def preserves_source_text(self):
        return normalize_text("".join(self.parts)) == normalize_text(self.source_text)


STRONG_PUNCTUATION = ".?!…。？！"
SOFT_PUNCTUATION = ",;:，；："
SEMANTIC_PHRASES = (
    "tuy nhiên", "vì vậy", "do đó", "sau đó", "hôm nay", "ngày mai", "thế nhưng",
    "trong khi", "chỉ vì", "nhưng", "rồi", "còn",
)
PROTECTED_PATTERNS = (
    r"\bCao\s+Câu\s+Ly\b",
    r"\b(?:Tần\s+Vương|Hoàng\s+đế|Bệ\s+hạ|Thái\s+tử)\s+điện\s+hạ\b",
    r"\b(?:lần\s+thứ\s+[\wÀ-ỹ]+|ngày\s+\d{1,2}|tháng\s+\d{1,2}|năm\s+\d{1,4})\b",
    r"\b\d+(?:[./-]\d+)+(?:\s*%|\b)",
    r"\b[A-ZÀ-ỸĐ][\wÀ-ỹ]+(?:\s+[A-ZÀ-ỸĐ][\wÀ-ỹ]+)+\b",
)


def normalize_text(text):
    return re.sub(r"\s+", " ", text).strip()


def restore_exact_parts(parts, source_text):
    """Restore boundary whitespace trimmed by structured-output string fields.

    Every non-whitespace character must still match the source consecutively;
    this does not permit rewriting, reordering, or punctuation changes.
    """
    restored = []
    cursor = 0
    for part in parts:
        token = part.strip()
        content_start = cursor
        while content_start < len(source_text) and source_text[content_start].isspace():
            content_start += 1
        if not token or not source_text.startswith(token, content_start):
            raise ValueError("parts không còn là các substring liên tiếp của văn bản nguồn")
        end = content_start + len(token)
        while end < len(source_text) and source_text[end].isspace():
            end += 1
        restored.append(source_text[cursor:end])
        cursor = end
    if cursor != len(source_text):
        raise ValueError("parts không phủ hết văn bản nguồn")
    return tuple(restored)


def protected_spans(text):
    spans = []
    for pattern in PROTECTED_PATTERNS:
        spans.extend(match.span() for match in re.finditer(pattern, text))
    return spans


def _inside_protected(position, spans):
    return any(start < position < end for start, end in spans)


def _raw_candidates(text):
    spans = protected_spans(text)
    found = {}
    for index, char in enumerate(text):
        if char in STRONG_PUNCTUATION:
            found[index + 1] = "sentence_boundary"
        elif char in SOFT_PUNCTUATION:
            found[index + 1] = "comma_boundary"
    phrase_pattern = r"(?<!\w)(?:" + "|".join(re.escape(item) for item in SEMANTIC_PHRASES) + r")(?!\w)"
    for match in re.finditer(phrase_pattern, text, re.IGNORECASE):
        found.setdefault(match.start(), "semantic_boundary")
    return [(position, kind) for position, kind in found.items()
            if 0 < position < len(text) and not _inside_protected(position, spans)]


def _projected_duration(text, total_text, total_duration):
    total = count_syllables(total_text)
    if total:
        return total_duration * count_syllables(text) / total
    return total_duration * len(text) / max(1, len(total_text))


def _part_is_acceptable(text, duration, settings):
    syllables = count_syllables(text)
    visual_capacity = settings.hard_max_chars_per_line * settings.max_lines
    return (duration <= settings.max_duration and syllables <= settings.max_syllables and
            len(normalize_text(text)) <= visual_capacity)


def _duration_fit(duration, settings):
    if settings.preferred_duration_min <= duration <= settings.preferred_duration_max:
        return 4.0
    if duration < settings.min_duration:
        return -6.0
    return max(-4.0, 2.0 - abs(duration - settings.preferred_duration_max))


def _syllable_fit(syllables, settings):
    if settings.preferred_syllables_min <= syllables <= settings.preferred_syllables_max:
        return 4.0
    if syllables > settings.max_syllables:
        return -8.0
    if syllables < 3:
        return -6.0
    return 1.0


def _visual_fit(text, settings):
    length = len(normalize_text(text))
    preferred = settings.preferred_chars_per_line * settings.max_lines
    maximum = settings.hard_max_chars_per_line * settings.max_lines
    if length <= preferred:
        return 3.0
    if length <= maximum:
        return 1.0
    return -6.0


def _candidate_score(text, position, kind, duration, settings):
    left, right = text[:position], text[position:]
    if _inside_protected(position, protected_spans(text)):
        return -1000.0
    punctuation = {"sentence_boundary": 12.0, "comma_boundary": 7.0, "semantic_boundary": 9.0}[kind]
    semantic = {"sentence_boundary": 7.0, "comma_boundary": 3.0, "semantic_boundary": 8.0}[kind]
    left_duration = _projected_duration(left, text, duration)
    right_duration = _projected_duration(right, text, duration)
    score = punctuation + semantic
    score += _duration_fit(left_duration, settings) + _duration_fit(right_duration, settings)
    score += _syllable_fit(count_syllables(left), settings) + _syllable_fit(count_syllables(right), settings)
    score += _visual_fit(left, settings) + _visual_fit(right, settings)
    if len(normalize_text(left).split()) <= 2 or len(normalize_text(right).split()) <= 2:
        score -= 15.0
    return score


class LocalSegmentationEngine:
    """Returns immutable text plans. It does not allocate child timestamps or mutate an Utterance."""
    def __init__(self, profile=SegmentationProfile.BALANCED, custom_settings=None):
        self.profile = SegmentationProfile(profile)
        self.settings = settings_for(self.profile, custom_settings)

    def segment(self, utterance):
        text = utterance.vi_subtitle
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Utterance must contain Vietnamese subtitle text")
        candidates = self._candidates(text, utterance.duration)
        if _part_is_acceptable(text, utterance.duration, self.settings):
            return SegmentationPlan(utterance.id, utterance.speaker_id, text, (text,), (), tuple(candidates), True, False)
        parts, reasons, unresolved = self._split(text, utterance.duration, candidates)
        flags = ("MANUAL_REVIEW",) if unresolved else ()
        plan = SegmentationPlan(utterance.id, utterance.speaker_id, text, tuple(parts), tuple(reasons),
            tuple(candidates), False, len(parts) > 1, flags)
        if not plan.preserves_source_text():
            raise ValueError("Local segmentation changed source text")
        return plan

    def _candidates(self, text, duration):
        return [BoundaryCandidate(position, kind, _candidate_score(text, position, kind, duration, self.settings))
                for position, kind in _raw_candidates(text)]

    def _split(self, text, duration, candidates):
        def recurse(start, end):
            fragment = text[start:end]
            estimate = _projected_duration(fragment, text, duration)
            if _part_is_acceptable(fragment, estimate, self.settings):
                return [(start, end)], [], False
            available = [candidate for candidate in candidates if start < candidate.position < end]
            if not available:
                return [(start, end)], [], True
            candidate = max(available, key=lambda item: self._range_score(text, start, end, item, duration))
            left, left_reasons, left_unresolved = recurse(start, candidate.position)
            right, right_reasons, right_unresolved = recurse(candidate.position, end)
            return left + right, left_reasons + [candidate.kind] + right_reasons, left_unresolved or right_unresolved

        ranges, reasons, unresolved = recurse(0, len(text))
        return [text[start:end] for start, end in ranges], reasons, unresolved

    def _range_score(self, text, start, end, candidate, duration):
        fragment = text[start:end]
        relative = candidate.position - start
        fragment_duration = _projected_duration(fragment, text, duration)
        return _candidate_score(fragment, relative, candidate.kind, fragment_duration, self.settings)


def segment_utterance(utterance, profile=SegmentationProfile.BALANCED, custom_settings=None):
    return LocalSegmentationEngine(profile, custom_settings).segment(utterance)
