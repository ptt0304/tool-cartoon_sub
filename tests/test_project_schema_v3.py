import json
import math
import tempfile
import unittest
from pathlib import Path

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.subtitle.models import Project, Segment


class ProjectSchemaV3Tests(unittest.TestCase):
    @staticmethod
    def legacy_payload(schema_version):
        rows = [
            {
                "id": 31,
                "start": 10.0,
                "end": 13.0,
                "zh": "先听我说",
                "vi_subtitle": "Trước hết hãy nghe tôi nói.",
                "vi_dubbing": "Trước hết nghe tôi nói.",
                "speaker_id": "SPK_01",
                "speaker_name": "Nhân vật một",
                "overlap": True,
                "overlap_group": "OVL_001",
                "translation_mode": "balanced_dubbing",
                "tts_audio_path": "audio/tts/segments/legacy.wav",
                "tts_duration": 3.4,
            },
            {
                "id": 32,
                "start": 11.0,
                "end": 14.0,
                "zh": "我不想听",
                "vi_subtitle": "Ta không muốn nghe.",
                "vi_dubbing": "Không muốn nghe.",
                "speaker_id": "SPK_02",
                "speaker_name": "Nhân vật hai",
                "overlap": True,
                "overlap_group": "OVL_001",
            },
        ]
        payload = {
            "schema_version": schema_version,
            "name": "legacy",
            "source_video_path": "source.mp4",
            "metadata": {"duration": 20.0},
            "speakers": {
                "SPK_01": {"id": "SPK_01", "name": "Nhân vật một", "voice_notes": ""},
                "SPK_02": {"id": "SPK_02", "name": "Nhân vật hai", "voice_notes": ""},
            },
            "translation_prompt": "Giữ nguyên ý",
            "translation_notes": {"31": "Đã duyệt"},
        }
        payload["segments" if schema_version == 1 else "master_timeline"] = rows
        return payload

    def assert_legacy_preserved(self, project):
        self.assertEqual(project.schema_version, 3)
        self.assertEqual([row.id for row in project.utterances], [31, 32])
        self.assertEqual([(row.start, row.end) for row in project.utterances], [(10.0, 13.0), (11.0, 14.0)])
        self.assertEqual(project.utterances[0].vi_subtitle, "Trước hết hãy nghe tôi nói.")
        self.assertEqual(project.utterances[0].vi_dubbing, "Trước hết nghe tôi nói.")
        self.assertTrue(all(row.overlap for row in project.utterances))
        self.assertEqual(project.utterances[0].overlap_group, project.utterances[1].overlap_group)
        self.assertEqual(project.utterances[0].tts_audio_path, "audio/tts/segments/legacy.wav")
        self.assertEqual(project.utterances[0].tts_duration, 3.4)
        self.assertEqual(project.utterances[0].tts_generation_status, "not_generated")
        self.assertEqual(project.speakers["SPK_01"]["tts_voice_id"], None)
        self.assertEqual(project.speakers["SPK_01"]["tts_speed"], 1.0)
        self.assertEqual(project.translation_prompt, "Giữ nguyên ý")
        self.assertEqual(project.translation_notes, {"31": "Đã duyệt"})

    def test_schema_1_and_2_migrate_without_timeline_loss(self):
        for schema_version in (1, 2):
            with self.subTest(schema_version=schema_version):
                self.assert_legacy_preserved(Project.from_dict(self.legacy_payload(schema_version)))

    def test_schema_3_roundtrip_preserves_voice_mapping_and_tts_metadata(self):
        row = Segment(
            31, 10.0, 13.0, "先听我说",
            vi_subtitle="Trước hết hãy nghe tôi nói.",
            vi_dubbing="Trước hết nghe tôi nói.",
            speaker_id="SPK_01",
            speaker_name="Nhân vật một",
            tts_audio_path="audio/tts/segments/utt_000031_abc.wav",
            tts_duration=2.5,
            tts_segment_id="utt_000031_abc",
            tts_fingerprint="abc",
            tts_generation_status="generated",
        )
        project = Project(
            "v3", "source.mp4", metadata={"duration": 20.0}, segments=[row],
            speakers={"SPK_01": {
                "id": "SPK_01", "name": "Nhân vật một", "voice_notes": "",
                "tts_voice_id": "zk_voice", "tts_speed": 0.95,
            }},
        )
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.to_dict()["schema_version"], 3)
        self.assertEqual(loaded.speakers["SPK_01"]["tts_voice_id"], "zk_voice")
        self.assertEqual(loaded.speakers["SPK_01"]["tts_speed"], 0.95)
        self.assertEqual(loaded.utterances[0].tts_segment_id, "utt_000031_abc")
        self.assertEqual(loaded.utterances[0].tts_fingerprint, "abc")
        self.assertEqual(loaded.utterances[0].tts_generation_status, "generated")
        self.assertEqual(loaded.utterances[0].tts_alignment_status, "fits")

    def test_schema_2_save_creates_non_overwriting_backups(self):
        old = self.legacy_payload(2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_path = root / "project.json"
            manager = ProjectManager()
            project_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            project = manager.load(project_path)
            manager.save(project, root)
            first = root / "project.v2.backup.json"
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), old)

            project_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            manager.save(project, root)
            backups = list(root.glob("project.v2*.backup.json"))
            self.assertEqual(len(backups), 2)
            self.assertTrue(all(json.loads(path.read_text(encoding="utf-8")) == old for path in backups))

    def test_speaker_tts_validation(self):
        self.assertEqual(Speaker("SPK_01", tts_voice_id="voice", tts_speed=3.0).tts_voice_id, "voice")
        for voice_id in ("", "   ", 1):
            with self.subTest(voice_id=voice_id), self.assertRaises(ValueError):
                Speaker("SPK_01", tts_voice_id=voice_id)
        for speed in (0, -1, 3.01, math.inf, math.nan, True):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                Speaker("SPK_01", tts_speed=speed)

    def test_generation_status_validation(self):
        with self.assertRaises(ValueError):
            Segment(1, 0.0, 1.0, tts_generation_status="unknown")


if __name__ == "__main__":
    unittest.main()
