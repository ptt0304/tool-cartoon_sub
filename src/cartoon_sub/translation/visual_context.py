from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

from cartoon_sub.ai.language_contract import (
    USER_FACING_AI_CORRECTION,
    USER_FACING_AI_INSTRUCTION,
    USER_FACING_AI_LANGUAGE,
    USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
    UserFacingLanguageError,
    story_context_user_facing_values,
    validate_user_facing_language,
)
from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.speaker.service import review_complete
from cartoon_sub.translation.character_registry import CharacterRegistry, REGISTRY_VERSION
from cartoon_sub.translation.context_models import CONTEXT_SCHEMA, StoryContext


logger = logging.getLogger(__name__)
VISUAL_ANALYSIS_VERSION = "visual-context-v5-vietnamese-first"
VISUAL_PROMPT_SCHEMA_VERSION = "speaker-context-v4-vietnamese-first"
BASE_FPS = 2
RESCAN_FPS = 4
LOW_CONFIDENCE = 0.70
VISUAL_CONTEXT_MAX_TARGETS_PER_REQUEST = 16

VISUAL_CONTEXT_SYSTEM = USER_FACING_AI_INSTRUCTION + "\n" + """ƯU TIÊN BẮT BUỘC: ngay từ phản hồi đầu tiên, mọi giá trị ngôn ngữ tự nhiên dành cho
người dùng phải bằng tiếng Việt. Quy tắc này áp dụng thống nhất cho khám phá bối cảnh toàn cục,
registry nhân vật toàn cục, phân tích từng cửa sổ, nhân vật mới và hòa giải StoryContext cuối.
Các trường setting, summary, narration, notes, lý do, vai trò/mô tả nhân vật, relationships,
terms target/notes, uncertainties, mô tả vật thể và new_character_candidates.description phải
bằng tiếng Việt. Không dịch JSON key, ID, enum/status, Chinese source, tên riêng hoặc alias nguồn.

You analyze a real video together with timestamped Chinese transcript rows.
The Chinese transcript is the source of spoken content. Never rewrite IDs, timestamps, Chinese text, or SPK IDs.
The visual context is authoritative for who is speaking, who is addressed, who is referred to, scene mode,
and visible objects/characters only when confidence is high.
Do NOT infer that the character currently visible on screen is the speaker merely because they are visible.
Do not assume 他 means male if visual/context evidence identifies a female referent.
SPK IDs are neutral audio clusters: never default unknown gender to male, and never infer gender from SPK order or pitch.
Distinguish speaker_character, addressee_character, referenced characters, and visible characters for every row.
Use audio continuity, clear lip movement, dialogue continuity, previous mapping, and scene structure before visibility.
FLASHBACK/MEMORY/IMAGINED/NARRATION_VISUAL footage may show a referenced character while another person keeps speaking.
For offscreen speech, reuse a high-confidence accumulated SPK mapping; otherwise return unknown.
Do not hallucinate a sword, pill, ring, artifact, gender, relationship, or identity from genre alone.
If evidence is uncertain, use UNKNOWN/unknown, low confidence, and NEED_REVIEW rather than inventing a fact.
Return all human-readable descriptions, summaries, notes, roles, relationships, terminology explanations,
addressing explanations, conditions, uncertainties, and review text in natural Vietnamese.
Keep Chinese source names and terms unchanged in source fields. Keep machine schema keys, SPK/CHAR IDs,
and enum values exactly as defined. Never put a CHAR_* ID into a Vietnamese name/target field and never put
a TERM_* placeholder into a Vietnamese translation field; leave it empty or say "Chưa xác định" instead.
Return one visual_contexts row for every target transcript ID. Candidate output never overrides user-approved context.
Use only character_id values present in canonical_character_registry, or UNKNOWN. Never invent a canonical
CHAR ID inside a window. If a genuinely new person appears, keep affected visual fields UNKNOWN and return
the person separately in new_character_candidates with evidence IDs and bindings. The application owns allocation
of stable CHAR IDs. An uncertain identity must set uncertain=true and remain UNKNOWN/NEED_REVIEW.
"""


