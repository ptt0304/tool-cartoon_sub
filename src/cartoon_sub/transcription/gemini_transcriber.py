import io
import json
import logging
import math
import wave
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event

from cartoon_sub.ai.provider_errors import AIProviderError as GeminiError
from cartoon_sub.ai.openrouter_client import OpenRouterTranscriptionClient
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.prompts import read
from cartoon_sub.subtitle.models import Segment
from cartoon_sub.transcription.contracts import (
    normalize_confidence,
    normalize_word_timestamps,
)
from cartoon_sub.transcription.segmentation_normalizer import TranscriptSegmentationNormalizer
from cartoon_sub.transcription.speech_regions import VAD_VERSION, detect_speech_regions, fallback_regions
from cartoon_sub.transcription.timing_quality import evaluate_timing, TRUSTED, PARTIAL
from cartoon_sub.transcription.language_consistency import assess_rows

PROMPT_VERSION = "zh-utterance-v4-language-guard"
LANGUAGE_RETRY_VERSION = "zh-language-retry-v1"
TRANSCRIPT_GEMINI_BACKOFF_SECONDS = (2, 4, 8, 16, 32)
PROMPT = read("transcription_v2.txt")
SCHEMA = {"type":"OBJECT","properties":{"segments":{"type":"ARRAY","items":{
    "type":"OBJECT","properties":{
        "id":{"type":"INTEGER"}, "start":{"type":"NUMBER"}, "end":{"type":"NUMBER"},
        "zh":{"type":"STRING"}, "transcript_confidence":{"type":"NUMBER","nullable":True}},
    "required":["id","start","end","zh","transcript_confidence"]}}},"required":["segments"]}

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
            segment = Segment(index, start, end, item["zh"],
                transcript_confidence=normalize_confidence(item.get("transcript_confidence")))
        except ValueError:
            raise TranscriptValidationError(f"dòng {index}, confidence không hợp lệ") from None
        segments.append(segment)
    segments.sort(key=lambda s: (s.start, s.end, s.id))
    for index, segment in enumerate(segments, 1):
        segment.id = index
    _detect_timing_overlaps(segments)
    return segments


