"""Normalized, independent geometry for the Mask subtitle-placement region."""

from cartoon_sub.transcription.video.roi import clamp_roi


DEFAULT_MASK_ROI = {"x_pct": .10, "y_pct": .70, "w_pct": .80, "h_pct": .22}


def normalize_mask_roi(value):
    """Return a bounded normalized Mask ROI, with a bottom-center default."""
    return clamp_roi(value or DEFAULT_MASK_ROI)


def sync_mask_geometry(mask, video_width, video_height):
    """Synchronize legacy render pixels from normalized Mask ROI when present.

    Old projects that only have pixel fields retain their original geometry; new
    projects use ``mask.roi`` as the resolution-independent source of truth.
    """
    width, height = max(2, int(video_width)), max(2, int(video_height))
    if getattr(mask, "roi", None):
        roi = normalize_mask_roi(mask.roi)
        mask.roi = dict(roi)
        mask.x = max(0, min(width - 1, round(roi["x_pct"] * width)))
        mask.y = max(0, min(height - 1, round(roi["y_pct"] * height)))
        right = max(mask.x + 2, min(width, round((roi["x_pct"] + roi["w_pct"]) * width)))
        bottom = max(mask.y + 2, min(height, round((roi["y_pct"] + roi["h_pct"]) * height)))
        mask.width, mask.height = right - mask.x, bottom - mask.y
    return mask
