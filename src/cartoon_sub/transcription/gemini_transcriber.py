import io
import json
import math
import wave
from dataclasses import asdict
from pathlib import Path
from threading import Event
from cartoon_sub.ai.gemini_client import GeminiClient, GeminiError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.subtitle.models import Segment

from cartoon_sub.prompts import read
from cartoon_sub.speaker.service import detect_overlaps
from dataclasses import replace
PROMPT_VERSION = "zh-utterance-v2"
SPEAKER_DETECTION_VERSION = "voice-reference-v1"
CHUNK_SECONDS = 180
PROMPT = read("transcription_v2.txt")
SCHEMA = {"type":"OBJECT", "properties":{"segments":{"type":"ARRAY","items":{
    "type":"OBJECT","properties":{
        "id":{"type":"INTEGER"}, "start":{"type":"NUMBER"}, "end":{"type":"NUMBER"},
        "speaker_id":{"type":"STRING"}, "zh":{"type":"STRING"},
        "overlap":{"type":"BOOLEAN"}, "overlap_group":{"type":"STRING","nullable":True},
        "speaker_confidence":{"type":"NUMBER","nullable":True},
        "transcript_confidence":{"type":"NUMBER","nullable":True}},
    "required":["id","start","end","speaker_id","zh","overlap","overlap_group","speaker_confidence","transcript_confidence"]}}},"required":["segments"]}


class TranscriptValidationError(GeminiError):
    def __init__(self, detail):
        super().__init__(f"Transcript không hợp lệ: {detail}. Chưa thay subtitle hiện tại.", retryable=True)


def parse_timestamp(value, row, field):
    # Only explicit formats are converted. Never guess whether a number means ms or seconds.
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
        if "zh" not in item and "text" in item: item = {**item, "zh":item["text"]}
        if not all(k in item for k in ("start", "end", "zh")):
            raise TranscriptValidationError(f"dòng {index} thiếu start/end/zh")
        # IDs from transcription are labels, unlike translation IDs. Assign our own IDs.
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
            segment=Segment(index,start,end,item["zh"],speaker_id=item.get("speaker_id","SPK_UNKNOWN"),
                speaker_confidence=item.get("speaker_confidence"),transcript_confidence=item.get("transcript_confidence"))
        except ValueError:
            raise TranscriptValidationError(f"dòng {index}, speaker_id hoặc confidence không hợp lệ") from None
        segments.append(segment)
    # Sorting does not serialize or change any interval.
    segments.sort(key=lambda s:(s.start,s.end,s.id))
    for index,s in enumerate(segments,1): s.id=index
    detect_overlaps(segments)
    return segments


class GeminiTranscriber:
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
                for chunk_index in range(total):
                    check_cancel(cancel)
                    raw = audio.readframes(frames_per_chunk)
                    duration = len(raw) / 32000
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as chunk:
                        chunk.setnchannels(1)
                        chunk.setsampwidth(2)
                        chunk.setframerate(16000)
                        chunk.writeframes(raw)
                    import hashlib
                    key = content_hash({"audio": hashlib.sha256(buffer.getvalue()).hexdigest(),
                        "model": self.model, "prompt": PROMPT, "schema": SCHEMA, "version": PROMPT_VERSION,
                        "speaker_version":SPEAKER_DETECTION_VERSION, "references":{k:hashlib.sha256(v).hexdigest() for k,v in references.items()}})
                    path = self.cache_directory / f"{key}.json"
                    state = None
                    if path.exists():
                        try:
                            state = json.loads(path.read_text(encoding="utf-8"))
                            if state.get("status") == "completed":
                                local = validate_response(state["response"], duration)
                            else:
                                state = None
                        except (ValueError, KeyError, AttributeError, GeminiError):
                            state = None
                    if state is not None:
                        report(f"Đoạn {chunk_index + 1}/{total}: dùng cache, không gọi Gemini")
                    else:
                        if client is None:
                            client = self.client_factory(self.key_provider())
                        correction = ""
                        for attempt in range(self.retry_count + 1):
                            check_cancel(cancel)
                            report(f"Gemini transcription: đoạn {chunk_index + 1}/{total}, lần {attempt + 1}")
                            atomic_json(path, {"status": "running", "attempt": attempt + 1})
                            payload = None
                            try:
                                payload = client.transcribe_json(buffer.getvalue(), PROMPT + f"\nTARGET AUDIO duration: {duration:.6f} seconds. Known IDs: {sorted({s.speaker_id for s in result})}" + correction,
                                    SCHEMA, self.model, cancel=cancel, references=references)
                                local = validate_response(payload, duration)
                                check_cancel(cancel)
                                response = {"segments": [{k: v for k, v in asdict(s).items() if k != "vi"} for s in local]}
                                atomic_json(path, {"status": "completed", "response": response})
                                break
                            except GeminiError as exc:
                                failure = {"status": "failed", "attempt": attempt + 1, "error": str(exc),
                                           "duration": duration, "chunk": chunk_index + 1}
                                if isinstance(exc, TranscriptValidationError):
                                    # Keep the response locally for diagnosis, outside normal logs/project subtitle data.
                                    failure["raw_response"] = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                                    correction = ("\nYour previous response failed local validation: " + str(exc)
                                                  + "\nTranscribe the same audio again. Correct the indicated field; "
                                                    "use decimal seconds within the audio duration, short phrases, "
                                                    "and non-empty Chinese text. Do not invent or drop speech to bypass validation.")
                                atomic_json(path, failure)
                                if not exc.retryable or attempt >= self.retry_count:
                                    raise
                                report(f"Đoạn {chunk_index + 1}: thử lại sau {2 ** (attempt + 1)} giây…")
                                if cancel.wait(2 ** (attempt + 1)):
                                    check_cancel(cancel)
                    offset = chunk_index * CHUNK_SECONDS
                    base_id = len(result)
                    result.extend(replace(s,id=base_id+i,start=s.start+offset,end=s.end+offset)
                                  for i,s in enumerate(local,1))
                    # Small clean audio exemplars carry identity across chunks; no voice embedding claims.
                    for s in local:
                        if s.speaker_id != "SPK_UNKNOWN" and s.speaker_id not in references and not s.overlap and len(references)<8:
                            sample=raw[int(s.start*16000)*2:int(min(s.end,s.start+3)*16000)*2]
                            if len(sample)<16000: continue
                            voice=io.BytesIO()
                            with wave.open(voice,"wb") as out:
                                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(sample)
                            references[s.speaker_id]=voice.getvalue()
            check_cancel(cancel)
            detect_overlaps(result)
            return result
        finally:
            if client is not None:
                client.close()
