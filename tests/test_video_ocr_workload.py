import unittest

from cartoon_sub.transcription.video.ocr_workload import OcrWorkloadProgress, create_ocr_workload


ROI = {"x_pct": .2, "y_pct": .8, "w_pct": .6, "h_pct": .1}
REGION = {"id": "region_001", "start_time": 1.0, "end_time": 10.0, "roi": ROI}


def event(event_id=1, **changes):
    value = {
        "event_id": event_id, "region_id": "region_001", "refined_start": 2.0,
        "refined_end": 3.0, "representative_time": 2.5, "status": "ACCEPTED",
        "visual_signature_id": "test-signature",
    }
    value.update(changes)
    return value


class OcrWorkloadTests(unittest.TestCase):
    def test_only_valid_refined_region_events_enter_pending_workload(self):
        workload = create_ocr_workload([
            event(1), event(2, refined_end=2.0), event(3, status="VERY_SHORT"),
            event(4, region_id="missing"), event(5, representative_time=99),
            {"event_id": 6},
        ], [REGION], video_fingerprint={"source": "v"}, detector_version="test")
        self.assertEqual(workload["total_events"], 1)
        self.assertEqual(workload["visual_events_total"], 6)
        self.assertEqual(workload["visual_events_rejected"], 5)
        row = workload["events"][0]
        self.assertEqual((row["event_index"], row["event_id"], row["status"]), (1, 1, "PENDING"))
        self.assertTrue(row["representative_crop_path_or_identity"])
        self.assertEqual(row["visual_signature_id"], "test-signature")

    def test_workload_order_and_identity_are_stable(self):
        first = create_ocr_workload([event(2, refined_start=5, refined_end=6, representative_time=5.5), event(1)],
                                    [REGION], video_fingerprint={"source": "v"}, detector_version="test")
        second = create_ocr_workload([event(1), event(2, refined_start=5, refined_end=6, representative_time=5.5)],
                                     [REGION], video_fingerprint={"source": "v"}, detector_version="test")
        self.assertEqual(first["events"], second["events"])
        self.assertEqual([row["event_id"] for row in first["events"]], [1, 2])

    def test_progress_counts_each_terminal_event_once_including_cache_hits(self):
        progress = OcrWorkloadProgress(3)
        self.assertEqual(progress.text(), "Đang OCR Video: 0/3 event")
        self.assertEqual(progress.text(retry="1/1"), "Đang OCR Video: 0/3 event — retry 1/1")
        self.assertEqual(progress.terminal(1, "SUCCESS"), "Đang OCR Video: 1/3 event")
        self.assertEqual(progress.terminal(1, "SUCCESS"), "Đang OCR Video: 1/3 event")
        self.assertEqual(progress.terminal(2, "OCR_EMPTY"), "Đang OCR Video: 2/3 event")
        self.assertEqual(progress.terminal(3, "FAILED"), "Đang OCR Video: 3/3 event")
        with self.assertRaises(ValueError):
            progress.terminal(4, "PENDING")


if __name__ == "__main__":
    unittest.main()
