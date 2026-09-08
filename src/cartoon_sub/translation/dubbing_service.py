from pathlib import Path
from dataclasses import asdict
from .chunker import translation_batches, source_rows
from .prompts import dubbing_prompt, dubbing_system, PROMPT_VERSION, editorial
from .gemini_translator import TRANSLATION_SCHEMA, validate_translation
from .requests import CachedRequests
from .artifacts import save_translation_artifacts
from .context_service import source_fingerprint
from .context_models import StoryContext
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.speaker.service import refresh_timeline, review_complete
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.syllable.vietnamese import count_syllables
from cartoon_sub.project.cache import check_cancel, content_hash
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project


class DubbingService:
    def __init__(self, store, client_factory=GeminiClient):
        self.store,self.factory=store,client_factory

    def optimize(self, project, directory, ids, *, cancel=None, progress=None):
        project=Project.from_dict(project.to_dict())
        refresh_timeline(project)
        if not review_complete(project): raise ValueError("Cần duyệt speaker trước khi tối ưu dubbing")
        if project.context_source_hash != source_fingerprint(project):
            raise ValueError("Hãy kiểm tra và áp dụng hồ sơ ngữ cảnh cho transcript hiện tại")
        StoryContext.from_dict(project.story_context, {s.id for s in project.segments})
        chosen=set(ids)
        if not chosen or not chosen.issubset({s.id for s in project.segments}): raise ValueError("Chọn các câu cần tối ưu")
        if any(not s.vi_subtitle.strip() for s in project.segments if s.id in chosen):
            raise ValueError("Hãy dịch bản subtitle trước khi tối ưu dubbing")
        settings=self.store.load()
        budget=DubbingSettings(**project.dubbing_settings)
        requests=CachedRequests(self.store,Path(directory)/"cache"/"dubbing",settings.translation_model,
            settings.retry_count,cancel,progress,self.factory)
        all_rows=source_rows(project.segments)
        by_id={s.id:s for s in project.segments}
        def fingerprint(segment):
            return content_hash({"version":PROMPT_VERSION,"source":all_rows,"id":segment.id,
                "current_vi":segment.vi_dubbing,"subtitle":segment.vi_subtitle,"editorial":editorial(project),
                "budget":project.dubbing_settings,"system":dubbing_system(),"model":settings.translation_model,
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
                for attempt in range(budget.strict_retry):
                    failed=[r for r in result if by_id[r["id"]].translation_mode=="strict_iso_syllabic"
                            and count_syllables(r["vi"])!=by_id[r["id"]].target_syllables]
                    if not failed: break
                    retry_ids=[r["id"] for r in failed]
                    import json
                    feedback=[{"id":r["id"],"previous_vi":r["vi"],"actual_syllables":count_syllables(r["vi"]),
                               "required_exactly":by_id[r["id"]].target_syllables} for r in failed]
                    correction=(prompt+"\nSTRICT REWRITE: return ONLY these failed IDs. Local counts are authoritative. "
                                "Rewrite with exactly the required count; never reverse meaning. "
                                +json.dumps(feedback,ensure_ascii=False)+f"\nRewrite pass {attempt+1}")
                    revised,_=requests.request(dubbing_system(),correction,TRANSLATION_SCHEMA,
                        lambda p:validate_translation(p,retry_ids),f"Strict rewrite {attempt+1}/{budget.strict_retry}")
                    replacements={r["id"]:r for r in revised}
                    result=[replacements.get(r["id"],r) for r in result]
                check_cancel(cancel)
                for row in result:
                    segment=by_id[row["id"]]
                    segment.vi_dubbing=row["vi"]
                    segment.dubbing_optimized=True
                    segment.semantic_compression=row["compressed"] or segment.translation_mode=="short_dub"
                    segment.meaning_preservation=row["meaning_preservation"]
                    if not budget.separate_texts: segment.vi_subtitle=row["vi"]
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
