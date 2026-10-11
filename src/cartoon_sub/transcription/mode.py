"""Transcript acquisition modes and the persisted Video subtitle ROI contract.

Kept free of project/model imports so ``subtitle.models`` can validate with it.
"""
import math
from enum import Enum

ROI_KEYS = ("x_pct", "y_pct", "w_pct", "h_pct")
_EPSILON = 1e-9


class TranscriptSourceMode(str, Enum):
    AUDIO = "audio"
    VIDEO = "video_subtitle"
    IMPORT_SRT = "import_srt"


DEFAULT_MODE = TranscriptSourceMode.AUDIO.value
MODE_LABELS = {
    TranscriptSourceMode.AUDIO.value: "Lấy transcript từ Audio",
    TranscriptSourceMode.VIDEO.value: "Lấy transcript từ Video",
    TranscriptSourceMode.IMPORT_SRT.value: "Import Chinese SRT",
}


def normalize_mode(value):
    """Return the canonical mode string, migrating the historical ``video`` value."""
    if value == "video":
        return TranscriptSourceMode.VIDEO.value
    try:
        return TranscriptSourceMode(value).value
    except ValueError:
        raise ValueError("Chế độ lấy transcript không hợp lệ") from None


def normalize_roi(value):
    """Validate a normalized ROI. ``{}``/None mean "not selected".

    Coordinates are fractions of the source frame in [0, 1]: x_pct/y_pct is the
    top-left corner, w_pct/h_pct the size. The rectangle must lie inside the frame.
    """
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or set(value) != set(ROI_KEYS):
        raise ValueError("Vùng phụ đề Video không hợp lệ")
    roi = {}
    for key in ROI_KEYS:
        raw = value[key]
        if type(raw) not in (int, float) or not math.isfinite(raw):
            raise ValueError("Vùng phụ đề Video không hợp lệ")
        roi[key] = float(raw)
    if (roi["x_pct"] < 0 or roi["y_pct"] < 0 or roi["w_pct"] <= 0 or roi["h_pct"] <= 0
            or roi["x_pct"] + roi["w_pct"] > 1 + _EPSILON
            or roi["y_pct"] + roi["h_pct"] > 1 + _EPSILON):
        raise ValueError("Vùng phụ đề Video phải nằm trong khung hình")
    return roi
