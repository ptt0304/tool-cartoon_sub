import json
from cartoon_sub.ai.language_contract import (
    story_context_user_facing_values,
    validate_user_facing_language,
)
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


def validate_translation_response(payload, ids):
    data = read_json(payload)
    continuity = data.get("continuity_updates", []) if isinstance(data, dict) else []
    uncertainties = data.get("uncertainties", []) if isinstance(data, dict) else []
    if isinstance(data, dict) and "translations" in data:
        data={"segments":data["translations"]}
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise TranslationValidationError("Thiếu segments trong bản dịch")
    if (not isinstance(continuity, list) or not isinstance(uncertainties, list)
            or any(not isinstance(item, (str, dict)) for item in continuity)
            or any(not isinstance(item, str) for item in uncertainties)):
        raise TranslationValidationError("Continuity/uncertainties sai kiểu")
    result = {}
    for row in data["segments"]:
        if not isinstance(row, dict) or set(row) - {"id", "vi", "confidence", "review_note", "meaning_preservation", "compressed"}:
            raise TranslationValidationError("Bản dịch có trường không cho phép; chỉ trả id, vi, review_note")
        sid = row.get("id")
        if type(sid) is not int or sid not in ids or sid in result:
            raise TranslationValidationError("Bản dịch chứa ID lạ, sai kiểu hoặc lặp ID")
        text, note = row.get("vi"), row.get("review_note", "")
        if not isinstance(text, str) or not text.strip() or not isinstance(note, str):
            raise TranslationValidationError(f"ID {sid}: bản dịch rỗng/sai kiểu")
        meaning=row.get("meaning_preservation","unknown")
        compressed=row.get("compressed",False)
        confidence = row.get("confidence", 0.0)
        if meaning not in ("high","medium","low","unknown") or type(compressed) is not bool:
            raise TranslationValidationError(f"ID {sid}: metadata meaning/compressed không hợp lệ")
        if type(confidence) not in (int, float) or not 0 <= float(confidence) <= 1:
            raise TranslationValidationError(f"ID {sid}: confidence không hợp lệ")
        result[sid] = {"id": sid, "vi": text.strip(), "review_note": note.strip(),
                       "meaning_preservation":meaning,"compressed":compressed,
                       "confidence": float(confidence)}
    if set(result) != set(ids):
        missing = sorted(set(ids) - set(result))
        raise TranslationValidationError(f"Thiếu bản dịch cho ID {missing[:10]}")
    validate_user_facing_language(
        [value for row in result.values() for value in (row["vi"], row["review_note"])],
        "Bản dịch/ghi chú AI",
    )
    return {"translations": [result[i] for i in ids],
            "continuity_updates": continuity, "uncertainties": uncertainties}


def validate_translation(payload, ids):
    """Compatibility adapter for callers that consume only translated rows."""
    return validate_translation_response(payload, ids)["translations"]


def validate_context(payload, ids):
    try:
        data = read_json(payload)
        if not isinstance(data, dict) or set(data) != set(StoryContext().to_dict()):
            raise ValueError("Thiếu trường hồ sơ")
        value = StoryContext.from_dict(data, set(ids)).to_dict()
        validate_user_facing_language(story_context_user_facing_values(value), "Story Context")
        return value
    except (ValueError, TypeError):
        raise TranslationValidationError("Hồ sơ không đúng cấu trúc hoặc viện dẫn ID ngoài transcript đã đọc") from None


TRANSLATION_SCHEMA = {"type": "OBJECT", "properties": {"translations": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"id": {"type": "INTEGER"}, "vi": {"type": "STRING"},
    "confidence": {"type": "NUMBER"}, "review_note": {"type": "STRING"},
    "meaning_preservation":{"type":"STRING","enum":["high","medium","low","unknown"]},
    "compressed":{"type":"BOOLEAN"}}, "required": ["id", "vi"]}},
    "continuity_updates": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "key": {"type": "STRING"}, "value": {"type": "STRING"}, "confidence": {"type": "NUMBER"}},
        "required": ["key", "value", "confidence"]}},
    "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["translations"]}
