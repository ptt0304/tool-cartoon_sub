"""Provider-neutral transcription capabilities and optional result metadata."""
from dataclasses import dataclass
from enum import Enum
import math
import re


class TranscriptionCapability(str, Enum):
    TRANSCRIPTION = "TRANSCRIPTION"
    SEGMENT_TIMESTAMPS = "SEGMENT_TIMESTAMPS"
    WORD_TIMESTAMPS = "WORD_TIMESTAMPS"
    SPEAKER_HINTS = "SPEAKER_HINTS"


class CapabilityAvailability(str, Enum):
    PROVIDED = "PROVIDED"
    BEST_EFFORT = "BEST_EFFORT"
    RESPONSE_DEPENDENT = "RESPONSE_DEPENDENT"
    NOT_PROVIDED = "NOT_PROVIDED"


@dataclass(frozen=True)
class TranscriptionCapabilities:
    provider: str
    values: dict[TranscriptionCapability, CapabilityAvailability]

    def availability(self, capability):
        return self.values.get(capability, CapabilityAvailability.NOT_PROVIDED)


_BASE = {
    TranscriptionCapability.TRANSCRIPTION: CapabilityAvailability.PROVIDED,
    TranscriptionCapability.SEGMENT_TIMESTAMPS: CapabilityAvailability.RESPONSE_DEPENDENT,
    TranscriptionCapability.WORD_TIMESTAMPS: CapabilityAvailability.NOT_PROVIDED,
    TranscriptionCapability.SPEAKER_HINTS: CapabilityAvailability.NOT_PROVIDED,
}


def capabilities_for(provider):
    """Describe the adapter path, never infer capabilities from a model name."""
    values = dict(_BASE)
    if provider == "gemini":
        values[TranscriptionCapability.SEGMENT_TIMESTAMPS] = CapabilityAvailability.PROVIDED
        values[TranscriptionCapability.SPEAKER_HINTS] = CapabilityAvailability.BEST_EFFORT
    elif provider == "openrouter":
        # Dedicated STT requests verbose output, but fields still depend on the
        # selected provider's actual response through OpenRouter.
        values[TranscriptionCapability.WORD_TIMESTAMPS] = CapabilityAvailability.RESPONSE_DEPENDENT
        values[TranscriptionCapability.SPEAKER_HINTS] = CapabilityAvailability.RESPONSE_DEPENDENT
    return TranscriptionCapabilities(str(provider or "unknown"), values)


def normalize_speaker_hint(value):
    """Normalize only explicit SPK hints; arbitrary labels remain unresolved."""
    if not isinstance(value, str):
        return "SPK_UNKNOWN"
    text = value.strip().upper()
    if text in {"UNKNOWN", "SPK_UNKNOWN"}:
        return "SPK_UNKNOWN"
    match = re.fullmatch(r"SPK[_ -]?(\d+)", text)
    if not match or int(match.group(1)) < 1:
        return "SPK_UNKNOWN"
    return f"SPK_{int(match.group(1)):02d}"


def normalize_confidence(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def normalize_word_timestamps(rows, duration):
    """Return safe optional word timing metadata; malformed entries are ignored."""
    if not isinstance(rows, list):
        return []
    words = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        text = raw.get("word", raw.get("text", ""))
        try:
            start, end = float(raw.get("start")), float(raw.get("end"))
        except (TypeError, ValueError):
            continue
        if (not isinstance(text, str) or not text.strip() or not math.isfinite(start)
                or not math.isfinite(end) or start < 0 or end <= start):
            continue
        item = {"word": text.strip(), "start": start, "end": min(end, duration)}
        if item["end"] <= item["start"]:
            continue
        confidence = normalize_confidence(raw.get("confidence", raw.get("probability")))
        if confidence is not None:
            item["confidence"] = confidence
        speaker_id = normalize_speaker_hint(raw.get("speaker_id", raw.get("speaker")))
        if speaker_id != "SPK_UNKNOWN":
            item["speaker_id"] = speaker_id
        words.append(item)
    return words
