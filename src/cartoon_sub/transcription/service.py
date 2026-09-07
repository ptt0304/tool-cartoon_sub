from typing import Protocol
from cartoon_sub.subtitle.models import Segment
from cartoon_sub.media.process import CancelledError

class TranscriptionService(Protocol):
    def transcribe(self, audio_path, *, cancel=None, progress=None) -> list[Segment]: ...

class MockTranscriptionService:
    def transcribe(self, audio_path, *, cancel=None, progress=None):
        if cancel and cancel.is_set():
            raise CancelledError("Job cancelled")
        if progress:
            progress("MOCK transcription: returning sample data, no network call")
        return [Segment(1, 0, 2.5, "这个女孩叫小美"), Segment(2, 2.5, 5, "今天她收到了一封信")]
