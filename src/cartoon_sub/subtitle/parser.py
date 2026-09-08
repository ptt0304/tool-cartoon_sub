from pathlib import Path
import pysubs2
from cartoon_sub.subtitle.models import Segment

def import_srt(path):
    subtitles = pysubs2.load(str(path), encoding="utf-8-sig")
    return [Segment(i, event.start / 1000, event.end / 1000, event.plaintext) for i, event in enumerate(subtitles, 1)]

def export_srt(segments, path, language="zh"):
    if language not in ("zh", "vi", "vi_subtitle", "vi_dubbing"):
        raise ValueError("Expected zh or vi")
    subtitles = pysubs2.SSAFile()
    for segment in segments:
        subtitles.append(pysubs2.SSAEvent(start=round(segment.start * 1000), end=round(segment.end * 1000), text=getattr(segment, language).replace("\n", r"\N")))
    subtitles.save(str(Path(path)), encoding="utf-8", format_="srt")
