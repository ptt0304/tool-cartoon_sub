import json
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import DisplaySegment, Project, Segment, SubtitleSegment, Utterance


class SegmentationModelTests(unittest.TestCase):
    def utterance(self, speaker_id="SPK_02"):
        return Utterance(12, 32.2, 36.2, "你先听我说", vi_subtitle="Trước hết hãy nghe tôi nói.",
            vi_dubbing="Trước hết nghe tôi nói.", speaker_id=speaker_id, speaker_name="Quan viên",
            overlap=True, overlap_group="OVL_001", semantic_compression=True,
            tts_audio_path="tts/000012.wav", tts_duration=3.4,
            display_segments=[DisplaySegment("12.1", 12, 32.2, 34.1, "Trước hết hãy nghe"),
                              DisplaySegment("12.2", 12, 34.1, 36.2, "tôi nói.", segmentation_reason="manual", manual=True)])

    def test_utterance_save_load_preserves_master_timeline_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Project("p", "source.mp4", segments=[self.utterance(),
                Utterance(13, 33, 35, "请听", vi="Hãy nghe", speaker_id="SPK_01")])
            ProjectManager().save(project, directory)
            loaded = ProjectManager().load(directory)
            row = loaded.segments[0]
            self.assertIsInstance(row, Utterance)
            self.assertEqual((row.id, row.speaker_id, row.start, row.end, row.zh),
                             (12, "SPK_02", 32.2, 36.2, "你先听我说"))
            self.assertEqual((row.vi_subtitle, row.vi_dubbing, row.overlap),
                             ("Trước hết hãy nghe tôi nói.", "Trước hết nghe tôi nói.", True))
            self.assertEqual((row.tts_audio_path, row.tts_duration), ("tts/000012.wav", 3.4))

    def test_display_segment_roundtrip_recomputes_local_measurements(self):
        row = self.utterance()
        data = row.to_dict()
        data["display_segments"][0]["vi_syllables"] = 999
        data["display_segments"][0]["line_count"] = 999
        loaded = Utterance.from_dict(data)
        child = loaded.display_segments[0]
        self.assertEqual(child.id, "12.1")
        self.assertEqual(child.utterance_id, 12)
        self.assertEqual((child.start, child.end, child.vi_text), (32.2, 34.1, "Trước hết hãy nghe"))
        self.assertEqual(child.vi_syllables, 4)
        self.assertEqual(child.line_count, 1)
        self.assertNotIn("speaker_id", child.to_dict())

    def test_display_segment_inherits_parent_speaker(self):
        row = self.utterance()
        self.assertTrue(all(child.speaker_id == "SPK_02" for child in row.display_segments))
        row.speaker_id = "SPK_03"
        row.recalculate()
        self.assertTrue(all(child.speaker_id == "SPK_03" for child in row.display_segments))

    def test_cross_speaker_overlap_is_valid_for_utterances_and_display_segments(self):
        first = Utterance(1, 10, 14, "甲", vi="Người thứ nhất", speaker_id="SPK_01",
            display_segments=[DisplaySegment("1.1", 1, 10, 14, "Người thứ nhất")])
        second = Utterance(2, 11, 13, "乙", vi="Người thứ hai", speaker_id="SPK_02",
            display_segments=[DisplaySegment("2.1", 2, 11, 13, "Người thứ hai")])
        project = Project("overlap", "source.mp4", segments=[first, second])
        restored = Project.from_dict(project.to_dict())
        self.assertEqual([(row.start, row.end) for row in restored.segments], [(10, 14), (11, 13)])
        self.assertEqual([(row.display_segments[0].start, row.display_segments[0].end) for row in restored.segments],
                         [(10, 14), (11, 13)])

    def test_old_project_without_display_segments_loads_empty_presentation_state(self):
        old = {"schema_version": 2, "name": "old", "source_video_path": "source.mp4",
               "master_timeline": [{"id": 1, "start": 1, "end": 3, "zh": "你好", "vi": "Xin chào",
                                    "speaker_id": "SPK_01"}]}
        project = Project.from_dict(old)
        self.assertEqual(project.segments[0].display_segments, [])
        self.assertIs(project.utterances, project.segments)
        self.assertEqual(project.segments[0].vi_subtitle, "Xin chào")
        self.assertEqual(Segment, Utterance)
        self.assertEqual(SubtitleSegment, Utterance)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            loaded = ProjectManager().load(path)
            self.assertEqual(loaded.segments[0].display_segments, [])


if __name__ == "__main__":
    unittest.main()
