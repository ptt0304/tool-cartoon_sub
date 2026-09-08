from dataclasses import dataclass
import re

@dataclass
class Speaker:
    id: str
    name: str = "Unknown"
    voice_notes: str = ""

    def __post_init__(self):
        if not re.fullmatch(r"SPK_(?:[0-9]{2,}|UNKNOWN)", self.id):
            raise ValueError("Speaker ID phải có dạng SPK_01 hoặc SPK_UNKNOWN")
        if not isinstance(self.name,str) or not isinstance(self.voice_notes,str):
            raise ValueError("Tên speaker phải là văn bản")
