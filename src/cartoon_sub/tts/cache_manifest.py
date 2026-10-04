from __future__ import annotations

import json
import logging
from pathlib import Path

from cartoon_sub.project.cache import atomic_json


logger = logging.getLogger(__name__)
CACHE_MANIFEST_VERSION = 1
MANIFEST_KEY_SEPARATOR = "::"


def segment_manifest_key(cache_key: str, source: str) -> str:
    if source not in {"vi_subtitle", "vi_dubbing"}:
        raise ValueError("Nguồn TTS không hợp lệ")
    return f"{cache_key}{MANIFEST_KEY_SEPARATOR}{source}"


def find_segment_entry(segments: dict, cache_key: str, source: str):
    """Return a source-specific entry, accepting the old dubbing-only key once."""
    key = segment_manifest_key(cache_key, source)
    entry = segments.get(key)
    if entry is not None:
        return key, entry
    legacy = segments.get(cache_key)
    if legacy is not None and legacy.get("source", "vi_dubbing") == source:
        return cache_key, legacy
    return key, None


def entry_utterance_cache_key(key: str, entry: dict) -> str:
    stored = entry.get("utterance_cache_key")
    if isinstance(stored, str) and stored:
        return stored
    return key.split(MANIFEST_KEY_SEPARATOR, 1)[0]


def manifest_path(project_dir) -> Path:
    return Path(project_dir) / "audio" / "tts" / "tts_cache.json"


def empty_manifest() -> dict:
    return {"version": CACHE_MANIFEST_VERSION, "segments": {}}


def load_manifest(project_dir) -> tuple[dict, str]:
    path = manifest_path(project_dir)
    if not path.is_file():
        return empty_manifest(), "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != CACHE_MANIFEST_VERSION:
            raise ValueError(f"unsupported version {payload.get('version')!r}")
        segments = payload.get("segments")
        if not isinstance(segments, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in segments.items()
        ):
            raise ValueError("segments must be an object")
        return payload, "valid"
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("[TTS CACHE] Ignoring unsafe manifest %s: %s", path, exc)
        return empty_manifest(), "invalid"


def save_manifest(project_dir, payload: dict) -> Path:
    path = manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, payload)
    return path


def project_audio_path(project_dir, manifest_file: str) -> Path:
    tts_root = (Path(project_dir) / "audio" / "tts").resolve()
    candidate = (tts_root / manifest_file).resolve()
    candidate.relative_to(tts_root)
    return candidate


def manifest_file_for_project(project_dir, project_audio_file: str) -> str:
    root = Path(project_dir).resolve()
    path = (root / project_audio_file).resolve()
    tts_root = (root / "audio" / "tts").resolve()
    return path.relative_to(tts_root).as_posix()
