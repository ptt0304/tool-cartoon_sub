"""Explicit-preview AI actions for the Master Dialogue timeline."""
from pathlib import Path

from cartoon_sub.ai.model_selection import AIModelSelection
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.translation.gemini_translator import (
    TRANSLATION_SCHEMA, TranslationValidationError, read_json, validate_translation,
)
from cartoon_sub.translation.requests import CachedRequests


QA_SCHEMA = {"type": "OBJECT", "properties": {"items": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {
        "id": {"type": "INTEGER"}, "status": {"type": "STRING", "enum": ["ok", "warning", "error"]},
        "issue_type": {"type": "STRING"}, "original": {"type": "STRING"},
        "suggested": {"type": "STRING"}, "target": {"type": "STRING", "enum": ["none", "vi_subtitle", "vi_dubbing"]},
        "reason": {"type": "STRING"},
    }, "required": ["id", "status", "issue_type", "original", "suggested", "target", "reason"]}}},
    "required": ["items"]}


def _rows(project, ids):
    chosen = set(ids)
    rows = []
    for item in project.utterances:
        if item.id in chosen:
            rows.append({"id": item.id, "start": item.start, "end": item.end,
                         "zh": item.zh, "vi_subtitle": item.vi_subtitle, "vi_dubbing": item.vi_dubbing})
    if {row["id"] for row in rows} != chosen:
        raise ValueError("Có dòng Master Dialogue không còn tồn tại")
    return rows


def _validate_qa(payload, ids):
    data = read_json(payload)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise TranslationValidationError("QA/QC thiếu items")
    result = {}
    for item in data["items"]:
        required = {"id", "status", "issue_type", "original", "suggested", "target", "reason"}
        if not isinstance(item, dict) or set(item) != required:
            raise TranslationValidationError("QA/QC có cấu trúc item không hợp lệ")
        item_id = item.get("id")
        if type(item_id) is not int or item_id not in ids or item_id in result:
            raise TranslationValidationError("QA/QC có ID lạ hoặc trùng")
        if item["status"] not in {"ok", "warning", "error"} or item["target"] not in {"none", "vi_subtitle", "vi_dubbing"}:
            raise TranslationValidationError("QA/QC có trạng thái không hợp lệ")
        if not all(isinstance(item[key], str) for key in required - {"id"}):
            raise TranslationValidationError("QA/QC có trường text không hợp lệ")
        result[item_id] = item
    if set(result) != set(ids):
        raise TranslationValidationError("QA/QC thiếu dòng được chọn")
    return [result[item_id] for item_id in ids]


class MasterDialogueAIService:
    def __init__(self, store):
        self.store = store

    def run(self, project, directory, ids, operation, selection, *, cancel=None, progress=None):
        if operation not in {"dubbing", "qa"}:
            raise ValueError("Master Dialogue AI operation không hợp lệ")
        selection = AIModelSelection.from_value(selection)
        if selection.provider != "openrouter" or not selection.model_id:
            raise ValueError("Chọn model AI hợp lệ trước khi chạy Master Dialogue")
        rows = _rows(project, ids)
        check_cancel(cancel)
        system = (
            "Bạn là biên tập viên lồng tiếng Việt. Chỉ trả JSON đúng schema. Không đổi thứ tự, "
            "tên riêng, thuật ngữ, phủ định, quan hệ hay sự kiện. Không tự áp dụng thay đổi."
        )
        if operation == "dubbing":
            prompt = ("Đề xuất VI Dubbing tự nhiên, dễ đọc và gọn hơn khi cần. Giữ nguyên nghĩa so với Chinese và "
                      "VI Subtitle. Trả translations, mỗi ID đúng một lần.\nROWS=" + repr(rows))
            schema, validate, label = TRANSLATION_SCHEMA, lambda value: validate_translation(value, list(ids)), "Master Dialogue Dubbing"
        else:
            prompt = ("Review từng dòng Master Dialogue: sai/mất/thêm ý, tên riêng, thuật ngữ, xưng hô, continuity, "
                      "văn phong và độ phù hợp dubbing. Với dòng OK dùng target=none và suggested rỗng. Chỉ đề xuất "
                      "khi chắc chắn; không tự sửa.\nROWS=" + repr(rows))
            schema, validate, label = QA_SCHEMA, lambda value: _validate_qa(value, list(ids)), "Master Dialogue QA/QC"
        settings = self.store.load()
        requests = CachedRequests(self.store, Path(directory) / "cache" / "master_dialogue_ai",
                                  selection.model_id, settings.retry_count, cancel, progress,
                                  text_client_factory("openrouter"), "openrouter")
        try:
            result, _ = requests.request(system, prompt, schema, validate, label)
        finally:
            requests.close()
        items = result if operation == "qa" else [
            {"id": row["id"], "status": "warning", "issue_type": "dubbing_optimization",
             "original": next(item["vi_dubbing"] for item in rows if item["id"] == row["id"]),
             "suggested": row["vi"], "target": "vi_dubbing", "reason": row["review_note"]}
            for row in result]
        project.master_dialogue_ai_results[operation] = {
            "selection": selection.to_dict(), "items": items,
        }
        ProjectManager().save(project, directory)
        return project, Path(directory)

    def apply(self, project, directory, operation, ids):
        record = project.master_dialogue_ai_results.get(operation, {})
        wanted, changed = set(ids), []
        by_id = {item.id: item for item in project.utterances}
        for proposal in record.get("items", []):
            item_id = proposal.get("id")
            target, suggested = proposal.get("target"), str(proposal.get("suggested") or "").strip()
            if item_id not in wanted or not suggested or target not in {"vi_subtitle", "vi_dubbing"}:
                continue
            row = by_id.get(item_id)
            if row and getattr(row, target) != suggested:
                setattr(row, target, suggested)
                if target == "vi_dubbing":
                    row.tts_generation_status = "stale"
                    row.tts_error = ""
                changed.append(item_id)
        if changed:
            project.final_audio_status = "stale"
            project.final_audio_fingerprint = ""
            ProjectManager().save(project, directory)
        return changed
