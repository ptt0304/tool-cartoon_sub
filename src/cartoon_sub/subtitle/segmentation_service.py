"""Project-level segmentation workflows; no UI, transcription, or translation calls."""
from dataclasses import asdict
import math
import re

from cartoon_sub.project.cache import content_hash
from cartoon_sub.ai.gemini_client import GeminiError
from cartoon_sub.subtitle.models import DisplaySegment, Utterance
from cartoon_sub.subtitle.segmentation import (LocalSegmentationEngine, SegmentationPlan, SegmentationProfile,
    SegmentationSettings, settings_for)
from cartoon_sub.subtitle.segmentation_qc import apply_display_qc, review_display_segment
from cartoon_sub.subtitle.segmentation_timing import allocate_display_segments
from cartoon_sub.syllable.vietnamese import count_syllables


SEGMENTATION_VERSION = "local-segmentation-v2"
SYLLABLE_COUNTER_VERSION = "vietnamese-local-v1"


def project_settings(project):
    profile = SegmentationProfile(project.segmentation_profile)
    custom = SegmentationSettings(**project.segmentation_settings) if profile is SegmentationProfile.CUSTOM else None
    return profile, settings_for(profile, custom)


def segmentation_fingerprint(utterance, profile, settings, semantic_identity=None):
    return content_hash({"version": SEGMENTATION_VERSION, "syllable_counter": SYLLABLE_COUNTER_VERSION,
        "utterance": {"id": utterance.id, "start": utterance.start, "end": utterance.end,
                      "vi_subtitle": utterance.vi_subtitle}, "profile": str(profile), "settings": asdict(settings),
        "semantic_fallback": semantic_identity})


def presentation_segments(utterance):
    if utterance.display_segments:
        return list(utterance.display_segments)
    if not utterance.vi_subtitle.strip():
        return []
    fallback = DisplaySegment(f"{utterance.id}.1", utterance.id, utterance.start, utterance.end,
        utterance.vi_subtitle, segmentation_reason="utterance_source")
    fallback.inherit_speaker(utterance.speaker_id)
    return [fallback]


