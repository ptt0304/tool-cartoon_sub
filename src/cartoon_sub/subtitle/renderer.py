"""ASS is derived from subtitle text; never modify the master timeline."""
import math
import re
import textwrap
from pathlib import Path
import pysubs2
from cartoon_sub.subtitle.canonical_timeline import canonical_timeline
from cartoon_sub.subtitle.segmentation_service import presentation_segments


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
        raise ValueError('Màu phải có định dạng #RRGGBB')
    return pysubs2.Color(*(int(value[index:index + 2], 16) for index in (1, 3, 5)))


def validate_visuals(project):
    width, height = (int(project.metadata[k]) for k in ('width', 'height'))
    if width < 2 or height < 2:
        raise ValueError('Kích thước video không hợp lệ')
    m, s = project.mask, project.subtitle_style
    if m.kind not in ('solid', 'gaussian', 'none'):
        raise ValueError('Kiểu mask không hợp lệ')
    _color(m.mask_color); _color(s.text_color); _color(s.outline_color)
    needs_region = m.enabled and (m.kind != 'none' or s.center_in_mask)
    if needs_region and (any(type(v) is not int for v in (m.x,m.y,m.width,m.height)) or
                         m.x < 0 or m.y < 0 or m.width < 2 or m.height < 2 or
                         m.x+m.width > width or m.y+m.height > height):
        raise ValueError('Vùng mask phải nằm trong video và rộng/cao ít nhất 2 pixel')
    if m.enabled and m.kind != 'none' and (type(m.strength) is not int or not 1 <= m.strength <= 20):
        raise ValueError('Độ nhòe mask phải từ 1 đến 20')
    if not s.font.strip() or any(c in s.font for c in '\r\n,'):
        raise ValueError('Tên font không hợp lệ')
    if not 8 <= s.font_size <= 300 or s.alignment not in range(1,10) or not 1 <= s.max_lines <= 4:
        raise ValueError('Font 8–300; alignment 1–9; số dòng 1–4')
    if any(not math.isfinite(v) or not 0 <= v <= 20 for v in (s.outline,s.shadow)) or not 0 <= s.margin_bottom < height:
        raise ValueError('Outline/shadow 0–20; margin phải nhỏ hơn chiều cao video')
    if s.center_in_mask and not m.enabled:
        raise ValueError('Bật vùng mask trước khi căn phụ đề vào giữa vùng đó')
    if getattr(s, 'speaker_label_mode', 'overlap_only') not in ('off', 'overlap_only', 'always', 'debug'):
        raise ValueError('Chế độ speaker label không hợp lệ')
    for logo in project.logos:
        if not logo.id or not logo.path or not Path(logo.path).is_file():
            raise ValueError('Không tìm thấy file logo')
        if min(logo.x, logo.y) < 0 or logo.width < 2 or logo.height < 2 or logo.x + logo.width > width or logo.y + logo.height > height:
            raise ValueError('Logo phải nằm trong khung hình')
        if not 0 <= logo.transparency <= 100 or not math.isfinite(logo.rotation):
            raise ValueError('Độ trong suốt hoặc góc xoay logo không hợp lệ')
    watermark = project.watermark
    if watermark.text and (not watermark.font.strip() or not 8 <= watermark.font_size <= 300 or
                           not 0 <= watermark.transparency <= 100 or not 10 <= watermark.speed <= 1000):
        raise ValueError('Cấu hình watermark không hợp lệ')


