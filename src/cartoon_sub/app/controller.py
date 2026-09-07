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

class Controller:
    def __init__(self, settings_store=None):
        self.manager = ProjectManager()
        self.project = None
        self.directory = None
        self.settings_store = settings_store or SettingsStore()
        self.pipeline = TranscriptionPipeline(self.settings_store)
        self.context_service = ContextService(self.settings_store)
        self.translation_pipeline = TranslationPipeline(self.settings_store)

    def create(self, video, directory, **job):
        metadata = probe(video, **job)
        return self.manager.create(directory, video, metadata), Path(directory)

    def load(self, path):
        return self.manager.load(path), Path(path).parent

    def accept(self, result):
        self.project, self.directory = result
        mark_stale(self.project, self.settings_store.load())

    def save(self):
        if self.project:
            mark_stale(self.project, self.settings_store.load())
            self.manager.save(self.project, self.directory)
            if self.project.transcription_status != "not_started":
                save_subtitle_artifacts(self.project, self.directory)
            if self.project.translation_status != "not_started" or (self.directory / "subtitle" / "vi.srt").exists():
                save_translation_artifacts(self.project, self.directory)

    def import_subtitles(self, path):
        segments = import_srt(path)
        self.project.segments = segments
        self.project.transcription_status = "imported"
        self.project.translation_status = "not_started"
        self.project.translation_notes = {}
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

