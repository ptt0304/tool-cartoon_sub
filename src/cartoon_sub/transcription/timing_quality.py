"""Deterministic evidence-quality gate for transcript timing.

This module deliberately scores timestamp evidence only.  It never infers
word positions from character counts and has no model/provider dependency.
"""
from dataclasses import dataclass


TRUSTED = "TRUSTED"
PARTIAL = "PARTIAL"
SUSPICIOUS = "SUSPICIOUS"
INVALID = "INVALID"


@dataclass(frozen=True)
class TimingQuality:
    status: str
    timestamp_capability: str
    valid_ratio: float
    span: float
    reasons: tuple[str, ...] = ()


def evaluate_timing(segments, words, coarse_start, coarse_end):
    """Assess actual provider timestamps against a coarse VAD window."""
    rows = list(words or []) or list(segments or [])
    capability = "WORD" if words else ("SEGMENT" if segments else "NONE")
    window = max(0.001, float(coarse_end) - float(coarse_start))
    if not rows:
        return TimingQuality(INVALID, capability, 0.0, 0.0, ("no timestamps",))
    valid, reasons, previous_end = [], [], None
    for row in sorted(rows, key=lambda item: (float(item.get("start", -1)), float(item.get("end", -1)))):
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            reasons.append("malformed timestamp"); continue
        if not (0 <= start < end <= window + .05):
            reasons.append("timestamp outside region"); continue
        if previous_end is not None and start < previous_end - .10:
            reasons.append("backward/overlap")
        valid.append((start, end)); previous_end = max(previous_end or end, end)
    ratio = len(valid) / max(1, len(rows))
    if not valid:
        return TimingQuality(INVALID, capability, ratio, 0.0, tuple(dict.fromkeys(reasons)))
    span = valid[-1][1] - valid[0][0]
    text = "".join(str(item.get("zh", item.get("text", item.get("word", "")))
                       if isinstance(item, dict) else getattr(item, "zh", ""))
                   for item in segments or [])
    content = sum(ch.isalnum() or "\u3400" <= ch <= "\u9fff" for ch in text)
    if content <= 4 and span > 8:
        reasons.append("short text over long span")
    durations = [end - start for start, end in valid]
    if len(durations) >= 5 and max(durations) - min(durations) < .015:
        reasons.append("uniform allocation pattern")
    if "backward/overlap" in reasons or "short text over long span" in reasons:
        return TimingQuality(SUSPICIOUS, capability, ratio, span, tuple(dict.fromkeys(reasons)))
    if ratio < .65:
        return TimingQuality(PARTIAL, capability, ratio, span, tuple(dict.fromkeys(reasons)))
    return TimingQuality(TRUSTED, capability, ratio, span, tuple(dict.fromkeys(reasons)))
