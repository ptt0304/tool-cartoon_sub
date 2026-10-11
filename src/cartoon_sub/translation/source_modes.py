"""Translate-owned source snapshots and source-scoped translation state."""
from __future__ import annotations

import shutil
from pathlib import Path

from cartoon_sub.project.cache import content_hash
from cartoon_sub.subtitle.models import Project, Utterance


SOURCES = ("audio", "video", "srt")
SOURCE_LABELS = {"audio": "Audio", "video": "Video", "srt": "SRT"}


def timeline_hash(rows) -> str:
    return content_hash([{"id": row.id, "start": row.start, "end": row.end,
                          "zh": row.zh, "provenance": row.transcript_timing_provenance}
                         for row in rows])


def current_timeline_source(project: Project) -> str | None:
    rows = project.segments
    if not rows:
        return None
    if all(row.transcript_timing_provenance == "VIDEO_SUBTITLE" and row.zh.strip() for row in rows):
        return "video"
    if project.transcription_status == "imported" and all(row.zh.strip() for row in rows):
        return "srt"
    if project.transcript_source_mode == "audio" and all(row.zh.strip() for row in rows):
        return "audio"
    return None


class TranslationSourceService:
    def snapshot(self, project: Project, source: str) -> dict | None:
        return (project.translation_source_snapshots or {}).get(source)

    def available(self, project: Project, source: str) -> bool:
        return source in SOURCES and (self.snapshot(project, source) is not None or current_timeline_source(project) == source)

    def ensure(self, project: Project, source: str) -> dict:
        if source not in SOURCES:
            raise ValueError("Nguồn translate không hợp lệ.")
        existing = self.snapshot(project, source)
        if existing:
            return existing
        if current_timeline_source(project) != source:
            raise ValueError(f"Chưa có Transcript từ {SOURCE_LABELS[source]}.")
        snapshot = {"source": source, "timeline_hash": timeline_hash(project.segments),
                    "rows": [row.to_dict() for row in project.segments]}
        project.translation_source_snapshots[source] = snapshot
        return snapshot

    def select(self, project: Project, source: str) -> dict:
        snapshot = self.ensure(project, source)
        project.translate_source = source
        self.restore_if_current(project, source)
        return snapshot

    def working_project(self, project: Project, source: str) -> Project:
        snapshot = self.ensure(project, source)
        working = Project.from_dict(project.to_dict())
        working.segments = [Utterance.from_dict(row) for row in snapshot["rows"]]
        working.translate_source = source
        result = (project.translation_source_results or {}).get(source, {})
        if result.get("timeline_hash") == snapshot["timeline_hash"]:
            working.segments = [Utterance.from_dict(row) for row in result.get("rows", snapshot["rows"])]
            working.translation_status = result.get("translation_status", "not_started")
            working.translation_qa = dict(result.get("translation_qa", {}))
            working.translation_notes = dict(result.get("translation_notes", {}))
            working.translation_continuity_memory = list(result.get("continuity", []))
        return working

    def commit(self, project: Project, source: str, translated: Project) -> None:
        snapshot = self.ensure(project, source)
        project.translation_source_results[source] = {
            "timeline_hash": snapshot["timeline_hash"], "rows": [row.to_dict() for row in translated.segments],
            "translation_status": translated.translation_status, "translation_qa": translated.translation_qa,
            "translation_notes": translated.translation_notes, "continuity": translated.translation_continuity_memory,
            "cache_hash": translated.cache_hashes.get("translation", ""),
        }
        self.restore_if_current(project, source)

    def restore_if_current(self, project: Project, source: str) -> bool:
        snapshot = self.snapshot(project, source)
        result = (project.translation_source_results or {}).get(source, {})
        if (not snapshot or result.get("timeline_hash") != snapshot.get("timeline_hash")
                or current_timeline_source(project) != source
                or timeline_hash(project.segments) != snapshot.get("timeline_hash")):
            return False
        project.segments = [Utterance.from_dict(row) for row in result.get("rows", snapshot["rows"])]
        project.translation_status = result.get("translation_status", "not_started")
        project.translation_qa = dict(result.get("translation_qa", {}))
        project.translation_notes = dict(result.get("translation_notes", {}))
        project.translation_continuity_memory = list(result.get("continuity", []))
        project.cache_hashes["translation"] = result.get("cache_hash", "")
        return True

    def cache_dir(self, directory, source: str) -> Path:
        return Path(directory) / "cache" / "translation" / source

    def clear_cache(self, project: Project, directory, source: str) -> None:
        if source not in SOURCES:
            raise ValueError("Nguồn translate không hợp lệ.")
        for relative in (Path("cache") / "translation" / source,
                         Path("cache") / "translation_qa" / source,
                         Path("cache") / "manual_translation_qa" / source):
            shutil.rmtree(Path(directory) / relative, ignore_errors=True)
        state = project.translation_source_results.get(source)
        if state:
            state["translation_status"] = "stale"
            state["translation_qa"] = {}
            state["continuity"] = []
        if project.translate_source == source and current_timeline_source(project) == source:
            project.translation_status = "stale" if project.segments else "not_started"
            project.translation_qa = {}
            project.translation_continuity_memory = []
