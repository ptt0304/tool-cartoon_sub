from dataclasses import dataclass, field, asdict
from uuid import uuid4
from cartoon_sub.translation.context_models import StoryContext


TTS_GENERATION_STATUSES = frozenset({
    "not_generated", "stale", "generating", "generated", "cached", "failed",
})


@dataclass(init=False)
class DisplaySegment:
    """Derived presentation data for a single Utterance; never a speech source."""
    id: str
    utterance_id: int
    start: float
    end: float
    vi_text: str
    zh_text: str | None = None
    vi_syllables: int = 0
    line_count: int = 1
    segmentation_reason: str = "manual"
    qc_flags: list[str] = field(default_factory=list)
    manual: bool = False
    speaker_id: str = field(default="SPK_UNKNOWN", init=False, repr=False, compare=False)

    def __init__(self, id, utterance_id, start, end, vi_text, zh_text=None,
                 vi_syllables=0, line_count=1, segmentation_reason="manual",
                 qc_flags=None, manual=False, **kwargs):
        if kwargs:
            raise TypeError(f"Unknown display segment fields: {', '.join(kwargs)}")
        self.id = str(id)
        self.utterance_id, self.start, self.end = utterance_id, start, end
        self.vi_text, self.zh_text = vi_text, zh_text
        self.segmentation_reason, self.qc_flags, self.manual = segmentation_reason, list(qc_flags or []), manual
        self.speaker_id = "SPK_UNKNOWN"
        self.validate()
        self.recalculate()

    def validate(self):
        import math
        if not self.id:
            raise ValueError("Display segment ID must not be empty")
        if type(self.utterance_id) is not int or self.utterance_id < 1:
            raise ValueError("Display segment utterance ID must be a positive integer")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (self.start, self.end)):
            raise ValueError("Invalid display segment timestamp")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Display segment requires 0 <= start < end")
        if not isinstance(self.vi_text, str) or not self.vi_text.strip():
            raise ValueError("Display segment Vietnamese text must not be empty")
        if self.zh_text is not None and not isinstance(self.zh_text, str):
            raise ValueError("Display segment Chinese text must be a string or null")
        if not isinstance(self.segmentation_reason, str) or not self.segmentation_reason.strip():
            raise ValueError("Display segment reason must not be empty")
        if type(self.manual) is not bool or any(not isinstance(flag, str) for flag in self.qc_flags):
            raise ValueError("Invalid display segment QC/manual state")

    @property
    def duration(self):
        return self.end - self.start

    def recalculate(self):
        from cartoon_sub.syllable.vietnamese import count as vi_count
        self.vi_syllables = vi_count(self.vi_text).count
        self.line_count = max(1, len(self.vi_text.splitlines()))

    def inherit_speaker(self, speaker_id):
        from cartoon_sub.speaker.models import Speaker
        Speaker(speaker_id)
        self.speaker_id = speaker_id

    def to_dict(self):
        self.recalculate()
        data = asdict(self)
        data.pop("speaker_id", None)
        data["duration"] = self.duration
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data.pop("speaker_id", None)
        data.pop("duration", None)
        return cls(**data)


