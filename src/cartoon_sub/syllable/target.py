from dataclasses import dataclass, asdict
from enum import StrEnum
import math
from cartoon_sub.translation.modes import TranslationMode

class TargetSyllableStrategy(StrEnum):
    CHINESE_COUNT = "chinese_count"
    TIME_BASED = "time_based"
    HYBRID = "hybrid"

@dataclass
class DubbingSettings:
    mode: str = "balanced_dubbing"
    target_strategy: str = "time_based"
    speech_rate: float = 3.5
    tolerance_percent: float = 15
    tolerance_syllables: int = 1
    tolerance_kind: str = "percent"
    strict_retry: int = 3
    separate_texts: bool = True
    hybrid_time_weight: float = 0.5

    def validate(self):
        TranslationMode(self.mode)
        TargetSyllableStrategy(self.target_strategy)
        for name, low, high in (("speech_rate", 0.5, 12), ("tolerance_percent", 0, 100), ("hybrid_time_weight", 0, 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Dubbing setting không hợp lệ: {name}")
        if type(self.strict_retry) is not int or not 0 <= self.strict_retry <= 5:
            raise ValueError("Strict retry phải từ 0 đến 5")
        if type(self.tolerance_syllables) is not int or not 0 <= self.tolerance_syllables <= 20:
            raise ValueError("Tolerance syllables phải từ 0 đến 20")
        if self.tolerance_kind not in ("percent", "syllables") or type(self.separate_texts) is not bool:
            raise ValueError("Cấu hình dubbing không hợp lệ")
        return self

    def to_dict(self):
        return asdict(self)

def round_half_up(value):
    return int(math.floor(value + 0.5))

def target_syllables(zh_count, duration, settings):
    settings.validate()
    timed = duration * settings.speech_rate
    if settings.target_strategy == "chinese_count":
        value = zh_count
    elif settings.target_strategy == "hybrid":
        value = (1 - settings.hybrid_time_weight) * zh_count + settings.hybrid_time_weight * timed
    else:
        value = timed
    return max(1, round_half_up(value))

def allowed_delta(target, mode, settings):
    if mode == "strict_iso_syllabic":
        return 0
    if settings.tolerance_kind == "syllables":
        return settings.tolerance_syllables
    return int(math.floor(target * settings.tolerance_percent / 100))
