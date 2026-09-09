from pathlib import Path
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.app.settings import SettingsStore
from cartoon_sub.transcription.pipeline import TranscriptionPipeline, save_subtitle_artifacts
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.translation.context_service import ContextService, source_fingerprint
from cartoon_sub.translation.pipeline import TranslationPipeline, mark_stale
from cartoon_sub.translation.context_models import StoryContext
from cartoon_sub.translation.artifacts import save_translation_artifacts
from cartoon_sub.translation.glossary import parse_glossary
from cartoon_sub.translation.dubbing_service import DubbingService
from cartoon_sub.speaker.service import refresh_timeline, approve_review, review_complete
from cartoon_sub.speaker import editor_service as speaker_editor
from cartoon_sub.tts.export_service import export_speakers
from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
from cartoon_sub.subtitle.semantic_segmentation import SemanticSegmentationService
from cartoon_sub.subtitle.audio_timing import AudioTimingRefiner

class Controller:
    def __init__(self, settings_store=None):
        self.manager = ProjectManager()
        self.project = None
        self.directory = None
        self.settings_store = settings_store or SettingsStore()
        self.pipeline = TranscriptionPipeline(self.settings_store)
        self.context_service = ContextService(self.settings_store)
        self.translation_pipeline = TranslationPipeline(self.settings_store)
        self.dubbing_service=DubbingService(self.settings_store)
        self.segmentation_service=SubtitleSegmentationService(SemanticSegmentationService(self.settings_store))
        self.audio_timing_refiner=AudioTimingRefiner(self.settings_store)

    def create(self, video, directory, **job):
        metadata = probe(video, **job)
        project=self.manager.create(directory, video, metadata)
        project.dubbing_settings=self.settings_store.load_dubbing().to_dict()
        self.manager.save(project,directory)
        return project,Path(directory)

    def load(self, path):
        return self.manager.load(path), Path(path).parent

    def accept(self, result):
        self.project, self.directory = result
        mark_stale(self.project, self.settings_store.load())

    def save(self):
        if self.project:
            refresh_timeline(self.project)
            mark_stale(self.project, self.settings_store.load())
            self.manager.save(self.project, self.directory)
            if self.project.transcription_status != "not_started":
                save_subtitle_artifacts(self.project, self.directory)
            if self.project.translation_status != "not_started" or (self.directory / "subtitle" / "vi.srt").exists():
                save_translation_artifacts(self.project, self.directory)

    def import_subtitles(self, path):
        segments = import_srt(path)
        self.project.segments = segments
        self.project.speakers={}
        self.project.speaker_review_hash=""
        for s in segments: s.translation_mode=self.project.dubbing_settings.get("mode","balanced_dubbing")
        self.project.transcription_status = "imported"
        self.project.translation_status = "not_started"
        self.project.translation_notes = {}
        self.project.segmentation_cache = {}
        self.project.cache_hashes.pop("translation", None)
        self.project.chunk_states.pop("translation", None)
        self.save()

    def update_translation_options(self, preset, custom_prompt, glossary_text, genres):
        from cartoon_sub.translation.presets import GENRES, STYLES
        if preset not in STYLES or any(g not in GENRES for g in genres):
            raise ValueError("Thể loại/văn phong không hợp lệ")
        glossary = parse_glossary(glossary_text)
        self.project.translation_preset = preset
        self.project.translation_prompt = custom_prompt
        self.project.glossary = glossary
        self.project.translation_genres = sorted(set(genres))
        mark_stale(self.project, self.settings_store.load())

    def analyze_context(self, **job):
        if not review_complete(self.project): raise ValueError("Cần duyệt speaker trong Transcript trước khi phân tích ngữ cảnh dịch")
        return self.context_service.analyze(self.project, self.directory, **job)

    def apply_context(self, context):
        self.project.story_context = StoryContext.from_dict(context, {s.id for s in self.project.segments}).to_dict()
        self.project.context_source_hash = source_fingerprint(self.project)
        self.project.context_status = "applied"
        self.save()

    def translate(self, **job):
        return self.translation_pipeline.run(self.project, self.directory, **job)

    def transcribe(self, **job):
        return self.pipeline.run(self.project, self.directory, **job)

    def test_connection(self, settings, entered_key="", **job):
        settings.validate()
        client = GeminiClient(entered_key.strip() or self.settings_store.get_key())
        try:
            return client.test_connection(settings.transcription_model, **job)
        finally:
            client.close()

    def speaker_action(self, action, *args):
        getattr(speaker_editor,action)(self.project,*args)
        self.save()

    def approve_speakers(self):
        approve_review(self.project)
        self.save()

    def optimize_dubbing(self, ids, **job):
        return self.dubbing_service.optimize(self.project,self.directory,ids,**job)

    def export_speaker_files(self, text_type, **job):
        return export_speakers(self.project,self.directory,text_type,**job)

    def edit_utterance(self, sid, subtitle, dubbing, mode, target):
        from cartoon_sub.translation.modes import TranslationMode
        TranslationMode(mode)
        s=next(s for s in self.project.segments if s.id==sid)
        s.vi_subtitle,s.vi_dubbing=subtitle,dubbing
        s.translation_mode=mode
        s.target_override=target or None
        s.dubbing_optimized=dubbing!=subtitle
        s.dubbing_status="manual"
        s.dubbing_fingerprint=""
        s.display_segments=[]
        self.segmentation_service.invalidate(self.project,[sid])
        self.save()

    def update_segmentation_settings(self, profile, settings):
        self.segmentation_service.update_settings(self.project, profile, settings)

    def auto_segment(self, utterance_ids=None, **job):
        changed, skipped=self.segmentation_service.auto_segment(self.project,utterance_ids,
            cancel=job.get("cancel"), progress=job.get("progress"))
        self.save()
        if job.get("progress"):
            job["progress"](f"Đã segment {len(changed)} utterance; giữ {len(skipped)} bản chỉnh tay")
        return self.project,self.directory

    def reset_segmentation(self, utterance_ids):
        self.segmentation_service.reset(self.project,utterance_ids)
        self.save()

    def split_display_segment(self, utterance_id, display_id, word_index):
        self.segmentation_service.split_manual(self.project,utterance_id,display_id,word_index)
        self.save()

    def merge_display_segments(self, utterance_id, display_ids):
        self.segmentation_service.merge_manual(self.project,utterance_id,display_ids)
        self.save()

    def refine_display_timing(self, utterance_ids, **job):
        chosen = set(utterance_ids)
        if not chosen:
            raise ValueError("Chọn ít nhất một Utterance để căn thời gian audio")
        audio = self.directory / "audio" / "source.wav"
        if not audio.is_file():
            raise ValueError("Chưa có audio nguồn; hãy chạy Gemini transcript trước")
        for utterance in self.project.utterances:
            if utterance.id not in chosen:
                continue
            segments = self.audio_timing_refiner.refine(utterance, audio, self.directory / "cache" / "audio_timing",
                cancel=job.get("cancel"), progress=job.get("progress"))
            utterance.set_display_segments(segments)
            self.project.segmentation_cache[str(utterance.id)] = {"manual": True, "timing_source": "audio_alignment"}
        self.save()
        return self.project, self.directory