@dataclass(init=False)
class Utterance:
    id: int
    start: float
    end: float
    tts_cache_key: str = field(default_factory=lambda: uuid4().hex)
    zh: str = ""
    vi_subtitle: str = ""
    vi_dubbing: str = ""
    speaker_id: str = "SPK_UNKNOWN"
    speaker_name: str = "Unknown"
    speaker_confidence: float | None = None
    transcript_confidence: float | None = None
    overlap: bool = False
    overlap_group: str | None = None
    overlap_type: str = "NONE"
    overlap_diagnostics: list[str] = field(default_factory=list)
    translation_mode: str = "balanced_dubbing"
    target_override: int | None = None
    zh_syllables: int = 0
    vi_syllables: int = 0
    vi_subtitle_syllables: int = 0
    target_syllables: int = 0
    syllable_delta: int = 0
    semantic_compression: bool = False
    meaning_preservation: str = "unknown"
    translation_source: str = "ai"
    dubbing_optimized: bool = False
    dubbing_status: str = "not_started"
    dubbing_fingerprint: str = ""
    pre_optimization_vi_subtitle: str | None = None
    pre_optimization_vi_dubbing: str | None = None
    syllable_warnings: list[str] = field(default_factory=list)
    tts_audio_path: str | None = None
    tts_duration: float | None = None
    tts_speed_factor: float | None = None
    tts_alignment_status: str = "not_imported"
    tts_alignment_diagnostic: str = ""
    allowed_audio_start: float | None = None
    allowed_audio_end: float | None = None
    tts_fit_ratio: float | None = None
    dubbing_fit_status: str = "NOT_MEASURED"
    dubbing_rewrite_attempts: int = 0
    dubbing_voice_id: str | None = None
    dubbing_estimated_rate: float | None = None
    dubbing_budget_duration: float | None = None
    tts_segment_id: str | None = None
    tts_fingerprint: str = ""
    tts_generation_status: str = "not_generated"
    tts_error: str = ""
    display_segments: list[DisplaySegment] = field(default_factory=list)

    def __init__(self, id, start, end, zh="", vi=None, vi_subtitle=None, vi_dubbing=None, **kwargs):
        from dataclasses import fields, MISSING
        raw_display_segments = kwargs.pop("display_segments", [])
        self.id, self.start, self.end, self.zh = id, start, end, zh
        self.vi_subtitle = vi if vi is not None else (vi_subtitle if vi_subtitle is not None else "")
        self.vi_dubbing = self.vi_subtitle if vi_dubbing is None or (vi is not None and not kwargs.get("dubbing_optimized",False)) else vi_dubbing
        for f in fields(self):
            if f.name in ("id","start","end","zh","vi_subtitle","vi_dubbing","display_segments"): continue
            if f.name in kwargs: value=kwargs.pop(f.name)
            elif f.default_factory is not MISSING: value=f.default_factory()
            else: value=f.default
            setattr(self,f.name,value)
        if kwargs: raise TypeError(f"Unknown utterance fields: {', '.join(kwargs)}")
        self.display_segments = []
        self.validate()
        self.recalculate()
        self.set_display_segments(raw_display_segments)

    def validate(self):
        import math
        from cartoon_sub.speaker.models import Speaker
        from cartoon_sub.translation.modes import TranslationMode
        if type(self.id) is not int or self.id < 1: raise ValueError("ID must be a positive integer")
        if any(type(t) not in (float,int) or not math.isfinite(t) for t in (self.start,self.end)):
            raise ValueError("Invalid timestamp")
        if self.start < 0 or self.end <= self.start: raise ValueError("Requires 0 <= start < end")
        if any(not isinstance(t,str) for t in (self.zh,self.vi_subtitle,self.vi_dubbing)):
            raise ValueError("Text must be a string")
        Speaker(self.speaker_id,self.speaker_name)
        TranslationMode(self.translation_mode)
        for confidence in (self.speaker_confidence,self.transcript_confidence):
            if confidence is not None and (type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1):
                raise ValueError("Confidence must be null or in [0,1]")
        if self.target_override is not None and (type(self.target_override) is not int or self.target_override<1):
            raise ValueError("Target override must be a positive integer")
        if self.tts_duration is not None and (type(self.tts_duration) not in (int,float) or not math.isfinite(self.tts_duration) or self.tts_duration<=0):
            raise ValueError("TTS duration must be positive")
        if self.tts_audio_path is not None and not isinstance(self.tts_audio_path,str): raise ValueError("TTS path must be a string")
        for name in ("allowed_audio_start", "allowed_audio_end", "tts_fit_ratio", "dubbing_estimated_rate", "dubbing_budget_duration"):
            value = getattr(self, name)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid dubbing duration metadata: {name}")
        if self.allowed_audio_start is not None and self.allowed_audio_end is not None and self.allowed_audio_end <= self.allowed_audio_start:
            raise ValueError("Allowed audio window must have positive duration")
        if self.dubbing_voice_id is not None and (not isinstance(self.dubbing_voice_id, str) or not self.dubbing_voice_id.strip()):
            raise ValueError("Dubbing voice ID must be non-empty text or null")
        if type(self.dubbing_rewrite_attempts) is not int or not 0 <= self.dubbing_rewrite_attempts <= 2:
            raise ValueError("Dubbing rewrite attempts must be between 0 and 2")
        if self.dubbing_fit_status not in {
            "NOT_MEASURED", "FIT", "BORROWED", "LIGHT_FIT", "REWRITE_SHORTER",
            "STRONG_REWRITE", "REWRITTEN", "LONG_DENSE_CHAIN", "NEED_REVIEW",
        }:
            raise ValueError("Invalid dubbing fit status")
        if self.tts_segment_id is not None and (not isinstance(self.tts_segment_id, str) or not self.tts_segment_id.strip()):
            raise ValueError("TTS segment ID must be a non-empty string or null")
        if not isinstance(self.tts_cache_key, str) or not self.tts_cache_key.strip():
            raise ValueError("TTS cache key must be non-empty text")
        if not isinstance(self.tts_fingerprint, str) or not isinstance(self.tts_error, str) or not isinstance(self.tts_alignment_diagnostic, str):
            raise ValueError("TTS metadata must be text")
        if self.tts_generation_status not in TTS_GENERATION_STATUSES:
            raise ValueError("Invalid TTS generation status")
        if self.overlap_type not in {
            "NONE", "LEGITIMATE_OVERLAP", "SAME_SPEAKER_CONFLICT",
            "UNKNOWN_SPEAKER_REVIEW", "TIMING_REVIEW_REQUIRED",
        }:
            raise ValueError("Invalid overlap type")
        if not isinstance(self.overlap_diagnostics, list) or any(not isinstance(item, str) for item in self.overlap_diagnostics):
            raise ValueError("Overlap diagnostics must be text")

    @property
    def vi(self):
        return self.vi_subtitle

    @vi.setter
    def vi(self,value):
        if value != self.vi_subtitle:
            # Presentation children are derived from the source subtitle and
            # cannot safely survive a source translation change.
            self.display_segments=[]
        self.vi_subtitle=value
        if not self.dubbing_optimized: self.vi_dubbing=value
        else: self.dubbing_status="stale"
        self.recalculate()

    @property
    def duration(self): return self.end-self.start

    def recalculate(self, settings=None):
        from cartoon_sub.syllable.chinese import count as zh_count
        from cartoon_sub.syllable.vietnamese import count as vi_count
        from cartoon_sub.syllable.target import DubbingSettings, target_syllables
        config=settings or DubbingSettings()
        if self.translation_mode=="time_fit":
            from dataclasses import replace
            config=replace(config,target_strategy="time_based")
        zh,vi,sub=zh_count(self.zh),vi_count(self.vi_dubbing),vi_count(self.vi_subtitle)
        self.zh_syllables,self.vi_syllables,self.vi_subtitle_syllables=zh.count,vi.count,sub.count
        budget_duration = self.dubbing_budget_duration or self.duration
        if self.dubbing_estimated_rate is not None:
            from dataclasses import replace
            config = replace(config, speech_rate=self.dubbing_estimated_rate, target_strategy="time_based")
        self.target_syllables=self.target_override or target_syllables(zh.count,budget_duration,config)
        self.syllable_delta=vi.count-self.target_syllables
        self.syllable_warnings=list(dict.fromkeys(zh.warnings+vi.warnings+sub.warnings))
        if self.tts_duration is not None:
            self.tts_speed_factor=self.tts_duration/self.duration
            tolerance = max(0.10, self.duration * 0.03)
            if self.tts_duration <= self.duration + tolerance:
                self.tts_alignment_status = "fits"
                self.tts_alignment_diagnostic = "SYNC_OK"
            elif self.tts_speed_factor <= 1.20:
                self.tts_alignment_status = "warning"
                self.tts_alignment_diagnostic = "AUTO_FIT"
            else:
                self.tts_alignment_status = "warning"
                self.tts_alignment_diagnostic = "NEEDS_TIMING_REVIEW"
        else:
            self.tts_alignment_status = "not_imported"
            self.tts_alignment_diagnostic = ""
        for display_segment in self.display_segments:
            display_segment.inherit_speaker(self.speaker_id)

    def set_display_segments(self, display_segments):
        if not isinstance(display_segments, list):
            raise ValueError("Display segments must be a list")
        children = [item if isinstance(item, DisplaySegment) else DisplaySegment.from_dict(item) for item in display_segments]
        if len({item.id for item in children}) != len(children):
            raise ValueError("Duplicate display segment IDs within utterance")
        for item in children:
            if item.utterance_id != self.id:
                raise ValueError("Display segment must reference its parent utterance")
            if item.start < self.start or item.end > self.end:
                raise ValueError("Display segment must remain inside its utterance timing")
            item.inherit_speaker(self.speaker_id)
        self.display_segments = children

    def to_dict(self):
        return {**asdict(self), "display_segments":[item.to_dict() for item in self.display_segments], "duration":self.duration}

    @classmethod
    def from_dict(cls,data):
        data=dict(data)
        data.pop("duration",None)
        data.pop("slot_duration",None)
        return cls(**data)

