from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
import wave
from dataclasses import dataclass
from pathlib import Path

from cartoon_sub.project.cache import atomic_json
from cartoon_sub.syllable.vietnamese import count_syllables
from cartoon_sub.tts.cache_identity import normalize_local_tts_base_url


FIT_LIMIT = 1.05
LIGHT_FIT_LIMIT = 1.12
REWRITE_LIMIT = 1.30
MAX_SEMANTIC_REWRITES = 2
SILENCE_GUARD_SECONDS = 0.08

CALIBRATION_SAMPLES = (
    "Xin chào, hôm nay chúng ta bắt đầu một câu chuyện mới.",
    "Lưu Vân Lạp là pháp khí trung phẩm, nhưng uy lực của nó không hề tầm thường.",
    "Sau khi cân nhắc kỹ, hắn quyết định trở về Thanh Vân Tông để báo tin.",
)


def _wav_bytes_duration(value: bytes) -> float:
    try:
        with wave.open(io.BytesIO(value), "rb") as reader:
            rate = reader.getframerate()
            frames = reader.getnframes()
            if rate <= 0 or frames <= 0:
                raise ValueError
            return frames / rate
    except (EOFError, wave.Error, ValueError) as exc:
        raise ValueError("Local_TTS trả về WAV calibration không hợp lệ") from exc


