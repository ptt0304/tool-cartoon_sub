import os
import tempfile
from pathlib import Path
from dataclasses import asdict
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.subtitle.parser import export_srt
from .qc import review_translation


def save_translation_artifacts(project, directory):
    folder = Path(directory) / "subtitle"
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix=".srt")
    os.close(fd)
    try:
        export_srt(project.segments, temporary, "vi")
        os.replace(temporary, folder / "vi.srt")
    finally:
        Path(temporary).unlink(missing_ok=True)
    atomic_json(folder / "segments.json", [asdict(s) for s in project.segments])
    atomic_json(folder / "translation_review.json", {"status": project.translation_status,
                "warnings": review_translation(project), "context_uncertainties": project.story_context.get("uncertainties", [])})
