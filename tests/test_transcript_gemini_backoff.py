import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from cartoon_sub.ai.gemini_client import GeminiError, safe_error
from cartoon_sub.media.process import CancelledError
from cartoon_sub.transcription.gemini_transcriber import (
    GeminiTranscriber,
    TRANSCRIPT_GEMINI_BACKOFF_SECONDS,
    _wait_for_transcript_retry,
    classify_gemini_error,
)


def write_wav(path, seconds=1):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        for index in range(seconds):
            audio.writeframes((index + 1).to_bytes(2, "little") * 16000)


def valid_response(text="你好"):
    return {"segments": [{"id": 1, "start": 0, "end": 0.5, "zh": text}]}


def http_error(code):
    return safe_error(SimpleNamespace(code=code, response=SimpleNamespace(headers={})))


class ManagedGeminiFake:
    managed_transcript_retry = True

    def __init__(self, outcomes, keys=1):
        self.outcomes = list(outcomes)
        self.transcript_key_count = keys
        self.selected = []
        self.calls = []

    def select_transcript_key(self, index):
        self.selected.append(index)

    def transcribe_json_once(self, *args, **kwargs):
        self.calls.append(self.selected[-1])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        pass


class TranscriptGeminiBackoffTests(unittest.TestCase):
    def run_transcriber(self, outcomes, *, keys=1, seconds=1, cache=None):
        temporary = None
        if cache is None:
            temporary = tempfile.TemporaryDirectory()
            root = Path(temporary.name)
            cache = root / "cache"
        else:
            root = cache.parent
        audio = root / "audio.wav"
        write_wav(audio, seconds)
        client = ManagedGeminiFake(outcomes, keys)
        transcriber = GeminiTranscriber(lambda: ["key"] * keys, "model", cache, 2, lambda _: client)
        delays = []

        def skip_wait(cancel, delay, report, message):
            delays.append(delay)

        return temporary, client, transcriber, audio, delays, skip_wait

    def test_503_succeeds_on_third_attempt_with_exact_backoff(self):
        values = [http_error(503), http_error(503), valid_response()]
        temp, client, transcriber, audio, delays, skip = self.run_transcriber(values)
        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
            self.assertEqual(len(transcriber.transcribe(audio)), 1)
        self.assertEqual(delays, [2, 4])
        self.assertEqual(client.calls, [0, 0, 0])

    def test_transient_failure_uses_all_delays_then_stops(self):
        values = [http_error(503) for _ in range(6)]
        temp, client, transcriber, audio, delays, skip = self.run_transcriber(values)
        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
            with self.assertRaises(GeminiError) as caught:
                transcriber.transcribe(audio)
        self.assertEqual(delays, list(TRANSCRIPT_GEMINI_BACKOFF_SECONDS))
        self.assertEqual(len(client.calls), 6)
        self.assertIn("sau 5 lần thử lại", str(caught.exception))

    def test_all_transient_statuses_retry(self):
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                temp, client, transcriber, audio, delays, skip = self.run_transcriber(
                    [http_error(code), valid_response()])
                with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
                    self.assertEqual(len(transcriber.transcribe(audio)), 1)
                self.assertEqual(delays, [2])
                self.assertEqual(len(client.calls), 2)

    def test_429_uses_two_backoffs_then_succeeds(self):
        temp, client, transcriber, audio, delays, skip = self.run_transcriber(
            [http_error(429), http_error(429), valid_response()])
        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
            self.assertEqual(len(transcriber.transcribe(audio)), 1)
        self.assertEqual(delays, [2, 4])

    def test_network_timeout_uses_two_backoffs_then_succeeds(self):
        network = safe_error(httpx.ReadTimeout("private detail must not escape"))
        temp, client, transcriber, audio, delays, skip = self.run_transcriber(
            [network, network, valid_response()])
        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
            self.assertEqual(len(transcriber.transcribe(audio)), 1)
        self.assertEqual(delays, [2, 4])

    def test_client_errors_fail_immediately(self):
        for code in (400, 401, 403, 404, 422):
            with self.subTest(code=code):
                temp, client, transcriber, audio, delays, skip = self.run_transcriber([http_error(code)])
                with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
                    with self.assertRaises(GeminiError):
                        transcriber.transcribe(audio)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(delays, [])

    def test_key_rotates_only_after_full_sequence(self):
        values = [http_error(503) for _ in range(6)] + [valid_response()]
        temp, client, transcriber, audio, delays, skip = self.run_transcriber(values, keys=2)
        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
            self.assertEqual(len(transcriber.transcribe(audio)), 1)
        self.assertEqual(client.calls, [0] * 6 + [1])
        self.assertEqual(client.selected, [0, 1])
        self.assertEqual(delays, list(TRANSCRIPT_GEMINI_BACKOFF_SECONDS))

    def test_cancel_interrupts_countdown(self):
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(CancelledError):
            _wait_for_transcript_retry(cancelled, 32, lambda _: None, "waiting")

    def test_cancel_during_32_second_wait_sends_no_next_request(self):
        values = [http_error(503) for _ in range(6)]
        temp, client, transcriber, audio, delays, _ = self.run_transcriber(values)

        def cancel_at_32(cancel, delay, report, message):
            delays.append(delay)
            if delay == 32:
                raise CancelledError("cancelled")

        with temp, patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", cancel_at_32):
            with self.assertRaises(CancelledError):
                transcriber.transcribe(audio)
        self.assertEqual(delays, [2, 4, 8, 16, 32])
        self.assertEqual(len(client.calls), 5)

    def test_completed_chunks_are_cached_and_resume_only_failed_chunk(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "cartoon_sub.transcription.gemini_transcriber.CHUNK_SECONDS", 1):
            root = Path(directory)
            cache = root / "cache"
            values = [valid_response("一")] + [http_error(503) for _ in range(6)]
            _, first, transcriber, audio, delays, skip = self.run_transcriber(
                values, seconds=2, cache=cache)
            with patch("cartoon_sub.transcription.gemini_transcriber._wait_for_transcript_retry", skip):
                with self.assertRaises(GeminiError):
                    transcriber.transcribe(audio)
            second = ManagedGeminiFake([valid_response("二")])
            transcriber.client_factory = lambda _: second
            output = transcriber.transcribe(audio)
            self.assertEqual([row.zh for row in output], ["一", "二"])
            self.assertEqual(len(second.calls), 1)

    def test_classifier_uses_structured_status(self):
        timeout = http_error(408)
        self.assertFalse(timeout.retryable)  # shared Gemini features remain unchanged
        self.assertEqual(classify_gemini_error(timeout), "TRANSIENT")
        self.assertEqual(classify_gemini_error(http_error(422)), "BAD_REQUEST")
        self.assertEqual(classify_gemini_error(http_error(501)), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
