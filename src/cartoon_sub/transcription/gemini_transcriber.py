import io
import json
import logging
import math
import wave
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event

from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.prompts import read
from cartoon_sub.speaker.service import detect_overlaps
from cartoon_sub.subtitle.models import Segment

PROMPT_VERSION = "zh-utterance-v2"
SPEAKER_DETECTION_VERSION = "voice-reference-v1"
CHUNK_SECONDS = 60
TRANSCRIPT_GEMINI_BACKOFF_SECONDS = (2, 4, 8, 16, 32)
PROMPT = read("transcription_v2.txt")
SCHEMA = {"type":"OBJECT", "properties":{"segments":{"type":"ARRAY","items":{
    "type":"OBJECT","properties":{
        "id":{"type":"INTEGER"}, "start":{"type":"NUMBER"}, "end":{"type":"NUMBER"},
        "speaker_id":{"type":"STRING"}, "zh":{"type":"STRING"},
        "overlap":{"type":"BOOLEAN"}, "overlap_group":{"type":"STRING","nullable":True},
        "speaker_confidence":{"type":"NUMBER","nullable":True},
        "transcript_confidence":{"type":"NUMBER","nullable":True}},
    "required":["id","start","end","speaker_id","zh","overlap","overlap_group","speaker_confidence","transcript_confidence"]}}},"required":["segments"]}

log = logging.getLogger(__name__)


def classify_gemini_error(exc):
    """Stable Transcript-only classification; never parses SDK error strings."""
    if isinstance(exc, CancelledError):
        return "CANCELLED"
    if isinstance(exc, TranscriptValidationError):
        return "VALIDATION"
    if getattr(exc, "status_code", None) in (408, 429, 500, 502, 503, 504):
        return "TRANSIENT"
    if getattr(exc, "category", None) in ("AUTH", "PERMISSION", "BAD_REQUEST", "MODEL_NOT_FOUND"):
        return exc.category
    if getattr(exc, "status_code", None) is not None:
        return "UNKNOWN"
    if getattr(exc, "category", None) == "TRANSIENT" or getattr(exc, "retryable", False):
        return "TRANSIENT"
    return "UNKNOWN"


def _wait_for_transcript_retry(cancel, seconds, report, message):
    """Interruptible one-second countdown, called from the existing worker thread."""
    for remaining in range(seconds, 0, -1):
        report(f"{message} Thử lại sau {remaining} giây…")
        if cancel.wait(1):
            check_cancel(cancel)
    check_cancel(cancel)


def _transcript_exhausted_error(last_error, key_count):
    detail = str(last_error) if last_error else "Gemini không phản hồi."
    keys = f" Đã thử {key_count} API key." if key_count > 1 else ""
    schedule = ", ".join(f"{seconds}s" for seconds in TRANSCRIPT_GEMINI_BACKOFF_SECONDS)
    return GeminiError(
        f"Gemini vẫn không khả dụng sau {len(TRANSCRIPT_GEMINI_BACKOFF_SECONDS)} lần thử lại "
        f"({schedule})." + keys + f"\n\nLỗi cuối:\n{detail}\n\n"
        "Các đoạn transcript đã hoàn tất vẫn được giữ trong cache; hãy chạy lại để tiếp tục.",
        status_code=getattr(last_error, "status_code", None),
        category=getattr(last_error, "category", "TRANSIENT"),
    )


class TranscriptValidationError(GeminiError):
    def __init__(self, detail):
        super().__init__(f"Transcript không hợp lệ: {detail}. Chưa thay subtitle hiện tại.", retryable=True)


