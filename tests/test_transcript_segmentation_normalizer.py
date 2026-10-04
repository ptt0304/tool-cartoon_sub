import json
import tempfile
import unittest
import wave
from pathlib import Path

from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.transcription.gemini_transcriber import GeminiTranscriber
from cartoon_sub.transcription.pipeline import (
    invalidate_transcript_dependents,
    transcript_timeline_signature,
)
from cartoon_sub.transcription.segmentation_normalizer import (
    TRANSCRIPT_SEGMENTATION_VERSION,
    TranscriptSegmentationNormalizer,
)


def compact(text):
    return "".join(text.split())


def words(texts, starts, ends, speakers=None):
    speakers = speakers or [None] * len(texts)
    return [dict(word=text, start=start, end=end, **({"speaker_id": speaker} if speaker else {}))
            for text, start, end, speaker in zip(texts, starts, ends, speakers)]


class TranscriptSegmentationNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = TranscriptSegmentationNormalizer()

    def test_word_timestamps_chinese_punctuation_preserve_tokens_and_timing(self):
        parent = Segment(1, 0, 2, "你好。再见！")
        output = self.normalizer.normalize([parent], words(
            ["你", "好", "再", "见"], [0, .3, 1.1, 1.4], [.2, .8, 1.3, 1.9]))
        self.assertEqual([row.zh for row in output], ["你好。", "再见！"])
        self.assertEqual([(row.start, row.end) for row in output], [(0, .8), (1.1, 1.9)])
        self.assertEqual(compact("".join(row.zh for row in output)), compact(parent.zh))

    def test_word_pause_and_speaker_change_are_boundaries(self):
        parent = Segment(9, 0, 3, "你好吗我很好")
        timed = words(["你", "好", "吗", "我", "很", "好"],
                      [0, .2, .4, 1.3, 1.5, 1.7], [.15, .35, .55, 1.45, 1.65, 1.9],
                      ["SPK_01"] * 3 + ["SPK_02"] * 3)
        output = self.normalizer.normalize([parent], timed)
        self.assertEqual([row.zh for row in output], ["你好吗", "我很好"])
        self.assertEqual([row.speaker_id for row in output], ["SPK_01", "SPK_02"])
        self.assertEqual(output[0].end, .55)
        self.assertEqual(output[1].start, 1.3)

    def test_unknown_speaker_is_not_fabricated(self):
        parent = Segment(1, 0, 2, "你好再见")
        output = self.normalizer.normalize([parent], words(
            ["你", "好", "再", "见"], [0, .2, 1.2, 1.4], [.1, .3, 1.3, 1.5]))
        self.assertTrue(all(row.speaker_id == "SPK_UNKNOWN" for row in output))

    def test_no_words_many_sentences_get_weighted_contiguous_timing(self):
        parent = Segment(1, 2, 32, "你好。今天出发！我留下来等你？最后一起回家。")
        output = self.normalizer.normalize([parent])
        self.assertGreater(len(output), 1)
        self.assertEqual(output[0].start, parent.start)
        self.assertEqual(output[-1].end, parent.end)
        self.assertTrue(all(left.end == right.start for left, right in zip(output, output[1:])))
        self.assertEqual(compact("".join(row.zh for row in output)), compact(parent.zh))
        # Different spoken weights must not receive blind equal durations.
        self.assertGreater(len({round(row.end - row.start, 4) for row in output}), 1)

    def test_long_unpunctuated_text_falls_back_without_cutting_latin_or_number(self):
        parent = Segment(1, 0, 30, "今天我们去ABC123号房间然后继续寻找姐姐最后一起安全回家这是一个很长的句子没有标点")
        output = self.normalizer.normalize([parent])
        self.assertGreater(len(output), 1)
        self.assertLessEqual(max(row.end - row.start for row in output), 7.1)
        self.assertEqual(sum("ABC123" in row.zh for row in output), 1)
        self.assertEqual(compact("".join(row.zh for row in output)), compact(parent.zh))

    def test_short_natural_utterance_and_well_formed_srt_stay_unchanged(self):
        short = Segment(7, 1, 4, "我们现在出发。")
        self.assertEqual(self.normalizer.normalize([short]), [short])
        srt = Segment(3, 5, 9, "第一句。第二句。")
        self.assertEqual(self.normalizer.normalize([srt], source="srt"), [srt])

    def test_pathological_srt_is_split_inside_parent_bounds(self):
        parent = Segment(4, 5, 25, "第一句。第二句话更长一些。第三句。")
        output = self.normalizer.normalize([parent], source="srt")
        self.assertGreater(len(output), 1)
        self.assertEqual((output[0].start, output[-1].end), (5, 25))
        self.assertTrue(all(5 <= row.start < row.end <= 25 for row in output))

    def test_ids_and_output_are_deterministic_and_monotonic(self):
        parents = [Segment(8, 0, 20, "一二三四五六七八九十十一十二十三十四十五十六十七十八十九二十"),
                   Segment(2, 20, 24, "结束。")]
        first = self.normalizer.normalize(parents)
        second = self.normalizer.normalize(parents)
        self.assertEqual([(r.id, r.start, r.end, r.zh) for r in first],
                         [(r.id, r.start, r.end, r.zh) for r in second])
        self.assertEqual([r.id for r in first], list(range(1, len(first) + 1)))
        self.assertTrue(all(row.start < row.end for row in first))
        self.assertTrue(all(left.start <= right.start for left, right in zip(first, first[1:])))

    def test_segmentation_version_is_separate_from_raw_cache_identity(self):
        self.assertIsInstance(TRANSCRIPT_SEGMENTATION_VERSION, int)
        source = Path(__file__).parents[1] / "src/cartoon_sub/transcription/gemini_transcriber.py"
        code = source.read_text(encoding="utf-8")
        cache_key_block = code[code.index('key = content_hash'):code.index('path = self.cache_directory')]
        self.assertNotIn("TRANSCRIPT_SEGMENTATION_VERSION", cache_key_block)

    def test_completed_raw_cache_is_renormalized_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); audio = root / "source.wav"; cache = root / "cache"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
                stream.writeframes(b"\0\0" * 16000 * 30)
            # Create the exact cache path once through a fake client.
            class Client:
                calls = 0
                def transcribe_json(self, *args, **kwargs):
                    self.calls += 1
                    return {"segments": [{"start": 0, "end": 30,
                        "zh": "第一句。第二句。第三句。", "speaker_id": "SPK_UNKNOWN"}]}
                def close(self): pass
            client = Client()
            transcriber = GeminiTranscriber(lambda: "not-real", "model", cache, 0, lambda _: client)
            first = transcriber.transcribe(audio)
            self.assertGreater(len(first), 1)
            transcriber.client_factory = lambda _: self.fail("network must not be constructed")
            second = transcriber.transcribe(audio)
            self.assertEqual(client.calls, 1)
            self.assertEqual([(r.id, r.start, r.end, r.zh) for r in first],
                             [(r.id, r.start, r.end, r.zh) for r in second])
            saved = json.loads(next(cache.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(len(saved["response"]["segments"]), 1)

    def test_downstream_invalidation_only_when_signature_changes(self):
        old = [Segment(1, 0, 5, "你好。")]
        same = [Segment(1, 0, 5, "你好。")]
        changed = self.normalizer.normalize([Segment(1, 0, 20, "你好。再见。")])
        project = Project("p", "video.mp4", segments=old)
        project.speaker_review_hash = "approved"
        project.translation_status = "completed"
        project.final_audio_status = "generated"
        self.assertEqual(transcript_timeline_signature(old), transcript_timeline_signature(same))
        self.assertNotEqual(transcript_timeline_signature(old), transcript_timeline_signature(changed))
        # Caller does nothing for identical normalized output.
        self.assertEqual(project.speaker_review_hash, "approved")
        invalidate_transcript_dependents(project)
        self.assertEqual(project.speaker_review_hash, "")
        self.assertEqual(project.translation_status, "stale")
        self.assertEqual(project.final_audio_status, "stale")


if __name__ == "__main__":
    unittest.main()
