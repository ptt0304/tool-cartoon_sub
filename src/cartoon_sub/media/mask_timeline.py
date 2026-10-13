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
    inputs = "[0:v]split=" + str(len(events) + 1) + "[base]" + "".join(f"[source{i}]" for i in range(len(events))) + ";"
    parts = [inputs]
    for index, row in enumerate(events):
        parts.append(f"[source{index}]crop={row.width}:{row.height}:{row.x}:{row.y}:exact=1,format=yuv444p,gblur=sigma={mask.strength * 2.4:.1f}:steps=2[blur{index}];")
    current = "base"
    for index, row in enumerate(events):
        target = "masked" if index == len(events) - 1 else f"mask{index}"
        parts.append(f"[{current}][blur{index}]overlay={row.x}:{row.y}:format=auto:enable='{_enable(row)}'[{target}];")
        current = target
    return "".join(parts)