def save_ass(project, path, start=0, duration=None):
    validate_visuals(project)
    # Rendering must never consume an old persisted DisplaySegment text.
    from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
    SubtitleSegmentationService().sync_stale(project)
    width, height = (int(project.metadata[k]) for k in ('width','height'))
    style = project.subtitle_style
    subs = pysubs2.SSAFile()
    subs.info.update(PlayResX=str(width), PlayResY=str(height), WrapStyle='2', ScaledBorderAndShadow='yes')
    subs.styles['Default'] = pysubs2.SSAStyle(fontname=style.font, fontsize=style.font_size,
        bold=style.bold, outline=style.outline, shadow=style.shadow, alignment=pysubs2.Alignment(style.alignment),
        marginv=style.margin_bottom, marginl=20, marginr=20,
        primarycolor=_color(style.text_color), outlinecolor=_color(style.outline_color))
    watermark = project.watermark
    if watermark.text.strip():
        subs.styles['Watermark'] = pysubs2.SSAStyle(fontname=watermark.font, fontsize=watermark.font_size,
            bold=watermark.bold, outline=watermark.outline, shadow=watermark.shadow,
            alignment=pysubs2.Alignment.TOP_LEFT, marginv=0, marginl=0, marginr=0)
    end = start + duration if duration is not None else float(project.metadata.get('duration', 0))

    utterance_by_id = {row.id: row for row in project.utterances}
    for entry in canonical_timeline(project.utterances):
        if style.center_in_mask:
            center_x = project.mask.x + project.mask.width // 2
            mid_y = project.mask.y + project.mask.height // 2
            position_tag = r'{\an5\pos(%d,%d)}' % (round(center_x), round(mid_y))
        else:
            position_tag = ''
        if len(entry.source_utterance_ids) > 1:
            render_rows = [(entry.start, entry.end, entry.vi_subtitle)]
        else:
            utterance = utterance_by_id[entry.source_utterance_ids[0]]
            render_rows = [(row.start, row.end, row.vi_text) for row in presentation_segments(utterance)]
        for row_start, row_end, row_text in render_rows:
            if row_end <= start or row_start >= end:
                continue
            # Speaker metadata remains internal and never becomes user-visible text.
            text = re.sub(r'\s+', ' ', row_text).strip().replace('\\','／').replace('{','(').replace('}',')')
            if not text:
                continue
            columns = max(8, int((width-40)/(style.font_size*.55)))
            lines = textwrap.wrap(text, columns, break_long_words=False, break_on_hyphens=False)
            while len(lines) > style.max_lines:
                columns += 1
                lines = textwrap.wrap(text, columns, break_long_words=False, break_on_hyphens=False)
            subs.events.append(pysubs2.SSAEvent(start=round((max(row_start,start)-start)*1000),
                end=round((min(row_end,end)-start)*1000), text=position_tag + r'\N'.join(lines)))
    if watermark.text.strip():
        _add_moving_watermark(subs, watermark, width, height, start, end)
    subs.save(str(path), encoding='utf-8')
    return path


def _add_moving_watermark(subs, watermark, width, height, view_start, view_end):
    """Emit straight ASS moves whose velocity reflects at each video edge."""
    text = watermark.text.replace('\\', '／').replace('{', '(').replace('}', ')')
    text_width = min(width - 20, max(40, round(len(text) * watermark.font_size * .58)))
    text_height = max(24, round(watermark.font_size * 1.35))
    x, y, vx, vy, t = 20.0, 20.0, watermark.speed * .8, watermark.speed * .6, 0.0
    alpha = round(255 * watermark.transparency / 100)
    while t < view_end:
        tx = (width - text_width - x) / vx if vx > 0 else -x / vx
        ty = (height - text_height - y) / vy if vy > 0 else -y / vy
        raw_span = min(tx, ty)
        if raw_span <= .0001:
            if tx <= .0001: vx = -vx
            if ty <= .0001: vy = -vy
            continue
        span = raw_span
        until = t + span
        visible_start, visible_end = max(t, view_start), min(until, view_end)
        if visible_end > visible_start:
            ratio0, ratio1 = (visible_start - t) / span, (visible_end - t) / span
            ax, ay = x + vx * span * ratio0, y + vy * span * ratio0
            bx, by = x + vx * span * ratio1, y + vy * span * ratio1
            duration = round((visible_end - visible_start) * 1000)
            tag = r'{\an7\move(%d,%d,%d,%d,0,%d)\alpha&H%02X&}' % (round(ax), round(ay), round(bx), round(by), duration, alpha)
            subs.events.append(pysubs2.SSAEvent(start=round((visible_start-view_start)*1000),
                end=round((visible_end-view_start)*1000), text=tag + text, style='Watermark'))
        x += vx * span; y += vy * span; t = until
        if abs(tx - span) < .001: vx = -vx; x = max(0, min(width - text_width, x))
        if abs(ty - span) < .001: vy = -vy; y = max(0, min(height - text_height, y))
