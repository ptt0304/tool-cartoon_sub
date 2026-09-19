import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from cartoon_sub.subtitle.models import Segment, Project
from cartoon_sub.speaker.service import refresh_timeline, approve_review, review_complete, detect_overlaps
from cartoon_sub.speaker import editor_service
from cartoon_sub.syllable.chinese import count as count_zh
from cartoon_sub.syllable.vietnamese import count as count_vi
from cartoon_sub.syllable.target import DubbingSettings, target_syllables, allowed_delta
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.translation.dubbing_service import DubbingService
from cartoon_sub.translation.qc import review_translation
from cartoon_sub.translation.context_service import source_fingerprint
from cartoon_sub.translation.pipeline import translation_fingerprint
from cartoon_sub.app.settings import AISettings
from cartoon_sub.tts.export_service import export_speakers
from cartoon_sub.transcription.gemini_transcriber import validate_response
from cartoon_sub.subtitle.parser import import_srt


def ready_project(mode="balanced_dubbing"):
    project=Project("test","missing.mp4",segments=[Segment(1,10,12,"跟你没关系",vi="Chuyện này không liên quan đến cô",speaker_id="SPK_01",translation_mode=mode)])
    approve_review(project)
    project.context_source_hash=source_fingerprint(project)
    return project


def store():
    result=Mock()
    result.load.return_value=AISettings(retry_count=0)
    result.get_key.return_value="fake-key"
    return result


def reply(text,compressed=False):
    return {"translations":[{"id":1,"vi":text,"review_note":"","meaning_preservation":"high","compressed":compressed}]}


