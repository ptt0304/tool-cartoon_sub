from __future__ import annotations

import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

from cartoon_sub.media.process import run_process
from cartoon_sub.project.cache import atomic_json, check_cancel, content_hash
from cartoon_sub.translation.context_models import CONTEXT_SCHEMA, StoryContext


logger = logging.getLogger(__name__)
VISUAL_ANALYSIS_VERSION = "visual-context-v1"
CHUNK_SECONDS = 60.0
BASE_FPS = 2
RESCAN_FPS = 4
LOW_CONFIDENCE = 0.70

VISUAL_CONTEXT_SYSTEM = """You analyze a real video together with timestamped Chinese transcript rows.
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
Return one visual_contexts row for every target transcript ID. Candidate output never overrides user-approved context.
"""


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
        "version": VISUAL_ANALYSIS_VERSION,
    })


class VisualContextAnalyzer:
    def __init__(self, client, model, ffmpeg_executable="ffmpeg"):
        self.client = client
        self.model = model
        self.ffmpeg_executable = ffmpeg_executable
        self.cache_hits = 0
        self.cache_misses = 0

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

    def _request(self, project, cache_dir, rows, references, start, end, fps,
                 pass_name, cancel=None, progress=None):
        signature = content_hash({
            "video": video_signature(project.source_video_path),
            "targets": [self._row(row) for row in rows],
            "references": [self._row(row) for row in references],
            "range": [round(start, 3), round(end, 3)], "fps": fps,
            "pass": pass_name, "model": self.model, "version": VISUAL_ANALYSIS_VERSION,
        })
        cache_file = cache_dir / f"{signature}.json"
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                normalized = self._normalize_response(cached["response"])
                value = StoryContext.from_dict(normalized, {row.id for row in project.utterances}).to_dict()
                if {item["id"] for item in value["visual_contexts"]} == {row.id for row in rows}:
                    if progress:
                        progress(f"[VISUAL CONTEXT] range={start:.0f}-{end:.0f}s rows={len(rows)} cache=HIT")
                    self.cache_hits += 1
                    logger.info(
                        "[GEMINI VIDEO] provider=Gemini model=%s chunk_start=%.3f chunk_end=%.3f "
                        "video_input=no transcript_rows=%d request_status=CACHE_HIT",
                        self.model, start, end, len(rows),
                    )
                    return value
            except (OSError, ValueError, TypeError, KeyError):
                pass
        check_cancel(cancel)
        proxy = self._proxy(Path(project.source_video_path), start, end, fps, cache_dir / "proxies")
        self.cache_misses += 1
        try:
            approved = dict(project.story_context)
            relevant_ids = {row.id for row in [*rows, *references]}
            approved["visual_contexts"] = [item for item in approved.get("visual_contexts", [])
                                            if item.get("id") in relevant_ids]
            prompt = json.dumps({
                "analysis_pass": pass_name,
                "video_range_seconds": {"start": start, "end": end},
                "target_transcript_rows": [self._row(row) for row in rows],
                "neighboring_transcript_rows": [self._row(row) for row in references],
                "user_requirements": project.translation_prompt,
                "user_mappings": project.glossary,
                "approved_context": approved,
                "sampling": {"fps": fps, "adaptive": pass_name != "scene_chunk"},
            }, ensure_ascii=False)
            if progress:
                progress(f"[VISUAL CONTEXT] range={start:.0f}-{end:.0f}s rows={len(rows)} cache=MISS")
            logger.info(
                "[GEMINI VIDEO] provider=Gemini model=%s chunk_start=%.3f chunk_end=%.3f "
                "video_input=yes transcript_rows=%d request_status=STARTED",
                self.model, start, end, len(rows),
            )
            try:
                payload = self.client.generate_video_json(
                    VISUAL_CONTEXT_SYSTEM, prompt, proxy.read_bytes(), "video/mp4",
                    CONTEXT_SCHEMA, self.model, cancel=cancel, progress=progress,
                )
            except Exception:
                logger.exception(
                    "[GEMINI VIDEO] provider=Gemini model=%s chunk_start=%.3f chunk_end=%.3f "
                    "video_input=yes transcript_rows=%d request_status=FAILED",
                    self.model, start, end, len(rows),
                )
                raise
            value = json.loads(payload) if isinstance(payload, str) else payload
            value = self._normalize_response(value)
            value = StoryContext.from_dict(value, {row.id for row in project.utterances}).to_dict()
            if {item["id"] for item in value["visual_contexts"]} != {row.id for row in rows}:
                raise ValueError("Gemini visual context thiếu hoặc thừa target ID")
            atomic_json(cache_file, {"status": "completed", "response": value})
            logger.info(
                "[GEMINI VIDEO] provider=Gemini model=%s chunk_start=%.3f chunk_end=%.3f "
                "video_input=yes transcript_rows=%d request_status=COMPLETED",
                self.model, start, end, len(rows),
            )
            return value
        finally:
            proxy.unlink(missing_ok=True)

    def analyze(self, project, directory, cancel=None, progress=None):
        source = Path(project.source_video_path).resolve(strict=True)
        self.cache_hits = self.cache_misses = 0
        cache_dir = Path(directory) / "cache" / "visual_context"
        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        result = StoryContext().to_dict()
        max_end = max(row.end for row in ordered)
        chunks = []
        start = 0.0
        while start < max_end:
            end = min(max_end + 0.001, start + CHUNK_SECONDS)
            rows = [row for row in ordered if start <= row.start < end]
            if rows:
                chunks.append((start, end, rows))
            start += CHUNK_SECONDS
        for index, (start, end, rows) in enumerate(chunks, 1):
            check_cancel(cancel)
            first = ordered.index(rows[0])
            last = ordered.index(rows[-1]) + 1
            # Keep chunk cache dependencies causal and local.  A text edit in the
            # following chunk must not invalidate an already analysed chunk.
            references = ordered[max(0, first - 3):first]
            if progress:
                progress(f"[VISUAL CONTEXT] chunk={index}/{len(chunks)} range={start:.0f}-{end:.0f}s rows={len(rows)}")
            value = self._request(project, cache_dir, rows, references, start, end, BASE_FPS,
                                  "scene_chunk", cancel, progress)
            self._merge(result, value)

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
        result["visual_contexts"].sort(key=lambda row: row["id"])
        if not result["visual_contexts"]:
            raise ValueError("Gemini không trả Visual Context theo ID")
        logger.info("[VISUAL CONTEXT] rows=%d rescans=%d", len(result["visual_contexts"]), len(ambiguous))
        return StoryContext.from_dict(result, {row.id for row in ordered}).to_dict()
