"""Project-local, versioned SRT snapshots built from current project state."""

from pathlib import Path
import re

from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.subtitle.parser import export_canonical_srt, export_srt


EXPORT_KINDS = {
    "transcript": ("zh", False),
    "translate": ("vi_subtitle", False),
    "subtitle": ("vi_subtitle", True),
}


def next_export_path(directory, prefix, suffix=".srt"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+){re.escape(suffix)}$")
    indexes = [int(match.group(1)) for path in directory.iterdir()
               if path.is_file() and (match := pattern.match(path.name))]
    return directory / f"{prefix}_{max(indexes, default=-1) + 1}{suffix}"


def export_current_srt(project, project_root, kind):
    if kind not in EXPORT_KINDS:
        raise ValueError(f"Unsupported SRT export kind: {kind}")
    rows = list(project.utterances)
    if not rows:
        raise ValueError("Project chưa có dữ liệu để export SRT")
    language, use_presentation = EXPORT_KINDS[kind]
    output_dir = ProjectPaths(project_root).export_dir / kind
    path = next_export_path(output_dir, kind)
    if kind == "subtitle":
        from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
        service = SubtitleSegmentationService();service.sync_stale(project)
        for utterance in project.utterances:
            if not utterance.display_segments:
                service.sync_utterance(project, utterance)
        export_srt(rows, path, getattr(project, "subtitle_text_source", "vi_subtitle"), use_presentation)
    else:
        export_canonical_srt(rows, path, language)
    return path
