import io
import json
import tempfile
import unittest
import wave
from dataclasses import asdict
from pathlib import Path

from cartoon_sub.app.settings import AISettings, LocalTTSSettings
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.translation.dubbing_service import DubbingService
from cartoon_sub.tts.cache_identity import compute_tts_signature
from cartoon_sub.tts.duration_fit import DurationFitPlanner, VoiceCalibrationCache


def wav_bytes(duration, rate=16000):
    value = io.BytesIO()
    with wave.open(value, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\0\0" * int(duration * rate))
    return value.getvalue()


class FakeTTSClient:
    def __init__(self, durations):
        self.durations = durations
        self.voice = None

    def generate(self, segment_id, speaker_id, voice_id, text, speed):
        self.voice = voice_id
        return {"audio_url": f"/audio/{segment_id}.wav"}

    def download_audio(self, audio_url=None, *, audio_path=None):
        return wav_bytes(self.durations[self.voice])


class FakeAIClient:
    def __init__(self, credential):
        pass

    def generate_json(self, system, prompt, schema, model, cancel=None):
        target = json.loads(prompt)["target"]
        return {"translations": [{
            "id": target["id"], "vi": "Hắn giữ pháp khí Lưu Vân Lạp.",
            "review_note": "Rút gọn theo duration", "meaning_preservation": "high",
            "compressed": True,
        }]}

    def close(self):
        pass


class FakeStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    def load(self):
        return AISettings(translation_model="model", translation_provider="openai")

    def get_key(self, provider):
        return "test"


def project(rows, duration=20.0):
    speakers = {
        row.speaker_id: asdict(Speaker(row.speaker_id, row.speaker_id, tts_voice_id=f"voice_{row.speaker_id}"))
        for row in rows
    }
    return Project("test", "video.mp4", metadata={"duration": duration}, segments=rows, speakers=speakers)


class DurationFitTests(unittest.TestCase):
    def test_actual_voice_calibrations_are_voice_specific_and_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = VoiceCalibrationCache(Path(temp) / "calibration.json")
            client = FakeTTSClient({"slow": 4.0, "medium": 3.0, "fast": 2.0})
            values = []
            for voice_id in ("slow", "medium", "fast"):
                values.append(cache.calibrate(
                    client, "http://127.0.0.1:8765",
                    {"voice_id": voice_id, "engine": "vieneu", "status": "READY"}, 1.0,
                ))
            self.assertLess(values[0].syllables_per_second, values[1].syllables_per_second)
            self.assertLess(values[1].syllables_per_second, values[2].syllables_per_second)
            self.assertEqual(cache.get(values[1].signature), values[1])

    def test_borrow_prefers_after_then_before_without_collision(self):
        left = Utterance(1, 9.5, 9.8, vi_dubbing="ngắn", tts_duration=0.3)
        row = Utterance(2, 10.0, 12.0, vi_dubbing="Một câu cần thêm thời gian đọc", tts_duration=2.6)
        right = Utterance(3, 13.0, 13.4, vi_dubbing="ngắn", tts_duration=0.4)
        p = project([left, row, right], 14.0)
        DurationFitPlanner().apply(p)
        self.assertEqual(row.dubbing_fit_status, "BORROWED")
        self.assertAlmostEqual(row.allowed_audio_start, 10.0)
        self.assertAlmostEqual(row.allowed_audio_end, 12.6)
        self.assertLessEqual(row.allowed_audio_end + 0.08, right.start + 1e-6)

    def test_no_slack_requests_semantic_rewrite(self):
        row = Utterance(2, 10.0, 12.0, vi_dubbing="Một câu rất dài cần rút gọn", tts_duration=3.0)
        p = project([
            Utterance(1, 9.0, 10.0, vi_dubbing="trước", tts_duration=1.0), row,
            Utterance(3, 12.0, 13.0, vi_dubbing="sau", tts_duration=1.0),
        ], 13.0)
        DurationFitPlanner().apply(p)
        self.assertEqual(row.dubbing_fit_status, "STRONG_REWRITE")
        self.assertEqual((row.allowed_audio_start, row.allowed_audio_end), (10.0, 12.0))

    def test_three_dense_lines_disable_cascading_borrow(self):
        rows = [
            Utterance(101, 0.0, 2.0, vi_dubbing="một hai ba bốn năm sáu bảy tám", tts_duration=3.2),
            Utterance(102, 2.0, 4.0, vi_dubbing="một hai ba bốn năm sáu bảy tám", tts_duration=3.0),
            Utterance(103, 4.0, 6.0, vi_dubbing="một hai ba bốn năm sáu bảy tám", tts_duration=3.4),
        ]
        p = project(rows, 6.0)
        DurationFitPlanner().apply(p)
        self.assertTrue(all(row.dubbing_fit_status == "LONG_DENSE_CHAIN" for row in rows))
        self.assertEqual([(row.allowed_audio_start, row.allowed_audio_end) for row in rows],
                         [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])

    def test_mixed_long_normal_long_is_not_dense_chain(self):
        rows = [
            Utterance(1, 0.0, 2.0, vi_dubbing="một hai ba bốn năm sáu bảy tám", tts_duration=3.0),
            Utterance(2, 3.0, 5.0, vi_dubbing="bình thường", tts_duration=1.5),
            Utterance(3, 6.0, 8.0, vi_dubbing="một hai ba bốn năm sáu bảy tám", tts_duration=3.0),
        ]
        DurationFitPlanner().apply(project(rows, 10.0))
        self.assertNotIn("LONG_DENSE_CHAIN", {row.dubbing_fit_status for row in rows})

    def test_allowed_window_does_not_invalidate_raw_tts_signature(self):
        row = Utterance(1, 1.0, 3.0, vi_dubbing="Giữ nguyên lời đọc")
        speaker = Speaker("SPK_01", tts_voice_id="voice")
        first = compute_tts_signature(row, speaker, "http://127.0.0.1:8765", {"voice_id": "voice"})
        row.allowed_audio_start = 0.8
        row.allowed_audio_end = 3.4
        row.dubbing_fit_status = "BORROWED"
        second = compute_tts_signature(row, speaker, "http://127.0.0.1:8765", {"voice_id": "voice"})
        self.assertEqual(first, second)

    def test_targeted_rewrite_keeps_full_subtitle_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            row = Utterance(
                1, 0.0, 2.0, zh="他还有一件中品法器流云笠。",
                vi_subtitle="Hắn còn có một kiện pháp khí trung phẩm tên là Lưu Vân Lạp.",
                vi_dubbing="Hắn còn có một kiện pháp khí trung phẩm tên là Lưu Vân Lạp.",
                speaker_id="SPK_01", tts_duration=3.0,
                allowed_audio_start=0.0, allowed_audio_end=2.0,
                dubbing_fit_status="STRONG_REWRITE", dubbing_voice_id="voice_SPK_01",
                tts_generation_status="generated",
            )
            p = project([row], 4.0)
            ProjectManager().save(p, temp)
            service = DubbingService(FakeStore(temp), client_factory=FakeAIClient)
            changed = service.rewrite_duration_failures(p, temp, [1])
            self.assertEqual(changed, [1])
            self.assertEqual(row.vi_subtitle, "Hắn còn có một kiện pháp khí trung phẩm tên là Lưu Vân Lạp.")
            self.assertEqual(row.vi_dubbing, "Hắn giữ pháp khí Lưu Vân Lạp.")
            self.assertEqual(row.dubbing_rewrite_attempts, 1)
            self.assertEqual(row.tts_generation_status, "stale")

    def test_project_round_trip_persists_duration_metadata(self):
        row = Utterance(1, 1.0, 3.0, vi_dubbing="Một câu", allowed_audio_start=0.8,
                        allowed_audio_end=3.2, tts_fit_ratio=1.04, dubbing_fit_status="BORROWED",
                        dubbing_voice_id="voice", dubbing_estimated_rate=4.2, dubbing_budget_duration=2.4)
        restored = Project.from_dict(project([row], 5.0).to_dict()).utterances[0]
        self.assertEqual(restored.dubbing_fit_status, "BORROWED")
        self.assertEqual((restored.allowed_audio_start, restored.allowed_audio_end), (0.8, 3.2))


if __name__ == "__main__":
    unittest.main()
