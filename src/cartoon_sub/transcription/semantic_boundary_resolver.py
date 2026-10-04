"""Selective, boundary-only AI assistance for ambiguous Chinese transcript rows."""
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from threading import Event

from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.ai.openrouter_client import OpenRouterClient
from cartoon_sub.media.process import CancelledError
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.transcription.contracts import normalize_speaker_hint
from cartoon_sub.transcription.segmentation_normalizer import TranscriptSegmentationNormalizer


SEMANTIC_BOUNDARY_VERSION = 1
SEMANTIC_PROMPT_VERSION = "zh-semantic-boundaries-v1"
MIN_CONFIDENCE = 0.75
SYSTEM = """You identify boundaries in recognized Mandarin speech.
Return JSON only. Never rewrite, correct, punctuate, translate, or repeat source text.
Operate only on the supplied target IDs and source character indices.
KEEP/REMOVE applies to existing candidate boundaries; ADD applies to a missing boundary.
Prefer natural sentence, clause, vocative, question/answer, and likely dialogue-turn boundaries.
Be conservative: do not create tiny fragments and do not identify speakers."""
SCHEMA = {
    "type": "object",
    "properties": {"targets": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "target_id": {"type": "integer"},
            "boundaries": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "after_index": {"type": "integer"},
                    "action": {"type": "string", "enum": ["KEEP", "REMOVE", "ADD"]},
                    "type": {"type": "string", "enum": ["SENTENCE", "CLAUSE", "LIKELY_DIALOGUE_TURN"]},
                    "confidence": {"type": "number"},
                },
                "required": ["after_index", "action", "type", "confidence"],
                "additionalProperties": False,
            }},
        },
        "required": ["target_id", "boundaries"],
        "additionalProperties": False,
    }}},
    "required": ["targets"],
    "additionalProperties": False,
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticBoundaryStats:
    ambiguous_regions: int = 0
    requests: int = 0
    cache_hits: int = 0
    added: int = 0
    removed: int = 0
    changed: bool = False
    model: str = ""


@dataclass(frozen=True)
class SemanticBoundaryResult:
    utterances: list
    stats: SemanticBoundaryStats


def _significant(text):
    return "".join(ch for ch in text if not ch.isspace())


def _existing_boundaries(parent, children):
    """Map deterministic child ends back to exact original-source indices."""
    if len(children) <= 1:
        return []
    positions = [index for index, char in enumerate(parent.zh) if not char.isspace()]
    parent_compact = _significant(parent.zh)
    if "".join(_significant(child.zh) for child in children) != parent_compact:
        raise ValueError("Deterministic children do not preserve parent Chinese")
    boundaries = []
    consumed = 0
    for child in children[:-1]:
        consumed += len(_significant(child.zh))
        boundaries.append(positions[consumed - 1])
    return boundaries


def _children_for_parent(parent, rows):
    return [row for row in rows if row.start >= parent.start - 0.001
            and row.end <= parent.end + 0.001]


def _indexed_source(text):
    return " ".join(f"[{index}]{'␠' if char.isspace() else char}" for index, char in enumerate(text))


def _unsafe_lexical_boundary(text, index):
    """Reject obvious cuts where a one-character function word binds right."""
    if text[index].isalnum() and text[index + 1].isalnum():
        if text[index].isascii() and text[index + 1].isascii():
            return True
    right_binding = set("还又也都就才再很更最不没无别被把让给跟和与及或但而因所可要会能在从对向将正")
    left_binding = set("的了吗呢吧啊呀嘛着过地得")
    return text[index] in right_binding or text[index + 1] in left_binding


