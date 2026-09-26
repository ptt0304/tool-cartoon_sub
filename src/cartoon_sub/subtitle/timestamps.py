"""Shared GUI/SRT timestamp formatting without changing float-second storage."""

import math
import re


_SRT_TIMESTAMP = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)[,.](\d{1,3})$")


def format_srt_timestamp(seconds):
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Timestamp must be a finite non-negative number")
    total_ms = int(round(value * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def parse_srt_timestamp(value):
    if type(value) in (int, float):
        result = float(value)
    else:
        text = str(value).strip()
        match = _SRT_TIMESTAMP.fullmatch(text)
        if match:
            hours, minutes, seconds, milliseconds = match.groups()
            result = (int(hours) * 3600 + int(minutes) * 60 + int(seconds)
                      + int(milliseconds.ljust(3, "0")) / 1000)
        else:
            result = float(text)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Timestamp must be a finite non-negative number")
    return result