# Existing pipelines use Segment/SubtitleSegment for the master timeline.  Keep
# both names while making Utterance the explicit semantic model for new code.
Segment = Utterance
SubtitleSegment = Utterance

@dataclass
class Mask:
    enabled: bool = False
    kind: str = "solid"
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    strength: int = 12
    mask_color: str = "#000000"

@dataclass
class SubtitleStyle:
    font: str = "Arial"
    font_size: int = 42
    bold: bool = False
    outline: float = 2
    shadow: float = 1
    alignment: int = 2
    margin_bottom: int = 30
    max_lines: int = 2
    center_in_mask: bool = False
    speaker_label_mode: str = "overlap_only"
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"

@dataclass
class LogoOverlay:
    id: str
    path: str
    x: int = 40
    y: int = 40
    width: int = 160
    height: int = 160
    rotation: float = 0.0
    transparency: int = 0
    base_width: int | None = None
    base_height: int | None = None
    scale: int = 0
    scale_percent: int | None = None

    def __post_init__(self):
        if self.scale_percent is None:
            self.scale_percent = 100 + int(self.scale)

@dataclass
class WatermarkStyle:
    text: str = ""
    font: str = "Arial"
    font_size: int = 32
    bold: bool = False
    outline: float = 2.0
    shadow: float = 1.0
    transparency: int = 0
    speed: int = 120

