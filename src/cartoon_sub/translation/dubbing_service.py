from pathlib import Path
from dataclasses import asdict
import logging
import re
from .chunker import translation_batches, source_rows
from .prompts import dubbing_prompt, dubbing_system, duration_rewrite_prompt, PROMPT_VERSION, editorial
from .gemini_translator import TRANSLATION_SCHEMA, TranslationValidationError, validate_translation
from .requests import CachedRequests
from .artifacts import save_translation_artifacts
from .context_service import source_fingerprint
from .context_models import StoryContext
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import text_client_factory
from cartoon_sub.speaker.service import refresh_timeline, review_complete
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.syllable.vietnamese import count_syllables
from cartoon_sub.project.cache import check_cancel, content_hash
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project
from cartoon_sub.tts.duration_fit import DurationFitPlanner, MAX_SEMANTIC_REWRITES, VoiceCalibrationCache
from cartoon_sub.tts.local_tts_client import LocalTTSClient


logger = logging.getLogger(__name__)


def parse_dubbing_threshold(value):
    raw = str(value).strip()
    if not re.fullmatch(r"\+?[1-9]\d*", raw):
        raise ValueError("Ngưỡng Δ target phải là số nguyên dương, ví dụ +5")
    return int(raw.lstrip("+"))


def eligible_dubbing_ids(project, ids, threshold):
    if type(threshold) is not int or threshold <= 0:
        raise ValueError("Ngưỡng Δ target phải là số nguyên dương, ví dụ +5")
    selected = set(ids)
    return [
        segment.id for segment in project.segments
        if segment.id in selected and segment.syllable_delta > 0
        and segment.syllable_delta >= threshold
    ]


