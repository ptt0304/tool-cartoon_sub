from __future__ import annotations

import logging
import math
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path

from cartoon_sub.project.cache import check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.media.process import CancelledError
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.tts.cache_identity import (
    build_cached_segment_id,
    compute_tts_signature,
    normalize_tts_text,
    resolve_tts_text,
    TTS_SOURCE_DUBBING,
)
from cartoon_sub.tts.cache_manifest import (
    entry_utterance_cache_key,
    find_segment_entry,
    load_manifest,
    manifest_file_for_project,
    project_audio_path,
    save_manifest,
    segment_manifest_key,
)
from cartoon_sub.tts.local_tts_client import LocalTTSClient, LocalTTSError
from cartoon_sub.tts.duration_fit import VoiceCalibrationCache


logger = logging.getLogger(__name__)


@dataclass
class TTSGenerationResult:
    total: int
    generated: int = 0
    cached: int = 0
    needed: int = 0
    new: int = 0
    changed: int = 0
    missing_file: int = 0
    deleted: int = 0
    failed_ids: list[int] = field(default_factory=list)
    warnings: int = 0


@dataclass
class PlannedSegment:
    utterance: Utterance
    voice_id: str
    speed: float
    voice: dict
    signature: str
    reason: str
    entry: dict | None = None


@dataclass
class TTSCachePlan:
    manifest: dict
    manifest_state: str
    cached: list[PlannedSegment] = field(default_factory=list)
    actions: list[PlannedSegment] = field(default_factory=list)
    deleted: list[tuple[str, dict]] = field(default_factory=list)
    migrated: bool = False


