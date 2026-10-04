"""Read-only export of the complete Context-analysis diagnostic state."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path

from cartoon_sub.ai.language_contract import (
    USER_FACING_AI_LANGUAGE,
    USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
)
from cartoon_sub.project.cache import atomic_json
from cartoon_sub.speaker.service import review_complete
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.context_service import (
    CONTEXT_REVIEW_CANDIDATE_VERSION,
    context_config_fingerprint,
    source_fingerprint,
    translation_readiness,
)
from cartoon_sub.translation.prompts import PROMPT_VERSION
from cartoon_sub.translation.visual_context import (
    VISUAL_ANALYSIS_VERSION,
    VISUAL_PROMPT_SCHEMA_VERSION,
    visual_id_diagnostics,
)


CONTEXT_AI_EXPORT_VERSION = 1
_SECRET_PARTS = ("authorization", "api_key", "apikey", "credential", "secret", "token")


def _application_version():
    try:
        return version("cartoon-sub")
    except PackageNotFoundError:
        return "0.3.0"


def sanitize_export(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if folded == "headers" or any(part in folded for part in _SECRET_PARTS):
                continue
            clean[key] = sanitize_export(item)
        return clean
    if isinstance(value, list):
        return [sanitize_export(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_export(item) for item in value]
    return value


def _validate_context(value, valid_ids):
    if not value:
        return False, "Không có dữ liệu"
    try:
        StoryContext.from_dict(value, valid_ids)
        return True, "PASS"
    except (TypeError, ValueError) as exc:
        return False, str(exc)


def _visual_chunks(request_plan, model):
    chunks = []
    for item in deepcopy(list(request_plan or [])):
        requested = list(item.get("target_ids", []))
        returned = list(item.get("returned_ids", []))
        diagnostics = visual_id_diagnostics(
            {"visual_contexts": [{"id": value} for value in returned]}, requested)
        chunks.append({
            **item,
            "model": item.get("model") or model,
            "missing_ids": diagnostics["missing_ids"],
            "extra_ids": diagnostics["extra_ids"],
            "duplicate_ids": diagnostics["duplicate_ids"],
        })
    return chunks


def _retained_responses(directory):
    root = Path(directory) / "cache" / "visual_context"
    diagnostics, responses = [], []
    for path in sorted((root / "diagnostics").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        diagnostics.append({"artifact": path.name, **sanitize_export(payload)})
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("status") == "completed" and "response" in payload:
            responses.append({"artifact": path.name, "response": sanitize_export(payload["response"])})
    return diagnostics, responses


def build_context_ai_diagnostic(project, directory, request_plan=None):
    """Create a JSON-safe snapshot without mutating or refreshing the project."""
    valid_ids = {row.id for row in project.utterances}
    expected_ids = [row.id for row in project.utterances]
    candidate = deepcopy(project.context_proposal or {})
    approved = deepcopy(project.story_context or {})
    review_context = candidate or approved or StoryContext().to_dict()
    visual_rows = list(review_context.get("visual_contexts", []))
    visual_coverage = visual_id_diagnostics(
        {"visual_contexts": visual_rows}, expected_ids)
    speaker_valid = review_complete(project)
    candidate_valid, candidate_reason = _validate_context(candidate, valid_ids)
    approved_valid, approved_reason = _validate_context(approved, valid_ids)
    ready, readiness_reason = translation_readiness(project)
    source_hash = source_fingerprint(project) if project.utterances else ""
    config_hash = context_config_fingerprint(project)
    approved_fresh = bool(project.context_source_hash) and (
        project.context_source_hash == source_hash
        and project.context_approved_config_hash == config_hash
    )
    selected_models = deepcopy(project.selected_models)
    effective_model = selected_models.get("vision_speaker") or selected_models.get("translation")
    chunks = _visual_chunks(request_plan, effective_model)
    retained_diagnostics, raw_responses = _retained_responses(directory)
    known_chunk_artifacts = {item.get("artifact") for item in chunks}
    for item in retained_diagnostics:
        if item.get("artifact") in known_chunk_artifacts:
            continue
        chunks.append({
            "artifact": item.get("artifact"),
            "model": item.get("model") or effective_model,
            "target_ids": deepcopy(item.get("requested_ids", [])),
            "returned_ids": deepcopy(item.get("returned_ids", [])),
            "missing_ids": deepcopy(item.get("missing_ids", [])),
            "extra_ids": deepcopy(item.get("extra_ids", [])),
            "duplicate_ids": deepcopy(item.get("duplicate_ids", [])),
            "validation": "FAIL",
            "failure_reason": item.get("validation_reason", ""),
            "http_status": item.get("http_status"),
        })
    failed_attempts = sum(item.get("validation") == "FAIL" for item in chunks)
    final_visual_valid = (
        project.visual_context_status in {"proposal_ready", "applied"}
        and visual_coverage["exact_set"]
    )
    recovered = failed_attempts > 0 and final_visual_valid

    blocking = []
    if not speaker_valid:
        blocking.append("SPEAKER_REVIEW_INCOMPLETE")
    if project.visual_context_status not in {"proposal_ready", "applied"}:
        blocking.append(project.visual_context_status or "VISUAL_CONTEXT_NOT_READY")
    if project.visual_context_error:
        blocking.append("VISUAL_CONTEXT_ERROR: " + project.visual_context_error)
    if visual_coverage["missing_ids"]:
        blocking.append("VISUAL_TARGET_IDS_MISSING")
    if visual_coverage["extra_ids"]:
        blocking.append("VISUAL_TARGET_IDS_EXTRA")
    if visual_coverage["duplicate_ids"]:
        blocking.append("VISUAL_TARGET_IDS_DUPLICATE")
    if candidate and not candidate_valid:
        blocking.append("CANDIDATE_SCHEMA_INVALID")
    if not candidate and project.context_status != "applied":
        blocking.append("CANDIDATE_CONTEXT_UNAVAILABLE")
    if project.context_status == "applied" and not approved_fresh:
        blocking.append("APPROVED_CONTEXT_STALE")

    relationships = [
        {"character_id": row.get("character_id"), "relationship": relationship}
        for row in review_context.get("character_profiles", [])
        for relationship in row.get("relationships", [])
    ]
    timeline = [{
        "id": row.id,
        "start": row.start,
        "end": row.end,
        "source_text": row.zh,
        "vi_subtitle": row.vi_subtitle,
        "vi_dubbing": row.vi_dubbing,
        "speaker_id": row.speaker_id,
        "speaker_name": row.speaker_name,
        "speaker_confidence": row.speaker_confidence,
        "speaker_review_state": "USER_CONFIRMED" if speaker_valid else "UNCONFIRMED",
        "overlap": row.overlap,
        "overlap_group": row.overlap_group,
        "overlap_type": row.overlap_type,
        "canonical_edit_source": row.canonical_edit_source,
        "manual_split_parent_id": row.manual_split_parent_id,
    } for row in project.utterances]

    payload = {
        "export_meta": {
            "schema": "context_ai.json",
            "version": CONTEXT_AI_EXPORT_VERSION,
            "application_version": _application_version(),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "versions": {
                "context_candidate": CONTEXT_REVIEW_CANDIDATE_VERSION,
                "visual_analysis": VISUAL_ANALYSIS_VERSION,
                "visual_prompt_schema": VISUAL_PROMPT_SCHEMA_VERSION,
                "translation_prompt": PROMPT_VERSION,
                "user_facing_language": USER_FACING_AI_LANGUAGE,
                "language_contract": USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
            },
            "selected_models": selected_models,
            "effective_visual_model": effective_model,
            "visual_mode": selected_models.get("vision_input_mode"),
            "visual_strategy": "dialogue_windows_adaptive_frames"
                               if selected_models.get("vision_input_mode") == "frames"
                               else "bounded_direct_video",
        },
        "project": {
            "name": project.name,
            "schema_version": project.schema_version,
            "source_video": Path(project.source_video_path).name,
            "transcription_status": project.transcription_status,
            "translation_status": project.translation_status,
        },
        "timeline": {"count": len(timeline), "utterances": timeline},
        "analysis_status": {
            "speaker_review": "complete" if speaker_valid else "incomplete",
            "speaker_review_complete": speaker_valid,
            "candidate_context_status": project.context_status,
            "candidate_available": bool(candidate),
            "approved_context_status": "approved" if project.context_status == "applied" else "not_approved",
            "approved_context_available": bool(project.context_source_hash),
            "visual_context_status": project.visual_context_status,
            "visual_error": project.visual_context_error,
            "visual_rows_expected": len(expected_ids),
            "visual_rows_available": len(visual_rows),
            "expected_target_ids": expected_ids,
            "available_target_ids": visual_coverage["returned_ids"],
            "missing_ids": visual_coverage["missing_ids"],
            "extra_ids": visual_coverage["extra_ids"],
            "duplicate_ids": visual_coverage["duplicate_ids"],
            "approved_context_fresh": approved_fresh,
            "translation_ready": ready,
            "translation_readiness_reason": readiness_reason,
        },
        "story_context": {
            "candidate_story_context": candidate,
            "approved_story_context": approved,
        },
        "characters": deepcopy(review_context.get("characters", [])),
        "terms": deepcopy(review_context.get("terms", [])),
        "addressing_rules": deepcopy(review_context.get("address_rules", [])),
        "relationships": relationships,
        "speaker_character_mapping": {
            "context_mappings": deepcopy(review_context.get("speaker_character_mappings", [])),
            "speaker_proposals": deepcopy(project.speaker_proposals),
            "canonical_assignments": [
                {"utterance_id": row.id, "spk_id": row.speaker_id,
                 "status": "USER_CONFIRMED" if speaker_valid else "UNCONFIRMED"}
                for row in project.utterances
            ],
        },
        "visual_characters": deepcopy(review_context.get("character_profiles", [])),
        "visual_context_by_id": visual_rows,
        "uncertainties": deepcopy(review_context.get("uncertainties", [])),
        "speaker_evidence": deepcopy(project.speaker_evidence),
        "validation": {
            "success": not blocking,
            "blocking_reasons": blocking,
            "warnings": [],
            "speaker_review_valid": speaker_valid,
            "visual_context_valid": final_visual_valid,
            "target_id_coverage_valid": visual_coverage["exact_set"],
            "candidate_valid": candidate_valid,
            "candidate_validation_reason": candidate_reason,
            "approved_context_valid": approved_valid,
            "approved_context_validation_reason": approved_reason,
            "approved_context_fresh": approved_fresh,
        },
        "diagnostics": {
            "visual_chunks": chunks,
            "failed_attempts": failed_attempts,
            "recovered": recovered,
            "final_coverage_valid": visual_coverage["exact_set"],
            "retained_failures": retained_diagnostics,
            "raw_response_available": bool(raw_responses or any(
                item.get("response") is not None for item in retained_diagnostics)),
            "raw_ai_responses": raw_responses,
        },
    }
    return sanitize_export(payload)


def export_context_ai_json(payload, path):
    atomic_json(path, sanitize_export(payload))
    return Path(path)
