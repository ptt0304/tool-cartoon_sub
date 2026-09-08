from enum import StrEnum

class TranslationMode(StrEnum):
    FAITHFUL = "faithful"
    BALANCED_DUBBING = "balanced_dubbing"
    SYLLABLE_MATCH = "syllable_match"
    STRICT_ISO_SYLLABIC = "strict_iso_syllabic"
    TIME_FIT = "time_fit"
    SUBTITLE_NATURAL = "subtitle_natural"
    SHORT_DUB = "short_dub"

MODE_LABELS = {
    "faithful": "Ưu tiên sát nghĩa", "balanced_dubbing": "Cân bằng nghĩa / thời lượng",
    "syllable_match": "Ưu tiên khớp âm tiết", "strict_iso_syllabic": "Khớp âm tiết nghiêm ngặt",
    "time_fit": "Ưu tiên khớp thời lượng", "subtitle_natural": "Phụ đề tự nhiên", "short_dub": "Lồng tiếng ngắn"}
