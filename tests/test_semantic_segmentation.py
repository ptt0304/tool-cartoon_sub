import unittest
from unittest.mock import Mock

from cartoon_sub.app.settings import AISettings
from cartoon_sub.subtitle.models import DisplaySegment, Project, Utterance
from cartoon_sub.subtitle.semantic_segmentation import (SemanticSegmentationService,
    SemanticSegmentationValidationError, validate_parts)
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService


TEXT = "Đây là một câu rất dài không có dấu câu và vẫn cần được chia thành hai phần để người xem đọc dễ hơn"
PARTS = ("Đây là một câu rất dài không có dấu câu ", "và vẫn cần được chia thành hai phần để người xem đọc dễ hơn")


def store():
    result = Mock()
    result.load.return_value = AISettings(retry_count=0)
    result.get_key.return_value = "fake-key"
    return result


class SemanticSegmentationTests(unittest.TestCase):
    def test_validation_rejects_rewrite_addition_and_reorder(self):
        self.assertEqual(validate_parts({"parts": list(PARTS)}, TEXT, 3), PARTS)
        for parts in (("Đây là câu khác",), (PARTS[1], PARTS[0]), (PARTS[0], PARTS[1] + " thêm")):
            with self.subTest(parts=parts), self.assertRaises(SemanticSegmentationValidationError):
                validate_parts({"parts": list(parts)}, TEXT, 3)

    def test_validation_restores_boundary_spaces_trimmed_by_structured_output(self):
        trimmed = [part.strip() for part in PARTS]
        self.assertEqual(validate_parts({"parts": trimmed}, TEXT, 3), PARTS)

    def test_local_success_never_calls_gemini(self):
        client_factory = Mock()
        semantic = SemanticSegmentationService(store(), client_factory)
        service = SubtitleSegmentationService()
        project = Project("p", "source.mp4", segments=[Utterance(1, 0, 2.4, "中文",
            vi="Chuyện này không liên quan đến cô.", speaker_id="SPK_01")])
        service.auto_segment(project)
        client_factory.assert_not_called()
        self.assertEqual(len(project.segments[0].display_segments), 1)

    def test_unresolved_local_text_uses_local_whitespace_fallback_only(self):
        client = Mock()
        semantic = SemanticSegmentationService(store(), lambda key: client)
        service = SubtitleSegmentationService()
        utterance = Utterance(1, 0, 6, "中文", vi=TEXT, speaker_id="SPK_02")
        project = Project("p", "source.mp4", segments=[utterance])
        service.auto_segment(project)
        self.assertEqual("".join(item.vi_text for item in utterance.display_segments), TEXT)
        self.assertTrue(all(item.speaker_id == "SPK_02" for item in utterance.display_segments))
        self.assertTrue(all(item.vi_syllables <= 14 for item in utterance.display_segments))
        client.generate_json.assert_not_called()

    def test_unbreakable_text_never_calls_gemini_and_is_marked_for_review(self):
        unbreakable = "mộtchuỗirấtdàikhônghềcókhoảngtrắngđểtách" * 3
        client = Mock()
        semantic = SemanticSegmentationService(store(), lambda key: client)
        service = SubtitleSegmentationService()
        existing = DisplaySegment("1.1", 1, 0, 6, unbreakable, segmentation_reason="local")
        utterance = Utterance(1, 0, 6, "中文", vi=unbreakable, speaker_id="SPK_01", display_segments=[existing])
        project = Project("p", "source.mp4", segments=[utterance])
        before = (existing.id, existing.start, existing.end, existing.vi_text)
        service.auto_segment(project)
        after = utterance.display_segments[0]
        self.assertEqual((after.id, after.start, after.end, after.vi_text), before)
        self.assertEqual(utterance.vi_subtitle, unbreakable)
        self.assertIn("MANUAL_REVIEW", after.qc_flags)
        client.generate_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
