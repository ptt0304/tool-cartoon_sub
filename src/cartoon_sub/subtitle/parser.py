from pathlib import Path
import pysubs2
from cartoon_sub.subtitle.models import Segment, DisplaySegment
from cartoon_sub.subtitle.canonical_timeline import canonical_timeline

def import_srt(path):
    subtitles = pysubs2.load(str(path), encoding="utf-8-sig")
    return [Segment(i, event.start / 1000, event.end / 1000, event.plaintext) for i, event in enumerate(subtitles, 1)]

def export_srt(segments, path, language="zh", use_presentation=True):
    if language not in ("zh", "vi", "vi_subtitle", "vi_dubbing"):
        raise ValueError("Expected zh or vi")
    rows = list(segments)
    if use_presentation and language in ("vi", "vi_subtitle") and any(hasattr(segment, "display_segments") for segment in rows):
        from cartoon_sub.subtitle.segmentation_service import presentation_segments
        rows = [display for utterance in rows for display in presentation_segments(utterance)]
    subtitles = pysubs2.SSAFile()
    for segment in rows:
        text = segment.vi_text if isinstance(segment, DisplaySegment) and language in ("vi", "vi_subtitle") else getattr(segment, language)
        subtitles.append(pysubs2.SSAEvent(start=round(segment.start * 1000), end=round(segment.end * 1000), text=text.replace("\n", r"\N")))
    subtitles.save(str(Path(path)), encoding="utf-8", format_="srt")


def export_canonical_srt(utterances, path, language="zh"):
    fields = {
        "zh": "chinese",
        "vi": "vi_translation",
        "vi_translation": "vi_translation",
        "vi_subtitle": "vi_subtitle",
        "vi_dubbing": "vi_dubbing",
    }
    if language not in fields:
        raise ValueError("Unsupported canonical SRT language")
    subtitles = pysubs2.SSAFile()
    for entry in canonical_timeline(utterances):
        text = getattr(entry, fields[language])
        subtitles.append(pysubs2.SSAEvent(
            start=round(entry.start * 1000),
            end=round(entry.end * 1000),
            text=text.replace("\n", r"\N"),
        ))
    subtitles.save(str(Path(path)), encoding="utf-8", format_="srt")