def visual_language_contract():
    """The shared Vietnamese contract, made explicit in every Visual request."""
    return {
        "language": USER_FACING_AI_LANGUAGE,
        "instruction": USER_FACING_AI_INSTRUCTION,
        "applies_to": [
            "global_context_discovery",
            "global_character_registry",
            "window_visual_context",
            "new_character_candidate",
            "final_reconciliation_story_context",
        ],
        "user_facing_fields": [
            "setting", "summary", "narration", "notes", "reasons",
            "character.name", "character.role", "character.description",
            "relationships", "terms.target", "terms.notes", "uncertainties",
            "visible_objects.description", "new_character_candidates.description",
            "reconciliation_explanations",
        ],
        "preserve_verbatim": [
            "json_field_names", "schema", "CHAR_xx", "SPK_xx",
            "utterance_ids", "enums", "status", "chinese_source",
            "proper_names", "source_aliases",
        ],
    }


def visual_response_user_facing_values(value):
    """Include transient new-character prose that reconciliation later removes."""
    values = story_context_user_facing_values(value if isinstance(value, dict) else {})
    if isinstance(value, dict):
        for candidate in value.get("new_character_candidates", []) or []:
            if isinstance(candidate, dict):
                values.append(candidate.get("description", ""))
    return values


def validate_visual_response_language(value):
    """Validate each prose field as well as the response as a whole."""
    values = visual_response_user_facing_values(value)
    for item in values:
        validate_user_facing_language([item], "Visual Context")
    validate_user_facing_language(values, "Visual Context")


_SECRET_FIELDS = {"authorization", "api_key", "api_keys", "access_token", "token"}


def sanitize_visual_diagnostic(value):
    """Remove credential-shaped fields before retaining a structured response."""
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if str(key).casefold() in _SECRET_FIELDS
                  else sanitize_visual_diagnostic(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_visual_diagnostic(item) for item in value]
    return value


def visual_id_diagnostics(value, requested_ids):
    """Describe exact-set compliance without inferring or coercing any ID."""
    requested = list(requested_ids)
    items = value.get("visual_contexts", []) if isinstance(value, dict) else []
    if not isinstance(items, list):
        items = []
    returned = [item.get("id") if isinstance(item, dict) else None for item in items]
    integer_ids = [item for item in returned if type(item) is int]
    counts = Counter(integer_ids)
    duplicates = sorted(item for item, count in counts.items() if count > 1)
    missing = sorted(set(requested) - set(integer_ids))
    extra = sorted(set(integer_ids) - set(requested))
    exact = (not missing and not extra and not duplicates
             and len(returned) == len(requested)
             and all(type(item) is int for item in returned))
    return {
        "requested_ids": requested,
        "returned_ids": returned,
        "missing_ids": missing,
        "extra_ids": extra,
        "duplicate_ids": duplicates,
        "count": len(returned),
        "exact_set": exact,
    }


def bounded_target_chunks(rows, limit=VISUAL_CONTEXT_MAX_TARGETS_PER_REQUEST):
    """Yield canonical-order target batches without changing their IDs."""
    if type(limit) is not int or limit < 1:
        raise ValueError("Visual Context target limit phải là số nguyên dương")
    ordered = list(rows)
    return [ordered[index:index + limit] for index in range(0, len(ordered), limit)]


def validate_merged_visual_contexts(rows, requested_ids):
    diagnostics = visual_id_diagnostics({"visual_contexts": list(rows)}, requested_ids)
    if not diagnostics["exact_set"]:
        raise ValueError(
            "Visual Context merge không khớp canonical target IDs: "
            f"missing={diagnostics['missing_ids']} extra={diagnostics['extra_ids']} "
            f"duplicates={diagnostics['duplicate_ids']}"
        )
    return diagnostics


def video_signature(path) -> str:
    value = Path(path).resolve(strict=True)
    stat = value.stat()
    return content_hash({
        "path": str(value).casefold(), "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "version": VISUAL_ANALYSIS_VERSION,
    })


