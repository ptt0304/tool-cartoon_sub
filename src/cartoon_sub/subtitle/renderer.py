"""ASS is derived from subtitle text; never modify the master timeline."""
import math
import re
import textwrap
import pysubs2


def validate_visuals(project):
    width, height = (int(project.metadata[k]) for k in ('width', 'height'))
    if width < 2 or height < 2:
        raise ValueError('Kích thước video không hợp lệ')
    m, s = project.mask, project.subtitle_style
    if m.kind not in ('solid', 'blur'):
        raise ValueError('Mask phải là solid hoặc blur')
    if m.enabled and (any(type(v) is not int for v in (m.x,m.y,m.width,m.height)) or
                      m.x < 0 or m.y < 0 or m.width < 2 or m.height < 2 or
                      m.x+m.width > width or m.y+m.height > height):
        raise ValueError('Vùng mask phải nằm trong video và rộng/cao ít nhất 2 pixel')
    if not s.font.strip() or any(c in s.font for c in '\r\n,'):
        raise ValueError('Tên font không hợp lệ')
    if not 8 <= s.font_size <= 300 or s.alignment not in range(1,10) or not 1 <= s.max_lines <= 4:
        raise ValueError('Font 8–300; alignment 1–9; số dòng 1–4')
    if any(not math.isfinite(v) or not 0 <= v <= 20 for v in (s.outline,s.shadow)) or not 0 <= s.margin_bottom < height:
        raise ValueError('Outline/shadow 0–20; margin phải nhỏ hơn chiều cao video')


def save_ass(project, path, start=0, duration=None):
    validate_visuals(project)
    width, height = (int(project.metadata[k]) for k in ('width','height'))
    style = project.subtitle_style
    subs = pysubs2.SSAFile()
    subs.info.update(PlayResX=str(width), PlayResY=str(height), WrapStyle='2', ScaledBorderAndShadow='yes')
    subs.styles['Default'] = pysubs2.SSAStyle(fontname=style.font, fontsize=style.font_size,
        bold=style.bold, outline=style.outline, shadow=style.shadow, alignment=pysubs2.Alignment(style.alignment),
        marginv=style.margin_bottom, marginl=20, marginr=20)
    end = start + duration if duration is not None else float('inf')
    for row in project.segments:
        if row.end <= start or row.start >= end or not row.vi_subtitle.strip():
            continue
        # Neutralize ASS control syntax; wrapping changes display only, never drops words.
        text = re.sub(r'\s+', ' ', row.vi_subtitle).strip().replace('\\','／').replace('{','(').replace('}',')')
        columns = max(8, int((width-40)/(style.font_size*.55)))
        lines = textwrap.wrap(text, columns, break_long_words=False, break_on_hyphens=False)
        while len(lines) > style.max_lines:
            columns += 1
            lines = textwrap.wrap(text, columns, break_long_words=False, break_on_hyphens=False)
        subs.events.append(pysubs2.SSAEvent(start=round((max(row.start,start)-start)*1000),
            end=round((min(row.end,end)-start)*1000), text=r'\N'.join(lines)))
    subs.save(str(path), encoding='utf-8')
    return path
