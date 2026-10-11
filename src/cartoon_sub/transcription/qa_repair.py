"""Bounded local QA and one-pass AI repair for Chinese transcripts."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import logging
from pathlib import Path

from cartoon_sub.ai.openrouter_client import OpenRouterClient
from cartoon_sub.ai.provider_errors import AIProviderError
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.subtitle.models import Segment
from cartoon_sub.transcription.segmentation_normalizer import _spoken_weight
from cartoon_sub.transcription.language_consistency import assess_rows


QA_VERSION = 3
REPAIR_PROMPT_VERSION = "zh-transcript-repair-v2"
MIN_DURATION = 0.20
CRITICAL_MIN_DURATION = 0.12
SHORT_TEXT_LONG_THRESHOLDS = ((1, 2.5), (2, 4.0), (4, 7.0), (6, 10.0), (10, 15.0), (15, 22.0))
SECONDS_PER_CHAR_THRESHOLDS = ((2, 1.5), (6, 1.8), (None, 2.2))
LONG_SEGMENT_SECONDS = 20.0
BLOCKING_LONG_SEGMENT_SECONDS = 30.0
CRITICAL_LONG_SEGMENT_SECONDS = 45.0
OVERLAP_TOLERANCE = 0.10
SUSPICIOUS_GAP_SECONDS = 3.0
REGION_NEAR_SECONDS = 2.0
SYSTEM = """You review only suspicious Mandarin transcript regions.
Context is read-only. Return JSON only. Never edit context rows or invent content.
Use KEEP unless a repair is clearly justified. Every proposal must copy source_ids
exactly from affected_ids, in the same order. For MERGE, SPLIT, or BOUNDARY_REPAIR,
parts must preserve every source character in the affected region in order. For
TEXT_CORRECTION, correct only an obvious recognition error and retain nearly all source
characters. Do not provide timestamps; Cartoon_Sub rebuilds timing locally."""
SCHEMA = {"type": "object", "properties": {"regions": {"type": "array", "items": {
    "type": "object", "properties": {
        "region_id": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "integer"}},
        "action": {"type": "string", "enum": ["KEEP", "MERGE", "SPLIT", "TEXT_CORRECTION", "BOUNDARY_REPAIR"]},
        "parts": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    }, "required": ["region_id", "source_ids", "action", "parts", "confidence"], "additionalProperties": False,
}}}, "required": ["regions"], "additionalProperties": False}
log = logging.getLogger(__name__)


def chinese_content_chars(text):
    """Count meaningful Han/letter/digit content, excluding punctuation and whitespace."""
    return sum(ch.isalnum() or "\u3400" <= ch <= "\u9fff" for ch in str(text or ""))


def _significant(text):
    return "".join(ch for ch in str(text or "") if ch.isalnum() or "\u3400" <= ch <= "\u9fff")


def _punctuation_only(text):
    return bool(text.strip()) and not _significant(text)


def is_vocalization(text):
    value = _significant(text)
    if not value:
        return False
    # This deliberately accepts only stand-alone interjections.  A sentence
    # such as "啊，我们走吧" contains other content and remains dialogue.
    if all(ch in "啊嗯哦呀哎唉呜哈呵嘿" for ch in value):
        return True
    lowered = value.lower()
    return lowered in {"oh", "ah", "hmm", "hm", "hey", "ha"}


def classify_region_type(rows):
    """Return a conservative content type without making another AI call."""
    text = "".join(row.zh for row in rows)
    if is_vocalization(text):
        return "VOCALIZATION"
    # Lyric/music classification requires persisted pipeline evidence.  This
    # transcript model currently has none, so do not guess from odd text.
    if _significant(text):
        return "DIALOGUE"
    return "UNKNOWN"


def _short_text_limit(chars):
    return next((limit for maximum, limit in SHORT_TEXT_LONG_THRESHOLDS if chars <= maximum), None)


def _seconds_per_char_limit(chars):
    return next(limit for maximum, limit in SECONDS_PER_CHAR_THRESHOLDS
                if maximum is None or chars <= maximum)


def _severity(code, duration=0.0, vocalization=False):
    if code in {"INVALID_DURATION", "NON_MONOTONIC", "BACKWARD_TIMING", "PUNCTUATION_ONLY", "EMPTY_TEXT"}:
        return "BLOCKING"
    if code == "CRITICAL_SHORT_DURATION":
        return "BLOCKING"
    if code == "IMPLAUSIBLY_LONG_SEGMENT" and duration >= LONG_SEGMENT_SECONDS:
        return "WARNING" if vocalization else ("BLOCKING" if duration > BLOCKING_LONG_SEGMENT_SECONDS else "WARNING")
    if code in {"SHORT_TEXT_LONG_DURATION", "EXTREME_TEXT_TIME_RATIO", "REGION_TIMING_SUSPICIOUS"}:
        return "WARNING" if vocalization else "BLOCKING"
    return "WARNING"


@dataclass(frozen=True)
class TranscriptViolation:
    segment_id: int
    code: str
    detail: str
    severity: str = "WARNING"


@dataclass(frozen=True)
class TranscriptRegion:
    region_id: str
    indexes: tuple[int, ...]
    affected_ids: tuple[int, ...]
    start: float
    end: float
    violations: tuple[str, ...]
    context_before: tuple[str, ...]
    context_after: tuple[str, ...]
    region_type: str = "UNKNOWN"


@dataclass(frozen=True)
class TranscriptQAResult:
    violations: tuple[TranscriptViolation, ...]
    regions: tuple[TranscriptRegion, ...]


@dataclass(frozen=True)
class TranscriptRepairStats:
    regions_sent: int = 0
    requests: int = 0
    cache_hits: int = 0
    repaired_pass: int = 0
    manual_review: int = 0
    model: str = ""
    regions_returned: int = 0
    regions_applied: int = 0
    regions_rejected: int = 0
    regions_missing: int = 0
    rows_repaired: int = 0


class TranscriptQAService:
    """Pure deterministic checks plus compact, nearby suspicious regions."""

    def inspect(self, rows):
        rows = list(rows)
        violations = []
        language_assessments = assess_rows(rows, project_language="zh")
        def add(row, code, detail, *, vocalization=False, severity=None):
            violations.append(TranscriptViolation(row.id, code, detail,
                severity or _severity(code, row.end - row.start, vocalization)))
        for index, row in enumerate(rows):
            text = row.zh.strip()
            chars = chinese_content_chars(text)
            duration = row.end - row.start
            if duration <= 0:
                add(row, "INVALID_DURATION", "end <= start")
            elif duration < CRITICAL_MIN_DURATION:
                add(row, "TOO_SHORT_DURATION", f"{duration:.3f}s")
                add(row, "CRITICAL_SHORT_DURATION", f"{duration:.3f}s")
            elif duration < MIN_DURATION:
                add(row, "TOO_SHORT_DURATION", f"{duration:.3f}s")
            vocalization = is_vocalization(text)
            language = language_assessments[index]
            if (language.classification == "LANGUAGE_MISMATCH"
                    or row.transcript_language_status in {"LANGUAGE_MISMATCH", "NEEDS_MANUAL_REVIEW"}):
                add(row, "LANGUAGE_MISMATCH", "foreign script in Chinese transcript", severity="BLOCKING")
            elif language.isolated_foreign_script:
                add(row, "ISOLATED_FOREIGN_SCRIPT", "foreign script between Han-dominant neighbours")
            if not text and duration > 0:
                add(row, "EMPTY_TEXT", "empty text")
            if _punctuation_only(text):
                add(row, "PUNCTUATION_ONLY", text)
            if text and not chars:
                add(row, "WEIRD_SYMBOL_ONLY", text)
            short_limit = _short_text_limit(chars)
            sec_per_char = duration / max(chars, 1)
            if vocalization and duration > (short_limit or 2.5):
                add(row, "LONG_VOCALIZATION", f"{chars} chars / {duration:.3f}s", vocalization=True)
            elif short_limit is not None and duration > short_limit:
                add(row, "SHORT_TEXT_LONG_DURATION", f"{chars} chars / {duration:.3f}s")
            if chars and duration >= 2 and sec_per_char > _seconds_per_char_limit(chars) and not vocalization:
                add(row, "EXTREME_TEXT_TIME_RATIO", f"{sec_per_char:.3f} sec/char")
            if duration > LONG_SEGMENT_SECONDS and not vocalization:
                add(row, "IMPLAUSIBLY_LONG_SEGMENT", f"{duration:.3f}s")
            if index and row.start < rows[index - 1].start - 0.001:
                add(row, "NON_MONOTONIC", "start precedes previous row")
            if index:
                previous = rows[index - 1]
                if row.start < previous.end - OVERLAP_TOLERANCE:
                    add(row, "OVERLAPPING_SEGMENTS", f"{previous.end - row.start:.3f}s overlap")
                if row.end <= previous.start:
                    add(row, "BACKWARD_TIMING", "row lies before previous row")
                if (row.start - previous.end > SUSPICIOUS_GAP_SECONDS and previous.zh.strip()
                        and text and not previous.zh.rstrip().endswith(("。", "！", "？", "!", "?", "；", ";"))):
                    add(row, "SUSPICIOUS_GAP", f"{row.start - previous.end:.3f}s")
            normalized = _significant(text)
            for previous in rows[max(0, index - 3):index]:
                prior = _significant(previous.zh)
                if not normalized or not prior or row.start - previous.end > SUSPICIOUS_GAP_SECONDS:
                    continue
                similarity = SequenceMatcher(None, normalized, prior).ratio()
                if normalized == prior:
                    add(row, "DUPLICATE_NEARBY", text); break
                if similarity >= .85:
                    add(row, "NEAR_DUPLICATE", f"similarity={similarity:.2f}"); break
        index_by_id = {row.id: index for index, row in enumerate(rows)}
        # Three adjacent single-character rows are a structural failure even if
        # their individual timestamps are technically valid.
        start = None
        for index, row in enumerate(rows + [None]):
            single = row is not None and chinese_content_chars(row.zh) == 1 and not _punctuation_only(row.zh)
            if single and start is None:
                start = index
            if not single and start is not None:
                if index - start >= 3:
                    for item in rows[start:index]:
                        add(item, "SINGLE_CHAR_CHAIN", "adjacent single-character chain")
                    add(rows[start], "BROKEN_SENTENCE_FRAGMENTATION", "single-character chain")
                start = None
        # Three micro fragments in under half a second each are structurally unreliable.
        for index in range(len(rows) - 2):
            window = rows[index:index + 3]
            if all(chinese_content_chars(item.zh) <= 2 and item.end - item.start < .5 for item in window):
                for item in window:
                    add(item, "MICRO_FRAGMENT_CHAIN", "three adjacent micro fragments")
        initial_regions = self._regions(rows, violations, index_by_id)
        for region in initial_regions:
            affected = [rows[index] for index in region.indexes]
            duration = region.end - region.start
            chars = sum(chinese_content_chars(item.zh) for item in affected)
            sec_per_char = duration / max(chars, 1)
            if ((duration > 20 and chars < 20) or (duration > 40 and chars < 35)
                    or sec_per_char > 1.8):
                for item in affected:
                    add(item, "REGION_TIMING_SUSPICIOUS",
                        f"{duration:.3f}s / {chars} chars / {sec_per_char:.3f} sec/char",
                        severity="WARNING" if region.region_type in {
                            "VOCALIZATION", "LYRIC_OR_MUSIC_HEAVY"} or is_vocalization(item.zh) else None)
        for item in violations:
            log.info("[TRANSCRIPT_QA] id=%s duration=%.3f chars=%d sec_per_char=%.3f violation=%s severity=%s",
                     item.segment_id, rows[index_by_id[item.segment_id]].end - rows[index_by_id[item.segment_id]].start,
                     chinese_content_chars(rows[index_by_id[item.segment_id]].zh),
                     (rows[index_by_id[item.segment_id]].end - rows[index_by_id[item.segment_id]].start) /
                     max(chinese_content_chars(rows[index_by_id[item.segment_id]].zh), 1), item.code, item.severity)
        return TranscriptQAResult(tuple(violations), self._regions(rows, violations, index_by_id))

    @staticmethod
    def _regions(rows, violations, index_by_id):
        bad = sorted({index_by_id[item.segment_id] for item in violations if item.segment_id in index_by_id})
        groups = []
        for index in bad:
            if not groups or index > groups[-1][-1] + 1 and rows[index].start - rows[groups[-1][-1]].end > REGION_NEAR_SECONDS:
                groups.append([index])
            else:
                groups[-1].append(index)
        result = []
        for number, group in enumerate(groups, 1):
            first, last = group[0], group[-1]
            affected = rows[first:last + 1]
            codes = tuple(sorted({item.code for item in violations if item.segment_id in {row.id for row in affected}}))
            result.append(TranscriptRegion(
                f"R{number}", tuple(range(first, last + 1)), tuple(row.id for row in affected),
                affected[0].start, affected[-1].end, codes,
                tuple(row.zh for row in rows[max(0, first - 2):first]),
                tuple(row.zh for row in rows[last + 1:last + 3]),
                classify_region_type(affected),
            ))
        return tuple(result)


class TranscriptAIRepairService:
    """One request for all suspicious regions; never retries automatically."""

    def __init__(self, settings_store, cache_directory, client_factory=OpenRouterClient, qa_service=None):
        self.store = settings_store
        self.cache_directory = Path(cache_directory)
        self.client_factory = client_factory
        self.qa = qa_service or TranscriptQAService()

    @staticmethod
    def _request_rows(rows, regions):
        output = []
        for region in regions:
            output.append({"region_id": region.region_id, "affected_ids": list(region.affected_ids),
                "range": [region.start, region.end], "violations": list(region.violations),
                "rows": [{"id": rows[index].id, "text": rows[index].zh} for index in region.indexes],
                "context_before": list(region.context_before), "context_after": list(region.context_after)})
        return output

    @staticmethod
    def _proposal_items(payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
            raise ValueError("Malformed transcript repair response")
        return payload["regions"]

    @staticmethod
    def _validate_proposal(item, region):
        """Validate one proposal only; a bad neighbour must not reject it."""
        if not isinstance(item, dict):
            raise ValueError("Malformed transcript repair item")
        if item.get("region_id") != region.region_id:
            raise ValueError("Transcript repair response has unknown region")
        if set(item) != {"region_id", "source_ids", "action", "parts", "confidence"}:
            raise ValueError("Malformed transcript repair item")
        if (not isinstance(item["source_ids"], list)
                or any(type(value) is not int for value in item["source_ids"])
                or tuple(item["source_ids"]) != region.affected_ids):
            raise ValueError("Transcript repair source IDs do not match region")
        if item["action"] not in {"KEEP", "MERGE", "SPLIT", "TEXT_CORRECTION", "BOUNDARY_REPAIR"}:
            raise ValueError("Unsupported transcript repair action")
        if not isinstance(item["parts"], list) or not all(isinstance(text, str) and text.strip() for text in item["parts"]):
            raise ValueError("Transcript repair parts must be non-empty text")
        if not isinstance(item["confidence"], (int, float)) or not 0 <= item["confidence"] <= 1:
            raise ValueError("Invalid transcript repair confidence")
        return item

    @classmethod
    def _validate(cls, payload, regions):
        """Compatibility helper for callers/tests that need every proposal."""
        items = cls._proposal_items(payload)
        expected = {region.region_id for region in regions}
        result = {}
        for item in items:
            if not isinstance(item, dict) or item.get("region_id") not in expected:
                raise ValueError("Transcript repair response has missing or unknown region")
            region_id = item["region_id"]
            if region_id in result:
                raise ValueError("Transcript repair response has duplicate region")
            region = next(candidate for candidate in regions if candidate.region_id == region_id)
            result[region_id] = cls._validate_proposal(item, region)
        if set(result) != expected:
            raise ValueError("Transcript repair response has missing or unknown region")
        return result

    @staticmethod
    def _rebuild(rows, region, proposal):
        source = "".join(row.zh for row in rows)
        parts = proposal["parts"]
        repaired = "".join(parts)
        if proposal["action"] == "KEEP":
            if repaired != source:
                raise ValueError("KEEP proposal changed Chinese text")
            return rows
        if proposal["action"] != "TEXT_CORRECTION" and repaired != source:
            raise ValueError("Boundary repair changed Chinese text")
        if proposal["action"] == "TEXT_CORRECTION":
            if proposal["confidence"] < .9 or SequenceMatcher(None, _significant(source), _significant(repaired)).ratio() < .75:
                raise ValueError("Unsafe transcript text correction")
        if any(_punctuation_only(part) or len(_significant(part)) < 2 for part in parts):
            raise ValueError("Transcript repair creates tiny or punctuation-only text")
        duration = region.end - region.start
        if duration < MIN_DURATION * len(parts):
            raise ValueError("Transcript repair cannot assign safe durations")
        weights = [_spoken_weight(part) for part in parts]
        total = sum(weights)
        cursor = region.start
        rebuilt = []
        for index, (part, weight) in enumerate(zip(parts, weights)):
            remaining = len(parts) - index - 1
            end = region.end if not remaining else cursor + max(MIN_DURATION, duration * weight / total)
            end = min(end, region.end - remaining * MIN_DURATION)
            rebuilt.append(Segment(1, cursor, end, part, transcript_timing_provenance="ESTIMATED",
                                   transcript_timing_confidence=0.0))
            cursor = end
        return rebuilt

    def repair(self, rows, qa_result, *, cancel=None, progress=None):
        rows = list(rows); regions = qa_result.regions
        if not regions:
            return rows, TranscriptRepairStats()
        from cartoon_sub.ai.model_resolver import AIModelResolver
        model = AIModelResolver(self.store).resolve("TRANSCRIPT_REPAIR", "transcript", ("text",)).model_id
        request_rows = self._request_rows(rows, regions)
        prompt = json.dumps({"regions": request_rows}, ensure_ascii=False, separators=(",", ":"))
        key = content_hash({"version": QA_VERSION, "prompt_version": REPAIR_PROMPT_VERSION,
                            "model": model, "prompt": prompt, "schema": SCHEMA})
        path = self.cache_directory / f"{key}.json"; payload = None; cache_hits = 0; requests = 0
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("status") == "completed": payload = saved["response"]; cache_hits = 1
            except (ValueError, KeyError, TypeError, json.JSONDecodeError): pass
        client = None
        try:
            if payload is None:
                check_cancel(cancel); client = self.client_factory(self.store.openrouter_key_pool()); requests = 1
                payload = client.generate_json(SYSTEM, prompt, SCHEMA, model, cancel=cancel)
                atomic_json(path, {"status": "completed", "response": payload})
            items = self._proposal_items(payload)
            by_region = {}
            unknown_items = 0
            duplicate_items = set()
            expected = {region.region_id for region in regions}
            for item in items:
                region_id = item.get("region_id") if isinstance(item, dict) else None
                if region_id not in expected:
                    unknown_items += 1
                    continue
                if region_id in by_region:
                    duplicate_items.add(region_id)
                    continue
                by_region[region_id] = item
            replacements = {}
            accepted_actions = {}
            rejected = {}
            missing = 0
            for region in regions:
                item = by_region.get(region.region_id)
                if item is None:
                    missing += 1; rejected[region.region_id] = "missing proposal"; continue
                if region.region_id in duplicate_items:
                    rejected[region.region_id] = "duplicate proposal"; continue
                try:
                    proposal = self._validate_proposal(item, region)
                    candidate = self._rebuild(
                        [rows[i] for i in region.indexes], region, proposal)
                    structural_codes = {"INVALID_DURATION", "NON_MONOTONIC", "BACKWARD_TIMING",
                                        "PUNCTUATION_ONLY", "EMPTY_TEXT", "CRITICAL_SHORT_DURATION"}
                    if any(item.code in structural_codes and item.severity == "BLOCKING"
                           for item in self.qa.inspect(candidate).violations):
                        raise ValueError("Transcript repair candidate failed structural QA")
                    replacements[region.region_id] = candidate
                    accepted_actions[region.region_id] = proposal["action"]
                except (ValueError, TypeError, KeyError) as exc:
                    rejected[region.region_id] = str(exc)
            for region_id, reason in rejected.items():
                log.warning("Transcript repair rejected region %s: %s", region_id, reason)
            output = []; by_start = {region.indexes[0]: region for region in regions}; skipped = set()
            for index, row in enumerate(rows):
                if index in skipped: continue
                region = by_start.get(index)
                if region:
                    output.extend(replacements.get(region.region_id, [rows[i] for i in region.indexes]))
                    skipped.update(region.indexes)
                else: output.append(row)
            for index, row in enumerate(output, 1): row.id = index
            second = self.qa.inspect(output)
            output_by_id = {row.id: row for row in output}
            failed_after_apply = sum(
                any(region.start - .001 <= output_by_id[item.segment_id].start
                    and output_by_id[item.segment_id].end <= region.end + .001
                    for item in second.violations if item.segment_id in output_by_id)
                for region in regions if region.region_id in replacements
            )
            applied = len(replacements)
            return output, TranscriptRepairStats(
                len(regions), requests, cache_hits, applied - failed_after_apply,
                len(rejected) + failed_after_apply, model, len(items), applied,
                len(rejected), missing,
                sum(len(region.indexes) for region in regions
                    if accepted_actions.get(region.region_id) != "KEEP"
                    and region.region_id in replacements),
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, AIProviderError) as exc:
            log.warning("Transcript repair stopped after one attempt: %s", type(exc).__name__)
            return rows, TranscriptRepairStats(len(regions), requests, cache_hits, 0, len(regions), model)
        except CancelledError: raise
        finally:
            if client is not None: client.close()