def _detect_timing_overlaps(segments):
    """Mark only timestamp intersections; identity metadata is deliberately ignored."""
    ordered = sorted(segments, key=lambda row: (row.start, row.end, row.id))
    for row in ordered:
        row.overlap = False
        row.overlap_group = None
        row.overlap_type = "NONE"
        row.overlap_diagnostics = []
    group = 0
    for index, row in enumerate(ordered):
        overlapping = [other for other in ordered[index + 1:] if other.start < row.end and other.end > row.start]
        if not overlapping:
            continue
        group += 1
        group_id = f"OVERLAP_{group:03d}"
        members = [row, *overlapping]
        for member in members:
            member.overlap = True
            member.overlap_group = group_id
            member.overlap_type = "LEGITIMATE_OVERLAP"
            member.overlap_diagnostics = ["Timestamp overlap"]


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
    def __init__(self, key_provider, model, cache_directory, retry_count=2, client_factory=OpenRouterTranscriptionClient,
                 timing_retry_model="assemblyai/universal-3-5-pro"):
        self.key_provider = key_provider
        self.model = model
        self.cache_directory = Path(cache_directory)
        self.retry_count = retry_count
        self.client_factory = client_factory
        self.timing_retry_model = timing_retry_model
        self.last_raw_result = None
        self.language_stats = {}
        self.timing_manifest = []

    @staticmethod
    def _apply_language_normalization(rows):
        """Normalize only high-confidence non-lexical foreign vocal sounds."""
        output, mismatch = [], False
        stats = {"language_mismatch": 0, "isolated_foreign_script": 0,
                 "vocalization_normalized": 0}
        for row, assessment in zip(rows, assess_rows(rows, project_language="zh")):
            if assessment.isolated_foreign_script:
                stats["isolated_foreign_script"] += 1
            if assessment.classification == "VOCALIZATION":
                output.append(replace(row, zh=assessment.normalized_text,
                                      transcript_language_status="VOCALIZATION_NORMALIZED",
                                      transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh))
                stats["vocalization_normalized"] += 1
            elif assessment.classification == "LANGUAGE_MISMATCH":
                output.append(replace(row, transcript_language_status="LANGUAGE_MISMATCH",
                                      transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh))
                stats["language_mismatch"] += 1
                mismatch = True
            else:
                output.append(replace(row, transcript_language_status="PASS",
                                      transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh))
        return output, mismatch, stats

    def _language_retry(self, client, audio_bytes, duration, region, source_rows, *, cancel, progress):
        """Retry one foreign-script region once, with independent durable cache."""
        import hashlib
        retry_key = content_hash({"audio": hashlib.sha256(audio_bytes).hexdigest(), "model": self.model,
                                  "language": "zh", "region": [round(region.start, 6), round(region.end, 6)],
                                  "version": LANGUAGE_RETRY_VERSION})
        path = self.cache_directory / "language_retry" / f"{retry_key}.json"
        state = None
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            state = None
        if isinstance(state, dict) and state.get("status") == "completed":
            payload = state.get("response")
            cache_hit = 1
        elif isinstance(state, dict) and state.get("status") == "manual_review":
            return [replace(row, transcript_language_status="NEEDS_MANUAL_REVIEW",
                            transcript_language_retry_count=1,
                            transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh) for row in source_rows], False, {
                                         "retry_regions": 0, "retry_success": 0,
                                         "retry_failed": 1, "retry_cache_hits": 1}
        else:
            cache_hit = 0
            try:
                payload = client.transcribe_json(
                    audio_bytes,
                    PROMPT + "\nMandatory language constraint: transcribe Mandarin Chinese only. "
                    "Do not render non-lexical vocal sounds as Japanese, Korean, or English.",
                    SCHEMA, self.model, cancel=cancel, progress=progress)
                atomic_json(path, {"status": "completed", "response": payload})
            except GeminiError as exc:
                atomic_json(path, {"status": "manual_review", "error": type(exc).__name__})
                return [replace(row, transcript_language_status="NEEDS_MANUAL_REVIEW",
                                transcript_language_retry_count=1,
                                transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh) for row in source_rows], False, {
                    "retry_regions": 1, "retry_success": 0, "retry_failed": 1, "retry_cache_hits": cache_hit}
        try:
            candidate = validate_response(payload, duration)
            candidate, mismatch, _ = self._apply_language_normalization(candidate)
        except (ValueError, TypeError, KeyError, GeminiError):
            mismatch = True
            candidate = source_rows
        if not mismatch:
            return [replace(row, transcript_language_retry_count=1) for row in candidate], True, {
                                     "retry_regions": 1, "retry_success": 1,
                                     "retry_failed": 0, "retry_cache_hits": cache_hit}
        retry_text = "\n".join(row.zh for row in candidate).strip()
        atomic_json(path, {"status": "manual_review", "response": payload})
        return [replace(row, transcript_language_status="NEEDS_MANUAL_REVIEW",
                        transcript_language_retry_count=1,
                        transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh,
                        transcript_language_retry_text=retry_text) for row in source_rows], False, {
            "retry_regions": 1, "retry_success": 0, "retry_failed": 1, "retry_cache_hits": cache_hit}

    def _timing_retry(self, client, audio_bytes, duration, region, primary, primary_quality, *, cancel, progress):
        """One bounded OpenRouter-only re-transcription for bad timing evidence."""
        import hashlib
        key = content_hash({"audio": hashlib.sha256(audio_bytes).hexdigest(), "model": self.timing_retry_model,
                            "language": "zh", "region": [round(region.start, 6), round(region.end, 6)],
                            "version": "timing-retry-v1"})
        path = self.cache_directory / "timing_retry" / f"{key}.json"
        payload = None
        try:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8")).get("response")
            if not isinstance(payload, dict):
                payload = client.transcribe_json(audio_bytes, PROMPT, SCHEMA, self.timing_retry_model,
                                                 cancel=cancel, progress=progress)
                atomic_json(path, {"response": payload})
            candidate = validate_response(payload, duration)
            quality = evaluate_timing(candidate, payload.get("words"), 0.0, duration)
        except (GeminiError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return primary, primary_quality, False
        # A retry is selected only if it has usable acoustic timestamps.
        if quality.status in {TRUSTED, PARTIAL}:
            return candidate, quality, True
        return primary, primary_quality, False

    def transcribe(self, audio_path, *, cancel=None, progress=None):
        cancel = cancel or Event()
        report = progress or (lambda text: None)
        client = None
        result = []
        raw_segments = []
        raw_words = []
        languages = []
        capabilities = set()
        language_stats = {"language_mismatch": 0, "isolated_foreign_script": 0,
                          "vocalization_normalized": 0, "retry_regions": 0,
                          "retry_success": 0, "retry_failed": 0, "retry_cache_hits": 0}
        try:
            with wave.open(str(audio_path), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError("Audio phải là WAV PCM 16-bit mono 16 kHz")
                source_duration = audio.getnframes() / audio.getframerate()
                regions = detect_speech_regions(audio_path)
                vad_fallback = not regions
                regions = regions or fallback_regions(source_duration)
                total = len(regions)
                log.info("[TRANSCRIPT TRACE] source_audio_duration=%.6f speech_regions=%d vad_fallback=%s",
                         source_duration, total, vad_fallback)
                for chunk_index, region in enumerate(regions):
                    check_cancel(cancel)
                    audio.setpos(int(region.start * audio.getframerate()))
                    raw = audio.readframes(int((region.end - region.start) * audio.getframerate()))
                    duration = len(raw) / (audio.getframerate() * audio.getsampwidth())
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as chunk:
                        chunk.setnchannels(1); chunk.setsampwidth(2); chunk.setframerate(16000); chunk.writeframes(raw)
                    import hashlib
                    key = content_hash({"audio": hashlib.sha256(buffer.getvalue()).hexdigest(),
                        "model": self.model, "prompt": PROMPT, "schema": SCHEMA, "version": PROMPT_VERSION,
                        "vad_version": VAD_VERSION, "region": [round(region.start, 6), round(region.end, 6)],
                        "timestamp_policy": "region_relative"})
                    path = self.cache_directory / f"{key}.json"
                    state = None
                    if path.exists():
                        try:
                            state = json.loads(path.read_text(encoding="utf-8"))
                            if state.get("status") == "completed":
                                response = state["response"]
                                local = validate_response(response, duration)
                                for cached, segment in zip(response["segments"], local):
                                    if cached.get("tts_cache_key"):
                                        segment.tts_cache_key = cached["tts_cache_key"]
                            else:
                                state = None
                        except (ValueError, KeyError, AttributeError, GeminiError):
                            state = None
                    chunk_start = region.start
                    chunk_end = region.start + duration
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
                                        PROMPT + f"\nTARGET AUDIO duration: {duration:.6f} seconds." + correction,
                                        SCHEMA, self.model, cancel=cancel, progress=report)
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
                                    if isinstance(payload, dict):
                                        for metadata_key in ("language", "words", "transcription_capabilities",
                                                             "transcription_provider"):
                                            if metadata_key in payload:
                                                response[metadata_key] = payload[metadata_key]
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
                    primary_quality = evaluate_timing(local, response.get("words"), 0.0, duration)
                    selected_source = "PRIMARY"
                    timing_retry_used = False
                    if primary_quality.status not in {TRUSTED, PARTIAL}:
                        if client is None:
                            client = self.client_factory(self.key_provider())
                        local, selected_quality, timing_retry_used = self._timing_retry(
                            client, buffer.getvalue(), duration, region, local, primary_quality,
                            cancel=cancel, progress=report)
                        if timing_retry_used:
                            selected_source = "RETRY"
                            response = {"segments": [{"start": row.start, "end": row.end, "zh": row.zh}
                                                     for row in local]}
                    else:
                        selected_quality = primary_quality
                    provenance = (("RETRY_" if timing_retry_used else "PRIMARY_")
                                  + selected_quality.timestamp_capability
                                  if selected_quality.timestamp_capability in {"WORD", "SEGMENT"}
                                  else "COARSE_ONLY")
                    if selected_quality.status not in {TRUSTED, PARTIAL}:
                        provenance = "COARSE_ONLY"
                    local = [replace(row, transcript_timing_provenance=provenance,
                                     transcript_timing_confidence=(selected_quality.valid_ratio
                                                                    if provenance != "COARSE_ONLY" else 0.0),
                                     transcript_language_status=("NEEDS_MANUAL_REVIEW"
                                         if provenance == "COARSE_ONLY" else row.transcript_language_status))
                             for row in local]
                    self.timing_manifest.append({
                        "region_id": chunk_index + 1, "coarse_start": region.start, "coarse_end": region.end,
                        "primary_model": self.model, "primary_timestamp_type": primary_quality.timestamp_capability,
                        "primary_quality": primary_quality.status, "primary_span": primary_quality.span,
                        "retry_used": timing_retry_used, "retry_model": self.timing_retry_model if timing_retry_used else "",
                        "retry_quality": selected_quality.status if timing_retry_used else "",
                        "retry_span": selected_quality.span if timing_retry_used else 0.0,
                        "selected_source": selected_source,
                        "timing_source": provenance,
                        "status": selected_quality.status,
                    })
                    local, mismatch, local_language_stats = self._apply_language_normalization(local)
                    if provenance == "COARSE_ONLY":
                        local = [replace(row, transcript_language_status="NEEDS_MANUAL_REVIEW") for row in local]
                    for name, value in local_language_stats.items():
                        language_stats[name] += value
                    if mismatch:
                        if client is None:
                            client = self.client_factory(self.key_provider())
                        local, accepted, retry_stats = self._language_retry(
                            client, buffer.getvalue(), duration, region, local, cancel=cancel, progress=report)
                        for name, value in retry_stats.items():
                            language_stats[name] += value
                        if accepted:
                            # Count the accepted retry output, not the rejected foreign first pass.
                            _, _, accepted_stats = self._apply_language_normalization(local)
                            for name in ("isolated_foreign_script", "vocalization_normalized"):
                                language_stats[name] += accepted_stats[name]
                    raw_local = list(local)
                    raw_word_local = normalize_word_timestamps(response.get("words"), duration)
                    if response.get("language"):
                        languages.append(response["language"])
                    capabilities.update(response.get("transcription_capabilities", []))
                    # The completed cache remains raw provider-neutral evidence.
                    # Segmentation is deliberately recomputed so algorithm changes
                    # never require another paid STT request.
                    local = TranscriptSegmentationNormalizer().normalize_region(
                        local, response.get("words"), response.get("language", "zh"), source="vad")
                    offset = region.start
                    raw_segments.extend(replace(s, id=len(raw_segments) + i,
                                                start=s.start + offset, end=s.end + offset)
                                        for i, s in enumerate(raw_local, 1))
                    raw_words.extend({**word, "start": word["start"] + offset,
                                      "end": word["end"] + offset}
                                     for word in raw_word_local)
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
            check_cancel(cancel)
            _detect_timing_overlaps(result)
            self.last_raw_result = {
                "segments": raw_segments,
                "words": raw_words,
                "language": languages[0] if languages else "zh",
                "transcription_capabilities": sorted(capabilities),
            }
            self.language_stats = language_stats
            return result
        finally:
            if client is not None:
                client.close()
