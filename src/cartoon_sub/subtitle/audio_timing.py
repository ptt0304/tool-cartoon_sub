"""Audio-grounded timing refinement for already translated display subtitles."""
import hashlib
import io
import json
import logging
import wave
from pathlib import Path

from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.subtitle.models import DisplaySegment
from cartoon_sub.subtitle.segmentation import restore_exact_parts


log = logging.getLogger(__name__)


TIMING_VERSION = "audio-display-timing-v1"
TIMING_SCHEMA = {"type": "OBJECT", "properties": {"segments": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
    "vi": {"type": "STRING"}}, "required": ["start", "end", "vi"]}}}, "required": ["segments"]}
TIMING_PROMPT = """Listen to the supplied Chinese audio clip and align the supplied Vietnamese subtitle to its speech.
Return 2 to 8 consecutive display segments only when the Vietnamese subtitle is too long for one display.
Each vi value must be an exact consecutive substring of vietnamese_subtitle. Do not translate, correct,
paraphrase, add, remove, reorder words, or change punctuation. Use seconds relative to this audio clip.
The first start must be 0 and the final end must equal clip_duration. Adjacent segments must touch.
Chinese transcript is context only; never return it."""


class AudioTimingValidationError(GeminiError):
    def __init__(self, detail):
        super().__init__(f"Căn thời gian audio không hợp lệ: {detail}", retryable=True)


def _reject(detail):
    log.warning("[TIMING] rejected=%s", detail)
    raise AudioTimingValidationError(detail)


def validate_timing(payload, utterance, tolerance=.15):
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except ValueError:
            log.warning("[TIMING] rejected=không đọc được JSON raw_response=%r", payload)
            raise AudioTimingValidationError("không đọc được JSON") from None
    rows = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not 2 <= len(rows) <= 8:
        _reject("cần 2–8 segments")
    parts, times = [], []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"start", "end", "vi"} or not isinstance(row["vi"], str) or not row["vi"].strip():
            _reject(f"dòng {index} sai cấu trúc")
        if type(row["start"]) not in (int, float) or type(row["end"]) not in (int, float):
            _reject(f"dòng {index} có timestamp sai")
        start, end = float(row["start"]), float(row["end"])
        if start < 0 or end <= start or end > utterance.duration + tolerance:
            _reject(f"dòng {index} vượt khoảng audio: start={start}, end={end}, duration={utterance.duration}")
        parts.append(row["vi"]); times.append((start, end))
    try:
        parts = list(restore_exact_parts(parts, utterance.vi_subtitle))
    except ValueError as exc:
        log.warning("[TIMING] rejected=Gemini đã thay đổi bản dịch tiếng Việt detail=%s parts=%r", exc, parts)
        raise AudioTimingValidationError("Gemini đã thay đổi bản dịch tiếng Việt") from None
    if abs(times[0][0]) > tolerance or abs(times[-1][1] - utterance.duration) > tolerance:
        _reject(f"không phủ đúng đầu/cuối Utterance: first={times[0][0]}, last={times[-1][1]}, duration={utterance.duration}")
    if any(abs(left[1] - right[0]) > tolerance for left, right in zip(times, times[1:])):
        _reject(f"các đoạn không liên tục: {times}")
    result = []
    for index, (text, (_, end)) in enumerate(zip(parts, times), 1):
        start = utterance.start if index == 1 else result[-1].end
        finish = utterance.end if index == len(parts) else utterance.start + end
        result.append(DisplaySegment(f"{utterance.id}.{index}", utterance.id, start, finish, text,
            segmentation_reason="audio_timing"))
    log.info("[TIMING] accepted=%r", [(item.start, item.end, item.vi_text) for item in result])
    return result


class AudioTimingRefiner:
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def refine(self, utterance, audio_path, cache_directory, *, cancel=None, progress=None):
        if not utterance.zh.strip() or not utterance.vi_subtitle.strip():
            raise ValueError("Cần transcript Trung và bản dịch Việt trước khi căn audio")
        audio = self._slice(audio_path, utterance.start, utterance.end)
        settings = self.store.load()
        with wave.open(io.BytesIO(audio), "rb") as clip:
            actual_duration = clip.getnframes() / clip.getframerate()
        log.info("[TIMING] utterance_id=%s utterance_start=%.3f utterance_end=%.3f "
                 "utterance_duration=%.3f text=%r audio_segment_duration=%.3f selected_model=%s",
                 utterance.id, utterance.start, utterance.end, utterance.duration,
                 utterance.vi_subtitle, actual_duration, settings.transcription_model)
        key = content_hash({"version": TIMING_VERSION, "audio": hashlib.sha256(audio).hexdigest(),
            "zh": utterance.zh, "vi": utterance.vi_subtitle, "model": settings.transcription_model})
        path = Path(cache_directory) / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "completed": return validate_timing(data["response"], utterance)
            except (ValueError, KeyError, GeminiError): pass
        check_cancel(cancel)
        if settings.transcription_provider != "gemini":
            raise ValueError(f"Provider {settings.transcription_provider} không hỗ trợ căn timing audio.")
        client = self.factory(self.store.get_gemini_keys(settings))
        try:
            if progress: progress(f"Căn audio Utterance {utterance.id} bằng Gemini…")
            prompt = TIMING_PROMPT + "\n" + json.dumps({"clip_duration": utterance.duration,
                "chinese_transcript": utterance.zh, "vietnamese_subtitle": utterance.vi_subtitle}, ensure_ascii=False)
            payload = client.transcribe_json(audio, prompt, TIMING_SCHEMA, settings.transcription_model,
                cancel=cancel, progress=progress)
            log.info("[TIMING] result_status=success raw_response=%r", payload)
            check_cancel(cancel)
            segments = validate_timing(payload, utterance)
            atomic_json(path, {"status": "completed", "response": payload})
            return segments
        except GeminiError as exc:
            log.warning("[TIMING] result_status=failed error=%s", exc)
            atomic_json(path, {"status": "failed", "error": str(exc)})
            raise
        finally:
            client.close()

    @staticmethod
    def _slice(path, start, end):
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
                raise ValueError("Audio căn thời gian phải là WAV PCM mono 16 kHz")
            source.setpos(round(start * 16000))
            raw = source.readframes(round((end - start) * 16000))
        output = io.BytesIO()
        with wave.open(output, "wb") as clip:
            clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(16000); clip.writeframes(raw)
        return output.getvalue()
