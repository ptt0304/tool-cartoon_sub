"""Audio-grounded timing refinement for already translated display subtitles."""
import hashlib
import io
import json
import wave
from pathlib import Path

from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.subtitle.models import DisplaySegment
from cartoon_sub.subtitle.segmentation import normalize_text


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


def validate_timing(payload, utterance, tolerance=.15):
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except ValueError: raise AudioTimingValidationError("không đọc được JSON") from None
    rows = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not 2 <= len(rows) <= 8:
        raise AudioTimingValidationError("cần 2–8 segments")
    parts, times = [], []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"start", "end", "vi"} or not isinstance(row["vi"], str) or not row["vi"].strip():
            raise AudioTimingValidationError(f"dòng {index} sai cấu trúc")
        if type(row["start"]) not in (int, float) or type(row["end"]) not in (int, float):
            raise AudioTimingValidationError(f"dòng {index} có timestamp sai")
        start, end = float(row["start"]), float(row["end"])
        if start < 0 or end <= start or end > utterance.duration + tolerance:
            raise AudioTimingValidationError(f"dòng {index} vượt khoảng audio")
        parts.append(row["vi"]); times.append((start, end))
    if normalize_text("".join(parts)) != normalize_text(utterance.vi_subtitle):
        raise AudioTimingValidationError("Gemini đã thay đổi bản dịch tiếng Việt")
    if abs(times[0][0]) > tolerance or abs(times[-1][1] - utterance.duration) > tolerance:
        raise AudioTimingValidationError("không phủ đúng đầu/cuối Utterance")
    if any(abs(left[1] - right[0]) > tolerance for left, right in zip(times, times[1:])):
        raise AudioTimingValidationError("các đoạn không liên tục")
    result = []
    for index, (text, (_, end)) in enumerate(zip(parts, times), 1):
        start = utterance.start if index == 1 else result[-1].end
        finish = utterance.end if index == len(parts) else utterance.start + end
        result.append(DisplaySegment(f"{utterance.id}.{index}", utterance.id, start, finish, text,
            segmentation_reason="audio_timing"))
    return result


class AudioTimingRefiner:
    def __init__(self, store, client_factory=GeminiClient):
        self.store, self.factory = store, client_factory

    def refine(self, utterance, audio_path, cache_directory, *, cancel=None, progress=None):
        if not utterance.zh.strip() or not utterance.vi_subtitle.strip():
            raise ValueError("Cần transcript Trung và bản dịch Việt trước khi căn audio")
        audio = self._slice(audio_path, utterance.start, utterance.end)
        settings = self.store.load()
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
            payload = client.transcribe_json(audio, prompt, TIMING_SCHEMA, settings.transcription_model, cancel=cancel)
            check_cancel(cancel)
            segments = validate_timing(payload, utterance)
            atomic_json(path, {"status": "completed", "response": payload})
            return segments
        except GeminiError as exc:
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
