import json
import tempfile
import unittest
import wave
from pathlib import Path

from cartoon_sub.project.cache_status_service import CacheStatusService, write_dubbed_mix_state
from cartoon_sub.project.stage_reset_service import ProjectStageResetService
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.tts.cache_manifest import empty_manifest, save_manifest, segment_manifest_key


def write_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(8000)
        writer.writeframes(b"\0\0" * 80)


class CacheStatusPanelTests(unittest.TestCase):
    def make_project(self, count=2):
        rows = [Segment(i, i, i + 1, f"中{i}", vi_subtitle=f"Phụ đề {i}",
                        vi_dubbing=f"Lồng tiếng {i}", speaker_id="SPK_01")
                for i in range(1, count + 1)]
        return Project("cache", "source.mp4", segments=rows, speakers={"SPK_01": {
            "id": "SPK_01", "name": "Một", "tts_voice_id": "voice_a", "tts_speed": 1.0,
        }})

    def add_entry(self, root, project, row, source):
        manifest_path = Path(root) / "audio" / "tts" / "tts_cache.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else empty_manifest()
        name = f"{row.tts_cache_key}_{source}.wav"
        write_wav(Path(root) / "audio" / "tts" / name)
        manifest["segments"][segment_manifest_key(row.tts_cache_key, source)] = {
            "signature": f"sig-{source}-{row.id}", "file": name, "source": source,
            "utterance_cache_key": row.tts_cache_key,
            "text": row.vi_subtitle if source == "vi_subtitle" else row.vi_dubbing,
            "speaker_id": row.speaker_id, "voice_id": "voice_a", "speed": 1.0,
        }
        save_manifest(root, manifest)

    def test_none_and_partial_are_derived_from_persisted_state(self):
        with tempfile.TemporaryDirectory() as root:
            project = self.make_project(2)
            self.assertEqual(CacheStatusService(project, root).transcript()["STT / nguồn transcript"], "NONE")
            self.add_entry(root, project, project.utterances[0], "vi_dubbing")
            audio = CacheStatusService(project, root).audio()
            self.assertEqual(audio["TTS — VI Dubbing"], "PARTIAL 1/2")
            self.assertEqual(audio["TTS — VI Subtitle"], "NONE")

    def test_source_clear_preserves_other_source_and_invalidates_dependent_mix(self):
        with tempfile.TemporaryDirectory() as root:
            project = self.make_project(1)
            self.add_entry(root, project, project.utterances[0], "vi_subtitle")
            self.add_entry(root, project, project.utterances[0], "vi_dubbing")
            write_wav(Path(root) / "audio" / "tts" / "dubbed_mix.wav")
            write_dubbed_mix_state(project, root, "vi_dubbing")
            before = CacheStatusService(project, root).audio()
            self.assertEqual(before["Dubbed Audio"], "CACHED")
            ProjectStageResetService(project, root).clear_audio_target("tts_vi_dubbing")
            after = CacheStatusService(project, root).audio()
            self.assertEqual(after["TTS — VI Dubbing"], "NONE")
            self.assertEqual(after["TTS — VI Subtitle"], "CACHED 1/1")
            self.assertEqual(after["Dubbed Audio"], "NONE")

    def test_clear_inactive_source_does_not_remove_current_mix(self):
        with tempfile.TemporaryDirectory() as root:
            project = self.make_project(1)
            project.audio_settings.tts_text_source = "vi_dubbing"
            self.add_entry(root, project, project.utterances[0], "vi_subtitle")
            self.add_entry(root, project, project.utterances[0], "vi_dubbing")
            write_wav(Path(root) / "audio" / "tts" / "dubbed_mix.wav")
            write_dubbed_mix_state(project, root, "vi_dubbing")
            ProjectStageResetService(project, root).clear_audio_target("tts_vi_subtitle")
            after = CacheStatusService(project, root).audio()
            self.assertEqual(after["TTS — VI Subtitle"], "NONE")
            self.assertEqual(after["TTS — VI Dubbing"], "CACHED 1/1")
            self.assertEqual(after["Dubbed Audio"], "CACHED")

    def test_stale_entry_is_not_counted_as_cached(self):
        with tempfile.TemporaryDirectory() as root:
            project = self.make_project(1)
            self.add_entry(root, project, project.utterances[0], "vi_dubbing")
            project.utterances[0].vi_dubbing = "Nội dung đã đổi"
            self.assertEqual(CacheStatusService(project, root).audio()["TTS — VI Dubbing"], "STALE")

    def test_clear_all_tts_clears_both_sources_and_mix(self):
        with tempfile.TemporaryDirectory() as root:
            project = self.make_project(1)
            self.add_entry(root, project, project.utterances[0], "vi_subtitle")
            self.add_entry(root, project, project.utterances[0], "vi_dubbing")
            write_wav(Path(root) / "audio" / "tts" / "dubbed_mix.wav")
            write_dubbed_mix_state(project, root, "vi_dubbing")
            ProjectStageResetService(project, root).clear_audio_target("tts_all")
            after = CacheStatusService(project, root).audio()
            self.assertEqual(after["TTS — VI Subtitle"], "NONE")
            self.assertEqual(after["TTS — VI Dubbing"], "NONE")
            self.assertEqual(after["Dubbed Audio"], "NONE")


if __name__ == "__main__":
    unittest.main()