def visual_source_signature(project) -> str:
    return content_hash({
        "video": video_signature(project.source_video_path),
        "rows": [{"id": row.id, "start": row.start, "end": row.end, "zh": row.zh,
                  "speaker_id": row.speaker_id} for row in project.utterances],
        "model": project.selected_models.get("vision_speaker", "legacy"),
        "mode": project.selected_models.get("vision_input_mode", "legacy"),
        "version": VISUAL_ANALYSIS_VERSION,
    })


class VisualContextAnalyzer:
    def __init__(self, client, model, ffmpeg_executable="ffmpeg", input_mode="legacy"):
        self.client = client
        self.model = model
        self.ffmpeg_executable = ffmpeg_executable
        self.input_mode = input_mode
        self.cache_hits = 0
        self.cache_misses = 0
        self.request_plan = []
        self.character_registry = None
        self.registry_path = None
        self.registry_identity = None

    @staticmethod
    def _row(row):
        return {"id": row.id, "start": row.start, "end": row.end, "speaker_id": row.speaker_id,
                "speaker_name": row.speaker_name, "zh": row.zh}

    @staticmethod
    def _merge(base, update):
        for key in ("setting", "summary", "narration"):
            if update.get(key):
                base[key] = update[key]
        base["uncertainties"] = list(dict.fromkeys(base["uncertainties"] + update.get("uncertainties", [])))
        for key, identity in (
            ("characters", lambda row: (row["source"], row["target"])),
            ("terms", lambda row: (row["source"], row["target"])),
            ("address_rules", lambda row: (row["speaker"], row["listener"], row["condition"])),
            ("character_profiles", lambda row: row["character_id"]),
            ("speaker_character_mappings", lambda row: (row["spk_id"], row["character_id"])),
            ("visual_contexts", lambda row: row["id"]),
        ):
            values = {identity(row): row for row in base[key]}
            for row in update.get(key, []):
                old = values.get(identity(row))
                if old is None or float(row.get("confidence", 1)) >= float(old.get("confidence", 1)):
                    values[identity(row)] = row
            base[key] = list(values.values())
        return base

    @staticmethod
    def _normalize_response(value):
        """Normalize harmless model vocabulary without weakening the strict schema."""
        aliases = {
            "approved": "ANALYZED", "confirmed": "ANALYZED", "complete": "ANALYZED",
            "completed": "ANALYZED", "success": "ANALYZED", "high_confidence": "ANALYZED",
            "uncertain": "LOW_CONFIDENCE", "ambiguous": "LOW_CONFIDENCE",
            "review": "NEED_REVIEW", "needs_review": "NEED_REVIEW",
        }
        for row in value.get("visual_contexts", []) if isinstance(value, dict) else []:
            status = row.get("analysis_status")
            if isinstance(status, str):
                row["analysis_status"] = aliases.get(status.strip().casefold(), status.strip().upper())
        return value

    def _proxy(self, source, start, end, fps, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, suffix=".mp4", delete=False) as handle:
            destination = Path(handle.name)
        duration = max(0.25, end - start)
        command = [
            self.ffmpeg_executable, "-nostdin", "-y", "-ss", f"{start:.3f}", "-i", str(source),
            "-t", f"{duration:.3f}", "-vf", f"fps={fps},scale=-2:480", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-ac", "1", "-ar", "16000",
            "-b:a", "48k", "-movflags", "+faststart", str(destination),
        ]
        try:
            run_process(command, cwd=directory)
            if not destination.is_file() or destination.stat().st_size <= 1024:
                raise ValueError("Không tạo được video proxy cho visual context")
            return destination
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _frame_times(start, end, rows):
        """Bounded dialogue-grounded samples, reused when adjacent windows overlap."""
        values = [min(end, start + .05), max(start, end - .10)]
        for row in rows:
            values.extend((max(start, row.start - .25), row.start,
                           (row.start + row.end) / 2, min(end, row.end + .25)))
        values = sorted({round(max(start, min(max(start, end - .05), value)), 2)
                         for value in values})
        if len(values) > 16:
            values = [values[round(index * (len(values) - 1) / 15)] for index in range(16)]
        return values

    def _frame(self, source, timestamp, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        identity = content_hash({"video": video_signature(source), "time": timestamp, "height": 480})
        destination = directory / f"{identity}.jpg"
        if destination.is_file() and destination.stat().st_size > 512:
            return destination
        command = [self.ffmpeg_executable, "-nostdin", "-y", "-ss", f"{timestamp:.3f}",
                   "-i", str(source), "-frames:v", "1", "-vf", "scale=-2:480",
                   "-q:v", "4", str(destination)]
        try:
            run_process(command, cwd=directory)
            if not destination.is_file() or destination.stat().st_size <= 512:
                raise ValueError("Không trích xuất được frame visual context")
            return destination
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def _request(self, project, cache_dir, rows, references, start, end, fps,
                 pass_name, cancel=None, progress=None):
        if self.character_registry is None:
            self.character_registry = CharacterRegistry.from_project(project)
        signature = content_hash({
            "video": video_signature(project.source_video_path),
            "targets": [self._row(row) for row in rows],
            "references": [self._row(row) for row in references],
            "range": [round(start, 3), round(end, 3)], "fps": fps,
            "pass": pass_name, "model": self.model, "mode": self.input_mode,
            "character_registry_identity": self.registry_identity,
            "character_registry_version": REGISTRY_VERSION,
            "strategy": "dialogue_windows_adaptive_frames" if self.input_mode == "frames"
                        else "bounded_direct_video" if self.input_mode == "video" else "legacy_gemini_video",
            "prompt_schema": VISUAL_PROMPT_SCHEMA_VERSION,
            "version": VISUAL_ANALYSIS_VERSION,
        })
        cache_file = cache_dir / f"{signature}.json"
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if (cached.get("user_facing_ai_language") != USER_FACING_AI_LANGUAGE
                        or cached.get("language_contract_version")
                        != USER_FACING_AI_LANGUAGE_CONTRACT_VERSION):
                    raise ValueError("Visual Context cache dùng language contract cũ")
                normalized = self._normalize_response(cached["response"])
                value = StoryContext.from_dict(normalized, {row.id for row in project.utterances}).to_dict()
                validate_visual_response_language(value)
                self.character_registry.absorb_context(value)
                if self.registry_path is not None:
                    atomic_json(self.registry_path, self.character_registry.to_dict())
                cached_ids = visual_id_diagnostics(value, [row.id for row in rows])
                if cached_ids["exact_set"]:
                    if progress:
                        progress(f"[VISUAL CONTEXT] range={start:.0f}-{end:.0f}s rows={len(rows)} cache=HIT")
                    self.cache_hits += 1
                    self.request_plan.append({
                        "pass": pass_name,
                        "target_ids": [row.id for row in rows],
                        "window": [start, end],
                        "frames": (len(self._frame_times(start, end, rows))
                                   if self.input_mode == "frames" else 1),
                        "cache": "HIT",
                        "returned_ids": [item["id"] for item in value["visual_contexts"]],
                        "validation": "PASS",
                    })
                    logger.info(
                        "[VISUAL AI] provider=%s model=%s chunk_start=%.3f chunk_end=%.3f "
                        "video_input=no transcript_rows=%d request_status=CACHE_HIT",
                        "Gemini" if self.input_mode == "legacy" else "OpenRouter",
                        self.model, start, end, len(rows),
                    )
                    return value
            except (OSError, ValueError, TypeError, KeyError):
                pass
        check_cancel(cancel)
        source = Path(project.source_video_path)
        transient = None
        if self.input_mode == "frames":
            frame_paths = [self._frame(source, timestamp, cache_dir / "frames")
                           for timestamp in self._frame_times(start, end, rows)]
            media = [("image/jpeg", path.read_bytes()) for path in frame_paths]
            sampling = {"mode": "frames", "timestamps": self._frame_times(start, end, rows),
                        "adaptive": pass_name != "scene_chunk"}
        else:
            transient = self._proxy(source, start, end, fps, cache_dir / "proxies")
            media = [("video/mp4", transient.read_bytes())]
            sampling = {"mode": "video", "fps": fps, "adaptive": pass_name != "scene_chunk"}
        self.cache_misses += 1
        try:
            approved = dict(project.story_context)
            relevant_ids = {row.id for row in [*rows, *references]}
            approved["visual_contexts"] = [item for item in approved.get("visual_contexts", [])
                                            if item.get("id") in relevant_ids]
            prompt = json.dumps({
                "analysis_pass": pass_name,
                "output_language_contract": visual_language_contract(),
                "video_range_seconds": {"start": start, "end": end},
                "expected_target_ids": [row.id for row in rows],
                "canonical_character_registry": self.character_registry.snapshot(),
                "character_identity_contract": (
                    "Use only registry character_id values or UNKNOWN in visual/context fields. "
                    "Do not invent CHAR IDs. Report a genuinely new identity only in "
                    "new_character_candidates; keep bound fields UNKNOWN until application reconciliation."
                ),
                "visual_contexts_output_contract": (
                    "Return EXACTLY one visual_contexts item for every expected_target_ids value. "
                    "Preserve each exact integer id; do not invent, omit, merge, duplicate, or renumber IDs. "
                    "The returned ID set must exactly equal expected_target_ids."
                ),
                "target_transcript_rows": [self._row(row) for row in rows],
                "neighboring_transcript_rows": [self._row(row) for row in references],
                "user_requirements": project.translation_prompt,
                "user_mappings": project.glossary,
                "approved_context": approved,
                "sampling": sampling,
            }, ensure_ascii=False)
            if progress:
                progress(f"[VISUAL CONTEXT] range={start:.0f}-{end:.0f}s rows={len(rows)} cache=MISS")
            logger.info(
                "[VISUAL AI] provider=%s model=%s chunk_start=%.3f chunk_end=%.3f "
                "video_input=yes transcript_rows=%d request_status=STARTED",
                "Gemini" if self.input_mode == "legacy" else "OpenRouter",
                self.model, start, end, len(rows),
            )
            requested_ids = [row.id for row in rows]
            def retain_validation_failure(exc, raw_value, diagnostics):
                diagnostic_file = cache_dir / "diagnostics" / f"{signature}.json"
                atomic_json(diagnostic_file, {
                    "status": "validation_failed",
                    "model": self.model,
                    "http_status": getattr(self.client, "last_http_status", None),
                    **diagnostics,
                    "validation_reason": str(exc).strip() or type(exc).__name__,
                    "response": sanitize_visual_diagnostic(raw_value),
                })
                logger.warning(
                    "[VISUAL AI DIAGNOSTIC] model=%s requested=%s returned=%s "
                    "missing=%s extra=%s duplicates=%s reason=%s",
                    self.model, diagnostics["requested_ids"], diagnostics["returned_ids"],
                    diagnostics["missing_ids"], diagnostics["extra_ids"],
                    diagnostics["duplicate_ids"], str(exc),
                )
                self.request_plan.append({
                    "pass": pass_name, "target_ids": requested_ids, "window": [start, end],
                    "frames": len(media), "cache": "MISS",
                    "returned_ids": diagnostics["returned_ids"],
                    "validation": "FAIL",
                })

            language_correction_count = 0
            while True:
                request_prompt = prompt + (
                    "\n" + USER_FACING_AI_CORRECTION if language_correction_count else "")
                try:
                    if self.input_mode == "legacy":
                        payload = self.client.generate_video_json(
                            VISUAL_CONTEXT_SYSTEM, request_prompt, media[0][1], "video/mp4",
                            CONTEXT_SCHEMA, self.model, cancel=cancel, progress=progress,
                        )
                    else:
                        payload = self.client.generate_multimodal_json(
                            VISUAL_CONTEXT_SYSTEM, request_prompt, media, CONTEXT_SCHEMA, self.model,
                            cancel=cancel, progress=progress,
                        )
                except Exception:
                    logger.exception(
                        "[VISUAL AI] provider=%s model=%s chunk_start=%.3f chunk_end=%.3f "
                        "video_input=yes transcript_rows=%d request_status=FAILED",
                        "Gemini" if self.input_mode == "legacy" else "OpenRouter",
                        self.model, start, end, len(rows),
                    )
                    raise
                raw_value = json.loads(payload) if isinstance(payload, str) else payload
                diagnostics = visual_id_diagnostics(raw_value, requested_ids)
                try:
                    validate_visual_response_language(raw_value)
                    value = self._normalize_response(raw_value)
                    value = self.character_registry.reconcile_response(value, set(requested_ids))
                    value = StoryContext.from_dict(
                        value, {row.id for row in project.utterances}).to_dict()
                    diagnostics = visual_id_diagnostics(value, requested_ids)
                    if not diagnostics["exact_set"]:
                        raise ValueError("Visual AI context thiếu hoặc thừa target ID")
                    validate_user_facing_language(
                        story_context_user_facing_values(value), "Visual Context")
                    if self.registry_path is not None:
                        atomic_json(self.registry_path, self.character_registry.to_dict())
                    break
                except UserFacingLanguageError as exc:
                    if language_correction_count == 0:
                        language_correction_count = 1
                        if progress:
                            progress("Visual Context sai ngôn ngữ; yêu cầu AI trả lại bằng tiếng Việt")
                        continue
                    retain_validation_failure(exc, raw_value, diagnostics)
                    raise
                except Exception as exc:
                    retain_validation_failure(exc, raw_value, diagnostics)
                    raise
            atomic_json(cache_file, {
                "status": "completed", "response": value,
                "visual_analysis_version": VISUAL_ANALYSIS_VERSION,
                "prompt_schema_version": VISUAL_PROMPT_SCHEMA_VERSION,
                "user_facing_ai_language": USER_FACING_AI_LANGUAGE,
                "language_contract_version": USER_FACING_AI_LANGUAGE_CONTRACT_VERSION,
            })
            self.request_plan.append({
                "pass": pass_name, "target_ids": requested_ids, "window": [start, end],
                "frames": len(media), "cache": "MISS",
                "returned_ids": diagnostics["returned_ids"],
                "validation": "PASS",
            })
            logger.info(
                "[VISUAL AI] provider=%s model=%s chunk_start=%.3f chunk_end=%.3f "
                "video_input=yes transcript_rows=%d request_status=COMPLETED",
                "Gemini" if self.input_mode == "legacy" else "OpenRouter",
                self.model, start, end, len(rows),
            )
            return value
        finally:
            if transient is not None:
                transient.unlink(missing_ok=True)

    def analyze(self, project, directory, cancel=None, progress=None):
        source = Path(project.source_video_path).resolve(strict=True)
        self.cache_hits = self.cache_misses = 0
        self.request_plan = []
        cache_dir = Path(directory) / "cache" / "visual_context"
        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        registry_identity = content_hash({
            "version": REGISTRY_VERSION,
            "visual_version": VISUAL_ANALYSIS_VERSION,
            "prompt_schema": VISUAL_PROMPT_SCHEMA_VERSION,
            "video": video_signature(source),
            "model": self.model,
            "mode": self.input_mode,
            "timeline": [self._row(row) for row in ordered],
        })
        self.registry_identity = registry_identity
        self.registry_path = cache_dir / "registry" / f"{registry_identity}.json"
        try:
            cached_registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self.character_registry = CharacterRegistry.from_dict(cached_registry)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.character_registry = CharacterRegistry.from_project(project)
        if not self.character_registry.entries:
            self.character_registry = CharacterRegistry.from_project(project)
        result = StoryContext().to_dict()
        max_end = max(row.end for row in ordered)
        chunks = bounded_target_chunks(ordered)
        merged_primary_ids = set()
        for index, rows in enumerate(chunks, 1):
            check_cancel(cancel)
            start, end = rows[0].start, rows[-1].end
            first = ordered.index(rows[0])
            last = ordered.index(rows[-1]) + 1
            # Previous dialogue gives lightweight continuity without making a
            # completed chunk depend on edits in a later target chunk.
            references = [
                *ordered[max(0, first - 2):first],
                *ordered[last:min(len(ordered), last + 2)],
            ]
            if progress:
                progress(f"[VISUAL CONTEXT] chunk={index}/{len(chunks)} range={start:.0f}-{end:.0f}s rows={len(rows)}")
            value = self._request(project, cache_dir, rows, references, start, end, BASE_FPS,
                                  "scene_chunk", cancel, progress)
            returned_ids = [item["id"] for item in value["visual_contexts"]]
            duplicate_across_chunks = merged_primary_ids.intersection(returned_ids)
            if duplicate_across_chunks:
                raise ValueError(
                    f"Visual Context trùng ID giữa các chunk: {sorted(duplicate_across_chunks)}")
            merged_primary_ids.update(returned_ids)
            self._merge(result, value)

        result = self.character_registry.normalize_final_context(result)
        if review_complete(project):
            by_utterance = {row.id: row for row in ordered}
            for visual in result["visual_contexts"]:
                confirmed = by_utterance[visual["id"]].speaker_id
                proposed = visual["speaker"].get("spk_id")
                if proposed not in {confirmed, "SPK_UNKNOWN", ""}:
                    visual["analysis_status"] = "NEED_REVIEW"
                    visual["notes"] = " ".join(filter(None, (
                        visual.get("notes", ""),
                        f"Xung đột speaker AI {proposed}; giữ USER_CONFIRMED {confirmed}."
                    )))
                visual["speaker"]["spk_id"] = confirmed
        validate_merged_visual_contexts(
            result["visual_contexts"], [row.id for row in ordered])

        by_id = {row["id"]: row for row in result["visual_contexts"]}
        ambiguous = [row for row in ordered if row.id in by_id and (
            by_id[row.id]["confidence"] < LOW_CONFIDENCE
            or by_id[row.id]["scene_mode"] == "UNKNOWN"
            or by_id[row.id]["analysis_status"] in {"LOW_CONFIDENCE", "NEED_REVIEW"}
        )]
        for row in ambiguous:
            check_cancel(cancel)
            visual = by_id[row.id]
            margin = 5.0 if visual["scene_mode"] in {"FLASHBACK", "MEMORY", "IMAGINED", "UNKNOWN"} else 3.0
            start, end = max(0.0, row.start - margin), min(max_end, row.end + margin)
            index = ordered.index(row)
            references = ordered[max(0, index - 3):index] + ordered[index + 1:index + 4]
            value = self._request(project, cache_dir, [row], references, start, end, RESCAN_FPS,
                                  "targeted_rescan", cancel, progress)
            self._merge(result, value)
        by_id = {row["id"]: row for row in result["visual_contexts"]}
        result["visual_contexts"] = [by_id[row.id] for row in ordered if row.id in by_id]
        result = self.character_registry.normalize_final_context(result)
        if review_complete(project):
            by_utterance = {row.id: row for row in ordered}
            for visual in result["visual_contexts"]:
                confirmed = by_utterance[visual["id"]].speaker_id
                proposed = visual["speaker"].get("spk_id")
                if proposed not in {confirmed, "SPK_UNKNOWN", ""}:
                    visual["analysis_status"] = "NEED_REVIEW"
                    conflict = f"Xung đột speaker AI {proposed}; giữ USER_CONFIRMED {confirmed}."
                    if conflict not in visual.get("notes", ""):
                        visual["notes"] = " ".join(filter(None, (visual.get("notes", ""), conflict)))
                visual["speaker"]["spk_id"] = confirmed
        if not result["visual_contexts"]:
            raise ValueError("Visual AI không trả Visual Context theo ID")
        validate_merged_visual_contexts(
            result["visual_contexts"], [row.id for row in ordered])
        logger.info("[VISUAL CONTEXT] rows=%d rescans=%d", len(result["visual_contexts"]), len(ambiguous))
        return StoryContext.from_dict(result, {row.id for row in ordered}).to_dict()
