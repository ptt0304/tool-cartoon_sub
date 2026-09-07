from dataclasses import dataclass, field, asdict
from cartoon_sub.translation.context_models import StoryContext

@dataclass
class Segment:
    id: int
    start: float
    end: float
    zh: str = ""
    vi: str = ""

    def __post_init__(self):
        import math
        if not isinstance(self.id, int) or self.id < 1:
            raise ValueError("Subtitle ID must be a positive integer")
        if not all(isinstance(t, (float, int)) and math.isfinite(t) for t in (self.start, self.end)):
            raise ValueError("Invalid subtitle timestamp")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Subtitle requires 0 <= start < end")
        if not isinstance(self.zh, str) or not isinstance(self.vi, str):
            raise ValueError("Subtitle text must be a string")

    @property
    def duration(self):
        return self.end - self.start

@dataclass
class Mask:
    enabled: bool = False
    kind: str = "solid"
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

@dataclass
class SubtitleStyle:
    font: str = "Arial"
    font_size: int = 42
    bold: bool = False
    outline: float = 2
    shadow: float = 1
    alignment: int = 2
    margin_bottom: int = 30
    max_lines: int = 2

@dataclass
class Project:
    name: str
    source_video_path: str
    schema_version: int = 1
    metadata: dict = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    transcription_status: str = "not_started"
    translation_prompt: str = ""
    translation_preset: str = "Natural Vietnamese"
    glossary: dict[str, str] = field(default_factory=dict)
    mask: Mask = field(default_factory=Mask)
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    selected_models: dict = field(default_factory=lambda: {"transcription": "mock", "translation": "mock"})
    cache_hashes: dict = field(default_factory=dict)
    chunk_states: dict = field(default_factory=dict)
    translation_genres: list[str] = field(default_factory=list)
    story_context: dict = field(default_factory=lambda: StoryContext().to_dict())
    context_proposal: dict = field(default_factory=dict)
    context_source_hash: str = ""
    context_proposal_hash: str = ""
    context_status: str = "not_started"
    translation_status: str = "not_started"
    translation_notes: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported project schema")
        data["segments"] = [Segment(**s) for s in data.get("segments", [])]
        ids = [s.id for s in data["segments"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate subtitle IDs")
        data["mask"] = Mask(**data.get("mask", {}))
        data["subtitle_style"] = SubtitleStyle(**data.get("subtitle_style", {}))
        data["story_context"] = StoryContext.from_dict(data.get("story_context", {})).to_dict()
        if data.get("context_proposal"):
            data["context_proposal"] = StoryContext.from_dict(data["context_proposal"]).to_dict()
        from cartoon_sub.translation.presets import GENRES
        if not isinstance(data.get("translation_genres", []), list) or any(g not in GENRES for g in data.get("translation_genres", [])):
            raise ValueError("Thể loại dịch không hợp lệ")
        return cls(**data)
