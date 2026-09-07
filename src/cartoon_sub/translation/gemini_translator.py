import json
from .context_models import StoryContext
from cartoon_sub.ai.gemini_client import GeminiError


class TranslationValidationError(GeminiError):
    def __init__(self, detail):
        super().__init__(detail, retryable=True)


def read_json(payload):
    if not isinstance(payload, str):
        return payload
    try:
        return json.loads(payload)
    except ValueError:
        raise TranslationValidationError("Kết quả không đọc được JSON") from None


def validate_translation(payload, ids):
    data = read_json(payload)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise TranslationValidationError("Thiếu segments trong bản dịch")
    result = {}
    for row in data["segments"]:
        if not isinstance(row, dict) or set(row) - {"id", "vi", "review_note"}:
            raise TranslationValidationError("Bản dịch có trường không cho phép; chỉ trả id, vi, review_note")
        sid = row.get("id")
        if type(sid) is not int or sid not in ids or sid in result:
            raise TranslationValidationError("Bản dịch chứa ID lạ, sai kiểu hoặc lặp ID")
        text, note = row.get("vi"), row.get("review_note", "")
        if not isinstance(text, str) or not text.strip() or not isinstance(note, str):
            raise TranslationValidationError(f"ID {sid}: bản dịch rỗng/sai kiểu")
        result[sid] = {"id": sid, "vi": text.strip(), "review_note": note.strip()}
    if set(result) != set(ids):
        missing = sorted(set(ids) - set(result))
        raise TranslationValidationError(f"Thiếu bản dịch cho ID {missing[:10]}")
    return [result[i] for i in ids]


def validate_context(payload, ids):
    try:
        data = read_json(payload)
        if not isinstance(data, dict) or set(data) != set(StoryContext().to_dict()):
            raise ValueError("Thiếu trường hồ sơ")
        return StoryContext.from_dict(data, set(ids)).to_dict()
    except (ValueError, TypeError):
        raise TranslationValidationError("Hồ sơ không đúng cấu trúc hoặc viện dẫn ID ngoài transcript đã đọc") from None


TRANSLATION_SCHEMA = {"type": "OBJECT", "properties": {"segments": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"id": {"type": "INTEGER"}, "vi": {"type": "STRING"},
    "review_note": {"type": "STRING"}}, "required": ["id", "vi", "review_note"]}}}, "required": ["segments"]}
