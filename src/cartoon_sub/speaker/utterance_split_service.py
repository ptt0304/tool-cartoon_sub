"""Canonical manual split operation for sequential multi-speaker speech."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4

from cartoon_sub.speaker.models import Speaker
from cartoon_sub.speaker.service import capture_initial_speaker_state, refresh_timeline
from cartoon_sub.subtitle.models import Utterance


UNKNOWN_SPEAKER = "SPK_UNKNOWN"


def _new_child(parent, *, utterance_id, start, end, text, speaker_id, speaker_name):
    return Utterance(
        id=utterance_id,
        start=start,
        end=end,
        zh=text,
        speaker_id=speaker_id,
        speaker_name=speaker_name,
        speaker_confidence=None,
        transcript_confidence=parent.transcript_confidence,
        translation_mode=parent.translation_mode,
        canonical_edit_source="manual_split",
        manual_split_parent_id=parent.id,
        tts_cache_key=uuid4().hex,
    )


def _invalidate_split_dependents(project):
    """Invalidate timeline-derived state without touching media or raw STT cache."""
    project.speaker_review_hash = ""
    project.context_proposal = {}
    project.context_proposal_hash = ""
    project.context_proposal_config_hash = ""
    project.context_status = "stale" if project.context_source_hash else "not_started"
    project.visual_context_status = "stale" if project.context_source_hash else "not_started"
    project.visual_context_signature = ""
    project.visual_context_error = ""
    project.speaker_evidence = []
    project.speaker_proposals = {}
    if project.translation_status != "not_started":
        project.translation_status = "stale"
    project.translation_notes = {}
    project.translation_qa = {}
    project.segmentation_cache = {}
    project.cache_hashes.pop("translation", None)
    project.cache_hashes.pop("translation_run", None)
    project.cache_hashes.pop("context_review_candidate_version", None)
    project.chunk_states.pop("translation", None)
    project.chunk_states.pop("dubbing", None)
    for row in project.utterances:
        row.display_segments = []
        if row.dubbing_optimized or row.dubbing_status != "not_started":
            row.dubbing_status = "stale"
        if row.tts_generation_status in {"generated", "cached"}:
            row.tts_generation_status = "stale"
            row.tts_error = ""
    project.final_audio_status = "stale"
    project.final_audio_fingerprint = ""


def split_utterance(project, utterance_id, boundary_index, split_time,
                    left_speaker_id, right_speaker_id):
    """Replace one canonical utterance by two ordered children.

    The left child retains the parent's stable ID.  The right child receives a
    new monotonically allocated ID; unrelated IDs never change.
    """
    order = [row.start for row in project.utterances]
    if order != sorted(order):
        raise ValueError("Canonical timeline không đúng thứ tự thời gian")
    try:
        index = next(i for i, row in enumerate(project.utterances) if row.id == utterance_id)
    except StopIteration as exc:
        raise ValueError("Không tìm thấy utterance cần tách") from exc
    parent = project.utterances[index]
    if parent.overlap_type == "LEGITIMATE_OVERLAP" or parent.overlap:
        raise ValueError("Dòng đang là speech chồng lấn; hãy giữ overlap, không tách tuần tự")
    if type(boundary_index) is not int or not 0 < boundary_index < len(parent.zh):
        raise ValueError("Ranh giới chữ phải nằm bên trong câu tiếng Trung")
    left_text, right_text = parent.zh[:boundary_index], parent.zh[boundary_index:]
    if not left_text.strip() or not right_text.strip():
        raise ValueError("Hai phần tiếng Trung đều phải có nội dung")
    if left_text + right_text != parent.zh:
        raise ValueError("Tách dòng làm mất hoặc lặp ký tự tiếng Trung")
    if type(split_time) not in (int, float) or not parent.start < split_time < parent.end:
        raise ValueError("Mốc thời gian phải nằm giữa Start và End")

    registry = deepcopy(project.speakers)
    if UNKNOWN_SPEAKER in (left_speaker_id, right_speaker_id) and UNKNOWN_SPEAKER not in registry:
        registry[UNKNOWN_SPEAKER] = asdict(Speaker(UNKNOWN_SPEAKER))
    for speaker_id in (left_speaker_id, right_speaker_id):
        if speaker_id not in registry:
            raise ValueError(f"Speaker không tồn tại: {speaker_id}")

    right_id = max((row.id for row in project.utterances), default=0) + 1
    left = _new_child(
        parent, utterance_id=parent.id, start=parent.start, end=float(split_time),
        text=left_text, speaker_id=left_speaker_id,
        speaker_name=registry[left_speaker_id]["name"],
    )
    right = _new_child(
        parent, utterance_id=right_id, start=float(split_time), end=parent.end,
        text=right_text, speaker_id=right_speaker_id,
        speaker_name=registry[right_speaker_id]["name"],
    )
    if left.duration <= 0 or right.duration <= 0:
        raise ValueError("Hai dòng con phải có duration dương")

    candidate_rows = list(project.utterances)
    candidate_rows[index:index + 1] = [left, right]
    new_order = [row.start for row in candidate_rows]
    if new_order != sorted(new_order):
        raise ValueError("Tách dòng làm sai thứ tự canonical timeline")
    project.speakers = registry
    project.utterances = candidate_rows
    _invalidate_split_dependents(project)
    refresh_timeline(project)
    capture_initial_speaker_state(project, replace=True)
    # The baseline now matches the new canonical timeline, but it has not been
    # user-confirmed.  Confirmation signs a fresh review hash later.
    project.speaker_review_hash = ""
    return left, right