class LocalTTSGenerationService:
    def __init__(
        self,
        client: LocalTTSClient,
        manager: ProjectManager | None = None,
        server_base_url: str | None = None,
        calibration_cache: VoiceCalibrationCache | None = None,
        text_source: str = TTS_SOURCE_DUBBING,
    ):
        self.client = client
        self.manager = manager or ProjectManager()
        configured_url = server_base_url or getattr(getattr(client, "settings", None), "base_url", None)
        if not configured_url:
            raise ValueError("Local_TTS server base URL is required for cache identity")
        self.server_base_url = configured_url
        self.calibration_cache = calibration_cache
        self.text_source = text_source

        self._voice_registry: dict[str, dict] = {}

    def fingerprint(self, utterance: Utterance, voice_id: str, speed: float, voice=None) -> str:
        return compute_tts_signature(
            utterance, voice_id, speed, self.server_base_url,
            voice if voice is not None else self._voice_registry.get(voice_id), self.text_source,
        )

    def segment_id(self, utterance: Utterance, fingerprint: str = "") -> str:
        return build_cached_segment_id(utterance, self.text_source)

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 44:
                return False
            with wave.open(str(path), "rb") as reader:
                return reader.getnchannels() > 0 and reader.getframerate() > 0 and reader.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    def _manifest_entry(self, utterance, voice_id, speed, voice, signature, file_name, duration, segment_id):
        return {
            "signature": signature,
            "file": file_name,
            "segment_id": segment_id,
            "utterance_cache_key": utterance.tts_cache_key,
            "source": self.text_source,
            "text": resolve_tts_text(utterance, self.text_source),
            "voice_id": voice_id,
            "engine": voice.get("engine") or voice.get("backend") or voice.get("source"),
            "speed": float(speed),
            "duration": duration,
        }

    def _bootstrap_entry(self, root, utterance, voice_id, speed, voice, signature):
        if not utterance.tts_audio_path:
            return None
        path = (root / utterance.tts_audio_path).resolve()
        try:
            file_name = manifest_file_for_project(root, utterance.tts_audio_path)
        except (ValueError, OSError):
            return None
        current_signature = utterance.tts_fingerprint == signature
        if not current_signature or not self._valid_wav(path):
            return None
        duration = utterance.tts_duration or self._wav_duration(path)
        segment_id = utterance.tts_segment_id or path.stem
        utterance.tts_fingerprint = signature
        utterance.tts_generation_status = "cached"
        utterance.tts_error = ""
        return self._manifest_entry(
            utterance, voice_id, speed, voice, signature, file_name, duration, segment_id,
        )

    def _plan(self, project: Project, root: Path, voice_id: str, speed: float) -> TTSCachePlan:
        manifest, state = load_manifest(root)
        plan = TTSCachePlan(manifest=manifest, manifest_state=state)
        entries = manifest["segments"]
        current_keys = {row.tts_cache_key for row in project.utterances}
        if state == "valid":
            plan.deleted = [
                (key, entry) for key, entry in entries.items()
                if entry_utterance_cache_key(key, entry) not in current_keys
            ]

        for utterance in project.utterances:
            voice = self._voice_registry[voice_id]
            signature = self.fingerprint(utterance, voice_id, speed, voice)
            manifest_key, entry = find_segment_entry(
                entries, utterance.tts_cache_key, self.text_source,
            )
            current_manifest_key = segment_manifest_key(utterance.tts_cache_key, self.text_source)
            if entry is not None and manifest_key != current_manifest_key:
                entries[current_manifest_key] = entries.pop(manifest_key)
                manifest_key = current_manifest_key
                plan.migrated = True
            if entry is None and state != "invalid":
                entry = self._bootstrap_entry(root, utterance, voice_id, speed, voice, signature)
                if entry is not None:
                    entries[current_manifest_key] = entry
                    plan.migrated = True

            reason = "CACHED"
            if entry is None:
                reason = "CHANGED" if utterance.tts_audio_path else "NEW"
            elif entry.get("signature") != signature:
                reason = "CHANGED"
            else:
                try:
                    cached_file = project_audio_path(root, entry.get("file", ""))
                except (ValueError, OSError, TypeError):
                    cached_file = None
                if cached_file is None or not self._valid_wav(cached_file):
                    reason = "MISSING_FILE"

            item = PlannedSegment(utterance, voice_id, speed, voice, signature, reason, entry)
            if reason == "CACHED":
                plan.cached.append(item)
            else:
                plan.actions.append(item)
        return plan

    @staticmethod
    def _remove_deleted(root: Path, plan: TTSCachePlan) -> int:
        if plan.manifest_state != "valid" or not plan.deleted:
            return 0
        entries = plan.manifest["segments"]
        current_files = {
            entry.get("file") for key, entry in entries.items()
            if key not in {deleted_key for deleted_key, _ in plan.deleted}
        }
        removed = 0
        for cache_key, entry in plan.deleted:
            file_name = entry.get("file")
            delete_entry = True
            if isinstance(file_name, str) and file_name not in current_files:
                try:
                    relative = Path(file_name)
                    if relative.parts and relative.parts[0] == "segments":
                        path = project_audio_path(root, file_name)
                        path.unlink(missing_ok=True)
                        logger.info("[TTS CACHE DELETE] uuid=%s file=%s", cache_key, file_name)
                except (OSError, ValueError):
                    logger.warning("[TTS CACHE] Could not remove orphan %s", file_name)
                    delete_entry = False
            if delete_entry:
                entries.pop(cache_key, None)
                removed += 1
        return removed

    def _preflight(self, project: Project, project_dir: Path) -> tuple[str, float]:
        if not isinstance(project, Project) or not (project_dir / "project.json").is_file():
            raise ValueError("Project chưa được lưu")
        if not project.utterances:
            raise ValueError("Project không có Utterance")
        empty = [row.id for row in project.utterances if not resolve_tts_text(row, self.text_source)]
        if empty:
            raise ValueError(
                f"Utterance chưa có text từ nguồn TTS {self.text_source}: "
                f"{', '.join(map(str, empty))}"
            )

        voice_id = getattr(project.audio_settings, "tts_voice_id", None)
        speed = float(getattr(project.audio_settings, "tts_speed", 1.0))
        if not voice_id:
            raise ValueError("Chưa chọn Giọng đọc cho project")

        health = self.client.health()
        if health.get("status") != "READY":
            raise ValueError(f"Local_TTS chưa READY: {health.get('status', 'UNKNOWN')}")
        voices = {
            voice.get("voice_id"): voice
            for voice in self.client.list_voices()
            if isinstance(voice.get("voice_id"), str)
        }
        self._voice_registry = voices
        voice = voices.get(voice_id)
        if voice is None:
            raise ValueError(f"Local_TTS không có voice {voice_id}")
        if voice.get("status") != "READY":
            raise ValueError(f"Local_TTS voice chưa READY: {voice_id}")
        return voice_id, speed

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as reader:
                frame_rate = reader.getframerate()
                frame_count = reader.getnframes()
                if frame_rate <= 0 or frame_count <= 0:
                    raise ValueError("Local_TTS trả về WAV không hợp lệ")
                return frame_count / frame_rate
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError("Local_TTS trả về WAV không hợp lệ") from exc

    @classmethod
    def _write_wav_atomic(cls, destination: Path, wav_bytes: bytes) -> float:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp.wav")
        try:
            with temporary.open("wb") as handle:
                handle.write(wav_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            actual_duration = cls._wav_duration(temporary)
            os.replace(temporary, destination)
            return actual_duration
        finally:
            temporary.unlink(missing_ok=True)

    def generate(self, project: Project, project_dir, cancel=None, progress=None) -> TTSGenerationResult:
        root = Path(project_dir).resolve()
        voice_id, speed = self._preflight(project, root)
        plan = self._plan(project, root, voice_id, speed)
        result = TTSGenerationResult(
            total=len(project.utterances),
            cached=len(plan.cached),
            needed=len(plan.actions),
            new=sum(item.reason == "NEW" for item in plan.actions),
            changed=sum(item.reason == "CHANGED" for item in plan.actions),
            missing_file=sum(item.reason == "MISSING_FILE" for item in plan.actions),
        )
        segments_dir = root / "audio" / "tts" / "segments"
        result.deleted = self._remove_deleted(root, plan)
        logger.info(
            "[TTS CACHE] Total current: %d Cached/reuse: %d Changed: %d New: %d "
            "Missing file: %d Deleted/orphan: %d Need generate: %d",
            result.total, result.cached, result.changed, result.new,
            result.missing_file, result.deleted, result.needed,
        )
        if progress:
            source_label = "VI Subtitle" if self.text_source == "vi_subtitle" else "VI Dubbing"
            progress(
                f"Nguồn TTS: {source_label} | TTS cache: {result.cached} reused • {result.needed} to generate "
                f"({result.changed} changed, {result.new} new, "
                f"{result.missing_file} missing, {result.deleted} deleted)"
            )

        for item in plan.cached:
            utterance = item.utterance
            entry = item.entry
            cached_file = project_audio_path(root, entry["file"])
            utterance.tts_audio_path = cached_file.relative_to(root).as_posix()
            utterance.tts_duration = entry.get("duration") or self._wav_duration(cached_file)
            utterance.tts_segment_id = entry.get("segment_id") or cached_file.stem
            utterance.tts_fingerprint = item.signature
            utterance.tts_generation_status = "cached"
            utterance.tts_error = ""
            utterance.recalculate()
            result.warnings += utterance.tts_alignment_status == "warning"
            logger.debug("[TTS CACHE HIT] %s", utterance.tts_cache_key)

        if plan.migrated or result.deleted or plan.manifest_state != "valid":
            save_manifest(root, plan.manifest)
        self.manager.save(project, root)

        for index, item in enumerate(plan.actions, 1):
            check_cancel(cancel)
            utterance = item.utterance
            signature = item.signature
            existing_file = item.entry.get("file") if item.entry else None
            if existing_file:
                try:
                    destination = project_audio_path(root, existing_file)
                except (ValueError, OSError, TypeError):
                    destination = segments_dir / f"{self.segment_id(utterance)}.wav"
            else:
                destination = segments_dir / f"{self.segment_id(utterance)}.wav"
            segment_id = destination.stem
            if progress:
                source_label = "VI Subtitle" if self.text_source == "vi_subtitle" else "VI Dubbing"
                voice_label = item.voice.get("display_name") or item.voice_id
                progress(
                    f"TTS {index}/{result.needed} | {source_label} | Utterance {utterance.id} | {voice_label}"
                )
            utterance.tts_generation_status = "generating"
            utterance.tts_error = ""
            try:
                check_cancel(cancel)
                metadata = self.client.generate(
                    segment_id,
                    "project",
                    item.voice_id,
                    resolve_tts_text(utterance, self.text_source),
                    item.speed,
                )
                check_cancel(cancel)
                audio_url = metadata.get("audio_url")
                audio_path = metadata.get("audio_path")
                if audio_url:
                    try:
                        wav_bytes = self.client.download_audio(audio_url)
                    except Exception:
                        if audio_path:
                            wav_bytes = self.client.download_audio(audio_path=audio_path)
                        else:
                            raise
                elif audio_path:
                    wav_bytes = self.client.download_audio(audio_path=audio_path)
                else:
                    raise LocalTTSError("INVALID_RESPONSE", "Local_TTS response has neither audio_url nor audio_path")
                check_cancel(cancel)
                metadata_duration = metadata.get("duration")
                actual_duration = self._write_wav_atomic(destination, wav_bytes)
                if (
                    type(metadata_duration) in (int, float)
                    and math.isfinite(metadata_duration)
                    and abs(float(metadata_duration) - actual_duration) > max(0.05, actual_duration * 0.02)
                ):
                    logger.warning(
                        "Local_TTS duration mismatch segment=%s metadata=%.3f wav=%.3f; using WAV",
                        segment_id,
                        float(metadata_duration),
                        actual_duration,
                    )
                utterance.tts_audio_path = destination.relative_to(root).as_posix()
                utterance.tts_duration = actual_duration
                utterance.tts_segment_id = segment_id
                utterance.tts_fingerprint = signature
                utterance.tts_generation_status = "generated"
                utterance.tts_error = ""
                utterance.recalculate()
                if self.calibration_cache is not None:
                    self.calibration_cache.observe(
                        self.server_base_url, item.voice, item.speed,
                        resolve_tts_text(utterance, self.text_source), actual_duration,
                    )
                result.generated += 1
                result.warnings += utterance.tts_alignment_status == "warning"
                manifest_key = segment_manifest_key(utterance.tts_cache_key, self.text_source)
                plan.manifest["segments"][manifest_key] = self._manifest_entry(
                    utterance,
                    item.voice_id,
                    item.speed,
                    item.voice,
                    signature,
                    manifest_file_for_project(root, utterance.tts_audio_path),
                    actual_duration,
                    segment_id,
                )
                save_manifest(root, plan.manifest)
                if progress:
                    progress(f"TTS saved {index}/{result.needed} | {utterance.tts_cache_key}")
                check_cancel(cancel)
            except CancelledError:
                self.manager.save(project, root)
                raise
            except Exception as exc:
                utterance.tts_generation_status = "failed"
                utterance.tts_error = str(exc) or exc.__class__.__name__
                utterance.recalculate()
                result.failed_ids.append(utterance.id)

        self.manager.save(project, root)
        return result
