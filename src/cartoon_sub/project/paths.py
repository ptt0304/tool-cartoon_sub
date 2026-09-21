"""Canonical project-owned resource locations.

External inputs may remain external references.  Every path returned here is
inside the loaded project root and is safe to persist relative to it.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    def __init__(self, root):
        object.__setattr__(self, "root", Path(root).resolve())

    @property
    def project_file(self): return self.root / "project.json"
    @property
    def source_dir(self): return self.root / "source"
    @property
    def audio_dir(self): return self.root / "audio"
    @property
    def transcript_dir(self): return self.root / "transcription"
    @property
    def transcript_cache_dir(self): return self.root / "cache" / "transcription"
    @property
    def translate_dir(self): return self.root / "translation"
    @property
    def translate_cache_dir(self): return self.root / "cache" / "translation"
    @property
    def subtitle_dir(self): return self.root / "subtitle"
    @property
    def tts_dir(self): return self.audio_dir / "tts"
    @property
    def tts_segments_dir(self): return self.tts_dir / "segments"
    @property
    def tts_previews_dir(self): return self.root / "cache" / "tts" / "previews"
    @property
    def export_dir(self): return self.root / "exports"
    @property
    def logs_dir(self): return self.root / "logs"

    def ensure(self):
        for directory in (self.source_dir, self.audio_dir, self.transcript_dir,
                          self.transcript_cache_dir, self.translate_dir,
                          self.translate_cache_dir, self.subtitle_dir, self.tts_dir,
                          self.tts_segments_dir, self.tts_previews_dir,
                          self.export_dir, self.logs_dir, self.root / "preview",
                          self.root / "output"):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def relative(self, path):
        return Path(path).resolve().relative_to(self.root).as_posix()

