"""Timeline-aware Mask geometry derived from the rendered Vietnamese ASS events."""
import re
from dataclasses import dataclass

import pysubs2


@dataclass(frozen=True)
class MaskEvent:
    start: float
    end: float
    text: str
    x: int
    y: int
    width: int
    height: int


def _plain_ass_text(value):
    value = re.sub(r"\{[^}]*\}", "", value or "")
    return value.replace(r"\N", "\n").replace(r"\h", " ").strip()


def _text_extent(text, style):
    """Measure the same font/lines used by the ASS output, with safe fallback."""
    lines = text.splitlines() or [""]
    try:
        from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication
        if QGuiApplication.instance() is None:
            raise RuntimeError("No Qt application for font metrics")
        font = QFont(style.font)
        font.setBold(style.bold)
        font.setPixelSize(style.font_size)
        metrics = QFontMetrics(font)
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        height = max(1, metrics.height()) * len(lines)
    except Exception:
        width = max((round(len(line) * style.font_size * .56) for line in lines), default=0)
        height = max(1, round(style.font_size * 1.2)) * len(lines)
    # ASS outline/shadow expand the visible glyph block.
    extra = int(round(max(0, style.outline) * 2 + max(0, style.shadow)))
    return width + extra, height + extra


def text_mask_geometry(text, mask, style, video_width, video_height):
    """Fit the mask to rendered text and center the complete block on the ROI."""
    text_width, text_height = _text_extent(text, style)
    frame_width, frame_height = max(1, int(video_width)), max(1, int(video_height))
    width = min(frame_width, max(1, text_width + 2 * mask.padding_x))
    height = min(frame_height, max(1, text_height + 2 * mask.padding_y))
    center_x = mask.x + mask.width / 2
    center_y = mask.y + mask.height / 2
    x = max(0, min(frame_width - width, round(center_x - width / 2)))
    y = max(0, min(frame_height - height, round(center_y - height / 2)))
    return x, y, width, height


def event_geometry(text, project):
    """Return the text-sized Mask rectangle for a rendered subtitle event."""
    return text_mask_geometry(text, project.mask, project.subtitle_style,
                              project.metadata.get("width", 0), project.metadata.get("height", 0))


def mask_events_from_ass(path, project):
    """Read the final render timeline, so mask lifespan equals subtitle lifespan."""
    subtitle = pysubs2.load(str(path))
    result = []
    for row in subtitle.events:
        text = _plain_ass_text(row.text)
        if not text or row.end <= row.start:
            continue
        x, y, width, height = event_geometry(text, project)
        result.append(MaskEvent(row.start / 1000, row.end / 1000, text, x, y, width, height))
    return result


def _enable(event):
    # End is deliberately just below the exclusive subtitle end.
    return f"between(t,{event.start:.3f},{max(event.start, event.end - .001):.3f})"


def _has_overlaps(events):
    """Keep the original ordered composition for timelines with overlap."""
    latest_end = float("-inf")
    for event in sorted(events, key=lambda row: (row.start, row.end)):
        if event.start < latest_end:
            return True
        latest_end = max(latest_end, event.end)
    return False


def _mask_groups(events):
    """Reuse a blur branch for equal geometry when events never overlap.

    A Gaussian event samples the original input and then overlays that ROI.  If
    events do not overlap, the order of those inactive overlays is immaterial,
    so equal rectangles can share one crop/blur/overlay branch.  Overlapping
    timelines retain one branch per event to preserve their legacy ordering.
    """
    if _has_overlaps(events):
        return [[event] for event in events]
    groups = {}
    for event in events:
        geometry = (event.x, event.y, event.width, event.height)
        groups.setdefault(geometry, []).append(event)
    return list(groups.values())


def _enable_group(events):
    return "+".join(_enable(event) for event in events)


def mask_filter_chain(project, events):
    """FFmpeg chain that has no active Mask during subtitle gaps."""
    mask = project.mask
    if not mask.enabled or mask.kind == "none" or not events:
        return "[0:v]null[masked];"
    if mask.kind == "solid":
        filters = "[0:v]" + ",".join(
            f"drawbox=x={row.x}:y={row.y}:w={row.width}:h={row.height}:color=0x{mask.mask_color[1:]}:t=fill:enable='{_enable(row)}'"
            for row in events)
        return filters + "[masked];"
    if mask.kind != "gaussian":
        raise ValueError("Kiểu mask không hợp lệ")
    groups = _mask_groups(events)
    inputs = "[0:v]split=" + str(len(groups) + 1) + "[base]" + "".join(f"[source{i}]" for i in range(len(groups))) + ";"
    parts = [inputs]
    for index, group in enumerate(groups):
        row = group[0]
        # Overlay already has this time gate.  Gate the expensive ROI blur as
        # well: inactive branches are never composited, so bypassing their
        # blur preserves the rendered frame while avoiding per-event work.
        enable = _enable_group(group)
        parts.append(f"[source{index}]crop={row.width}:{row.height}:{row.x}:{row.y}:exact=1,format=yuv444p,gblur=sigma={mask.strength * 2.4:.1f}:steps=2:enable='{enable}'[blur{index}];")
    current = "base"
    for index, group in enumerate(groups):
        row = group[0]
        target = "masked" if index == len(groups) - 1 else f"mask{index}"
        parts.append(f"[{current}][blur{index}]overlay={row.x}:{row.y}:format=auto:enable='{_enable_group(group)}'[{target}];")
        current = target
    return "".join(parts)