class DubbingService:
    def __init__(self, store, client_factory=GeminiClient):
        self.store,self.factory=store,client_factory

    def _calibration_cache(self, directory):
        folder = Path(getattr(self.store, "folder", Path(directory) / "cache"))
        return VoiceCalibrationCache(folder / "voice_duration_calibration.json")

    def _apply_voice_budgets(self, project, directory, chosen, budget, progress=None):
        planner = DurationFitPlanner()
        calibrations = {}
        voices = {}
        try:
            local_settings = self.store.load_local_tts()
            client = LocalTTSClient(local_settings)
            try:
                if client.health().get("status") == "READY":
                    voices = {voice.get("voice_id"): voice for voice in client.list_voices()}
                    needed = {
                        (project.speakers[row.speaker_id].get("tts_voice_id"),
                         float(project.speakers[row.speaker_id].get("tts_speed", 1.0)))
                        for row in project.utterances if row.id in chosen
                        and row.speaker_id in project.speakers
                        and project.speakers[row.speaker_id].get("tts_voice_id")
                    }
                    cache = self._calibration_cache(directory)
                    for voice_id, speed in needed:
                        voice = voices.get(voice_id)
                        if voice and voice.get("status") == "READY":
                            calibrations[(voice_id, speed)] = cache.calibrate(
                                client, local_settings.base_url, voice, speed, progress,
                            )
            finally:
                client.close()
        except Exception as exc:
            logger.warning("Voice calibration unavailable; using configured fallback rate: %s", exc)

        for row in project.utterances:
            if row.id not in chosen:
                continue
            speaker = project.speakers.get(row.speaker_id, {})
            voice_id = speaker.get("tts_voice_id")
            speed = float(speaker.get("tts_speed", 1.0))
            calibration = calibrations.get((voice_id, speed))
            row.dubbing_voice_id = voice_id
            row.dubbing_estimated_rate = (
                calibration.syllables_per_second if calibration else float(budget.speech_rate) * speed
            )
            row.dubbing_budget_duration = min(
                planner.potential_duration(project, row), row.duration * 1.30,
            )
            row.recalculate(budget)

    def optimize(self, project, directory, ids, *, threshold=1, cancel=None, progress=None):
        project=Project.from_dict(project.to_dict())
        refresh_timeline(project)
        if not review_complete(project): raise ValueError("Cần duyệt speaker trước khi tối ưu dubbing")
        if project.context_source_hash != source_fingerprint(project):
            raise ValueError("Hãy kiểm tra và áp dụng hồ sơ ngữ cảnh cho transcript hiện tại")
        StoryContext.from_dict(project.story_context, {s.id for s in project.segments})
        chosen=set(eligible_dubbing_ids(project, ids, threshold))
        if not chosen:
            return project,Path(directory)
        if not chosen or not chosen.issubset({s.id for s in project.segments}): raise ValueError("Chọn các câu cần tối ưu")
        if any(not s.vi_subtitle.strip() for s in project.segments if s.id in chosen):
            raise ValueError("Hãy dịch bản subtitle trước khi tối ưu dubbing")
        settings=self.store.load()
        budget=DubbingSettings(**project.dubbing_settings)
        self._apply_voice_budgets(project, directory, chosen, budget, progress)
        factory=self.factory if self.factory is not GeminiClient else (GeminiClient if settings.translation_provider == "gemini" else text_client_factory(settings.translation_provider))
        requests=CachedRequests(self.store,Path(directory)/"cache"/"dubbing",settings.translation_model,
            settings.retry_count,cancel,progress,factory,settings.translation_provider)
        all_rows=source_rows(project.segments)
        by_id={s.id:s for s in project.segments}
        def fingerprint(segment):
            return content_hash({"version":PROMPT_VERSION,"source":all_rows,"id":segment.id,
                "current_vi":segment.vi_dubbing,"subtitle":segment.vi_subtitle,"editorial":editorial(project),
                "budget":project.dubbing_settings,"system":dubbing_system(),"provider":settings.translation_provider,"model":settings.translation_model,
                "mode_prompt":dubbing_prompt(project,[next(r for r in all_rows if r['id']==segment.id)],[],[])})
        chosen={sid for sid in chosen if by_id[sid].dubbing_status!="completed" or by_id[sid].dubbing_fingerprint!=fingerprint(by_id[sid])}
        try:
            for targets,before,after in translation_batches(project.segments,settings.translation_chunk_size):
                targets=[{**row,"current_vi":by_id[row["id"]].vi_dubbing,
                          "vi_subtitle":by_id[row["id"]].vi_subtitle} for row in targets if row["id"] in chosen]
                if not targets: continue
                target_ids=[r["id"] for r in targets]
                prompt=dubbing_prompt(project,targets,before,after)
                result,key=requests.request(dubbing_system(),prompt,TRANSLATION_SCHEMA,
                    lambda p:validate_translation(p,target_ids),f"Tối ưu dubbing ID {target_ids[0]}–{target_ids[-1]}")
                for attempt in range(2):
                    failed=[r for r in result
                            if count_syllables(r["vi"])>by_id[r["id"]].target_syllables]
                    if not failed: break
                    retry_ids=[r["id"] for r in failed]
                    import json
                    feedback=[{"id":r["id"],"previous_vi":r["vi"],"actual_syllables":count_syllables(r["vi"]),
                               "target_at_most":by_id[r["id"]].target_syllables} for r in failed]
                    correction=(prompt+"\nSHORTENING RETRY: return ONLY these failed IDs. Local counts are authoritative. "
                                "Rewrite at or below the required target; never reverse meaning, negation, names, "
                                "numbers, terminology, actors, or cause/result. "
                                +json.dumps(feedback,ensure_ascii=False)+f"\nRewrite pass {attempt+1}")
                    revised,_=requests.request(dubbing_system(),correction,TRANSLATION_SCHEMA,
                        lambda p:validate_translation(p,retry_ids),f"Rút gọn lại {attempt+1}/2")
                    replacements={r["id"]:r for r in revised}
                    result=[replacements.get(r["id"],r) for r in result]
                check_cancel(cancel)
                for row in result:
                    segment=by_id[row["id"]]
                    # Keep the pre-optimization master texts once so a user
                    # can explicitly undo this AI-only operation later.
                    if segment.pre_optimization_vi_dubbing is None:
                        segment.pre_optimization_vi_subtitle = segment.vi_subtitle
                        segment.pre_optimization_vi_dubbing = segment.vi_dubbing
                    segment.vi_dubbing=row["vi"]
                    segment.dubbing_optimized=True
                    segment.semantic_compression=row["compressed"] or segment.translation_mode=="short_dub"
                    segment.meaning_preservation=row["meaning_preservation"]
                    segment.recalculate(budget)
                    segment.dubbing_status=("failed" if segment.translation_mode=="strict_iso_syllabic" and segment.syllable_delta!=0 else "completed")
                    segment.dubbing_fingerprint=fingerprint(segment)
                    project.translation_notes[f"dub:{segment.id}"]=row["review_note"]
                project.chunk_states.setdefault("dubbing",{})[key]={"ids":target_ids,"status":"completed_with_qc"}
                ProjectManager().save(project,directory)
            save_translation_artifacts(project,directory)
            return project,Path(directory)
        finally:
            requests.close()

    def rewrite_duration_failures(self, project, directory, ids, *, cancel=None, progress=None):
        chosen = [row for row in project.utterances if row.id in set(ids)
                  and row.dubbing_rewrite_attempts < MAX_SEMANTIC_REWRITES
                  and row.dubbing_fit_status in {"REWRITE_SHORTER", "STRONG_REWRITE"}]
        if not chosen:
            return []
        settings = self.store.load()
        factory = self.factory if self.factory is not GeminiClient else (
            GeminiClient if settings.translation_provider == "gemini"
            else text_client_factory(settings.translation_provider)
        )
        requests = CachedRequests(
            self.store, Path(directory) / "cache" / "dubbing_duration", settings.translation_model,
            settings.retry_count, cancel, progress, factory, settings.translation_provider,
        )
        ordered = sorted(project.utterances, key=lambda row: (row.start, row.end, row.id))
        changed = []
        try:
            for row in chosen:
                check_cancel(cancel)
                index = ordered.index(row)
                before = source_rows(ordered[max(0, index - 3):index])
                after = source_rows(ordered[index + 1:index + 4])
                allowed = (row.allowed_audio_end or row.end) - (row.allowed_audio_start if row.allowed_audio_start is not None else row.start)
                target = {
                    "id": row.id,
                    "chinese_source": row.zh,
                    "full_vi_subtitle": row.vi_subtitle,
                    "current_vi_dubbing": row.vi_dubbing,
                    "speaker": row.speaker_id,
                    "voice_id": row.dubbing_voice_id or project.speakers.get(row.speaker_id, {}).get("tts_voice_id"),
                    "available_duration": round(allowed, 3),
                    "actual_tts_duration": round(float(row.tts_duration or 0), 3),
                    "required_reduction_percent": round(max(0.0, 1.0 - allowed / float(row.tts_duration or allowed)) * 100, 1),
                    "rewrite_attempt": row.dubbing_rewrite_attempts + 1,
                }
                prompt = duration_rewrite_prompt(project, target, before, after)
                def validate(payload):
                    result = validate_translation(payload, [row.id])
                    if re.search(r"[\u4e00-\u9fff]", result[0]["vi"]):
                        raise TranslationValidationError(f"ID {row.id}: VI Dubbing còn ký tự Trung")
                    return result
                result, _ = requests.request(
                    dubbing_system(), prompt, TRANSLATION_SCHEMA, validate,
                    f"Dubbing {row.id} • Rewrite {row.dubbing_rewrite_attempts + 1}/{MAX_SEMANTIC_REWRITES}",
                )
                candidate = result[0]
                row.dubbing_rewrite_attempts += 1
                if candidate["vi"] == row.vi_dubbing:
                    row.dubbing_fit_status = "NEED_REVIEW"
                    continue
                if row.pre_optimization_vi_dubbing is None:
                    row.pre_optimization_vi_subtitle = row.vi_subtitle
                    row.pre_optimization_vi_dubbing = row.vi_dubbing
                row.vi_dubbing = candidate["vi"]
                row.dubbing_optimized = True
                row.dubbing_status = "rewritten"
                row.semantic_compression = True
                row.meaning_preservation = candidate["meaning_preservation"]
                row.dubbing_fingerprint = ""
                row.tts_generation_status = "stale"
                row.tts_error = ""
                row.dubbing_fit_status = "REWRITTEN"
                project.translation_notes[f"dub:{row.id}"] = candidate["review_note"]
                row.recalculate(DubbingSettings(**project.dubbing_settings))
                changed.append(row.id)
            ProjectManager().save(project, directory)
            save_translation_artifacts(project, directory)
            return changed
        finally:
            requests.close()
