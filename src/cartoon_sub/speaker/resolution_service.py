"""Boundary between provider transcription hints and authoritative speaker review."""
from dataclasses import dataclass
from enum import Enum

from cartoon_sub.transcription.contracts import (
    CapabilityAvailability,
    TranscriptionCapability,
    capabilities_for,
    normalize_confidence,
    normalize_speaker_hint,
)

from .service import UNKNOWN_SPEAKER, refresh_timeline, review_complete


class SpeakerResolutionState(str, Enum):
    UNKNOWN = "UNKNOWN"
    PROPOSED = "PROPOSED"
    NEED_REVIEW = "NEED_REVIEW"
    CONFLICT = "CONFLICT"
    USER_CONFIRMED = "USER_CONFIRMED"


class SpeakerEvidenceSource(str, Enum):
    STT_HINT = "STT_HINT"
    VISUAL = "VISUAL"
    DIALOGUE_CONTEXT = "DIALOGUE_CONTEXT"
    AUDIO = "AUDIO"  # Reserved for a future phase; no audio inference here.
    USER_CONFIRMED = "USER_CONFIRMED"


@dataclass(frozen=True)
class SpeakerEvidence:
    utterance_id: int
    speaker_id: str
    confidence: float | None
    source: SpeakerEvidenceSource
    notes: str = ""
    character_id: str = "UNKNOWN"
    addressee_id: str = "UNKNOWN"
    semantic_label: str = ""
    source_fingerprint: str = ""


@dataclass(frozen=True)
class SpeakerEvidenceResult:
    applied_ids: tuple[int, ...] = ()
    conflict_ids: tuple[int, ...] = ()
    protected_ids: tuple[int, ...] = ()
    unresolved_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SpeakerResolutionStats:
    total: int
    proposed: int
    unresolved: int
    uncertain: int
    proposal_ids: tuple[str, ...]
    speaker_capability: CapabilityAvailability


def speaker_resolution_state(project, utterance):
    if utterance.speaker_id == UNKNOWN_SPEAKER:
        return SpeakerResolutionState.UNKNOWN
    if review_complete(project):
        return SpeakerResolutionState.USER_CONFIRMED
    return SpeakerResolutionState.PROPOSED


class SpeakerResolutionService:
    """Normalize STT proposals without inventing identities or confirming them."""

    @staticmethod
    def _stats(project, provider):
        capabilities = capabilities_for(provider)
        proposed_rows = [row for row in project.utterances
                         if normalize_speaker_hint(row.speaker_id) != UNKNOWN_SPEAKER]
        unresolved = len(project.utterances) - len(proposed_rows)
        uncertain = sum(normalize_confidence(row.speaker_confidence) is None
                        or normalize_confidence(row.speaker_confidence) < 0.70
                        for row in proposed_rows)
        return SpeakerResolutionStats(
            total=len(project.utterances),
            proposed=len(proposed_rows),
            unresolved=unresolved,
            uncertain=uncertain,
            proposal_ids=tuple(sorted({normalize_speaker_hint(row.speaker_id)
                                       for row in proposed_rows})),
            speaker_capability=capabilities.availability(TranscriptionCapability.SPEAKER_HINTS),
        )

    def resolve(self, project, provider):
        for utterance in project.utterances:
            utterance.speaker_id = normalize_speaker_hint(utterance.speaker_id)
            utterance.speaker_confidence = normalize_confidence(utterance.speaker_confidence)
            utterance.transcript_confidence = normalize_confidence(utterance.transcript_confidence)
        refresh_timeline(project)
        return self._stats(project, provider)

    @staticmethod
    def evidence_from_stt(project):
        return [SpeakerEvidence(
            utterance_id=row.id, speaker_id=normalize_speaker_hint(row.speaker_id),
            confidence=normalize_confidence(row.speaker_confidence),
            source=SpeakerEvidenceSource.STT_HINT,
        ) for row in project.utterances
                if normalize_speaker_hint(row.speaker_id) != UNKNOWN_SPEAKER]

    @staticmethod
    def evidence_from_context(context):
        evidence = []
        for item in context.get("visual_contexts", []) if isinstance(context, dict) else []:
            speaker = item.get("speaker", {}) if isinstance(item, dict) else {}
            speaker_id = normalize_speaker_hint(speaker.get("spk_id"))
            confidence = normalize_confidence(speaker.get("confidence", item.get("confidence")))
            evidence.append(SpeakerEvidence(
                utterance_id=item.get("id"), speaker_id=speaker_id,
                confidence=confidence, source=SpeakerEvidenceSource.VISUAL,
                notes=str(item.get("notes", "")),
            ))
        # StoryContext mappings are dialogue-level continuity evidence. They
        # remain SPK proposals; character_id is never used as speaker identity.
        for mapping in context.get("speaker_character_mappings", []) if isinstance(context, dict) else []:
            for utterance_id in mapping.get("evidence_ids", []):
                evidence.append(SpeakerEvidence(
                    utterance_id=utterance_id,
                    speaker_id=normalize_speaker_hint(mapping.get("spk_id")),
                    confidence=normalize_confidence(mapping.get("confidence")),
                    source=SpeakerEvidenceSource.DIALOGUE_CONTEXT,
                    notes=str(mapping.get("notes", "")),
                ))
        return evidence

    def apply_evidence(self, project, evidence):
        """Apply only high-confidence proposals; confirmed assignments are immutable."""
        by_id = {row.id: row for row in project.utterances}
        confirmed = review_complete(project)
        applied, conflicts, protected, unresolved = [], [], [], []
        for item in evidence:
            row = by_id.get(item.utterance_id)
            if row is None:
                continue
            proposed = normalize_speaker_hint(item.speaker_id)
            confidence = normalize_confidence(item.confidence)
            current = normalize_speaker_hint(row.speaker_id)
            if confirmed:
                protected.append(row.id)
                if proposed != UNKNOWN_SPEAKER and proposed != current:
                    conflicts.append(row.id)
                continue
            if proposed == UNKNOWN_SPEAKER or confidence is None or confidence < 0.70:
                unresolved.append(row.id)
                continue
            if current not in (UNKNOWN_SPEAKER, proposed):
                conflicts.append(row.id)
                continue
            row.speaker_id = proposed
            row.speaker_confidence = max(confidence, normalize_confidence(row.speaker_confidence) or 0.0)
            applied.append(row.id)
        refresh_timeline(project)
        return SpeakerEvidenceResult(tuple(sorted(set(applied))), tuple(sorted(set(conflicts))),
                                     tuple(sorted(set(protected))), tuple(sorted(set(unresolved))))

    @staticmethod
    def message(stats):
        if stats.proposed:
            ids = ", ".join(stats.proposal_ids)
            details = []
            if stats.uncertain:
                details.append(f"{stats.uncertain} dòng chưa chắc chắn")
            if stats.unresolved:
                details.append(f"{stats.unresolved} dòng chưa xác định")
            suffix = "; " + ", ".join(details) if details else ""
            return f"Gợi ý speaker từ STT: {ids}{suffix}. Cần duyệt trước khi tiếp tục."
        return ("STT đã hoàn tất nhưng model/response không cung cấp thông tin speaker. "
                f"{stats.unresolved} dòng cần gán và duyệt speaker.")

    def summarize(self, project, provider=None):
        provider = provider or project.selected_models.get("transcription_provider", "unknown")
        stats = self._stats(project, provider)
        if review_complete(project):
            return f"Speaker đã được người dùng xác nhận cho {stats.total} dòng."
        return self.message(stats)
