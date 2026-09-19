"""Original test doubles; production UI uses TranslationPipeline and ContextService."""
from dataclasses import dataclass, field, replace
from typing import Protocol
from cartoon_sub.subtitle.models import Segment
from cartoon_sub.media.process import CancelledError

PRESETS = ("Literal", "Natural Vietnamese", "Zhihu Story", "Drama", "Documentary", "Custom")

@dataclass
class TranslationOptions:
    preset: str = "Natural Vietnamese"
    custom_prompt: str = ""
    glossary: dict = field(default_factory=dict)
    model: str = "mock"
    chunk_size: int = 40
    retry_count: int = 2

class TranslationService(Protocol):
    def translate(self, segments: list[Segment], options: TranslationOptions, *, cancel=None, progress=None) -> list[Segment]: ...

class MockTranslationService:
    def translate(self, segments, options, *, cancel=None, progress=None):
        result = []
        for segment in segments:
            if cancel and cancel.is_set():
                raise CancelledError("Job cancelled")
            result.append(replace(segment, vi=f"[MOCK – chưa dịch] Dòng {segment.id}"))
        if progress:
            progress("MOCK translation completed; timestamps and IDs preserved")
        return result