class ChineseSemanticBoundaryResolver:
    def __init__(self, settings_store, cache_directory, client_factory=OpenRouterClient):
        self.store = settings_store
        self.cache_directory = Path(cache_directory)
        self.client_factory = client_factory

    @staticmethod
    def _is_ambiguous(parent, children, words, source):
        if source == "srt" and len(children) <= 1:
            return False
        if words:
            return False
        if normalize_speaker_hint(parent.speaker_id) != "SPK_UNKNOWN":
            return False
        strong = sum(parent.zh.count(mark) for mark in "。！？?!") + parent.zh.count("…")
        if strong >= max(1, len(children) - 1):
            return False
        return len(children) > 1 and (parent.duration > 7.0 or len(_significant(parent.zh)) > 40)

    def _targets(self, raw_segments, deterministic, words, source):
        targets = []
        for index, parent in enumerate(raw_segments):
            children = _children_for_parent(parent, deterministic)
            parent_words = [word for word in (words or [])
                            if word.get("end", 0) > parent.start and word.get("start", 0) < parent.end]
            if not self._is_ambiguous(parent, children, parent_words, source):
                continue
            previous = deterministic[deterministic.index(children[0]) - 1].zh if (
                children and deterministic.index(children[0]) > 0) else ""
            last_index = deterministic.index(children[-1]) if children else -1
            following = deterministic[last_index + 1].zh if 0 <= last_index < len(deterministic) - 1 else ""
            targets.append({
                "target_id": parent.id,
                "source": parent.zh,
                "indexed_source": _indexed_source(parent.zh),
                "existing_boundaries": _existing_boundaries(parent, children),
                "previous_context": previous,
                "next_context": following,
            })
        return targets

    @staticmethod
    def _validate(payload, targets):
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or set(payload) != {"targets"} or not isinstance(payload["targets"], list):
            raise ValueError("Malformed semantic boundary response")
        target_map = {target["target_id"]: target for target in targets}
        rows = payload["targets"]
        if {row.get("target_id") for row in rows if isinstance(row, dict)} != set(target_map):
            raise ValueError("Unknown or missing semantic target ID")
        validated = {}
        for row in rows:
            if set(row) != {"target_id", "boundaries"} or not isinstance(row["boundaries"], list):
                raise ValueError("Malformed semantic target")
            target = target_map[row["target_id"]]
            seen = []
            decisions = []
            for decision in row["boundaries"]:
                required = {"after_index", "action", "type", "confidence"}
                if not isinstance(decision, dict) or set(decision) != required:
                    raise ValueError("Semantic response may contain positions only")
                boundary = decision["after_index"]
                if type(boundary) is not int or boundary < 0 or boundary >= len(target["source"]) - 1:
                    raise ValueError("Invalid semantic boundary index")
                if seen and boundary <= seen[-1]:
                    raise ValueError("Semantic boundaries must be unique and sorted")
                if decision["action"] not in {"KEEP", "REMOVE", "ADD"}:
                    raise ValueError("Invalid semantic boundary action")
                existing = set(target["existing_boundaries"])
                if decision["action"] in {"KEEP", "REMOVE"} and boundary not in existing:
                    raise ValueError("KEEP/REMOVE must reference an existing boundary")
                if decision["action"] == "ADD" and boundary in existing:
                    raise ValueError("ADD must reference a missing boundary")
                if decision["type"] not in {"SENTENCE", "CLAUSE", "LIKELY_DIALOGUE_TURN"}:
                    raise ValueError("Invalid semantic boundary type")
                confidence = decision["confidence"]
                if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                    raise ValueError("Invalid semantic boundary confidence")
                seen.append(boundary)
                decisions.append(dict(decision))
            validated[row["target_id"]] = decisions
        return validated

    @staticmethod
    def _apply(raw_segments, deterministic, targets, decisions):
        normalizer = TranscriptSegmentationNormalizer()
        target_map = {target["target_id"]: target for target in targets}
        output, added, removed = [], 0, 0
        for parent in raw_segments:
            children = _children_for_parent(parent, deterministic)
            target = target_map.get(parent.id)
            if target is None:
                output.extend(children)
                continue
            existing = set(target["existing_boundaries"])
            final = set(existing)
            unsafe_adds = {
                decision["after_index"] for decision in decisions[parent.id]
                if (decision["action"] == "ADD"
                    and decision["confidence"] >= MIN_CONFIDENCE
                    and _unsafe_lexical_boundary(parent.zh, decision["after_index"]))
            }
            for decision in decisions[parent.id]:
                if decision["confidence"] < MIN_CONFIDENCE:
                    continue
                index, action = decision["after_index"], decision["action"]
                if action == "ADD" and index in unsafe_adds:
                    continue
                if action == "REMOVE" and any(abs(index - unsafe) <= 1 for unsafe in unsafe_adds):
                    continue
                if action == "REMOVE" and index in final:
                    final.remove(index); removed += 1
                elif action == "ADD" and index not in final:
                    final.add(index); added += 1
            if final == existing:
                output.extend(children)
                continue
            candidate = normalizer.split_at_source_indices(parent, sorted(final))
            if any(len(_significant(row.zh)) < 2 for row in candidate):
                raise ValueError("Semantic boundary creates an excessive tiny fragment")
            output.extend(candidate)
        for index, row in enumerate(output, 1):
            row.id = index
        original = "".join(_significant(row.zh) for row in raw_segments)
        normalized = "".join(_significant(row.zh) for row in output)
        if original != normalized:
            raise ValueError("Semantic resolution changed recognized Chinese")
        return output, added, removed

    def resolve(self, raw_segments, deterministic, *, words=None, source="stt", cancel=None, progress=None):
        cancel = cancel or Event()
        report = progress or (lambda text: None)
        targets = self._targets(raw_segments, deterministic, words, source)
        if not targets:
            return SemanticBoundaryResult(deterministic, SemanticBoundaryStats())
        settings = self.store.load()
        from cartoon_sub.ai.model_resolver import AIModelResolver
        model = AIModelResolver(self.store).resolve(
            "SEMANTIC_BOUNDARY", "transcript", ("text",)).model_id
        prompt = ("Return boundary decisions for these ambiguous targets. Context is read-only. "
                  "Only after_index positions inside indexed_source are editable.\n"
                  + json.dumps(targets, ensure_ascii=False, separators=(",", ":")))
        cache_key = content_hash({
            "version": SEMANTIC_BOUNDARY_VERSION,
            "prompt_version": SEMANTIC_PROMPT_VERSION,
            "model": model,
            "system": SYSTEM,
            "prompt": prompt,
            "schema": SCHEMA,
        })
        path = self.cache_directory / f"{cache_key}.json"
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("status") == "completed":
                    decisions = self._validate(state["response"], targets)
                    output, added, removed = self._apply(raw_segments, deterministic, targets, decisions)
                    report("Semantic boundaries: dùng cache")
                    return SemanticBoundaryResult(output, SemanticBoundaryStats(
                        len(targets), 0, 1, added, removed, output != deterministic, model))
            except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
                pass
        client = None
        requests = 0
        try:
            pool = self.store.openrouter_key_pool()
            client = self.client_factory(pool)
            if not getattr(client, "models", None):
                cached = self.store.openrouter_catalog_cache()
                if cached:
                    client.models = {item["id"]: item for item in cached["models"]}
            # Semantic fallback is optional and cheap; one corrective retry is
            # enough. Never amplify a persistent provider/model failure.
            max_attempts = min(settings.retry_count, 1) + 1
            for attempt in range(max_attempts):
                check_cancel(cancel)
                requests += 1
                atomic_json(path, {"status": "running", "attempt": attempt + 1})
                try:
                    payload = client.generate_json(SYSTEM, prompt, SCHEMA, model, cancel=cancel)
                    decisions = self._validate(payload, targets)
                    output, added, removed = self._apply(raw_segments, deterministic, targets, decisions)
                    atomic_json(path, {"status": "completed", "response": payload})
                    return SemanticBoundaryResult(output, SemanticBoundaryStats(
                        len(targets), requests, 0, added, removed, output != deterministic, model))
                except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError, GeminiError) as exc:
                    retryable = not isinstance(exc, GeminiError) or bool(getattr(exc, "retryable", False))
                    atomic_json(path, {"status": "failed", "attempt": attempt + 1,
                                       "error": type(exc).__name__,
                                       "category": getattr(exc, "category", "VALIDATION"),
                                       "status_code": getattr(exc, "status_code", None),
                                       "retryable": retryable})
                    if not retryable or attempt + 1 >= max_attempts:
                        log.warning("Semantic boundary fallback to deterministic output: %s", type(exc).__name__)
                        break
            return SemanticBoundaryResult(deterministic, SemanticBoundaryStats(
                len(targets), requests, 0, model=model))
        except CancelledError:
            raise
        finally:
            if client is not None:
                client.close()
