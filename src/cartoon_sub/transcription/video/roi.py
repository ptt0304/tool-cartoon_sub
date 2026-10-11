"""Pure normalized ROI geometry for Video Subtitle acquisition."""
from cartoon_sub.transcription.mode import normalize_roi

DEFAULT_VIDEO_SUBTITLE_ROI = {"x_pct": .10, "y_pct": .70, "w_pct": .80, "h_pct": .22}
MIN_ROI_PCT = .03


def clamp_roi(roi, minimum=MIN_ROI_PCT):
    value = dict(DEFAULT_VIDEO_SUBTITLE_ROI if not roi else roi)
    minimum = max(.001, min(float(minimum), .5))
    x = max(0., min(1. - minimum, float(value.get("x_pct", 0))))
    y = max(0., min(1. - minimum, float(value.get("y_pct", 0))))
    w = max(minimum, min(1. - x, float(value.get("w_pct", minimum))))
    h = max(minimum, min(1. - y, float(value.get("h_pct", minimum))))
    return normalize_roi({"x_pct": x, "y_pct": y, "w_pct": w, "h_pct": h})


def image_rect(widget_width, widget_height, image_width, image_height):
    """Letterbox-aware image rectangle inside a preview widget."""
    scale = min(widget_width / image_width, widget_height / image_height)
    width, height = image_width * scale, image_height * scale
    return ((widget_width - width) / 2, (widget_height - height) / 2, width, height)


def normalized_to_display(roi, rectangle):
    x, y, width, height = rectangle
    roi = clamp_roi(roi)
    return (x + roi["x_pct"] * width, y + roi["y_pct"] * height,
            roi["w_pct"] * width, roi["h_pct"] * height)


def display_to_normalized(rectangle, image_rectangle):
    x, y, width, height = rectangle
    ix, iy, iw, ih = image_rectangle
    left = max(ix, min(ix + iw, x)); top = max(iy, min(iy + ih, y))
    right = max(left, min(ix + iw, x + width)); bottom = max(top, min(iy + ih, y + height))
    return clamp_roi({"x_pct": (left - ix) / iw, "y_pct": (top - iy) / ih,
                      "w_pct": (right - left) / iw, "h_pct": (bottom - top) / ih})


def roi_to_pixels(roi, video_width, video_height):
    """Convert a normalized ROI to a bounded FFmpeg crop rectangle."""
    import math
    roi = clamp_roi(roi)
    width, height = int(video_width), int(video_height)
    if width <= 0 or height <= 0:
        raise ValueError("Video phải có kích thước hợp lệ")
    left = max(0, min(width - 1, math.floor(roi["x_pct"] * width)))
    top = max(0, min(height - 1, math.floor(roi["y_pct"] * height)))
    right = max(left + 1, min(width, math.ceil((roi["x_pct"] + roi["w_pct"]) * width)))
    bottom = max(top + 1, min(height, math.ceil((roi["y_pct"] + roi["h_pct"]) * height)))
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}
