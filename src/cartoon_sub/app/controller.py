from pathlib import Path
from dataclasses import asdict
import hashlib
import os
import re
import wave
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.media.ffprobe import probe
from cartoon_sub.subtitle.parser import import_srt
from cartoon_sub.app.settings import LocalTTSSettings, SettingsStore
from cartoon_sub.project.cache import check_cancel
from cartoon_sub.speaker.models import Speaker
from cartoon_sub.tts.generation_service import LocalTTSGenerationService
from cartoon_sub.tts.local_tts_client import LocalTTSClient
from cartoon_sub.tts.mix_service import TTSTimelineMixService
from cartoon_sub.transcription.pipeline import TranscriptionPipeline, save_subtitle_artifacts
from cartoon_sub.ai.gemini_client import GeminiClient
from cartoon_sub.ai.text_client import TextProviderClient
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
from cartoon_sub.tts.process_manager import LocalTTSProcessManager

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
        self.local_tts_manager = LocalTTSProcessManager()

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

    def test_translation_connection(self, settings, entered_key="", **job):
        settings.validate()
        if settings.translation_provider == "gemini":
            client = GeminiClient(entered_key.strip() or self.settings_store.get_key("gemini"))
        else:
            client = TextProviderClient(settings.translation_provider, entered_key.strip() or self.settings_store.get_key(settings.translation_provider))
        try:
            return client.test_connection(settings.translation_model, **job)
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

    def _local_tts_client(self):
        return LocalTTSClient(self.settings_store.load_local_tts())

    def test_local_tts_connection(self, base_url, **job):
        current = self.settings_store.load_local_tts()
        current.base_url = base_url
        settings = current.validate()
        self.settings_store.save_local_tts(settings)
        check_cancel(job.get("cancel"))
        client = LocalTTSClient(settings)
        try:
            health = client.health()
            check_cancel(job.get("cancel"))
            voices = client.list_ready_voices()
            return {"health": health, "voices": voices}
        finally:
            client.close()

    def ensure_local_tts_running(self, **job):
        settings = self.settings_store.load_local_tts()
        check_cancel(job.get("cancel"))
        ready, status = self.local_tts_manager.ensure_running(
            settings, cancel=job.get("cancel"), progress=job.get("progress")
        )
        check_cancel(job.get("cancel"))
        client = LocalTTSClient(settings)
        try:
            health = client.health()
            check_cancel(job.get("cancel"))
            voices = client.list_ready_voices()
            return {"health": health, "voices": voices, "launch_status": status}
        finally:
            client.close()

    def set_local_tts_executable(self, path: str):
        settings = self.settings_store.load_local_tts()
        settings.local_tts_executable = path
        self.settings_store.save_local_tts(settings)

    def shutdown_local_tts(self):
        settings = self.settings_store.load_local_tts()
        if settings.stop_local_tts_on_exit:
            self.local_tts_manager.shutdown_owned_process()

    def load_local_tts_voices(self, **job):
        check_cancel(job.get("cancel"))
        client = self._local_tts_client()
        try:
            voices = client.list_ready_voices()
            check_cancel(job.get("cancel"))
            return voices
        finally:
            client.close()

    def update_speaker_tts_voice(self, speaker_id, voice_id, speed):
        if not self.project or speaker_id not in self.project.speakers:
            raise ValueError(f"Không tìm thấy speaker {speaker_id}")
        values = dict(self.project.speakers[speaker_id])
        previous_voice = values.get("tts_voice_id")
        previous_speed = float(values.get("tts_speed", 1.0))
        values["tts_voice_id"] = voice_id or None
        values["tts_speed"] = speed
        speaker = Speaker(**values)
        self.project.speakers[speaker_id] = asdict(speaker)
        if previous_voice != speaker.tts_voice_id or previous_speed != float(speaker.tts_speed):
            for utterance in self.project.utterances:
                if utterance.speaker_id == speaker_id and utterance.tts_generation_status in {"generated", "cached"}:
                    utterance.tts_generation_status = "stale"
                    utterance.tts_error = ""
        self.save()

    def preview_local_tts_voice(self, voice_id, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        check_cancel(job.get("cancel"))
        client = self._local_tts_client()
        try:
            wav_bytes = client.preview_voice(voice_id)
        finally:
            client.close()
        check_cancel(job.get("cancel"))
        cache_dir = self.directory / "cache" / "tts" / "previews"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_voice_id = re.sub(r"[^A-Za-z0-9_-]", "_", voice_id).strip("_") or "voice"
        fingerprint = hashlib.sha256(voice_id.encode("utf-8")).hexdigest()[:12]
        destination = cache_dir / f"{safe_voice_id}_{fingerprint}.wav"
        temporary = destination.with_suffix(".tmp.wav")
        try:
            with temporary.open("wb") as handle:
                handle.write(wav_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            with wave.open(str(temporary), "rb") as reader:
                if reader.getnframes() <= 0:
                    raise ValueError("Local_TTS preview WAV không hợp lệ")
            os.replace(temporary, destination)
            return destination
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError("Local_TTS preview WAV không hợp lệ") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def generate_tts(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        settings = self.settings_store.load_local_tts()
        client = LocalTTSClient(settings)
        try:
            return LocalTTSGenerationService(
                client, self.manager, settings.base_url
            ).generate(self.project, self.directory, **job)
        finally:
            client.close()

    def mix_tts(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        settings = self.settings_store.load_local_tts()
        result = TTSTimelineMixService().mix(
            self.project, self.directory, settings.base_url, **job
        )
        self.project.final_audio_status = "stale"
        self.save()
        return result

    def update_audio_settings(
        self,
        original_volume=None,
        dubbed_volume=None,
        additional_audio_path=...,
        additional_audio_volume=None,
        additional_audio_start=None,
    ):
        if not self.project:
            raise ValueError("Chưa mở project")
        current = self.project.audio_settings
        new_orig = current.original_volume if original_volume is None else int(round(original_volume))
        new_dub = current.dubbed_volume if dubbed_volume is None else int(round(dubbed_volume))
        new_path = current.additional_audio_path if additional_audio_path is Ellipsis else (str(additional_audio_path).strip() if additional_audio_path else None)
        new_add_vol = current.additional_audio_volume if additional_audio_volume is None else int(round(additional_audio_volume))
        new_start = current.additional_audio_start if additional_audio_start is None else float(additional_audio_start)

        if (
            new_orig != current.original_volume
            or new_dub != current.dubbed_volume
            or new_path != current.additional_audio_path
            or new_add_vol != current.additional_audio_volume
            or abs(new_start - current.additional_audio_start) > 1e-4
        ):
            from cartoon_sub.subtitle.models import AudioSettings
            self.project.audio_settings = AudioSettings(
                original_volume=new_orig,
                dubbed_volume=new_dub,
                additional_audio_path=new_path,
                additional_audio_volume=new_add_vol,
                additional_audio_start=new_start,
            ).validate()
            self.project.final_audio_status = "stale"
            self.save()

    def clear_additional_audio(self):
        if not self.project:
            raise ValueError("Chưa mở project")
        self.project.audio_settings.additional_audio_path = None
        self.project.audio_settings.additional_audio_start = 0.0
        self.project.final_audio_status = "stale"
        self.save()

    def mix_final_audio(self, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        from cartoon_sub.tts.final_mix_service import FinalAudioMixService
        result = FinalAudioMixService().mix(self.project, self.directory, **job)
        self.save()
        return result

    def render_export(self, test_mode=False, start=0.0, **job):
        if not self.project or not self.directory:
            raise ValueError("Chưa mở project")
        from cartoon_sub.media.preview import VideoRenderer
        renderer = VideoRenderer()
        if test_mode:
            return renderer.render(
                self.project,
                self.directory,
                start=start,
                preview=False,
                duration=30.0,
                use_final_audio=True,
                **job,
            )
        else:
            return renderer.render(
                self.project,
                self.directory,
                start=0.0,
                preview=False,
                duration=None,
                use_final_audio=True,
                **job,
            )

    def edit_utterance(self, sid, subtitle, dubbing, mode, target):
        from cartoon_sub.translation.modes import TranslationMode
        TranslationMode(mode)
        s=next(s for s in self.project.segments if s.id==sid)
        previous_dubbing = s.vi_dubbing
        s.vi_subtitle,s.vi_dubbing=subtitle,dubbing
        s.translation_mode=mode
        s.target_override=target or None
        s.dubbing_optimized=dubbing!=subtitle
        s.dubbing_status="manual"
        s.dubbing_fingerprint=""
        if previous_dubbing != dubbing and s.tts_generation_status in {"generated", "cached"}:
            s.tts_generation_status = "stale"
            s.tts_error = ""
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

