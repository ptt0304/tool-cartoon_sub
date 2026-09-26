import os
import tempfile
from pathlib import Path
from cartoon_sub.speaker.service import refresh_timeline
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.subtitle.parser import export_canonical_srt
from .qc import review_translation


def save_translation_artifacts(project, directory):
    refresh_timeline(project)
    folder = Path(directory) / "subtitle"
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix=".srt")
    os.close(fd)
    try:
        export_canonical_srt(project.segments, temporary, "vi")
        os.replace(temporary, folder / "vi.srt")
    finally:
        Path(temporary).unlink(missing_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix=".srt")
    os.close(fd)
    try:
        export_canonical_srt(project.segments, temporary, "vi_dubbing")
        os.replace(temporary, folder / "vi_dubbing.srt")
    finally:
        Path(temporary).unlink(missing_ok=True)
    atomic_json(folder / "segments.json", [s.to_dict() for s in project.segments])
    atomic_json(folder / "translation_review.json", {"status": project.translation_status,
                "warnings": review_translation(project), "context_uncertainties": project.story_context.get("uncertainties", [])})
    live = Path(directory) / "exports" / "translate"
    live.mkdir(parents=True, exist_ok=True)
    for name, language in (("vi_subtitle.srt", "vi_subtitle"), ("vi_dubbing.srt", "vi_dubbing")):
        fd, temporary = tempfile.mkstemp(dir=live, suffix=".srt")
        os.close(fd)
        try:
            export_canonical_srt(project.segments, temporary, language)
            os.replace(temporary, live / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
