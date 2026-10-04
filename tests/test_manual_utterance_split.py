import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import commit_speaker_review_session, review_complete
from cartoon_sub.speaker.utterance_split_service import split_utterance
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.transcription.pipeline import TranscriptionPipeline


def make_project():
    project = Project("split", "source.mp4", segments=[
        Segment(2, 0.0, 1.0, "前句", speaker_id="SPK_01"),
        Segment(7, 1.0, 4.0, "好耶 出发找姐姐喽小主人", speaker_id="SPK_01"),
        Segment(11, 4.0, 5.0, "后句", speaker_id="SPK_02"),
    ])
    project.speakers = {
        "SPK_01": asdict(Speaker("SPK_01", "Girl")),
        "SPK_02": asdict(Speaker("SPK_02", "Cat")),
        "SPK_UNKNOWN": asdict(Speaker("SPK_UNKNOWN")),
    }
    return project


class ManualUtteranceSplitTests(unittest.TestCase):
    def test_success_preserves_chinese_timing_order_speakers_and_stable_ids(self):
        project = make_project()
        parent_text = project.utterances[1].zh
        boundary = parent_text.index("找")
        left, right = split_utterance(project, 7, boundary, 2.25, "SPK_01", "SPK_02")
        self.assertEqual(left.zh + right.zh, parent_text)
        self.assertEqual((left.start, left.end, right.start, right.end), (1.0, 2.25, 2.25, 4.0))
        self.assertEqual([row.id for row in project.utterances], [2, 7, 12, 11])
        self.assertEqual((left.speaker_id, right.speaker_id), ("SPK_01", "SPK_02"))
        self.assertEqual((left.canonical_edit_source, right.canonical_edit_source),
                         ("manual_split", "manual_split"))
        self.assertEqual((left.manual_split_parent_id, right.manual_split_parent_id), (7, 7))
        self.assertGreater(left.duration, 0); self.assertGreater(right.duration, 0)

    def test_unknown_is_allowed_and_bad_boundaries_preserve_project(self):
        project = make_project()
        left, right = split_utterance(project, 7, 2, 2.0, "SPK_UNKNOWN", "SPK_02")
        self.assertEqual(left.speaker_id, "SPK_UNKNOWN")
        self.assertEqual(left.zh + right.zh, "好耶 出发找姐姐喽小主人")
        for boundary, timestamp in ((0, 2.0), (2, 1.0), (2, 4.0)):
            untouched = make_project()
            with self.assertRaises(ValueError):
                split_utterance(untouched, 7, boundary, timestamp, "SPK_01", "SPK_02")
            self.assertEqual([row.id for row in untouched.utterances], [2, 7, 11])

    def test_reload_persists_manual_authority(self):
        project = make_project()
        split_utterance(project, 7, 3, 2.0, "SPK_01", "SPK_02")
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectManager()
            manager.save(project, directory)
            loaded = manager.load(Path(directory) / "project.json")
        self.assertEqual([row.id for row in loaded.utterances], [2, 7, 12, 11])
        self.assertEqual([row.canonical_edit_source for row in loaded.utterances[1:3]],
                         ["manual_split", "manual_split"])

    def test_split_invalidates_dependents_but_preserves_media_and_raw_stt(self):
        project = make_project()
        project.context_source_hash = "approved-context"
        project.context_status = "applied"
        project.context_proposal = {"summary": "old"}
        project.speaker_evidence = [{"utterance_id": 7, "frame": "reuse.jpg"}]
        project.speaker_proposals = {"utterances": [{"utterance_id": 7}]}
        project.translation_status = "completed"
        project.translation_notes = {"7": "old"}
        project.translation_qa = {"7": {"status": "PASS"}}
        project.cache_hashes = {"transcription_raw": "raw-stt", "translation": "old"}
        project.chunk_states = {"translation": {"x": {}}, "dubbing": {"x": {}}}
        project.final_audio_status = "ready"
        project.final_audio_fingerprint = "final"
        project.utterances[0].tts_generation_status = "generated"
        source = project.source_video_path
        split_utterance(project, 7, 3, 2.0, "SPK_01", "SPK_02")
        self.assertEqual(project.source_video_path, source)
        self.assertEqual(project.cache_hashes["transcription_raw"], "raw-stt")
        self.assertNotIn("translation", project.cache_hashes)
        self.assertEqual(project.context_status, "stale")
        self.assertEqual(project.context_proposal, {})
        self.assertEqual((project.speaker_evidence, project.speaker_proposals), ([], {}))
        self.assertEqual(project.translation_status, "stale")
        self.assertEqual((project.translation_notes, project.translation_qa), ({}, {}))
        self.assertEqual(project.utterances[0].tts_generation_status, "stale")
        self.assertEqual((project.final_audio_status, project.final_audio_fingerprint), ("stale", ""))
        self.assertFalse(review_complete(project))

    def test_manual_split_short_circuits_transcription_reprocessing(self):
        project = make_project()
        split_utterance(project, 7, 3, 2.0, "SPK_01", "SPK_02")
        factory = Mock(side_effect=AssertionError("STT must not be called"))
        pipeline = TranscriptionPipeline(Mock(), media=Mock(), transcriber_factory=factory)
        result, directory = pipeline.run(project, "ignored")
        self.assertEqual([row.id for row in result.utterances], [2, 7, 12, 11])
        factory.assert_not_called()
        pipeline.media.extract_audio.assert_not_called()
        self.assertEqual(directory, Path("ignored"))

    def test_subtitle_auto_segmentation_does_not_remerge_manual_children(self):
        project = make_project()
        left, right = split_utterance(project, 7, 3, 2.0, "SPK_01", "SPK_02")
        left.vi_subtitle = "Đi thôi"
        right.vi_subtitle = "tìm chị nào, chủ nhân nhỏ."
        before = [(row.id, row.start, row.end, row.zh) for row in project.utterances]
        SubtitleSegmentationService().auto_segment(project, [left.id, right.id], force=True)
        self.assertEqual([(row.id, row.start, row.end, row.zh) for row in project.utterances], before)

    def test_legitimate_overlap_is_not_converted_to_sequential_split(self):
        project = make_project()
        parent = project.utterances[1]
        parent.overlap = True
        parent.overlap_type = "LEGITIMATE_OVERLAP"
        with self.assertRaisesRegex(ValueError, "chồng lấn"):
            split_utterance(project, 7, 3, 2.0, "SPK_01", "SPK_02")
        self.assertEqual([row.id for row in project.utterances], [2, 7, 11])

    def test_atomic_confirm_commits_working_timeline_and_assignments(self):
        canonical = make_project()
        working = Project.from_dict(canonical.to_dict())
        split_utterance(working, 7, 3, 2.0, "SPK_01", "SPK_02")
        assignments = {row.id: row.speaker_id for row in working.utterances}
        self.assertEqual([row.id for row in canonical.utterances], [2, 7, 11])
        commit_speaker_review_session(canonical, working, working.speakers, assignments)
        self.assertEqual([row.id for row in canonical.utterances], [2, 7, 12, 11])
        self.assertTrue(review_complete(canonical))
        self.assertEqual(canonical.context_status, "not_started")


if __name__ == "__main__":
    unittest.main()