def parse_timestamp(value, row, field):
    import re
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[0-9]+:[0-5][0-9](?:[.,][0-9]+)?", value):
            minutes, seconds = value.replace(",", ".").split(":")
            value = int(minutes) * 60 + float(seconds)
        elif re.fullmatch(r"[0-9]+:[0-5][0-9]:[0-5][0-9](?:[.,][0-9]+)?", value):
            hours, minutes, seconds = value.replace(",", ".").split(":")
            value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        elif re.fullmatch(r"[0-9]+(?:[.][0-9]+)?", value):
            value = float(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TranscriptValidationError(f"dòng {row}, {field} phải là số giây hoặc MM:SS/HH:MM:SS")
    return value


def validate_response(payload, duration):
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            raise TranscriptValidationError("không đọc được JSON") from None
    else:
        data = payload
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise TranscriptValidationError("thiếu danh sách segments")
    segments = []
    for index, item in enumerate(data["segments"], 1):
        if not isinstance(item, dict):
            raise TranscriptValidationError(f"dòng {index} không phải object")
        if "zh" not in item and "text" in item:
            item = {**item, "zh": item["text"]}
        if not all(k in item for k in ("start", "end", "zh")):
            raise TranscriptValidationError(f"dòng {index} thiếu start/end/zh")
        if "id" in item and type(item["id"]) is not int:
            raise TranscriptValidationError(f"dòng {index}, id phải là số nguyên")
        if not isinstance(item["zh"], str) or not item["zh"].strip():
            raise TranscriptValidationError(f"dòng {index}, Chinese text rỗng hoặc không phải chuỗi")
        start = parse_timestamp(item["start"], index, "start")
        end = parse_timestamp(item["end"], index, "end")
        if start < 0 or end <= start:
            raise TranscriptValidationError(f"dòng {index}, cần 0 <= start < end (start={start}, end={end})")
        if end > duration + 0.001:
            raise TranscriptValidationError(f"dòng {index}, end={end:.3f}s vượt độ dài audio {duration:.3f}s")
        try:
            segment = Segment(index, start, end, item["zh"], speaker_id=item.get("speaker_id", "SPK_UNKNOWN"),
                speaker_confidence=item.get("speaker_confidence"),
                transcript_confidence=item.get("transcript_confidence"))
        except ValueError:
            raise TranscriptValidationError(f"dòng {index}, speaker_id hoặc confidence không hợp lệ") from None
        segments.append(segment)
    segments.sort(key=lambda s: (s.start, s.end, s.id))
    for index, segment in enumerate(segments, 1):
        segment.id = index
    detect_overlaps(segments)
    return segments


def _raw_time_range(payload):
    """Best-effort diagnostics only; validation remains authoritative."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        rows = data.get("segments", [])
        starts = [parse_timestamp(row["start"], index, "start") for index, row in enumerate(rows, 1)]
        ends = [parse_timestamp(row["end"], index, "end") for index, row in enumerate(rows, 1)]
        return (min(starts), max(ends)) if starts and ends else (None, None)
    except (ValueError, TypeError, KeyError, AttributeError, GeminiError):
        return None, None


class GeminiTranscriber:
    """Bounded chunk pipeline; client_factory may be Gemini or another ASR adapter."""
    def __init__(self, key_provider, model, cache_directory, retry_count=2, client_factory=GeminiClient):
        self.key_provider = key_provider
        self.model = model
        self.cache_directory = Path(cache_directory)
        self.retry_count = retry_count
        self.client_factory = client_factory

    def transcribe(self, audio_path, *, cancel=None, progress=None):
        cancel = cancel or Event()
        report = progress or (lambda text: None)
        client = None
        result = []
        references = {}
        try:
            with wave.open(str(audio_path), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError("Audio phải là WAV PCM 16-bit mono 16 kHz")
                frames_per_chunk = CHUNK_SECONDS * audio.getframerate()
                total = math.ceil(audio.getnframes() / frames_per_chunk)
                source_duration = audio.getnframes() / audio.getframerate()
                log.info("[TRANSCRIPT TRACE] source_audio_duration=%.6f chunks=%d chunk_seconds=%d",
                         source_duration, total, CHUNK_SECONDS)
                for chunk_index in range(total):
                    check_cancel(cancel)
                    raw = audio.readframes(frames_per_chunk)
                    duration = len(raw) / 32000
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as chunk:
                        chunk.setnchannels(1); chunk.setsampwidth(2); chunk.setframerate(16000); chunk.writeframes(raw)
                    import hashlib
                    key = content_hash({"audio": hashlib.sha256(buffer.getvalue()).hexdigest(),
                        "model": self.model, "prompt": PROMPT, "schema": SCHEMA, "version": PROMPT_VERSION,
                        "speaker_version": SPEAKER_DETECTION_VERSION,
                        "references": {k: hashlib.sha256(v).hexdigest() for k, v in references.items()}})
                    path = self.cache_directory / f"{key}.json"
                    state = None
                    if path.exists():
                        try:
                            state = json.loads(path.read_text(encoding="utf-8"))
                            if state.get("status") == "completed":
                                local = validate_response(state["response"], duration)
                                for cached, segment in zip(state["response"]["segments"], local):
                                    if cached.get("tts_cache_key"):
                                        segment.tts_cache_key = cached["tts_cache_key"]
                            else:
                                state = None
                        except (ValueError, KeyError, AttributeError, GeminiError):
                            state = None
                    chunk_start = chunk_index * CHUNK_SECONDS
                    chunk_end = chunk_start + duration
                    log.info(
                        "[TRANSCRIPT TRACE] chunk_index=%d chunk_start=%.6f chunk_end=%.6f "
                        "chunk_duration=%.6f cache_used=%s cache_timestamp_basis=relative",
                        chunk_index, chunk_start, chunk_end, duration, state is not None,
                    )
                    if state is not None:
                        report(f"Đoạn {chunk_index + 1}/{total}: dùng cache")
                    else:
                        if client is None:
                            client = self.client_factory(self.key_provider())
                        correction = ""
                        managed_gemini_retry = getattr(client, "managed_transcript_retry", False) is True
                        key_count = (max(1, int(client.transcript_key_count)) if managed_gemini_retry else 1)
                        key_indexes = range(key_count) if managed_gemini_retry else range(1)
                        completed = False
                        last_transient = None
                        for key_index in key_indexes:
                            if managed_gemini_retry:
                                check_cancel(cancel)
                                client.select_transcript_key(key_index)
                            transient_index = 0
                            max_attempts = (len(TRANSCRIPT_GEMINI_BACKOFF_SECONDS) + 1
                                            if managed_gemini_retry else self.retry_count + 1)
                            validation_failures = 0
                            for attempt in range(max_attempts):
                                check_cancel(cancel)
                                key_label = (f", key {key_index + 1}/{key_count}"
                                             if managed_gemini_retry and key_count > 1 else "")
                                if managed_gemini_retry and attempt:
                                    report(f"Transcript {chunk_index + 1}/{total} • Thử lại "
                                           f"{attempt}/{len(TRANSCRIPT_GEMINI_BACKOFF_SECONDS)}{key_label}")
                                else:
                                    report(f"Transcribing chunk {chunk_index + 1}/{total}, lần {attempt + 1}{key_label}")
                                log.info(
                                    "[GEMINI RETRY] feature=transcript chunk=%d/%d attempt=%d/%d key_index=%d/%d",
                                    chunk_index + 1, total, attempt + 1, max_attempts, key_index + 1, key_count,
                                )
                                atomic_json(path, {"status": "running", "attempt": attempt + 1})
                                payload = None
                                try:
                                    transcribe = (client.transcribe_json_once if managed_gemini_retry
                                                  else client.transcribe_json)
                                    payload = transcribe(
                                        buffer.getvalue(),
                                        PROMPT + f"\nTARGET AUDIO duration: {duration:.6f} seconds. Known IDs: "
                                                 f"{sorted({s.speaker_id for s in result})}" + correction,
                                        SCHEMA, self.model, cancel=cancel, references=references, progress=report)
                                    raw_start, raw_end = _raw_time_range(payload)
                                    log.info(
                                        "[TRANSCRIPT TRACE] chunk_index=%d raw_model_start=%s raw_model_end=%s "
                                        "validation_duration=%.6f cache_used=False cache_timestamp_basis=relative",
                                        chunk_index, raw_start, raw_end, duration,
                                    )
                                    local = validate_response(payload, duration)
                                    check_cancel(cancel)
                                    response = {"segments": [{k: v for k, v in asdict(s).items() if k != "vi"}
                                                             for s in local]}
                                    atomic_json(path, {"status": "completed", "response": response})
                                    completed = True
                                    break
                                except GeminiError as exc:
                                    failure = {"status": "failed", "attempt": attempt + 1, "error": str(exc),
                                               "duration": duration, "chunk": chunk_index + 1}
                                    if isinstance(exc, TranscriptValidationError):
                                        raw_start, raw_end = _raw_time_range(payload)
                                        log.warning(
                                            "[TRANSCRIPT TRACE] chunk_index=%d raw_model_start=%s raw_model_end=%s "
                                            "normalized_start=N/A normalized_end=N/A validation_duration=%.6f "
                                            "cache_used=False cache_timestamp_basis=relative error=%s",
                                            chunk_index, raw_start, raw_end, duration, exc,
                                        )
                                        failure["raw_response"] = (payload if isinstance(payload, str)
                                                                   else json.dumps(payload, ensure_ascii=False))
                                        correction = ("\nYour previous response failed local validation: " + str(exc)
                                                      + "\nTranscribe the same audio again. Correct the indicated field; "
                                                        "use decimal seconds within the audio duration, short phrases, "
                                                        "and non-empty Chinese text. Do not invent or drop speech to bypass validation.")
                                    atomic_json(path, failure)
                                    category = classify_gemini_error(exc)
                                    if category == "VALIDATION":
                                        validation_failures += 1
                                        if validation_failures > self.retry_count or attempt + 1 >= max_attempts:
                                            raise
                                        continue
                                    if not managed_gemini_retry:
                                        if not exc.retryable or attempt >= self.retry_count:
                                            raise
                                        delay = 2 ** (attempt + 1)
                                        report(f"Đoạn {chunk_index + 1}: thử lại sau {delay} giây…")
                                        if cancel.wait(delay):
                                            check_cancel(cancel)
                                        continue
                                    if category != "TRANSIENT":
                                        raise
                                    last_transient = exc
                                    status = getattr(exc, "status_code", None)
                                    log.warning(
                                        "[GEMINI RETRY] feature=transcript chunk=%d/%d attempt=%d/%d status=%s",
                                        chunk_index + 1, total, attempt + 1, max_attempts,
                                        status if status is not None else "network",
                                    )
                                    if transient_index >= len(TRANSCRIPT_GEMINI_BACKOFF_SECONDS):
                                        break
                                    configured_delay = TRANSCRIPT_GEMINI_BACKOFF_SECONDS[transient_index]
                                    retry_after = getattr(exc, "retry_after_seconds", None) or 0
                                    delay = max(configured_delay, math.ceil(retry_after))
                                    transient_index += 1
                                    log.info("[GEMINI RETRY] feature=transcript waiting=%ds", delay)
                                    countdown_key = (f" • key {key_index + 1}/{key_count}"
                                                     if key_count > 1 else "")
                                    _wait_for_transcript_retry(
                                        cancel, delay, report,
                                        f"Transcript {chunk_index + 1}/{total} • Gemini "
                                        f"{status if status is not None else 'network'}{countdown_key} •",
                                    )
                            if completed:
                                break
                        if not completed:
                            raise _transcript_exhausted_error(last_transient, key_count)
                    offset = chunk_index * CHUNK_SECONDS
                    base_id = len(result)
                    raw_start = min((s.start for s in local), default=None)
                    raw_end = max((s.end for s in local), default=None)
                    normalized_start = None if raw_start is None else raw_start + offset
                    normalized_end = None if raw_end is None else raw_end + offset
                    log.info(
                        "[TRANSCRIPT TRACE] chunk_index=%d raw_model_start=%s raw_model_end=%s "
                        "normalized_start=%s normalized_end=%s validation_duration=%.6f "
                        "cache_used=%s cache_timestamp_basis=relative",
                        chunk_index, raw_start, raw_end, normalized_start, normalized_end,
                        source_duration, state is not None,
                    )
                    result.extend(replace(s, id=base_id + i, start=s.start + offset, end=s.end + offset)
                                  for i, s in enumerate(local, 1))
                    for segment in local:
                        if (segment.speaker_id == "SPK_UNKNOWN" or segment.speaker_id in references
                                or segment.overlap or len(references) >= 8):
                            continue
                        sample = raw[int(segment.start * 16000) * 2:
                                     int(min(segment.end, segment.start + 3) * 16000) * 2]
                        if len(sample) < 16000:
                            continue
                        voice = io.BytesIO()
                        with wave.open(voice, "wb") as output:
                            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                            output.writeframes(sample)
                        references[segment.speaker_id] = voice.getvalue()
            check_cancel(cancel)
            detect_overlaps(result)
            return result
        finally:
            if client is not None:
                client.close()
