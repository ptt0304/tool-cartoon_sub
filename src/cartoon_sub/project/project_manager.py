import json
import os
import tempfile
import shutil
from pathlib import Path
from cartoon_sub.subtitle.models import Project

class ProjectManager:
    folders = ("source", "audio", "subtitle", "preview", "exports", "output")

    def create(self, directory, video, metadata):
        directory = Path(directory).resolve()
        if (directory / "project.json").exists():
            raise ValueError("Project already exists; open it instead")
        video = Path(video).resolve(strict=True)
        project = Project(directory.name, str(video), metadata=metadata)
        self.save(project, directory)
        return project

    def save(self, project, directory):
        # Validate before changing the persisted project; atomic replacement avoids partial JSON.
        Project.from_dict(project.to_dict())
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for folder in self.folders:
            (directory / folder).mkdir(exist_ok=True)
        existing=directory / "project.json"
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
            os.replace(temporary, directory / "project.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, path):
        path = Path(path)
        if path.is_dir():
            path = path / "project.json"
        project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
        source = Path(project.source_video_path)
        if not source.is_absolute():
            project.source_video_path = str((path.parent / source).resolve())
        return project
