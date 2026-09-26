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
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import review_complete
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.tts.cache_identity import build_tts_fingerprint, build_tts_segment_id
from cartoon_sub.tts.local_tts_client import LocalTTSClient, LocalTTSError


logger = logging.getLogger(__name__)


@dataclass
class TTSGenerationResult:
    total: int
    generated: int = 0
    cached: int = 0
    failed_ids: list[int] = field(default_factory=list)
    warnings: int = 0


class LocalTTSGenerationService:
    def __init__(
        self,
        client: LocalTTSClient,
        manager: ProjectManager | None = None,
        server_base_url: str | None = None,
    ):
        self.client = client
        self.manager = manager or ProjectManager()
        configured_url = server_base_url or getattr(getattr(client, "settings", None), "base_url", None)
        if not configured_url:
            raise ValueError("Local_TTS server base URL is required for cache identity")
        self.server_base_url = configured_url

    def fingerprint(self, utterance: Utterance, speaker: Speaker) -> str:
        return build_tts_fingerprint(utterance, speaker, self.server_base_url)

    @staticmethod
    def segment_id(utterance: Utterance, fingerprint: str) -> str:
        return build_tts_segment_id(utterance, fingerprint)

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 44:
                return False
            with wave.open(str(path), "rb") as reader:
                return reader.getnchannels() > 0 and reader.getframerate() > 0 and reader.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    @classmethod
    def _cache_hit(cls, project_dir: Path, utterance: Utterance, fingerprint: str, segment_id: str) -> bool:
        if utterance.tts_fingerprint != fingerprint or utterance.tts_segment_id != segment_id or not utterance.tts_audio_path:
            return False
        path = (project_dir / utterance.tts_audio_path).resolve()
        try:
            path.relative_to(project_dir.resolve())
        except ValueError:
            return False
        return cls._valid_wav(path)

    def _preflight(self, project: Project, project_dir: Path) -> dict[str, Speaker]:
        if not isinstance(project, Project) or not (project_dir / "project.json").is_file():
            raise ValueError("Project chưa được lưu")
        if not review_complete(project):
            raise ValueError("Cần hoàn tất duyệt speaker trước khi tạo TTS")
        if not project.utterances:
            raise ValueError("Project không có Utterance")
        empty = [row.id for row in project.utterances if not row.vi_dubbing.strip()]
        if empty:
            raise ValueError(f"Utterance chưa có vi_dubbing: {', '.join(map(str, empty))}")

        speakers: dict[str, Speaker] = {}
        for speaker_id in {row.speaker_id for row in project.utterances}:
            raw = project.speakers.get(speaker_id)
            if raw is None:
                raise ValueError(f"Không tìm thấy speaker {speaker_id}")
            speaker = Speaker(**raw)
            logger.info(
                "[AUDIO DEBUG] preflight speaker=%s stored_voice_id=%r runtime_voice_id=%r",
                speaker_id, raw.get("tts_voice_id"), speaker.tts_voice_id,
            )
            if not speaker.tts_voice_id:
                raise ValueError(f"Speaker {speaker_id} chưa chọn Local_TTS voice")
            speakers[speaker_id] = speaker

        health = self.client.health()
        if health.get("status") != "READY":
            raise ValueError(f"Local_TTS chưa READY: {health.get('status', 'UNKNOWN')}")
        voices = {
            voice.get("voice_id"): voice
            for voice in self.client.list_voices()
            if isinstance(voice.get("voice_id"), str)
        }
        for speaker in speakers.values():
            voice = voices.get(speaker.tts_voice_id)
            logger.info(
                "[AUDIO DEBUG] preflight speaker=%s runtime_voice_id=%r voice_exists=%s "
                "registry_voice_id=%r engine=%r status=%r",
                speaker.id,
                speaker.tts_voice_id,
                voice is not None,
                voice.get("voice_id") if voice else None,
                voice.get("engine") if voice else None,
                voice.get("status") if voice else None,
            )
            if voice is None:
                raise ValueError(f"Local_TTS không có voice {speaker.tts_voice_id}")
            if voice.get("status") != "READY":
                raise ValueError(f"Local_TTS voice chưa READY: {speaker.tts_voice_id}")
        return speakers

    @staticmethod
    def _clear_audio_metadata(utterance: Utterance) -> None:
        utterance.tts_audio_path = None
        utterance.tts_duration = None
        utterance.tts_speed_factor = None
        utterance.tts_alignment_status = "not_imported"

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
        speakers = self._preflight(project, root)
        result = TTSGenerationResult(total=len(project.utterances))
        segments_dir = root / "audio" / "tts" / "segments"

        for index, utterance in enumerate(project.utterances, 1):
            check_cancel(cancel)
            speaker = speakers[utterance.speaker_id]
            fingerprint = self.fingerprint(utterance, speaker)
            segment_id = self.segment_id(utterance, fingerprint)
            if progress:
                progress(
                    f"TTS {index}/{result.total} | {utterance.speaker_id} | "
                    f"Utterance {utterance.id} | {speaker.tts_voice_id}"
                )

            if self._cache_hit(root, utterance, fingerprint, segment_id):
                utterance.tts_generation_status = "cached"
                utterance.tts_error = ""
                utterance.recalculate()
                result.cached += 1
                result.warnings += utterance.tts_alignment_status == "warning"
                self.manager.save(project, root)
                continue

            utterance.tts_segment_id = segment_id
            utterance.tts_fingerprint = fingerprint
            self._clear_audio_metadata(utterance)
            utterance.tts_generation_status = "generating"
            utterance.tts_error = ""
            destination = segments_dir / f"{segment_id}.wav"
            try:
                check_cancel(cancel)
                metadata = self.client.generate(
                    segment_id,
                    utterance.speaker_id,
                    speaker.tts_voice_id,
                    utterance.vi_dubbing,
                    float(speaker.tts_speed),
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
                utterance.tts_generation_status = "generated"
                utterance.tts_error = ""
                utterance.recalculate()
                result.generated += 1
                result.warnings += utterance.tts_alignment_status == "warning"
                self.manager.save(project, root)
            except CancelledError:
                raise
            except Exception as exc:
                self._clear_audio_metadata(utterance)
                utterance.tts_generation_status = "failed"
                utterance.tts_error = str(exc) or exc.__class__.__name__
                utterance.recalculate()
                result.failed_ids.append(utterance.id)
                self.manager.save(project, root)

        return result
