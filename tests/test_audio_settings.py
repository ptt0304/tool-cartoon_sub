import json
import unittest

from cartoon_sub.subtitle.models import AudioSettings, Project


class AudioSettingsTests(unittest.TestCase):
    def test_volume_validation(self):
        valid = AudioSettings(original_volume=0, dubbed_volume=50, additional_audio_volume=100, additional_audio_start=0.0).validate()
        self.assertEqual((valid.original_volume, valid.dubbed_volume, valid.additional_audio_volume), (0, 50, 100))

        with self.assertRaises(ValueError):
            AudioSettings(original_volume=-1).validate()

        with self.assertRaises(ValueError):
            AudioSettings(dubbed_volume=101).validate()

        with self.assertRaises(ValueError):
            AudioSettings(additional_audio_volume=-5).validate()

        with self.assertRaises(ValueError):
            AudioSettings(additional_audio_start=-1.0).validate()

    def test_serialization_round_trip(self):
        project = Project(
            "test_proj", "test.mp4", metadata={"duration": 10.0},
            audio_settings=AudioSettings(
                original_volume=30,
                dubbed_volume=80,
                additional_audio_path="audio/bgm.mp3",
                additional_audio_volume=50,
                additional_audio_start=2.5,
            ),
        )
        data = project.to_dict()
        self.assertEqual(data["audio_settings"]["original_volume"], 30)
        self.assertEqual(data["audio_settings"]["dubbed_volume"], 80)
        self.assertEqual(data["audio_settings"]["additional_audio_path"], "audio/bgm.mp3")
        self.assertEqual(data["audio_settings"]["additional_audio_volume"], 50)
        self.assertEqual(data["audio_settings"]["additional_audio_start"], 2.5)

        restored = Project.from_dict(json.loads(json.dumps(data)))
        self.assertEqual(restored.audio_settings.original_volume, 30)
        self.assertEqual(restored.audio_settings.dubbed_volume, 80)
        self.assertEqual(restored.audio_settings.additional_audio_path, "audio/bgm.mp3")
        self.assertEqual(restored.audio_settings.additional_audio_volume, 50)
        self.assertEqual(restored.audio_settings.additional_audio_start, 2.5)

    def test_old_project_defaults(self):
        old_data = {
            "name": "old_project",
            "source_video_path": "video.mp4",
            "schema_version": 2,
            "metadata": {"duration": 20.0},
            "segments": [],
        }
        loaded = Project.from_dict(old_data)
        self.assertIsInstance(loaded.audio_settings, AudioSettings)
        self.assertEqual(loaded.audio_settings.original_volume, 100)
        self.assertEqual(loaded.audio_settings.dubbed_volume, 100)
        self.assertIsNone(loaded.audio_settings.additional_audio_path)
        self.assertEqual(loaded.audio_settings.additional_audio_volume, 100)
        self.assertEqual(loaded.audio_settings.additional_audio_start, 0.0)
        self.assertEqual(loaded.final_audio_status, "not_generated")


if __name__ == "__main__":
    unittest.main()
