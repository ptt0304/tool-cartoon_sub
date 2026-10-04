"""Application-wide language contract for human-readable AI output."""
from __future__ import annotations

import re
import unicodedata

from cartoon_sub.ai.gemini_client import GeminiError


USER_FACING_AI_LANGUAGE = "vi"
USER_FACING_AI_LANGUAGE_CONTRACT_VERSION = 1
USER_FACING_AI_INSTRUCTION = (
    "Trả toàn bộ nội dung mô tả, giải thích, tóm tắt, lập luận, quan hệ và chỗ "
    "chưa chắc dành cho người dùng bằng tiếng Việt. Giữ nguyên JSON/schema, ID, "
    "enum/status, SPK_xx, CHAR_xx, model ID và Chinese source. Tên riêng và alias "
    "nguồn có thể giữ nguyên."
)
USER_FACING_AI_CORRECTION = (
    "Bạn đã trả lời sai ngôn ngữ. Hãy giữ nguyên JSON/schema/ID/source text và "
    "trả toàn bộ nội dung mô tả cho người dùng bằng tiếng Việt."
)


class UserFacingLanguageError(GeminiError):
    """The response is structurally valid but clearly violates the UI language."""

    def __init__(self, detail):
        super().__init__(detail, retryable=True)


_ENGLISH_WORDS = {
    "a", "an", "and", "are", "as", "at", "background", "because", "between",
    "character", "characters", "description", "during", "evidence", "for", "from",
    "girl", "he", "her", "his", "in", "is", "it", "likely", "man", "narration",
    "of", "on", "outdoor", "relationship", "river", "scene", "setting", "she",
    "speaker", "summary", "the", "their", "there", "they", "this", "to",
    "uncertain", "uncertainty", "visible", "was", "woman", "with",
}
_VIETNAMESE_WORDS = {
    "anh", "bên", "bối", "cách", "cảnh", "chắc", "chưa", "chị", "cho", "con",
    "cô", "có", "của", "đang", "đây", "được", "gọi", "giữa", "không", "khi",
    "là", "mèo", "một", "ngoài", "người", "nhân", "này", "nói", "quan", "sông",
    "sự", "tại", "thể", "theo", "tóm", "trong", "trời", "và", "với",
}
_ID = re.compile(r"\b(?:SPK|CHAR|TERM)_\w+\b", re.IGNORECASE)
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def _fold(word):
    value = unicodedata.normalize("NFD", word.casefold())
    return "".join(char for char in value if unicodedata.category(char) != "Mn").replace("đ", "d")


_ENGLISH_FOLDED = {_fold(word) for word in _ENGLISH_WORDS}
_VIETNAMESE_FOLDED = {_fold(word) for word in _VIETNAMESE_WORDS}


def is_predominantly_wrong_language(values):
    """Detect obvious English prose while tolerating aliases, IDs and short text."""
    text = " ".join(str(value) for value in values if isinstance(value, str) and value.strip())
    text = _ID.sub(" ", text)
    words = _WORDS.findall(text)
    latin_words = [word for word in words if any("a" <= char <= "z" for char in _fold(word))]
    if len(latin_words) < 6:
        return False
    folded = [_fold(word) for word in latin_words]
    english = sum(word in _ENGLISH_FOLDED for word in folded)
    vietnamese = sum(word in _VIETNAMESE_FOLDED for word in folded)
    vietnamese += sum(any(unicodedata.combining(mark)
                          for mark in unicodedata.normalize("NFD", word)[1:])
                      for word in latin_words)
    return english >= 3 and english > vietnamese * 1.5


def validate_user_facing_language(values, label="AI output"):
    if is_predominantly_wrong_language(values):
        raise UserFacingLanguageError(
            f"{label} có nội dung mô tả chủ yếu không phải tiếng Việt"
        )


def story_context_user_facing_values(context):
    """Extract natural-language values, excluding source aliases and machine fields."""
    values = [context.get(key, "") for key in ("setting", "summary", "narration")]
    values.extend(context.get("uncertainties", []))
    for row in context.get("characters", []):
        values.extend(row.get(key, "") for key in ("target", "notes"))
    for row in context.get("terms", []):
        values.extend(row.get(key, "") for key in ("target", "notes"))
    for row in context.get("address_rules", []):
        values.extend(row.get(key, "") for key in ("self_term", "address_term", "condition"))
    for row in context.get("character_profiles", []):
        values.extend((row.get("role", ""), row.get("visual_description", "")))
        values.extend(row.get("relationships", []))
    for row in context.get("speaker_character_mappings", []):
        values.append(row.get("notes", ""))
    for row in context.get("visual_contexts", []):
        values.append(row.get("notes", ""))
        values.extend(item.get("description", "") for item in row.get("visible_objects", []))
    return values
