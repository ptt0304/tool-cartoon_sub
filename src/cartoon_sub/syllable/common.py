from dataclasses import dataclass, field
import unicodedata
import re

@dataclass
class CountResult:
    count: int
    normalized: str
    warnings: list[str] = field(default_factory=list)

def clean(text):
    return unicodedata.normalize("NFKC", text)

def tokens(text):
    return re.findall(r"[0-9]+(?:[.,][0-9]+)?|[^\W\d_]+|%", clean(text), re.UNICODE)
