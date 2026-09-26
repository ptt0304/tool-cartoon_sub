import tempfile
import unittest
from pathlib import Path

import pysubs2

from cartoon_sub.subtitle.canonical_timeline import canonical_timeline
from cartoon_sub.subtitle.models import Project, Utterance
from cartoon_sub.subtitle.renderer import save_ass
from cartoon_sub.subtitle.timestamps import format_srt_timestamp, parse_srt_timestamp
from cartoon_sub.transcription.pipeline import save_subtitle_artifacts
from cartoon_sub.translation.artifacts import save_translation_artifacts


def _identity(path):
    rows = pysubs2.load(str(path), encoding="utf-8-sig")
    return [(index, row.start, row.end) for index, row in enumerate(rows, 1)]


class CanonicalTimelineTests(unittest.TestCase):
    def _project(self):
        rows = [
            Utterance(112, 508.0, 510.0, "前一句", vi="Câu trước", speaker_id="SPK_01"),
            Utterance(113, 511.065, 512.205, "对，你若不嫌弃。", vi="Đúng, nếu ngươi không chê.", speaker_id="SPK_01"),
            Utterance(114, 511.425, 513.125, "我给你画伏底阵。", vi="Ta vẽ phù đáy trận cho ngươi.", speaker_id="SPK_02"),
            Utterance(115, 520.0, 521.0, "后一句", vi="Câu sau", speaker_id="SPK_01"),
        ]
        rows[1].vi_dubbing = "Đúng, nếu ngươi không chê."
        rows[2].vi_dubbing = "Ta vẽ phù đáy trận cho ngươi."
        return Project("canonical", "missing.mp4", metadata={"width": 1280, "height": 720, "duration": 530}, segments=rows)

    def test_overlap_113_114_merges_without_speaker_labels(self):
        project = self._project()
        entry = next(row for row in canonical_timeline(project.utterances)
                     if row.source_utterance_ids == (113, 114))
        self.assertEqual((entry.start, entry.end), (511.065, 513.125))
        self.assertEqual(entry.speaker_ids, ("SPK_01", "SPK_02"))
        self.assertEqual(entry.chinese, "对，你若不嫌弃。 我给你画伏底阵。")
        self.assertEqual(entry.vi_subtitle, "Đúng, nếu ngươi không chê. Ta vẽ phù đáy trận cho ngươi.")
        self.assertNotIn("SPK_", entry.vi_subtitle)

    def test_transitive_overlap_and_adjacent_gap(self):
        rows = [
            Utterance(1, 1.0, 3.0, "A", vi="A"),
            Utterance(2, 2.0, 4.0, "B", vi="B"),
            Utterance(3, 3.5, 5.0, "C", vi="C"),
            Utterance(4, 5.0, 6.0, "D", vi="D"),
            Utterance(5, 6.1, 7.0, "E", vi="E"),
        ]
        entries = canonical_timeline(rows)
        self.assertEqual([row.source_utterance_ids for row in entries], [(1, 2, 3), (4,), (5,)])

    def test_all_canonical_srt_files_have_same_identity(self):
        project = self._project()
        project.translation_status = "completed"
        with tempfile.TemporaryDirectory() as directory:
            save_subtitle_artifacts(project, directory)
            save_translation_artifacts(project, directory)
            paths = [
                Path(directory) / "subtitle" / "zh.srt",
                Path(directory) / "subtitle" / "vi.srt",
                Path(directory) / "exports" / "translate" / "vi_subtitle.srt",
                Path(directory) / "exports" / "translate" / "vi_dubbing.srt",
            ]
            identities = [_identity(path) for path in paths]
            self.assertTrue(all(identity == identities[0] for identity in identities[1:]))
            self.assertEqual(len(identities[0]), 3)

    def test_renderer_uses_one_overlap_event_without_speaker_prefix(self):
        project = self._project()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "canonical.ass"
            save_ass(project, output)
            rows = pysubs2.load(str(output))
        overlap = next(row for row in rows if "phù đáy" in row.text)
        visible = overlap.text.replace(r"\N", " ").replace(r"\\N", " ")
        self.assertIn("Đúng, nếu ngươi không chê. Ta vẽ phù đáy trận cho ngươi.", visible)
        self.assertNotIn("SPK_01", overlap.text)
        self.assertNotIn("SPK_02", overlap.text)
        self.assertLessEqual(abs(overlap.start - 511065), 5)
        self.assertLessEqual(abs(overlap.end - 513125), 5)

    def test_timestamp_format_and_backward_compatible_parse(self):
        self.assertEqual(format_srt_timestamp(18.914), "00:00:18,914")
        self.assertEqual(format_srt_timestamp(511.065), "00:08:31,065")
        self.assertEqual(format_srt_timestamp(611.458), "00:10:11,458")
        self.assertEqual(parse_srt_timestamp("00:08:31,065"), 511.065)
        self.assertEqual(parse_srt_timestamp("511.065"), 511.065)


if __name__ == "__main__":
    unittest.main()
