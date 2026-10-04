"""Crash-safe, per-conversation-chunk Translation checkpoints."""
from __future__ import annotations

import json
from pathlib import Path

from cartoon_sub.project.cache import atomic_json, content_hash

from .gemini_translator import TRANSLATION_SCHEMA, validate_translation_response
from .prompts import PROMPT_VERSION
from .prompts import effective_custom_rules


CHECKPOINT_VERSION = "translation-checkpoint-v1"


def chunk_fingerprint(project, settings, model, targets, before, after,
                      continuity_before, evidence_fingerprint):
    """Fingerprint every semantic input that can affect this chunk or later memory."""
    return content_hash({
        "checkpoint_version": CHECKPOINT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema": TRANSLATION_SCHEMA,
        "provider": settings.translation_provider,
        "model": model,
        "guidance": {
            "genres": project.translation_genres,
            "style": project.translation_preset,
            "proper_name_mode": project.proper_name_mode,
            "proper_name_rules": project.glossary,
            "user_defined_context": project.translation_prompt.strip(),
            "active_custom_rules": effective_custom_rules(project),
        },
        "targets": targets,
        "reference_before": before,
        "reference_after": after,
        "continuity_before": continuity_before,
        "evidence_fingerprint": evidence_fingerprint,
    })


class TranslationCheckpointStore:
    """Use the existing translation cache directory for durable resume data."""

    def __init__(self, directory):
        self.directory = Path(directory) / "checkpoints"

    def path(self, fingerprint):
        return self.directory / f"{fingerprint}.json"

    def load(self, fingerprint, expected_ids):
        path = self.path(fingerprint)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (payload.get("version") != CHECKPOINT_VERSION
                    or payload.get("status") != "completed"
                    or payload.get("input_fingerprint") != fingerprint
                    or payload.get("target_ids") != list(expected_ids)):
                return None
            response = {
                "translations": payload["translations"],
                "continuity_updates": [],
                "uncertainties": payload.get("uncertainties", []),
            }
            validated = validate_translation_response(response, expected_ids)
            continuity = payload.get("continuity_state_after")
            if (not isinstance(continuity, list) or len(continuity) > 64
                    or any(not isinstance(item, dict)
                           or not isinstance(item.get("key"), str)
                           or not item.get("key", "").strip()
                           or len(item.get("key", "")) > 120
                           or not isinstance(item.get("value"), str)
                           or not item.get("value", "").strip()
                           or len(item.get("value", "")) > 240
                           or type(item.get("confidence")) not in (int, float)
                           or not .70 <= float(item.get("confidence")) <= 1.0
                           for item in continuity)):
                return None
            return {
                "translations": validated["translations"],
                "continuity_state_after": continuity,
                "uncertainties": validated.get("uncertainties", []),
                "path": path,
            }
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def save(self, fingerprint, target_ids, translations, continuity_after,
             uncertainties, model, evidence_fingerprint):
        path = self.path(fingerprint)
        atomic_json(path, {
            "version": CHECKPOINT_VERSION,
            "status": "completed",
            "input_fingerprint": fingerprint,
            "target_ids": list(target_ids),
            "translations": translations,
            "continuity_state_after": continuity_after,
            "uncertainties": list(uncertainties or []),
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "evidence_fingerprint": evidence_fingerprint,
        })
        return path
