import json
import logging
import os
import tempfile
import shutil
from pathlib import Path
from cartoon_sub.subtitle.models import Project
from cartoon_sub.project.paths import ProjectPaths

class ProjectManager:
    folders = ("source", "audio", "subtitle", "preview", "exports", "output")

    def create(self, directory, video, metadata):
        paths = ProjectPaths(directory)
        directory = paths.root
        if paths.project_file.exists():
            raise ValueError("Project already exists; open it instead")
        video = Path(video).resolve(strict=True)
        project = Project(directory.name, str(video), metadata=metadata)
        self.save(project, directory)
        return project

    def save(self, project, directory):
        # Validate before changing the persisted project; atomic replacement avoids partial JSON.
        Project.from_dict(project.to_dict())
        paths = ProjectPaths(directory).ensure()
        directory = paths.root
        existing = paths.project_file
        if existing.exists():
            old=json.loads(existing.read_text(encoding="utf-8"))
            old_schema=old.get("schema_version")
            if old_schema in (1, 2):
                backup=directory / f"project.v{old_schema}.backup.json"
                if backup.exists():
                    from uuid import uuid4
                    backup=directory / f"project.v{old_schema}.{uuid4().hex}.backup.json"
                shutil.copy2(existing,backup)
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(project.to_dict(), handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, paths.project_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, path):
        path = Path(path)
        if path.is_dir():
            path = path / "project.json"
        paths = ProjectPaths(path.parent).ensure()
        project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        migrated_speaker_baseline = False
        if not project.speaker_review_initial_state:
            from cartoon_sub.speaker.service import capture_initial_speaker_state
            migrated_speaker_baseline = capture_initial_speaker_state(project)
            if migrated_speaker_baseline:
                logging.getLogger(__name__).info(
                    "Migrated missing speaker-review baseline from current persisted state: %s", path,
                )
        source = Path(project.source_video_path)
        if not source.is_absolute():
            project.source_video_path = str((paths.root / source).resolve())
        # Schema v3 allowed absolute generated WAV paths. Normalize only paths that
        # already belong to this project; external/server paths remain stale rather
        # than making the moved project depend on Local_TTS storage.
        for utterance in project.utterances:
            raw = utterance.tts_audio_path
            if not raw:
                continue
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    utterance.tts_audio_path = paths.relative(candidate)
                except ValueError:
                    utterance.tts_audio_path = None
                    utterance.tts_generation_status = "stale"
        if migrated_speaker_baseline:
            self.save(project, paths.root)
        return project