@dataclass
class AudioSettings:
    original_volume: int = 100
    dubbed_volume: int = 100
    additional_audio_path: str | None = None
    additional_audio_volume: int = 100
    additional_audio_start: float = 0.0

    def validate(self):
        import math
        for name, vol in (
            ("original_volume", self.original_volume),
            ("dubbed_volume", self.dubbed_volume),
            ("additional_audio_volume", self.additional_audio_volume),
        ):
            if type(vol) not in (int, float) or not math.isfinite(vol) or not (0 <= vol <= 100):
                raise ValueError(f"{name} must be between 0 and 100")
        self.original_volume = int(round(self.original_volume))
        self.dubbed_volume = int(round(self.dubbed_volume))
        self.additional_audio_volume = int(round(self.additional_audio_volume))
        if self.additional_audio_path is not None and not isinstance(self.additional_audio_path, str):
            raise ValueError("additional_audio_path must be a string or null")
        if self.additional_audio_path is not None and not self.additional_audio_path.strip():
            self.additional_audio_path = None
        if (
            type(self.additional_audio_start) not in (int, float)
            or not math.isfinite(self.additional_audio_start)
            or self.additional_audio_start < 0
        ):
            raise ValueError("additional_audio_start must be >= 0")
        self.additional_audio_start = float(self.additional_audio_start)
        return self

