import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication

from cartoon_sub.subtitle.models import Segment
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber
from cartoon_sub.transcription.language_consistency import assess_rows, assess_text, script_profile
from cartoon_sub.transcription.language_consistency import final_zh_language_guard, translation_eligible
from cartoon_sub.transcription.qa_repair import TranscriptQAService
from cartoon_sub.transcription.speech_regions import SpeechRegion
from cartoon_sub.subtitle.parser import export_canonical_srt
from cartoon_sub.subtitle.models import Project
from cartoon_sub.ui.tabs import transcript_tab


class TranscriptLanguageConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def test_chinese_and_acronyms_pass(self):
        self.assertEqual(assess_text("你好。").classification, "PASS")
        self.assertEqual(assess_text("使用 AI 和 CPU。").classification, "PASS")
        self.assertGreater(script_profile("你好 AI").han, 0)

    def test_short_foreign_vocals_are_not_dialogue(self):
        rows = [Segment(1, 0, 1, "你好。"), Segment(2, 1, 2, "ああ。"), Segment(3, 2, 3, "再见。")]
        assessment = assess_rows(rows)[1]
        self.assertEqual(assessment.classification, "VOCALIZATION")
        self.assertEqual(assessment.normalized_text, "啊")
        self.assertTrue(assessment.isolated_foreign_script)
        self.assertEqual(assess_text("Oh.").classification, "VOCALIZATION")
        self.assertEqual(assess_text("ああ。ああ。うう。し。").classification, "VOCALIZATION")

    def test_korean_dialogue_is_blocking_language_mismatch(self):
        row = Segment(1, 0, 2, "아쉽군. 리사드.")
        self.assertEqual(assess_text(row.zh).classification, "LANGUAGE_MISMATCH")
        violations = TranscriptQAService().inspect([row]).violations
        self.assertTrue(any(item.code == "LANGUAGE_MISMATCH" and item.severity == "BLOCKING"
                            for item in violations))

    def test_vocalization_keeps_short_timing_failure(self):
        rows, mismatch, _ = GeminiTranscriber._apply_language_normalization([Segment(1, 0, .1, "Oh.")])
        self.assertFalse(mismatch)
        self.assertEqual(rows[0].zh, "啊")
        codes = {item.code for item in TranscriptQAService().inspect(rows).violations}
        self.assertIn("CRITICAL_SHORT_DURATION", codes)

    def test_one_language_retry_accepts_chinese_without_changing_region_bounds(self):
        class Client:
            calls = 0
            def transcribe_json(self, *args, **kwargs):
                self.calls += 1
                return {"segments": [{"start": 0, "end": 1, "zh": "你好。", "transcript_confidence": .9}]}
        with tempfile.TemporaryDirectory() as root:
            service = GeminiTranscriber(lambda: object(), "google/gemini-3.5-transcribe", root)
            output, accepted, stats = service._language_retry(
                Client(), b"audio", 1, SpeechRegion(100, 101), [Segment(1, 0, 1, "아쉽군")],
                cancel=None, progress=None)
        self.assertTrue(accepted)
        self.assertEqual(output[0].zh, "你好。")
        self.assertEqual((output[0].start, output[0].end), (0, 1))
        self.assertEqual(output[0].transcript_language_retry_count, 1)
        self.assertEqual(stats["retry_regions"], 1)

    def test_failed_language_retry_is_cached_and_manual_review(self):
        class Client:
            calls = 0
            def transcribe_json(self, *args, **kwargs):
                self.calls += 1
                return {"segments": [{"start": 0, "end": 1, "zh": "아쉽군", "transcript_confidence": .9}]}
        client = Client()
        with tempfile.TemporaryDirectory() as root:
            service = GeminiTranscriber(lambda: object(), "google/gemini-3.5-transcribe", root)
            args = (client, b"audio", 1, SpeechRegion(0, 1), [Segment(1, 0, 1, "아쉽군")])
            first, accepted, _ = service._language_retry(*args, cancel=None, progress=None)
            second, accepted_again, cached = service._language_retry(*args, cancel=None, progress=None)
        self.assertFalse(accepted)
        self.assertFalse(accepted_again)
        self.assertEqual(client.calls, 1)
        self.assertEqual(first[0].transcript_language_status, "NEEDS_MANUAL_REVIEW")
        self.assertEqual(first[0].transcript_raw_stt_text, "아쉽군")
        self.assertEqual(first[0].transcript_language_retry_text, "아쉽군")
        self.assertEqual(second[0].transcript_language_status, "NEEDS_MANUAL_REVIEW")
        self.assertEqual(first[0].transcript_language_retry_count, 1)
        self.assertEqual(cached["retry_cache_hits"], 1)

    def test_final_guard_hides_lexical_hangul_but_keeps_diagnostics(self):
        row = Segment(14, 350.26, 351.06, "아쉽군. 리사드.",
                      transcript_raw_stt_text="아쉽군. 리사드.",
                      transcript_language_retry_text="아쉽군. 리사드.",
                      transcript_language_status="NEEDS_MANUAL_REVIEW",
                      transcript_language_retry_count=1)
        guarded, unresolved = final_zh_language_guard([row])
        self.assertEqual(unresolved, (14,))
        self.assertEqual(guarded[0].zh, "")
        self.assertEqual(guarded[0].transcript_raw_stt_text, "아쉽군. 리사드.")
        self.assertEqual(guarded[0].transcript_language_retry_text, "아쉽군. 리사드.")
        self.assertEqual(guarded[0].transcript_language_status, "NEEDS_MANUAL_REVIEW")
        self.assertFalse(translation_eligible(guarded[0]))

    def test_final_guard_catches_foreign_text_reintroduced_after_normalization(self):
        guarded, unresolved = final_zh_language_guard([
            Segment(1, 0, 1, "你好。"), Segment(2, 1, 2, "リサード。"), Segment(3, 2, 3, "再见。"),
        ])
        self.assertEqual(unresolved, (2,))
        self.assertEqual(guarded[1].zh, "")
        self.assertEqual(guarded[1].transcript_language_status, "NEEDS_MANUAL_REVIEW")

    def test_zh_srt_skips_unresolved_foreign_text(self):
        rows, _ = final_zh_language_guard([
            Segment(1, 0, 1, "你好。"), Segment(2, 1, 2, "아쉽군. 리사드."),
        ])
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "zh.srt"
            export_canonical_srt(rows, output, "zh")
            contents = output.read_text(encoding="utf-8")
        self.assertIn("你好。", contents)
        self.assertNotIn("아쉽군", contents)
        self.assertNotIn("리사드", contents)

    def test_transcript_ui_shows_canonical_language_status(self):
        row, _ = final_zh_language_guard([Segment(14, 350.26, 351.06, "아쉽군. 리사드.")])
        project = Project("language-status", source_video_path="video.mp4", segments=row)
        widget = transcript_tab.build()
        transcript_tab.populate(widget, project)
        self.assertEqual(widget.table.horizontalHeaderItem(5).text(), "Language status")
        self.assertEqual(widget.table.item(0, 4).text(), "")
        self.assertEqual(widget.table.item(0, 5).text(), "NEEDS_MANUAL_REVIEW")


if __name__ == "__main__":
    unittest.main()
