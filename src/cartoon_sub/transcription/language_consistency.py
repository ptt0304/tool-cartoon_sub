"""Deterministic script checks for a Mandarin transcript timeline."""
from dataclasses import dataclass
from dataclasses import replace


_VOCAL_HAN = set("啊嗯哦呀哎唉呜哈呵嘿")
_VOCAL_LATIN = {"ah", "oh", "hmm", "hm", "hey", "ha"}
_ACRONYMS = {"ai", "api", "cpu", "gpu", "ok", "vip"}
_VOCAL_HANGUL = set("아어오으음")
_VOCAL_KANA = set("あいうえおんふたし")


@dataclass(frozen=True)
class ScriptProfile:
    han: int = 0
    hiragana: int = 0
    katakana: int = 0
    hangul: int = 0
    latin: int = 0
    digit: int = 0
    punctuation: int = 0
    other: int = 0

    @property
    def foreign(self):
        return self.hiragana + self.katakana + self.hangul

    @property
    def meaningful(self):
        return self.han + self.foreign + self.latin + self.digit

    def to_dict(self):
        return {name: getattr(self, name) for name in (
            "han", "hiragana", "katakana", "hangul", "latin", "digit", "punctuation", "other")}


@dataclass(frozen=True)
class LanguageAssessment:
    profile: ScriptProfile
    classification: str
    normalized_text: str | None = None
    isolated_foreign_script: bool = False


def script_profile(text):
    counts = {name: 0 for name in ScriptProfile.__dataclass_fields__}
    for char in str(text or ""):
        point = ord(char)
        if "\u3400" <= char <= "\u9fff": counts["han"] += 1
        elif "\u3040" <= char <= "\u309f": counts["hiragana"] += 1
        elif "\u30a0" <= char <= "\u30ff": counts["katakana"] += 1
        elif "\uac00" <= char <= "\ud7af": counts["hangul"] += 1
        elif char.isascii() and char.isalpha(): counts["latin"] += 1
        elif char.isdigit(): counts["digit"] += 1
        elif char.isspace() or char in "，。！？；：、】【、】【,.!?;:'\"()[]{}-—…": counts["punctuation"] += 1
        elif point: counts["other"] += 1
    return ScriptProfile(**counts)


def _letters(text):
    return "".join(char for char in str(text or "") if char.isalpha())


def assess_text(text, *, project_language="zh"):
    """Classify without inferring dialogue meaning from a foreign script."""
    profile = script_profile(text)
    if project_language != "zh" or not profile.meaningful:
        return LanguageAssessment(profile, "PASS")
    letters = _letters(text)
    lowered = letters.casefold()
    if profile.han:
        return LanguageAssessment(profile, "PASS")
    if profile.foreign:
        # A tiny kana or a single Hangul vocal is a high-confidence non-lexical
        # sound, not evidence that the Chinese project became multilingual.
        kana_only = profile.foreign == profile.hiragana + profile.katakana
        kana_letters = "".join(char for char in letters if "\u3040" <= char <= "\u30ff")
        # The provider often combines repeated short cries into one coarse row;
        # accept that row only when every kana belongs to the small, explicit
        # vocal-sound set. This excludes ordinary Japanese lexical dialogue.
        if ((kana_only and profile.foreign <= 3)
                or (kana_only and kana_letters and set(kana_letters) <= _VOCAL_KANA)
                or (profile.hangul and profile.hangul <= 2 and set(letters) <= _VOCAL_HANGUL)):
            return LanguageAssessment(profile, "VOCALIZATION", "啊")
        return LanguageAssessment(profile, "LANGUAGE_MISMATCH")
    if profile.latin and not profile.digit:
        if lowered in _VOCAL_LATIN:
            return LanguageAssessment(profile, "VOCALIZATION", "啊")
        if lowered in _ACRONYMS or (letters.isupper() and len(letters) <= 5):
            return LanguageAssessment(profile, "PASS")
        return LanguageAssessment(profile, "LANGUAGE_MISMATCH")
    return LanguageAssessment(profile, "PASS")


def assess_rows(rows, *, project_language="zh"):
    """Add neighbour evidence for isolated short foreign-script artifacts."""
    initial = [assess_text(row.zh, project_language=project_language) for row in rows]
    result = []
    for index, assessment in enumerate(initial):
        previous = initial[index - 1] if index else None
        following = initial[index + 1] if index + 1 < len(initial) else None
        isolated = (assessment.profile.foreign > 0 and assessment.profile.han == 0
                    and previous is not None and following is not None
                    and previous.profile.han > 0 and following.profile.han > 0)
        result.append(LanguageAssessment(assessment.profile, assessment.classification,
                                         assessment.normalized_text, isolated))
    return tuple(result)


def final_zh_language_guard(rows):
    """Keep lexical Korean/Japanese evidence out of the visible ZH timeline.

    This is intentionally the last language check before persistence/export.
    It does not discard evidence: raw STT and retry text remain on the row for
    manual repair, while the canonical user-visible text becomes unresolved.
    """
    guarded = []
    unresolved = []
    for row in rows:
        assessment = assess_text(row.zh, project_language="zh")
        foreign_lexical = (assessment.classification == "LANGUAGE_MISMATCH"
                           and assessment.profile.foreign > 0)
        if foreign_lexical:
            guarded.append(replace(
                row,
                zh="",
                transcript_raw_stt_text=row.transcript_raw_stt_text or row.zh,
                transcript_language_status="NEEDS_MANUAL_REVIEW",
            ))
            unresolved.append(row.id)
        else:
            guarded.append(row)
    return guarded, tuple(unresolved)


def translation_eligible(row):
    """Unresolved transcript language must not enter Chinese translation."""
    return row.transcript_language_status not in {"LANGUAGE_MISMATCH", "NEEDS_MANUAL_REVIEW"}
