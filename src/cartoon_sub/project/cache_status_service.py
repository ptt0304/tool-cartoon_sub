"""Read-only, persisted processing/cache status derived from canonical project data."""
from __future__ import annotations

import json
import wave
from pathlib import Path

from cartoon_sub.project.cache import content_hash
from cartoon_sub.translation.qc import qa_entry_is_current
from cartoon_sub.tts.cache_identity import resolve_tts_text
from cartoon_sub.tts.cache_manifest import find_segment_entry, load_manifest, project_audio_path


def _valid_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as reader:
            return reader.getnframes() > 0 and reader.getframerate() > 0
    except (OSError, EOFError, wave.Error):
        return False


def _count_status(valid: int, total: int, stale: int = 0, failed: bool = False) -> str:
    if total <= 0:
        return "NONE"
    if valid == total:
        return f"CACHED {valid}/{total}"
    if valid:
        suffix = f" • STALE {stale}" if stale else ""
        return f"PARTIAL {valid}/{total}{suffix}"
    if stale:
        return "STALE"
    return "FAILED/RESUMABLE" if failed else "NONE"


def tts_source_counts(project, directory, source: str) -> tuple[int, int, int]:
    manifest, state = load_manifest(directory)
    segments = manifest.get("segments", {}) if state == "valid" else {}
    valid = stale = 0
    for row in project.utterances:
        _, entry = find_segment_entry(segments, row.tts_cache_key, source)
        if not entry:
            continue
        speaker = project.speakers.get(row.speaker_id, {})
        current = (
            bool(entry.get("signature"))
            and entry.get("source", "vi_dubbing") == source
            and entry.get("text") == resolve_tts_text(row, source)
            and entry.get("speaker_id") == row.speaker_id
            and entry.get("voice_id") == speaker.get("tts_voice_id")
            and abs(float(entry.get("speed", -1)) - float(speaker.get("tts_speed", 1.0))) < 1e-9
        )
        try:
            current = current and _valid_wav(project_audio_path(directory, entry.get("file", "")))
        except (ValueError, OSError):
            current = False
        if current:
            valid += 1
        else:
            stale += 1
    return valid, len(project.utterances), stale


def dubbed_mix_fingerprint(project, directory, source: str) -> str:
    manifest, state = load_manifest(directory)
    segments = manifest.get("segments", {}) if state == "valid" else {}
    rows = []
    for row in project.utterances:
        key, entry = find_segment_entry(segments, row.tts_cache_key, source)
        rows.append({
            "id": row.id, "start": row.start, "end": row.end,
            "overlap_group": row.overlap_group, "cache_key": key,
            "signature": entry.get("signature") if entry else None,
        })
    return content_hash({"version": 1, "source": source, "rows": rows})


def write_dubbed_mix_state(project, directory, source: str) -> None:
    from cartoon_sub.project.cache import atomic_json
    atomic_json(Path(directory) / "audio" / "tts" / "dubbed_mix.json", {
        "version": 1, "source": source,
        "fingerprint": dubbed_mix_fingerprint(project, directory, source),
    })


class CacheStatusService:
    def __init__(self, project, directory):
        self.project = project
        self.root = Path(directory)

    def transcript(self):
        rows = self.project.utterances
        state = self.project.transcription_status
        if state in {"failed", "cancelled"}:
            stt = "FAILED/RESUMABLE"
        elif state in {"completed", "imported", "no_speech"}:
            stt = "CACHED" if rows or state == "no_speech" else "NONE"
        else:
            stt = "NONE"
        return {"STT / nguồn transcript": stt, "Master Timeline": "CACHED" if rows else "NONE"}

    def translation(self):
        rows = self.project.utterances
        total = len(rows)
        translated = sum(bool(row.vi_subtitle.strip()) for row in rows)
        status = self.project.translation_status
        translation = _count_status(translated, total, failed=status in {"failed", "failed_resumable", "cancelled"})
        if status == "stale" and translated:
            translation = "STALE"
        qa = sum(qa_entry_is_current(row, self.project.translation_qa.get(str(row.id), {}), self.project) for row in rows)
        return {
            "Translation": translation,
            "Translation QA/QC": _count_status(qa, total),
            "Continuity memory": "CACHED" if self.project.translation_continuity_memory else "NONE",
        }

    def subtitle(self):
        rows = self.project.utterances
        total = len(rows)
        displays = sum(bool(row.display_segments) for row in rows)
        vi = sum(bool(row.vi_subtitle.strip()) for row in rows)
        return {"DisplaySegment": _count_status(displays, total), "VI Subtitle": _count_status(vi, total)}

    def audio(self):
        sub = tts_source_counts(self.project, self.root, "vi_subtitle")
        dub = tts_source_counts(self.project, self.root, "vi_dubbing")
        source = self.project.audio_settings.tts_text_source
        mix = self.root / "audio" / "tts" / "dubbed_mix.wav"
        sidecar = self.root / "audio" / "tts" / "dubbed_mix.json"
        mix_status = "NONE"
        if mix.exists() or sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                valid = (data.get("version") == 1 and data.get("source") == source
                         and data.get("fingerprint") == dubbed_mix_fingerprint(self.project, self.root, source)
                         and _valid_wav(mix))
                mix_status = "CACHED" if valid else "STALE"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                mix_status = "STALE"
        final = self.root / "audio" / "final_audio.wav"
        final_status = "NONE"
        if final.exists():
            final_status = "CACHED" if self.project.final_audio_status == "ready" and self.project.final_audio_fingerprint else "STALE"
        return {
            "TTS — VI Subtitle": _count_status(*sub),
            "TTS — VI Dubbing": _count_status(*dub),
            "Dubbed Audio": mix_status,
            "Final Audio": final_status,
        }