def calibration_signature(base_url: str, voice: dict, speed: float) -> str:
    identity_fields = (
        "voice_id", "engine", "backend", "model_id", "model", "version",
        "voice_version", "revision", "reference_id", "reference_hash", "source_hash",
    )
    value = {
        "version": 1,
        "server": normalize_local_tts_base_url(base_url),
        "voice": {field: voice.get(field) for field in identity_fields},
        "speed": float(speed),
        "pause_settings": None,
        "chunking_settings": None,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class VoiceCalibration:
    signature: str
    voice_id: str
    speed: float
    syllables_per_second: float
    sample_duration: float
    sample_count: int


class VoiceCalibrationCache:
    """Small global cache; it stores rates and durations, never generated WAV data."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.is_file():
            return {"version": 1, "calibrations": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 1 and isinstance(data.get("calibrations"), dict):
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"version": 1, "calibrations": {}}

    @staticmethod
    def _value(signature, row):
        try:
            return VoiceCalibration(
                signature=signature,
                voice_id=str(row["voice_id"]),
                speed=float(row["speed"]),
                syllables_per_second=float(row["syllables_per_second"]),
                sample_duration=float(row["sample_duration"]),
                sample_count=int(row["sample_count"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def get(self, signature):
        return self._value(signature, self._load()["calibrations"].get(signature, {}))

    def calibrate(self, client, base_url, voice, speed=1.0, progress=None):
        signature = calibration_signature(base_url, voice, speed)
        existing = self.get(signature)
        if existing is not None:
            return existing
        rates = []
        durations = []
        voice_id = voice["voice_id"]
        for index, text in enumerate(CALIBRATION_SAMPLES, 1):
            if progress:
                progress(f"Calibration {voice_id} {index}/{len(CALIBRATION_SAMPLES)}")
            digest = hashlib.sha256(f"{signature}:{index}".encode()).hexdigest()[:16]
            metadata = client.generate(f"cal_{digest}", "CALIBRATION", voice_id, text, float(speed))
            audio_url, audio_path = metadata.get("audio_url"), metadata.get("audio_path")
            wav_bytes = client.download_audio(audio_url, audio_path=audio_path) if audio_url else client.download_audio(audio_path=audio_path)
            duration = _wav_bytes_duration(wav_bytes)
            rates.append(count_syllables(text) / duration)
            durations.append(duration)
        calibration = VoiceCalibration(
            signature=signature,
            voice_id=voice_id,
            speed=float(speed),
            syllables_per_second=float(statistics.median(rates)),
            sample_duration=float(sum(durations)),
            sample_count=len(durations),
        )
        data = self._load()
        data["calibrations"][signature] = {
            "voice_id": calibration.voice_id,
            "speed": calibration.speed,
            "syllables_per_second": calibration.syllables_per_second,
            "sample_duration": calibration.sample_duration,
            "sample_count": calibration.sample_count,
            "observations": [],
        }
        atomic_json(self.path, data)
        return calibration

    def observe(self, base_url, voice, speed, text, actual_duration):
        if actual_duration <= 0 or count_syllables(text) < 3:
            return
        signature = calibration_signature(base_url, voice, speed)
        data = self._load()
        row = data["calibrations"].get(signature)
        if not isinstance(row, dict):
            return
        observed = count_syllables(text) / actual_duration
        baseline = float(row.get("syllables_per_second", observed))
        if not 0.45 * baseline <= observed <= 2.2 * baseline:
            return
        observations = [float(item) for item in row.get("observations", []) if type(item) in (int, float)]
        observations = (observations + [observed])[-21:]
        # The fixed calibration samples retain equal influence and a single unusual
        # sentence cannot swing the estimate sharply.
        row["observations"] = observations
        row["syllables_per_second"] = float(statistics.median([baseline, baseline, *observations]))
        atomic_json(self.path, data)


class DurationFitPlanner:
    def __init__(self, guard_seconds=SILENCE_GUARD_SECONDS):
        self.guard_seconds = float(guard_seconds)

    def potential_duration(self, project, utterance) -> float:
        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        index = ordered.index(utterance)
        previous_end = ordered[index - 1].end if index else 0.0
        project_end = float(project.metadata.get("duration") or utterance.end)
        next_start = ordered[index + 1].start if index + 1 < len(ordered) else project_end
        before = max(0.0, utterance.start - previous_end - self.guard_seconds)
        after = max(0.0, next_start - utterance.end - self.guard_seconds)
        return utterance.duration + after + before

    @staticmethod
    def _is_dense(row, usable_slack):
        if not row.tts_duration:
            return False
        ratio = row.tts_duration / row.duration
        density = row.vi_syllables / row.duration if row.duration else math.inf
        need = max(0.0, row.tts_duration - row.duration)
        return ratio > LIGHT_FIT_LIMIT and density >= 3.5 and usable_slack + 1e-6 < need

    def apply(self, project):
        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        if not ordered:
            return []
        project_end = float(project.metadata.get("duration") or ordered[-1].end)
        gaps = [max(0.0, right.start - left.end - self.guard_seconds) for left, right in zip(ordered, ordered[1:])]
        leading = max(0.0, ordered[0].start - self.guard_seconds)
        trailing = max(0.0, project_end - ordered[-1].end - self.guard_seconds)
        dense = []
        for index, row in enumerate(ordered):
            before = leading if index == 0 else gaps[index - 1]
            after = trailing if index == len(ordered) - 1 else gaps[index]
            dense.append(self._is_dense(row, before + after))
        dense_chain = set()
        run = []
        for index, is_dense in enumerate([*dense, False]):
            if is_dense:
                run.append(index)
            else:
                if len(run) >= 3:
                    dense_chain.update(run)
                run = []

        changed = []
        for index, row in enumerate(ordered):
            row.allowed_audio_start = float(row.start)
            row.allowed_audio_end = float(row.end)
            row.tts_fit_ratio = None
            if not row.tts_duration:
                row.dubbing_fit_status = "NOT_MEASURED"
                continue
            if index in dense_chain:
                row.tts_fit_ratio = row.tts_duration / row.duration
                row.dubbing_fit_status = "LONG_DENSE_CHAIN"
                row.tts_alignment_status = "warning"
                row.tts_alignment_diagnostic = "LONG_DENSE_CHAIN / NEED_REVIEW"
                changed.append(row.id)
                continue

            need = max(0.0, row.tts_duration - row.duration)
            borrowed_after = borrowed_before = 0.0
            if need > 0:
                if index == len(ordered) - 1:
                    borrowed_after = min(need, trailing)
                    trailing -= borrowed_after
                else:
                    borrowed_after = min(need, gaps[index])
                    gaps[index] -= borrowed_after
                need -= borrowed_after
                if need > 0:
                    if index == 0:
                        borrowed_before = min(need, leading)
                        leading -= borrowed_before
                    else:
                        borrowed_before = min(need, gaps[index - 1])
                        gaps[index - 1] -= borrowed_before
            row.allowed_audio_start -= borrowed_before
            row.allowed_audio_end += borrowed_after
            allowed = row.allowed_audio_end - row.allowed_audio_start
            ratio = row.tts_duration / allowed
            row.tts_fit_ratio = ratio
            borrowed = borrowed_after > 1e-6 or borrowed_before > 1e-6
            if ratio <= FIT_LIMIT:
                row.dubbing_fit_status = "BORROWED" if borrowed else "FIT"
                row.tts_alignment_status = "fits"
                row.tts_alignment_diagnostic = "BORROWED" if borrowed else "SYNC_OK"
            elif ratio <= LIGHT_FIT_LIMIT:
                row.dubbing_fit_status = "LIGHT_FIT"
                row.tts_alignment_status = "warning"
                row.tts_alignment_diagnostic = "LIGHT_FIT"
            elif ratio <= REWRITE_LIMIT:
                row.dubbing_fit_status = "REWRITE_SHORTER"
                row.tts_alignment_status = "warning"
                row.tts_alignment_diagnostic = "REWRITE_SHORTER"
            else:
                row.dubbing_fit_status = "STRONG_REWRITE"
                row.tts_alignment_status = "warning"
                row.tts_alignment_diagnostic = "STRONG_REWRITE"
            changed.append(row.id)
        return changed

    @staticmethod
    def mix_parameters(row):
        start = row.allowed_audio_start if row.allowed_audio_start is not None else row.start
        allowed_end = row.allowed_audio_end if row.allowed_audio_end is not None else row.end
        allowed = max(0.001, allowed_end - start)
        actual = row.tts_duration or allowed
        ratio = actual / allowed
        tempo = ratio if 1.0 < ratio <= LIGHT_FIT_LIMIT else 1.0
        return float(start), float(tempo)