class MasterTimelineTests(unittest.TestCase):
    def test_required_counts_and_normalization(self):
        self.assertEqual(count_zh("跟你没关系").count,5)
        self.assertEqual(count_vi("Chuyện này không liên quan đến cô").count,7)
        self.assertEqual(count_vi("... — !").count,0)
        self.assertEqual(count_zh("１２").count,2)
        self.assertEqual(count_vi("12").count,2)
        self.assertEqual(count_vi("15%").count,4)
        self.assertTrue(count_vi("AI 2024").warnings)
        self.assertEqual(count_vi("đi-làm").count,2)

    def test_overlap_never_changes_timing(self):
        segments=[Segment(31,10,13,"先听我说",speaker_id="SPK_01"),Segment(32,11,14,"不想听",speaker_id="SPK_02")]
        detect_overlaps(segments)
        self.assertEqual([(s.start,s.end) for s in segments],[(10,13),(11,14)])
        self.assertTrue(all(s.overlap for s in segments))
        self.assertEqual(segments[0].overlap_group,segments[1].overlap_group)

    def test_same_speaker_sequential_stays_independent(self):
        segments=[Segment(1,10,12,speaker_id="SPK_01"),Segment(2,12.1,14,speaker_id="SPK_01")]
        detect_overlaps(segments)
        self.assertEqual(len(segments),2)
        self.assertTrue(all(not s.overlap for s in segments))

    def test_targets_not_forced_to_chinese_count(self):
        self.assertEqual(target_syllables(5,2,DubbingSettings()),7)
        self.assertEqual(target_syllables(5,2,DubbingSettings(target_strategy="chinese_count")),5)
        self.assertEqual(target_syllables(5,2,DubbingSettings(target_strategy="hybrid")),6)
        s=Segment(1,0,2,"跟你没关系",translation_mode="time_fit")
        s.recalculate(DubbingSettings(target_strategy="chinese_count"))
        self.assertEqual(s.target_syllables,7)

    def test_v1_migration_backup_preserves_editorial_and_vi(self):
        old={"schema_version":1,"name":"old","source_video_path":"old.mp4",
             "segments":[{"id":17,"start":10,"end":13,"zh":"你好","vi":"Xin chào"},
                         {"id":18,"start":11,"end":14,"zh":"你好","vi":"Chào bạn"}],
             "translation_genres":["cultivation","system"],"translation_preset":"Light Classical",
             "translation_prompt":"Giữ sắc thái cổ phong","glossary":{"师兄":"sư huynh"}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"project.json"
            path.write_text(json.dumps(old,ensure_ascii=False),encoding="utf-8")
            manager=ProjectManager()
            project=manager.load(path)
            self.assertEqual(project.segments[0].vi_subtitle,"Xin chào")
            self.assertEqual(project.segments[0].vi_dubbing,"Xin chào")
            self.assertEqual(project.segments[0].speaker_id,"SPK_UNKNOWN")
            manager.save(project,directory)
            data=json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"],3)
            self.assertIn("master_timeline",data)
            self.assertNotIn("segments",data)
            self.assertEqual(data["translation_genres"],old["translation_genres"])
            self.assertEqual(data["glossary"],old["glossary"])
            self.assertEqual(json.loads((Path(directory)/"project.v1.backup.json").read_text(encoding="utf-8")),old)
            self.assertEqual(manager.load(directory).segments[0].start,10)

    def test_local_fields_recomputed_on_load_not_trusted(self):
        data=ready_project().to_dict()
        data["master_timeline"][0]["vi_syllables"]=999
        data["master_timeline"][0]["duration"]=1000
        project=Project.from_dict(data)
        self.assertEqual(project.segments[0].vi_syllables,7)
        self.assertEqual(project.segments[0].duration,2)

    def test_speaker_review_rename_assign_split_merge(self):
        project=Project("p","v",segments=[Segment(1,0,1),Segment(2,.5,2)])
        refresh_timeline(project)
        with self.assertRaises(ValueError):approve_review(project)
        key=editor_service.new_speaker(project)
        editor_service.assign(project,[1,2],key)
        editor_service.rename(project,key,"Tiểu Mỹ")
        approve_review(project)
        self.assertTrue(review_complete(project))
        second=editor_service.split(project,[2])
        self.assertFalse(review_complete(project))
        self.assertEqual(project.segments[1].start,.5)
        editor_service.merge(project,second,key)
        self.assertEqual(len(project.segments),2)

    def test_transcription_preserves_speakers_and_recomputes_overlap(self):
        payload={"segments":[{"id":1,"start":10,"end":13,"speaker_id":"SPK_01","text":"你先听我说", "overlap":False},
                             {"id":2,"start":11,"end":14,"speaker_id":"SPK_02","text":"不想听", "overlap":False}]}
        rows=validate_response(payload,20)
        self.assertEqual([s.speaker_id for s in rows],["SPK_01","SPK_02"])
        self.assertTrue(all(s.overlap for s in rows))
        self.assertTrue(all(s.speaker_name=="Unknown" for s in rows))

    def test_strict_rewrite_uses_local_count_and_preserves_subtitle(self):
        with tempfile.TemporaryDirectory() as directory:
            project=ready_project("strict_iso_syllabic")
            project.segments[0].target_override=5
            client=Mock()
            client.generate_json.side_effect=[reply("Chuyện này không liên quan đến cô"),reply("Không liên quan đến cô",True)]
            service=DubbingService(store(),lambda key:client)
            result,_=service.optimize(project,directory,[1])
            s=result.segments[0]
            self.assertEqual(s.vi_subtitle,"Chuyện này không liên quan đến cô")
            self.assertEqual(s.vi_dubbing,"Không liên quan đến cô")
            self.assertEqual(s.vi_syllables,5)
            self.assertEqual(s.dubbing_status,"completed")
            self.assertEqual(client.generate_json.call_count,2)
            self.assertIn('"actual_syllables": 7',client.generate_json.call_args.args[1])
            self.assertIn('"required_exactly": 5',client.generate_json.call_args.args[1])
            service.factory=Mock(side_effect=AssertionError("repeat unchanged must use cache"))
            service.optimize(result,directory,[1])

    def test_strict_failure_is_qc_failed_after_bounded_rewrites(self):
        with tempfile.TemporaryDirectory() as directory:
            project=ready_project("strict_iso_syllabic");project.segments[0].target_override=5
            client=Mock();client.generate_json.return_value=reply("Chuyện này không liên quan đến cô")
            result,_=DubbingService(store(),lambda key:client).optimize(project,directory,[1])
            self.assertEqual(client.generate_json.call_count,4)
            self.assertEqual(result.segments[0].dubbing_status,"failed")
            self.assertTrue(any("QC FAILED" in note for note in review_translation(result)["1"]))

    def test_short_mode_compression_and_independent_edit(self):
        s=ready_project().segments[0]
        original=s.vi_subtitle
        s.vi_dubbing="Mặc tôi";s.recalculate()
        self.assertEqual(s.vi_subtitle,original)
        self.assertEqual(s.vi_syllables,2)
        self.assertEqual(s.vi_subtitle_syllables,7)

    def test_per_speaker_export_preserves_overlap_and_master_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Project("p","missing",segments=[Segment(31,10,13,"你好",vi="Xin chào",speaker_id="SPK_01"),
                Segment(32,11,14,"你好",vi="Chào bạn",speaker_id="SPK_02"),Segment(35,18,20,"好",vi="Được",speaker_id="SPK_01")])
            approve_review(project)
            before=project.to_dict()
            root=export_speakers(project,directory)
            one=next(root.glob("SPK_01*"));two=next(root.glob("SPK_02*"))
            self.assertTrue((one/"segments"/"000031.txt").exists())
            self.assertTrue((one/"segments"/"000035.txt").exists())
            srt1=import_srt(one/"speaker.srt");srt2=import_srt(two/"speaker.srt")
            self.assertEqual((srt1[0].start,srt1[0].end),(10,13))
            self.assertEqual((srt2[0].start,srt2[0].end),(11,14))
            manifest=json.loads((one/"manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([r["id"] for r in manifest["segments"]],[31,35])
            self.assertEqual(project.to_dict(),before)

    def test_mode_duration_and_speaker_invalidate_translation_not_transcription(self):
        project=ready_project();settings=AISettings()
        baseline=translation_fingerprint(project,settings)
        project.cache_hashes["transcription"]="keep"
        project.segments[0].translation_mode="faithful"
        refresh_timeline(project)
        self.assertNotEqual(translation_fingerprint(project,settings),baseline)
        self.assertEqual(project.cache_hashes["transcription"],"keep")

    def test_future_tts_slot_metadata(self):
        s=Segment(1,10,12.1,tts_audio_path="000001.wav",tts_duration=2.4)
        self.assertAlmostEqual(s.tts_speed_factor,2.4/2.1)
        self.assertEqual(s.tts_alignment_status,"warning")


if __name__=="__main__":unittest.main()
