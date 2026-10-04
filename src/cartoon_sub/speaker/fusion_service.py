"""Deterministic, provider-neutral speaker evidence fusion.

Fusion produces review proposals only.  It never changes the canonical
``Utterance.speaker_id``; the existing Speaker Review commit remains the only
authority that can confirm assignments.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import re
import unicodedata

from cartoon_sub.ai.language_contract import (
    USER_FACING_AI_LANGUAGE,
    USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
)
from cartoon_sub.project.cache import atomic_json, content_hash
from cartoon_sub.speaker.service import UNKNOWN_SPEAKER, review_complete
from cartoon_sub.transcription.contracts import normalize_confidence, normalize_speaker_hint
from cartoon_sub.translation.context_models import StoryContext


SPEAKER_EVIDENCE_FUSION_VERSION = 2
STRONG_VISUAL_CONFIDENCE = 0.65
UNKNOWN_CHARACTER = "UNKNOWN"


@dataclass(frozen=True)
class FusedEvidence:
    utterance_id: int
    source: str
    speaker_id: str = UNKNOWN_SPEAKER
    character_id: str = UNKNOWN_CHARACTER
    semantic_label: str = ""
    addressee_id: str = UNKNOWN_CHARACTER
    confidence: float | None = None
    status: str = "UNKNOWN"
    reason: str = ""
    source_fingerprint: str = ""


def load_visual_evidence_cache(directory, valid_ids):
    """Load completed Phase 3.1 cache entries without invoking Vision."""
    merged = StoryContext().to_dict()
    files = []
    cache_dir = Path(directory) / "cache" / "visual_context"
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "completed":
                continue
            from cartoon_sub.translation.visual_context import (
                VISUAL_ANALYSIS_VERSION, VISUAL_PROMPT_SCHEMA_VERSION, VisualContextAnalyzer,
            )
            if (payload.get("visual_analysis_version") != VISUAL_ANALYSIS_VERSION
                    or payload.get("prompt_schema_version") != VISUAL_PROMPT_SCHEMA_VERSION):
                continue
            if (payload.get("user_facing_ai_language") != USER_FACING_AI_LANGUAGE
                    or payload.get("language_contract_version")
                    != USER_FACING_AI_LANGUAGE_CONTRACT_VERSION):
                continue
            value = StoryContext.from_dict(payload["response"], set(valid_ids)).to_dict()
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        VisualContextAnalyzer._merge(merged, value)
        files.append(path)
    return merged, tuple(files)


class SpeakerEvidenceFusionService:
    """Conservative proposal builder over canonical utterance IDs."""

    @staticmethod
    def evidence_from_visual(context, source_fingerprint=""):
        profiles = {
            row.get("character_id"): row for row in context.get("character_profiles", [])
            if isinstance(row, dict)
        }
        evidence = []
        for row in context.get("visual_contexts", []):
            speaker = row.get("speaker", {})
            addressee = row.get("addressee", {})
            character_id = str(speaker.get("character_id") or UNKNOWN_CHARACTER)
            profile = profiles.get(character_id, {})
            semantic_label = str(profile.get("name") or profile.get("role") or "")
            confidence = normalize_confidence(speaker.get("confidence", row.get("confidence")))
            analysis_status = str(row.get("analysis_status", "UNKNOWN"))
            status = "PROPOSED" if (
                character_id != UNKNOWN_CHARACTER
                and confidence is not None and confidence >= STRONG_VISUAL_CONFIDENCE
                and analysis_status == "ANALYZED"
            ) else "NEED_REVIEW" if character_id != UNKNOWN_CHARACTER or analysis_status != "ANALYZED" else "UNKNOWN"
            evidence.append(FusedEvidence(
                utterance_id=int(row["id"]), source="VISUAL",
                speaker_id=normalize_speaker_hint(speaker.get("spk_id")),
                character_id=character_id, semantic_label=semantic_label,
                addressee_id=str(addressee.get("character_id") or UNKNOWN_CHARACTER),
                confidence=confidence, status=status, reason=str(row.get("notes", "")),
                source_fingerprint=source_fingerprint,
            ))
        return evidence

    @staticmethod
    def evidence_from_stt(project, source_fingerprint=""):
        return [FusedEvidence(
            utterance_id=row.id, source="STT_HINT",
            speaker_id=normalize_speaker_hint(row.speaker_id),
            confidence=normalize_confidence(row.speaker_confidence),
            status="PROPOSED" if normalize_speaker_hint(row.speaker_id) != UNKNOWN_SPEAKER
                   and (normalize_confidence(row.speaker_confidence) or 0) >= STRONG_VISUAL_CONFIDENCE
                   else "NEED_REVIEW",
            reason="Speaker hint từ transcription provider.",
            source_fingerprint=source_fingerprint,
        ) for row in project.utterances
                if normalize_speaker_hint(row.speaker_id) != UNKNOWN_SPEAKER]

    @staticmethod
    def _fingerprint(project, evidence):
        return content_hash({
            "version": SPEAKER_EVIDENCE_FUSION_VERSION,
            "timeline": [{"id": row.id, "start": row.start, "end": row.end,
                           "zh": row.zh, "speaker_id": row.speaker_id}
                          for row in project.utterances],
            "evidence": [asdict(item) for item in sorted(evidence, key=lambda x: (
                x.utterance_id, x.source, x.character_id, x.addressee_id))],
        })

    @staticmethod
    def _semantic_tokens(label):
        normalized = unicodedata.normalize("NFKC", str(label)).casefold()
        normalized = re.sub(r"chưa\s+rõ\s+tên|không\s+rõ\s+tên", " ", normalized)
        return set(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))

    @classmethod
    def _same_semantic_character(cls, left, right):
        left_tokens, right_tokens = cls._semantic_tokens(left), cls._semantic_tokens(right)
        if not left_tokens or not right_tokens:
            return False
        if len(left_tokens & right_tokens) >= 3:
            return True
        # Short labels such as "mèo cam (...)" are safe only when every token
        # in the shorter descriptive core agrees.
        shorter, longer = ((left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens)
                           else (right_tokens, left_tokens))
        return len(shorter) >= 2 and shorter.issubset(longer)

    @classmethod
    def _reconcile_character_ids(cls, evidence):
        """Normalize model-local CHAR IDs using descriptive identity evidence.

        Models may name the same character CHAR_1 in one window and CHAR_GIRL
        in another. Earliest evidence owns the stable project-local character
        ID; numeric suffixes are never interpreted as speaker IDs.
        """
        aliases, representatives = {}, []
        for item in sorted(evidence, key=lambda value: (value.utterance_id, value.character_id)):
            character_id = item.character_id
            if character_id == UNKNOWN_CHARACTER or character_id in aliases:
                continue
            if re.fullmatch(r"CHAR_\d+", character_id):
                # Numeric IDs have already been allocated by the global Visual
                # registry. Never collapse two canonical entities merely
                # because their generated semantic labels look similar.
                aliases[character_id] = character_id
                continue
            match = next((canonical for canonical, label in representatives
                          if cls._same_semantic_character(label, item.semantic_label)), None)
            aliases[character_id] = match or character_id
            if match is None:
                representatives.append((character_id, item.semantic_label))
        return [replace(
            item, character_id=aliases.get(item.character_id, item.character_id),
            addressee_id=aliases.get(item.addressee_id, item.addressee_id),
        ) for item in evidence]

    @staticmethod
    def _next_ids(count, reserved):
        result, number = [], 1
        while len(result) < count:
            candidate = f"SPK_{number:02d}"
            if candidate not in reserved:
                result.append(candidate)
            number += 1
        return result

    def fuse(self, project, evidence, directory=None):
        evidence = self._reconcile_character_ids(list(evidence))
        fingerprint = self._fingerprint(project, evidence)
        cache_file = None
        if directory is not None:
            cache_file = Path(directory) / "cache" / "speaker_fusion" / f"{fingerprint}.json"
            if cache_file.is_file():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                    if cached.get("fingerprint") == fingerprint:
                        project.speaker_evidence = cached["evidence"]
                        project.speaker_proposals = cached["result"]
                        return cached["result"], True
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    pass

        rows = {row.id: row for row in project.utterances}
        confirmed = review_complete(project)
        by_id = {}
        for item in evidence:
            if item.utterance_id in rows:
                by_id.setdefault(item.utterance_id, []).append(item)

        # A group requires actual speaker-character evidence, not merely a
        # visible character. Known incompatible addressees remain separate.
        group_rows = {}
        direct_groups = {}
        for utterance_id, items in by_id.items():
            strong = [item for item in items if item.status == "PROPOSED"]
            if strong:
                best = max(strong, key=lambda item: (item.confidence or 0, item.source))
                if best.character_id != UNKNOWN_CHARACTER:
                    group_rows.setdefault((best.character_id, best.addressee_id), []).append(best)
                elif best.speaker_id != UNKNOWN_SPEAKER:
                    direct_groups.setdefault(best.speaker_id, []).append(best)
        ordered_groups = sorted(group_rows.items(), key=lambda pair: min(item.utterance_id for item in pair[1]))
        reserved = {key for key in project.speakers if key != UNKNOWN_SPEAKER}
        proposed_ids = self._next_ids(len(ordered_groups), reserved)
        proposed_by_utterance = {}
        clusters = []
        for proposal_id, items in sorted(direct_groups.items(), key=lambda pair: min(
                item.utterance_id for item in pair[1])):
            items = sorted(items, key=lambda item: item.utterance_id)
            confidence = max(item.confidence or 0 for item in items)
            clusters.append({
                "speaker_id": proposal_id, "utterance_ids": [item.utterance_id for item in items],
                "character_id": UNKNOWN_CHARACTER, "semantic_label": "", "addressee_ids": [],
                "confidence": round(confidence, 4), "status": "PROPOSED",
                "sources": sorted({item.source for item in items}), "conflicts": [],
                "continuity_support": len(items) > 1,
            })
            for item in items:
                proposed_by_utterance[item.utterance_id] = (proposal_id, item, confidence)
        for ((character_id, addressee_id), items), proposal_id in zip(ordered_groups, proposed_ids):
            items = sorted(items, key=lambda item: item.utterance_id)
            confidence = min(0.99, max(item.confidence or 0 for item in items)
                             + (0.05 if len(items) > 1 else 0.0))
            cluster = {
                "speaker_id": proposal_id,
                "utterance_ids": [item.utterance_id for item in items],
                "character_id": character_id,
                "semantic_label": next((item.semantic_label for item in items if item.semantic_label), ""),
                "addressee_ids": [] if addressee_id == UNKNOWN_CHARACTER else [addressee_id],
                "confidence": round(confidence, 4), "status": "PROPOSED",
                "sources": sorted({item.source for item in items}), "conflicts": [],
                "continuity_support": len(items) > 1,
            }
            clusters.append(cluster)
            for item in items:
                proposed_by_utterance[item.utterance_id] = (proposal_id, item, confidence)

        utterances = []
        for row in project.utterances:
            items = by_id.get(row.id, [])
            if confirmed:
                conflicts = sorted({item.speaker_id for item in items
                                    if item.speaker_id not in (UNKNOWN_SPEAKER, row.speaker_id)})
                utterances.append({
                    "utterance_id": row.id, "proposed_speaker_id": row.speaker_id,
                    "character_id": conflicts[0] if len(conflicts) == 1 else UNKNOWN_CHARACTER,
                    "semantic_label": next((item.semantic_label for item in items if item.semantic_label), ""),
                    "addressee_id": next((item.addressee_id for item in items
                                           if item.addressee_id != UNKNOWN_CHARACTER), UNKNOWN_CHARACTER),
                    "confidence": 1.0, "status": "USER_CONFIRMED",
                    "sources": sorted({"USER_CONFIRMED", *(item.source for item in items)}),
                    "conflicts": conflicts,
                    "reason": ("Evidence mới xung đột nhưng gán speaker đã được người dùng xác nhận."
                               if conflicts else
                               "Gán speaker đã được người dùng xác nhận; evidence mới không được ghi đè."),
                })
                continue
            proposal = proposed_by_utterance.get(row.id)
            if proposal:
                proposal_id, item, confidence = proposal
                utterances.append({
                    "utterance_id": row.id, "proposed_speaker_id": proposal_id,
                    "character_id": item.character_id, "semantic_label": item.semantic_label,
                    "addressee_id": item.addressee_id, "confidence": round(confidence, 4),
                    "status": "PROPOSED", "sources": sorted({value.source for value in items}),
                    "reason": item.reason,
                })
            else:
                best = max(items, key=lambda item: item.confidence or 0, default=None)
                utterances.append({
                    "utterance_id": row.id, "proposed_speaker_id": UNKNOWN_SPEAKER,
                    "character_id": best.character_id if best else UNKNOWN_CHARACTER,
                    "semantic_label": best.semantic_label if best else "",
                    "addressee_id": best.addressee_id if best else UNKNOWN_CHARACTER,
                    "confidence": best.confidence if best else None,
                    "status": best.status if best else "UNKNOWN",
                    "sources": sorted({value.source for value in items}),
                    "reason": best.reason if best else "Chưa có evidence speaker đủ tin cậy.",
                })

        result = {
            "version": SPEAKER_EVIDENCE_FUSION_VERSION, "fingerprint": fingerprint,
            "utterances": utterances, "clusters": clusters,
        }
        persisted_evidence = [asdict(item) for item in evidence]
        project.speaker_evidence = persisted_evidence
        project.speaker_proposals = result
        if cache_file is not None:
            atomic_json(cache_file, {"fingerprint": fingerprint, "evidence": persisted_evidence,
                                     "result": result})
        return result, False

    @staticmethod
    def draft_assignments(project):
        """Return a review-only draft; confirmation still uses the existing commit path."""
        assignments = {row.id: row.speaker_id for row in project.utterances}
        for item in project.speaker_proposals.get("utterances", []):
            if item.get("status") == "PROPOSED" and assignments.get(item["utterance_id"]) == UNKNOWN_SPEAKER:
                assignments[item["utterance_id"]] = item["proposed_speaker_id"]
        return assignments
