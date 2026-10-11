"""OpenRouter-only OCR of already detected Video subtitle visual events."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from cartoon_sub.ai.model_resolver import AIModelResolver
from cartoon_sub.ai.openrouter_client import OpenRouterClient
from cartoon_sub.media.process import CancelledError, run_process
from cartoon_sub.project.cache import atomic_json, content_hash, file_hash, check_cancel
from cartoon_sub.project.paths import ProjectPaths
from cartoon_sub.transcription.video.roi import roi_to_pixels
from cartoon_sub.transcription.video.ocr_workload import OcrWorkloadProgress, OCR_WORKLOAD_VERSION


OCR_PROMPT_VERSION = "video-subtitle-ocr-v1"
OCR_SYSTEM = "Bạn là OCR phụ đề. Chỉ đọc chữ Trung Quốc đang nhìn thấy trong ảnh crop phụ đề."
OCR_PROMPT = (
    "Đọc CHỈ phụ đề tiếng Trung nhìn thấy. Giữ dấu câu và thứ tự dòng. Không dịch, "
    "không mô tả ảnh, không suy đoán chữ thiếu, không thêm tên người nói. Nếu không có phụ đề, "
    "trả has_subtitle=false và text rỗng."
)
OCR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["has_subtitle", "text"],
    "properties": {
        "has_subtitle": {"type": "boolean"},
        "text": {"type": "string"},
    },
}


def normalize_ocr_text(value):
    if not isinstance(value, str):
        raise ValueError("OCR text phải là chuỗi")
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[ \t]{2,}", " ", row).strip() for row in value.strip().split("\n"))


def language_status(text):
    han = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    foreign = len(re.findall(r"[\uac00-\ud7af\u3040-\u30ff]", text))
    if foreign >= 3 and foreign > han:
        return "SUSPICIOUS_FOREIGN_SCRIPT"
    return "PASS"


def validate_ocr_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {"has_subtitle", "text"}:
        raise ValueError("OCR response không đúng schema")
    has_subtitle, raw_text = payload["has_subtitle"], payload["text"]
    if type(has_subtitle) is not bool or not isinstance(raw_text, str):
        raise ValueError("OCR response không đúng kiểu dữ liệu")
    text = normalize_ocr_text(raw_text)
    if has_subtitle and not text:
        raise ValueError("OCR báo có phụ đề nhưng text rỗng")
    if not has_subtitle and text:
        raise ValueError("OCR báo không có phụ đề nhưng text không rỗng")
    return {"has_subtitle": has_subtitle, "raw_text": raw_text,
            "normalized_text": text, "language_status": language_status(text)}


def _usage_cost(usage):
    if not isinstance(usage, dict):
        return None
    for key in ("cost", "total_cost", "generation_cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


class VideoSubtitleOcrService:
    def __init__(self, settings_store, *, resolver=None, client_factory=None, cropper=None):
        self.settings_store = settings_store
        self.resolver = resolver or AIModelResolver(settings_store)
        self.client_factory = client_factory or (lambda credentials, models: OpenRouterClient(credentials, models=models))
        self.cropper = cropper or self._extract_crop

    @staticmethod
    def _extract_crop(project, crop, moment, target, *, cancel=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        run_process([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{float(moment):.6f}", "-i", project.source_video_path,
            "-vf", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}", "-frames:v", "1", str(target),
        ], cancel)
        if not target.is_file():
            raise ValueError("Không thể lấy crop phụ đề Video cho OCR")
        return target.read_bytes()

    def _cache_identity(self, source_hash, project, event, crop, model):
        crop_identity = content_hash({
            "source": source_hash, "region_id": event.get("region_id"), "crop": crop,
            "representative_time": event["representative_time"], "crop_version": "ffmpeg-png-v1",
        })
        return content_hash({
            "source": source_hash, "region_id": event.get("region_id"),
            "event_id": event["event_id"], "start": event["refined_start"], "end": event["refined_end"],
            "representative_time": event["representative_time"], "crop": crop_identity,
            "visual_signature_id": event.get("visual_signature_id", ""),
            "model": model, "prompt_schema_version": OCR_PROMPT_VERSION,
        })

    @staticmethod
    def load_workload(directory):
        """Read only a complete Phase 7B workload; malformed files are re-detected."""
        path = ProjectPaths(directory).transcript_video_cache_dir / "ocr_workload.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            required = {"event_index", "event_id", "region_id", "refined_start", "refined_end",
                        "representative_time", "status"}
            if (payload.get("version") != OCR_WORKLOAD_VERSION or not isinstance(payload.get("events"), list)
                    or payload.get("total_events") != len(payload["events"])
                    or not all(isinstance(row, dict) and required.issubset(row) for row in payload["events"])):
                return None
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
    def run(self, project, directory, *, cancel=None, progress=None):
        paths = ProjectPaths(directory)
        workload_path = paths.transcript_video_cache_dir / "ocr_workload.json"
        workload = self.load_workload(directory)
        if workload is None:
            raise ValueError("Chưa có OCR workload hợp lệ; hãy chạy phát hiện phụ đề Video cục bộ trước.")
        events = workload["events"]
        resolution = self.resolver.resolve("VIDEO_SUBTITLE_OCR", "transcript_video", ("vision_frames",))
        settings = self.settings_store.load()
        catalog = (self.settings_store.openrouter_catalog_cache() or {}).get("models", [])
        client = self.client_factory(self.settings_store.openrouter_key_pool(), catalog)
        source_hash = file_hash(project.source_video_path, cancel)
        from cartoon_sub.transcription.video.regions import regions_for_project
        regions = {row["id"]: row for row in regions_for_project(project)}
        ocr_dir = paths.transcript_video_cache_dir / "ocr"
        metrics = {"vision_requests": 0, "vision_retries": 0, "vision_images_sent": 0,
                   "cache_hits": 0, "cost": 0.0, "cost_available": 0,
                   "ocr_total_events": len(events), "ocr_cache_hits": 0, "ocr_api_events": 0,
                   "ocr_retries": 0, "input_tokens": 0, "output_tokens": 0, "total_cost": 0.0,
                   "ocr_success": 0, "ocr_empty": 0, "ocr_failed": 0,
                   "foreign_script_suspicious": 0}
        results = []
        tracker = OcrWorkloadProgress(len(events))
        report = progress or (lambda _text: None)
        report(tracker.text())
        try:
            for request_index, event in enumerate(events, 1):
                check_cancel(cancel)
                if isinstance(event, dict): event.setdefault("region_id", "region_001")
                required = {"event_id", "region_id", "refined_start", "refined_end", "representative_time"}
                if not isinstance(event, dict) or not required.issubset(event):
                    raise ValueError("visual_events.json chứa event không hợp lệ")
                region = regions.get(event["region_id"])
                if region is None: raise ValueError("OCR event có vùng phụ đề không hợp lệ")
                crop = roi_to_pixels(region["roi"], project.metadata.get("width"), project.metadata.get("height"))
                before = (event["refined_start"], event["refined_end"])
                cache_key = self._cache_identity(source_hash, project, event, crop, resolution.model_id)
                cache_path = ocr_dir / f"{cache_key}.json"
                result = self._load_cached(cache_path)
                if result is not None:
                    result["cache_hit"] = True
                    metrics["cache_hits"] += 1; metrics["ocr_cache_hits"] += 1
                else:
                    metrics["ocr_api_events"] += 1
                    result = self._ocr_one(client, project, crop, event, resolution.model_id, request_index,
                                           ocr_dir, cache_path, cancel=cancel, progress=None, metrics=metrics)
                    result["cache_hit"] = False
                if before != (event["refined_start"], event["refined_end"]):
                    raise AssertionError("OCR không được thay đổi timing visual event")
                results.append(result)
                if result["ocr_status"] == "OCR_SUCCESS": metrics["ocr_success"] += 1
                elif result["ocr_status"] == "OCR_EMPTY": metrics["ocr_empty"] += 1
                else: metrics["ocr_failed"] += 1
                if result.get("language_status") == "SUSPICIOUS_FOREIGN_SCRIPT":
                    metrics["foreign_script_suspicious"] += 1
                terminal = {"OCR_SUCCESS": "SUCCESS", "OCR_EMPTY": "OCR_EMPTY", "OCR_FAILED": "FAILED"}[result["ocr_status"]]
                event["status"] = terminal
                atomic_json(workload_path, workload)
                report(tracker.terminal(event["event_id"], terminal))
        except CancelledError:
            atomic_json(workload_path, workload)
            raise
        finally:
            if hasattr(client, "close"):
                client.close()
        adjacent_same = sum(1 for first, second in zip(results, results[1:])
                            if first.get("ocr_status") == second.get("ocr_status") == "OCR_SUCCESS"
                            and first.get("normalized_text") == second.get("normalized_text"))
        payload = {
            "version": OCR_PROMPT_VERSION, "model": resolution.model_id, "model_source": resolution.source,
            "workload_source": "ocr_workload.json", "regions": list(regions.values()),
            "events": results, "metrics": {**metrics, "adjacent_same_text_pairs": adjacent_same},
        }
        atomic_json(paths.transcript_video_cache_dir / "ocr_results.json", payload)
        return payload

    @staticmethod
    def _load_cached(path):
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "completed":
                return None
            result = dict(payload["result"])
            validate_ocr_payload({"has_subtitle": result["has_subtitle"], "text": result["raw_text"]})
            return result
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _ocr_one(self, client, project, crop, event, model, request_index, ocr_dir, cache_path, *, cancel, progress, metrics):
        temporary = ocr_dir / f".{uuid4().hex}.png"
        try:
            image = self.cropper(project, crop, event["representative_time"], temporary, cancel=cancel)
            reason = ""
            for attempt in range(2):
                if progress:
                    progress(f"Video OCR {request_index}: event {event['event_id']} ({attempt + 1}/2)")
                metrics["vision_requests"] += 1; metrics["vision_images_sent"] += 1
                try:
                    payload = client.generate_multimodal_json(
                        OCR_SYSTEM, OCR_PROMPT + reason, [("image/png", image)], OCR_SCHEMA, model,
                        cancel=cancel, progress=progress)
                    parsed = validate_ocr_payload(payload)
                    retry_reason = ""
                    if not parsed["has_subtitle"] and float(event.get("visual_score", 0)) >= .05:
                        retry_reason = "strong_visual_empty"
                    elif parsed["language_status"] == "SUSPICIOUS_FOREIGN_SCRIPT":
                        retry_reason = "foreign_script"
                    if retry_reason and attempt == 0:
                        metrics["vision_retries"] += 1
                        reason = "\nLần trước không hợp lệ cho OCR phụ đề; chỉ trả schema yêu cầu, không mô tả ảnh."
                        continue
                    status = "OCR_SUCCESS" if parsed["has_subtitle"] else "OCR_EMPTY"
                    cost = _usage_cost(getattr(client, "last_usage", None))
                    if cost is not None:
                        metrics["cost"] += cost; metrics["cost_available"] += 1
                    result = {
                        "event_id": event["event_id"], "refined_start": event["refined_start"],
                        "region_id": event["region_id"],
                        "refined_end": event["refined_end"], "representative_time": event["representative_time"],
                        "model": model, "ocr_status": status, **parsed, "request_index": request_index,
                        "retry_count": attempt, "cost": cost,
                    }
                    atomic_json(cache_path, {"status": "completed", "result": result})
                    return result
                except ValueError as exc:
                    if attempt == 0:
                        metrics["vision_retries"] += 1
                        reason = "\nTrả lại JSON đúng schema OCR, không giải thích."
                        continue
                    return self._failed(event, model, request_index, attempt, str(exc))
                except CancelledError:
                    raise
                except Exception as exc:
                    if attempt == 0:
                        metrics["vision_retries"] += 1; metrics["ocr_retries"] += 1
                        reason = "\nTrả lại JSON đúng schema OCR, không giải thích."
                        continue
                    return self._failed(event, model, request_index, attempt, str(exc))
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _failed(event, model, request_index, retry_count, error):
        return {
            "event_id": event["event_id"], "refined_start": event["refined_start"],
            "region_id": event.get("region_id", ""),
            "refined_end": event["refined_end"], "representative_time": event["representative_time"],
            "model": model, "ocr_status": "OCR_FAILED", "has_subtitle": False,
            "raw_text": "", "normalized_text": "", "language_status": "ERROR",
            "request_index": request_index, "retry_count": retry_count, "cost": None,
            "error": str(error)[:500],
        }
