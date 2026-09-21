import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event

from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.media.process import CancelledError
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber


def make_wav(path, seconds):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b"\0\0" * int(seconds * 16000))


class FakeClient:
    def __init__(self, responses=None, on_call=None):
        self.responses = list(responses or [])
        self.calls = []
        self.on_call = on_call
    def transcribe_json(self, audio, prompt, schema, model, **kwargs):
        self.calls.append(prompt)
        if self.on_call: self.on_call(len(self.calls))
        response = self.responses.pop(0) if self.responses else {"segments": []}
        if isinstance(response, Exception): raise response
        return response
    def close(self): pass


class GeminiLongTranscriptionTests(unittest.TestCase):
    def transcriber(self, directory, client, **kwargs):
        return GeminiTranscriber(lambda: "key", "model", directory,
            client_factory=lambda _: client, retry_wait=lambda *_: False, random_source=lambda *_: 1.0, **kwargs)

    def test_short_keeps_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "short.wav"; make_wav(path, 60)
            client = FakeClient([{"segments": [{"start": 1, "end": 2, "zh": "短片"}]}])
            output = self.transcriber(Path(tmp) / "cache", client).transcribe(path)
        self.assertEqual(1, len(client.calls)); self.assertEqual("短片", output[0].zh)
        self.assertNotIn("corresponds to approximately", client.calls[0])

    def test_long_offsets_deduplicates_and_preserves_legitimate_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"; make_wav(path, 200)
            client = FakeClient([
                {"segments": [{"start": 74, "end": 77, "zh": "Câu A", "speaker_id": "SPK_01"}]},
                {"segments": [{"start": 0, "end": 2.1, "zh": "Câu A", "speaker_id": "SPK_02"},
                              {"start": 1, "end": 4, "zh": "Câu B", "speaker_id": "SPK_03"},
                              {"start": 3, "end": 6, "zh": "Offset", "speaker_id": "SPK_03"}]},
                {"segments": []},
            ])
            output = self.transcriber(Path(tmp) / "cache", client).transcribe(path)
        self.assertEqual(3, len(client.calls)); self.assertEqual(3, len(output))
        self.assertEqual((78, 81), (next(s for s in output if s.zh == "Offset").start, next(s for s in output if s.zh == "Offset").end))
        self.assertEqual(1, sum(s.zh == "Câu A" for s in output))
        self.assertEqual(1, sum(s.zh == "Câu B" for s in output))
        self.assertTrue(next(s for s in output if s.zh == "Câu A").overlap)
        self.assertTrue(next(s for s in output if s.zh == "Câu B").overlap)

    def test_retries_429_and_503_then_succeeds(self):
        for error in (GeminiError("Gemini HTTP 429", True), GeminiError("Gemini HTTP 503", True)):
            with self.subTest(error=str(error)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "long.wav"; make_wav(path, 121)
                client = FakeClient([error, error, {"segments": []}, {"segments": []}])
                self.transcriber(Path(tmp) / "cache", client).transcribe(path)
                self.assertEqual(4, len(client.calls))

    def test_resume_skips_completed_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"; make_wav(path, 500)
            cache = Path(tmp) / "cache"
            first = FakeClient([{"segments": []}] * 5 + [GeminiError("Gemini HTTP 503", True)] * 6)
            with self.assertRaisesRegex(GeminiError, "GEMINI_SERVICE_UNAVAILABLE"):
                self.transcriber(cache, first).transcribe(path)
            second = FakeClient([{"segments": []}] * 2)
            self.transcriber(cache, second).transcribe(path)
            self.assertEqual(2, len(second.calls))

    def test_cancel_keeps_completed_chunk_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"; make_wav(path, 500)
            cache = Path(tmp) / "cache"; cancel = Event()
            first = FakeClient([{"segments": []}] * 7, on_call=lambda count: cancel.set() if count == 3 else None)
            with self.assertRaises(CancelledError):
                self.transcriber(cache, first).transcribe(path, cancel=cancel)
            second = FakeClient([{"segments": []}] * 4)
            self.transcriber(cache, second).transcribe(path)
            self.assertEqual(4, len(second.calls))


if __name__ == "__main__":
    unittest.main()