@dataclass
class Project:
    name: str
    source_video_path: str
    schema_version: int = 3
    metadata: dict = field(default_factory=dict)
    segments: list[Utterance] = field(default_factory=list)
    transcription_status: str = "not_started"
    translation_prompt: str = ""
    translation_preset: str = "Natural Vietnamese"
    glossary: dict[str, str] = field(default_factory=dict)
    mask: Mask = field(default_factory=Mask)
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    logos: list[LogoOverlay] = field(default_factory=list)
    watermark: WatermarkStyle = field(default_factory=WatermarkStyle)
    selected_models: dict = field(default_factory=lambda: {"transcription": "mock", "translation": "mock"})
    cache_hashes: dict = field(default_factory=dict)
    chunk_states: dict = field(default_factory=dict)
    translation_genres: list[str] = field(default_factory=list)
    proper_name_mode: str = "sino_vietnamese"
    story_context: dict = field(default_factory=lambda: StoryContext().to_dict())
    context_proposal: dict = field(default_factory=dict)
    context_source_hash: str = ""
    context_proposal_hash: str = ""
    context_approved_config_hash: str = ""
    context_proposal_config_hash: str = ""
    context_status: str = "not_started"
    translation_status: str = "not_started"
    translation_notes: dict = field(default_factory=dict)
    translation_qa: dict = field(default_factory=dict)

    speakers: dict = field(default_factory=dict)
    speaker_review_hash: str = ""
    speaker_review_initial_state: dict = field(default_factory=dict)
    dubbing_settings: dict = field(default_factory=dict)
    segmentation_profile: str = "BALANCED"
    segmentation_settings: dict = field(default_factory=dict)
    segmentation_cache: dict = field(default_factory=dict)
    audio_settings: AudioSettings = field(default_factory=AudioSettings)
    final_audio_status: str = "not_generated"
    final_audio_fingerprint: str = ""

    @property
    def utterances(self):
        """Semantic view of the existing master-timeline compatibility field."""
        return self.segments

    @utterances.setter
    def utterances(self, value):
        self.segments = value

    def to_dict(self):
        from cartoon_sub.speaker.service import refresh_timeline
        refresh_timeline(self)
        data=asdict(self)
        data["schema_version"]=3
        data["master_timeline"]=[s.to_dict() for s in self.segments]
        data.pop("segments",None)
        if isinstance(self.audio_settings, AudioSettings):
            data["audio_settings"] = asdict(self.audio_settings.validate())
        elif isinstance(self.audio_settings, dict):
            data["audio_settings"] = asdict(AudioSettings(**self.audio_settings).validate())
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if data.get("schema_version") not in (1,2,3):
            raise ValueError("Unsupported project schema")
        data["schema_version"]=3
        rows=data.pop("master_timeline", data.pop("segments",[]))
        data["segments"] = [Utterance.from_dict(s) for s in rows]
        ids = [s.id for s in data["segments"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate subtitle IDs")
        mask_data = dict(data.get("mask", {}))
        style_data = dict(data.get("subtitle_style", {}))
        # Migrate the removed white-background mode to explicit colors. Old
        # projects retain the same appearance without renderer special cases.
        if mask_data.get("kind") == "white_background":
            mask_data["kind"] = "solid"
            mask_data.setdefault("mask_color", "#FFFFFF")
            style_data.setdefault("text_color", "#000000")
        elif mask_data.get("kind") in ("blur", "pixelate", "frosted"):
            mask_data["kind"] = "gaussian"
        data["mask"] = Mask(**mask_data)
        data["subtitle_style"] = SubtitleStyle(**style_data)
        logo_rows = []
        for raw_logo in data.get("logos", []):
            row = dict(raw_logo)
            if row.get("scale_percent") is None:
                # Legacy scale was an additive percentage: 0=100%, 100=200%.
                row["scale_percent"] = 100 + int(row.get("scale", 0))
            logo_rows.append(LogoOverlay(**row))
        data["logos"] = logo_rows
        data["watermark"] = WatermarkStyle(**data.get("watermark", {}))
        data["story_context"] = StoryContext.from_dict(data.get("story_context", {})).to_dict()
        if data.get("context_proposal"):
            data["context_proposal"] = StoryContext.from_dict(data["context_proposal"]).to_dict()
        from cartoon_sub.translation.context_profiles import normalize_context_ids
        legacy_context = data.pop("translation_context", "")
        raw_contexts = data.get("translation_genres", [])
        data["translation_genres"] = normalize_context_ids(raw_contexts)[:3]
        if isinstance(raw_contexts, str) and raw_contexts.strip() and not data["translation_genres"]:
            legacy_context = "\n".join(filter(None, (legacy_context, raw_contexts.strip())))
        elif isinstance(raw_contexts, list):
            legacy_names = {"rebirth": "Trọng sinh", "face_slap": "Vả mặt"}
            unmapped = [legacy_names.get(key, str(key)) for key in raw_contexts
                        if key not in data["translation_genres"]
                        and key not in ("historical", "urban", "documentary")]
            if unmapped:
                legacy_context = "\n".join(filter(None, (
                    legacy_context, "Ngữ cảnh từ project cũ: " + ", ".join(unmapped))))
        if legacy_context:
            data["translation_prompt"] = "\n".join(filter(None, (data.get("translation_prompt", ""), legacy_context)))
        mode = data.get("proper_name_mode", "sino_vietnamese")
        if mode not in ("sino_vietnamese", "preserve_source", "user_mapping"):
            mode = "sino_vietnamese"
        data["proper_name_mode"] = mode
        if not isinstance(data.get("translation_qa", {}), dict):
            data["translation_qa"] = {}
        from cartoon_sub.subtitle.segmentation import SegmentationProfile, SegmentationSettings
        profile=SegmentationProfile(data.get("segmentation_profile", "BALANCED"))
        data["segmentation_profile"]=profile.value
        if not isinstance(data.get("segmentation_settings", {}), dict):
            raise ValueError("Cài đặt segmentation không hợp lệ")
        if profile is SegmentationProfile.CUSTOM:
            data["segmentation_settings"]=SegmentationSettings(**data["segmentation_settings"]).validate().__dict__.copy()
        else:
            data["segmentation_settings"]={}
        if not isinstance(data.get("segmentation_cache", {}), dict):
            raise ValueError("Cache segmentation không hợp lệ")
        from cartoon_sub.speaker.models import Speaker
        speakers = data.get("speakers", {})
        if not isinstance(speakers, dict):
            raise ValueError("Speaker registry must be an object")
        data["speakers"] = {
            key: asdict(Speaker(**value)) for key, value in speakers.items()
        }
        raw_audio = data.get("audio_settings", {})
        data["audio_settings"] = AudioSettings(**raw_audio).validate()
        data["final_audio_status"] = data.get("final_audio_status", "not_generated")
        data["final_audio_fingerprint"] = data.get("final_audio_fingerprint", "")
        project=cls(**data)
        from cartoon_sub.speaker.service import refresh_timeline
        refresh_timeline(project)
        # Project.utterances is canonical even for legacy project files that
        # persisted an older presentation-text copy.
        from cartoon_sub.subtitle.segmentation_service import SubtitleSegmentationService
        SubtitleSegmentationService().sync_stale(project)
        return project