class SubtitleSegmentationService:
    def __init__(self, semantic_service=None):
        self.semantic_service = semantic_service

    def settings_for(self, project):
        return project_settings(project)

    def update_settings(self, project, profile, settings=None):
        profile = SegmentationProfile(profile)
        if profile is SegmentationProfile.CUSTOM:
            settings = (settings or SegmentationSettings()).validate()
            serialized = asdict(settings)
        else:
            settings = settings_for(profile)
            serialized = {}
        changed = (project.segmentation_profile != profile.value or project.segmentation_settings != serialized)
        project.segmentation_profile, project.segmentation_settings = profile.value, serialized
        if changed:
            project.segmentation_cache = {}

    def invalidate(self, project, utterance_ids=None):
        if utterance_ids is None:
            project.segmentation_cache = {}
            return
        for utterance_id in utterance_ids:
            project.segmentation_cache.pop(str(utterance_id), None)

    def auto_segment(self, project, utterance_ids=None, force=False, *, cancel=None, progress=None):
        profile, settings = self.settings_for(project)
        chosen = set(utterance_ids or [item.id for item in project.utterances])
        if not chosen.issubset({item.id for item in project.utterances}):
            raise ValueError("Utterance cần segment không tồn tại")
        changed, skipped = [], []
        semantic_identity = self.semantic_service.cache_identity() if self.semantic_service is not None else None
        for utterance in project.utterances:
            if utterance.id not in chosen or not utterance.vi_subtitle.strip():
                continue
            cache_key = segmentation_fingerprint(utterance, profile, settings, semantic_identity)
            state = project.segmentation_cache.get(str(utterance.id), {})
            if state.get("manual") and not force:
                skipped.append(utterance.id)
                continue
            if state.get("fingerprint") == cache_key and utterance.display_segments and not force:
                continue
            plan = LocalSegmentationEngine(profile, settings if profile is SegmentationProfile.CUSTOM else None).segment(utterance)
            if "MANUAL_REVIEW" in plan.qc_flags and self.semantic_service is not None:
                try:
                    parts = self.semantic_service.split(utterance.vi_subtitle, "vi", settings.preferred_syllables_max,
                        settings.max_syllables, self._max_segments(utterance, settings), cancel=cancel)
                    plan = SegmentationPlan(utterance.id, utterance.speaker_id, utterance.vi_subtitle, parts,
                        ("gemini_semantic",) * (len(parts) - 1), (), False, True)
                except GeminiError:
                    self._mark_manual_review(utterance, settings, plan)
                    if progress:
                        progress(f"Utterance {utterance.id}: Gemini không tạo được điểm tách hợp lệ; cần duyệt tay")
                    changed.append(utterance.id)
                    continue
            allocated = allocate_display_segments(utterance, plan, settings)
            utterance.set_display_segments(list(allocated.segments))
            for segment in utterance.display_segments:
                apply_display_qc(segment, settings, utterance.vi_subtitle)
            project.segmentation_cache[str(utterance.id)] = {"fingerprint": cache_key, "manual": False,
                "timing_source": allocated.timing_source}
            changed.append(utterance.id)
        return changed, skipped

    @staticmethod
    def _max_segments(utterance, settings):
        by_syllables = math.ceil(max(1, count_syllables(utterance.vi_subtitle)) / settings.max_syllables)
        by_duration = math.ceil(utterance.duration / settings.max_duration)
        return min(8, max(2, by_syllables, by_duration))

    @staticmethod
    def _mark_manual_review(utterance, settings, plan):
        if utterance.display_segments:
            for segment in utterance.display_segments:
                if "MANUAL_REVIEW" not in segment.qc_flags:
                    segment.qc_flags.append("MANUAL_REVIEW")
            return
        allocated = allocate_display_segments(utterance, plan, settings)
        utterance.set_display_segments(list(allocated.segments))
        for segment in utterance.display_segments:
            apply_display_qc(segment, settings, utterance.vi_subtitle)

    def reset(self, project, utterance_ids):
        chosen = set(utterance_ids)
        if not chosen:
            raise ValueError("Chọn ít nhất một utterance để reset")
        for utterance in project.utterances:
            if utterance.id in chosen:
                utterance.set_display_segments([])
                project.segmentation_cache.pop(str(utterance.id), None)

    def split_manual(self, project, utterance_id, display_id, word_index):
        utterance = self._utterance(project, utterance_id)
        _, settings = self.settings_for(project)
        rows = presentation_segments(utterance)
        index = next((i for i, item in enumerate(rows) if item.id == display_id), None)
        if index is None:
            raise ValueError("DisplaySegment không tồn tại")
        target = rows[index]
        words = list(re.finditer(r"[^\W\d_]+", target.vi_text, re.UNICODE))
        if type(word_index) is not int or not 1 <= word_index < len(words):
            raise ValueError("Vị trí tách phải nằm giữa các từ của DisplaySegment")
        position = words[word_index - 1].end()
        parts = (target.vi_text[:position], target.vi_text[position:])
        temp = Utterance(utterance.id, target.start, target.end, utterance.zh, vi_subtitle=target.vi_text,
            speaker_id=utterance.speaker_id, speaker_name=utterance.speaker_name)
        plan = SegmentationPlan(utterance.id, utterance.speaker_id, target.vi_text, parts,
            ("manual",), (), False, True)
        allocated = allocate_display_segments(temp, plan, settings)
        replacement = list(allocated.segments)
        for item in replacement:
            item.segmentation_reason, item.manual = "manual", True
            apply_display_qc(item, settings, utterance.vi_subtitle)
        utterance.set_display_segments(self._reindex(utterance, rows[:index] + replacement + rows[index + 1:]))
        project.segmentation_cache[str(utterance.id)] = {"fingerprint": segmentation_fingerprint(utterance, *self.settings_for(project)),
            "manual": True, "timing_source": "manual"}

    def merge_manual(self, project, utterance_id, display_ids):
        utterance = self._utterance(project, utterance_id)
        _, settings = self.settings_for(project)
        rows = presentation_segments(utterance)
        selected = [item for item in rows if item.id in set(display_ids)]
        if len(selected) < 2:
            raise ValueError("Chọn ít nhất hai DisplaySegment cùng utterance để gộp")
        positions = [rows.index(item) for item in selected]
        if positions != list(range(min(positions), max(positions) + 1)):
            raise ValueError("Chỉ gộp các DisplaySegment liền nhau")
        first, last = selected[0], selected[-1]
        merged = DisplaySegment("manual", utterance.id, first.start, last.end, "".join(item.vi_text for item in selected),
            segmentation_reason="manual", manual=True)
        apply_display_qc(merged, settings, utterance.vi_subtitle)
        utterance.set_display_segments(self._reindex(utterance, rows[:positions[0]] + [merged] + rows[positions[-1] + 1:]))
        project.segmentation_cache[str(utterance.id)] = {"fingerprint": segmentation_fingerprint(utterance, *self.settings_for(project)),
            "manual": True, "timing_source": "manual"}

    def rows(self, project, warning_filter=None):
        _, settings = self.settings_for(project)
        result = []
        for utterance in project.utterances:
            if not utterance.vi_subtitle.strip():
                result.append((utterance, []))
                continue
            children = []
            for segment in presentation_segments(utterance):
                flags = review_display_segment(segment, settings, utterance.vi_subtitle)
                if warning_filter == "WARNINGS" and flags == ["OK"]:
                    continue
                if warning_filter and warning_filter not in ("ALL", "WARNINGS") and warning_filter not in flags:
                    continue
                children.append((segment, flags))
            if children:
                result.append((utterance, children))
        return result

    @staticmethod
    def _utterance(project, utterance_id):
        return next((item for item in project.utterances if item.id == utterance_id), None) or (_ for _ in ()).throw(ValueError("Utterance không tồn tại"))

    @staticmethod
    def _reindex(utterance, rows):
        for index, item in enumerate(rows, 1):
            item.id = f"{utterance.id}.{index}"
            item.inherit_speaker(utterance.speaker_id)
        return rows
