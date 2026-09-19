from dataclasses import dataclass
import math
import re


@dataclass
class Speaker:
    id: str
    name: str = "Unknown"
    voice_notes: str = ""
    tts_voice_id: str | None = None
    tts_speed: float = 1.0

    def __post_init__(self):
        if not re.fullmatch(r"SPK_(?:[0-9]{2,}|UNKNOWN)", self.id):
            raise ValueError("Speaker ID phải có dạng SPK_01 hoặc SPK_UNKNOWN")
        if not isinstance(self.name, str) or not isinstance(self.voice_notes, str):
            raise ValueError("Tên speaker phải là văn bản")
        if self.tts_voice_id is not None and (
            not isinstance(self.tts_voice_id, str) or not self.tts_voice_id.strip()
        ):
            raise ValueError("TTS voice ID phải là văn bản không rỗng hoặc null")
        if (
            type(self.tts_speed) not in (int, float)
            or not math.isfinite(self.tts_speed)
            or not 0 < self.tts_speed <= 3.0
        ):
            raise ValueError("Tốc độ TTS phải lớn hơn 0 và không quá 3.0")
