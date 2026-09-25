import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock

from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.audio_timing import AudioTimingRefiner, AudioTimingValidationError, validate_timing
from cartoon_sub.subtitle.models import DisplaySegment, Utterance


TEXT = "Câu thứ nhất đã được nói trước và câu thứ hai được nói sau"
PARTS = ["Câu thứ nhất đã được nói trước ", "và câu thứ hai được nói sau"]


def wav(path, seconds=10):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000 * seconds)


def store():
    result = Mock()
    result.load.return_value = AISettings(transcription_model="gemini-test")
    result.get_key.return_value = "test-key"
    return result


class AudioTimingTests(unittest.TestCase):
    def utterance(self):
        return Utterance(1, 0, 10, "第一句先说第二句后说", vi=TEXT, speaker_id="SPK_01")

    def payload(self):
        return {"segments": [{"start": 0, "end": 5, "vi": PARTS[0]},
                             {"start": 5, "end": 10, "vi": PARTS[1]}]}

    def test_valid_audio_alignment_preserves_vietnamese_and_sets_precise_boundaries(self):
        client = Mock(); client.transcribe_json.return_value = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "source.wav"; wav(audio)
            segments = AudioTimingRefiner(store(), lambda key: client).refine(self.utterance(), audio, Path(directory) / "cache")
        self.assertEqual([(item.start, item.end) for item in segments], [(0, 5), (5, 10)])
        self.assertEqual("".join(item.vi_text for item in segments), TEXT)
        self.assertTrue(all(item.segmentation_reason == "audio_timing" for item in segments))

    def test_rewritten_or_gapped_output_is_rejected_without_touching_current_segments(self):
        utterance = self.utterance()
        utterance.set_display_segments([DisplaySegment("1.1", 1, 0, 10, TEXT, segmentation_reason="local")])
        before = utterance.to_dict()
        bad = {"segments": [{"start": 0, "end": 4, "vi": "Câu đã bị sửa "},
                            {"start": 6, "end": 10, "vi": PARTS[1]}]}
        with self.assertRaises(AudioTimingValidationError):
            validate_timing(bad, utterance)
        self.assertEqual(utterance.to_dict(), before)

    def test_trimmed_boundary_whitespace_is_restored_from_original_text(self):
        payload = {"segments": [{"start": 0, "end": 5, "vi": PARTS[0].strip()},
                                {"start": 5, "end": 10, "vi": PARTS[1].strip()}]}
        segments = validate_timing(payload, self.utterance())
        self.assertEqual([item.vi_text for item in segments], PARTS)
        self.assertEqual("".join(item.vi_text for item in segments), TEXT)

    def test_real_utterance_91_uses_relative_points_and_preserves_exact_text(self):
        text = "Nếu không phải ngươi đào thì ta lấy đâu ra chuyện này cơ chứ."
        utterance = Utterance(91, 437.653, 444.363, "中文", vi=text, speaker_id="SPK_01")
        payload = {"segments": [
            {"start": 0, "end": 3.2, "vi": "Nếu không phải ngươi đào"},
            {"start": 3.2, "end": 6.71, "vi": "thì ta lấy đâu ra chuyện này cơ chứ."},
        ]}
        segments = validate_timing(payload, utterance)
        self.assertEqual([(item.start, item.end) for item in segments], [(437.653, 440.853), (440.853, 444.363)])
        self.assertEqual("".join(item.vi_text for item in segments), text)


if __name__ == "__main__":
    unittest.main()
