"""Project-level segmentation workflows; no UI, transcription, or translation calls."""
from dataclasses import asdict
from copy import copy
import logging
import re

from cartoon_sub.project.cache import content_hash
from cartoon_sub.subtitle.models import DisplaySegment, Utterance
from cartoon_sub.subtitle.segmentation import (LocalSegmentationEngine, SegmentationPlan, SegmentationProfile,
    SegmentationSettings, normalize_text, part_is_hard_valid, settings_for)
from cartoon_sub.subtitle.segmentation_qc import (apply_display_group_qc, apply_display_qc,
    review_display_segments)
from cartoon_sub.subtitle.segmentation_timing import allocate_display_segments
from cartoon_sub.syllable.vietnamese import count_syllables


SEGMENTATION_VERSION = "local-segmentation-v5-hard-settings"
SYLLABLE_COUNTER_VERSION = "vietnamese-local-v1"
SUBTITLE_TEXT_SOURCES = ("vi_subtitle", "vi_dubbing")
log = logging.getLogger(__name__)


def subtitle_source_text(project, utterance):
    source = getattr(project, "subtitle_text_source", "vi_subtitle")
    if source not in SUBTITLE_TEXT_SOURCES:
        raise ValueError("Nguồn nội dung phụ đề không hợp lệ")
    text = getattr(utterance, source)
    if source == "vi_dubbing" and not text.strip():
        log.warning("[SUBTITLE SOURCE] ID %s chưa có VI Dubbing; tạm dùng VI Subtitle.", utterance.id)
        return utterance.vi_subtitle
    return text


def subtitle_source_warning(project, utterance):
    if (getattr(project, "subtitle_text_source", "vi_subtitle") == "vi_dubbing"
            and not utterance.vi_dubbing.strip() and utterance.vi_subtitle.strip()):
        return f"ID {utterance.id} chưa có VI Dubbing; tạm dùng VI Subtitle."
    return ""


def subtitle_source_utterance(project, utterance):
    view = copy(utterance)
    view.vi_subtitle = subtitle_source_text(project, utterance)
    return view


def project_settings(project):
    profile = SegmentationProfile(project.segmentation_profile)
    custom = SegmentationSettings(**project.segmentation_settings) if profile is SegmentationProfile.CUSTOM else None
    return profile, settings_for(profile, custom)


def segmentation_fingerprint(utterance, profile, settings, semantic_identity=None,
                             source_type="vi_subtitle", source_text=None):
    text = utterance.vi_subtitle if source_text is None else source_text
    return content_hash({"version": SEGMENTATION_VERSION, "syllable_counter": SYLLABLE_COUNTER_VERSION,
        "utterance": {"id": utterance.id, "start": utterance.start, "end": utterance.end,
                      "source_type": source_type, "source_text": text}, "profile": str(profile), "settings": asdict(settings),
        "semantic_fallback": semantic_identity})


def presentation_segments(utterance, source_text=None):
    if utterance.display_segments:
        return list(utterance.display_segments)
    text = utterance.vi_subtitle if source_text is None else source_text
    if not text.strip():
        return []
    fallback = DisplaySegment(f"{utterance.id}.1", utterance.id, utterance.start, utterance.end,
        text, segmentation_reason="utterance_source")
    fallback.inherit_speaker(utterance.speaker_id)
    return [fallback]


def reflow_text_to_segments(text: str, segments: list[DisplaySegment]) -> list[str]:
    N = len(segments)
    if N <= 1:
        return [text] if N == 1 else []

    words = text.split()
    M = len(words)
    if M < N:
        parts = []
        rem = text
        for i in range(N - 1):
            cut = max(1, len(rem) - (N - 1 - i))
            parts.append(rem[:cut].strip() or rem[:cut])
            rem = rem[cut:]
        parts.append(rem.strip() or rem)
        return parts

    durations = [max(0.01, seg.duration) for seg in segments]
    total_dur = sum(durations)
    total_syl = max(1, count_syllables(text))

    def cost(j, k, slot_idx):
        slice_text = " ".join(words[j:k])
        s = max(1, count_syllables(slice_text))
        target = max(1.0, total_syl * (durations[slot_idx] / total_dur))
        err = ((s - target) ** 2) / target
        if slot_idx < N - 1:
            last = words[k - 1]
            if last[-1] in ".?!…" or last.endswith("..."):
                bonus = 12.0
            elif last[-1] in ",;:—":
                bonus = 6.0
            else:
                bonus = 0.0
            return err + (12.0 - bonus)
        return err

    dp = {}
    parent = {}
    for k in range(1, M - (N - 1) + 1):
        dp[(1, k)] = cost(0, k, 0)
        parent[(1, k)] = 0

    for i in range(2, N + 1):
        for k in range(i, M - (N - i) + 1):
            best = float("inf")
            best_j = -1
            for j in range(i - 1, k):
                val = dp[(i - 1, j)] + cost(j, k, i - 1)
                if val < best:
                    best = val
                    best_j = j
            dp[(i, k)] = best
            parent[(i, k)] = best_j

    bounds = [M]
    curr = M
    for i in range(N, 1, -1):
        curr = parent[(i, curr)]
        bounds.append(curr)
    bounds.append(0)
    bounds.reverse()

    return [" ".join(words[bounds[i]:bounds[i + 1]]) for i in range(N)]


class SubtitleSegmentationService:
    def settings_for(self, project):
        return project_settings(project)

    @staticmethod
    def source_text(project, utterance):
        return subtitle_source_text(project, utterance)

    @staticmethod
    def source_utterance(project, utterance):
        return subtitle_source_utterance(project, utterance)

    @staticmethod
    def _fingerprint(project, utterance, profile, settings, semantic_identity=None):
        return segmentation_fingerprint(
            utterance, profile, settings, semantic_identity,
            getattr(project, "subtitle_text_source", "vi_subtitle"),
            subtitle_source_text(project, utterance),
        )

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
        for utterance in project.utterances:
            source_text = self.source_text(project, utterance)
            apply_display_group_qc(utterance.display_segments, settings, source_text, utterance.duration)

    def invalidate(self, project, utterance_ids=None):
        if utterance_ids is None:
            project.segmentation_cache = {}
            return
        for utterance_id in utterance_ids:
            project.segmentation_cache.pop(str(utterance_id), None)

    def auto_segment(self, project, utterance_ids=None, force=False, *, cancel=None, progress=None):
        profile, settings = self.settings_for(project)
        log.info("[SUBTITLE SEGMENT] preferred_syllables=%s max_syllables=%s max_lines=%s "
                 "preferred_chars_per_line=%s hard_chars_per_line=%s preferred_duration=%.1f max_duration=%.1f",
                 settings.preferred_syllables_max, settings.max_syllables, settings.max_lines,
                 settings.preferred_chars_per_line, settings.hard_max_chars_per_line,
                 (settings.preferred_duration_min + settings.preferred_duration_max) / 2, settings.max_duration)
        chosen = set(utterance_ids or [item.id for item in project.utterances])
        if not chosen.issubset({item.id for item in project.utterances}):
            raise ValueError("Utterance cần segment không tồn tại")
        changed, skipped = [], []
        for utterance in project.utterances:
            source_text = self.source_text(project, utterance)
            if utterance.id not in chosen or not source_text.strip():
                continue
            source_utterance = self.source_utterance(project, utterance)
            cache_key = self._fingerprint(project, utterance, profile, settings)
            state = project.segmentation_cache.get(str(utterance.id), {})
            log.info("[AUTOSEG] ID=%s source=%s before_children=%s effective_max_syllables=%s "
                     "effective_max_lines=%s effective_hard_chars=%s source_hash=%s preview=%r",
                     utterance.id, getattr(project, "subtitle_text_source", "vi_subtitle"),
                     len(utterance.display_segments), settings.max_syllables, settings.max_lines,
                     settings.hard_max_chars_per_line, content_hash(source_text)[:12], source_text[:80])
            if state.get("manual") and not force:
                skipped.append(utterance.id)
                continue
            hard_failures = [segment for segment in utterance.display_segments
                             if self._requires_hard_split(segment, settings)]
            if (state.get("fingerprint") == cache_key and utterance.display_segments and not force
                    and (not hard_failures or state.get("hard_split_exhausted") is True)):
                continue
            plan = LocalSegmentationEngine(profile, settings if profile is SegmentationProfile.CUSTOM else None).segment(source_utterance)
            allocated = allocate_display_segments(source_utterance, plan, settings)
            utterance.set_display_segments(list(allocated.segments))
            apply_display_group_qc(utterance.display_segments, settings, source_text, utterance.duration)
            for segment in utterance.display_segments:
                log.info("[SUBTITLE SEGMENT] parent=%s child=%s text=%r syllable_count=%s max=%s "
                         "duration=%.3f QC=%s", utterance.id, segment.id, segment.vi_text,
                         count_syllables(segment.vi_text), settings.max_syllables, segment.duration,
                         "|".join(segment.qc_flags))
            remaining_hard = [segment for segment in utterance.display_segments
                              if self._requires_hard_split(segment, settings)]
            project.segmentation_cache[str(utterance.id)] = {"fingerprint": cache_key, "manual": False,
                "timing_source": allocated.timing_source,
                "source_type": getattr(project, "subtitle_text_source", "vi_subtitle"),
                "hard_split_exhausted": bool(remaining_hard and "MANUAL_REVIEW" in plan.qc_flags)}
            log.info("[AUTOSEG RESULT] ID=%s children=%s max_syllables=%s max_chars=%s remaining_hard=%s",
                     utterance.id, len(utterance.display_segments),
                     max(count_syllables(item.vi_text) for item in utterance.display_segments),
                     max(max((len(normalize_text(line)) for line in item.vi_text.splitlines()), default=0)
                         for item in utterance.display_segments),
                     [item.id for item in remaining_hard])
            changed.append(utterance.id)
        return changed, skipped

    @staticmethod
    def _requires_hard_split(segment, settings):
        return not part_is_hard_valid(segment.vi_text, segment.duration, settings)

    def sync_utterance(self, project, utterance):
        profile, settings = self.settings_for(project)
        source_text = self.source_text(project, utterance)
        source_utterance = self.source_utterance(project, utterance)
        if not source_text.strip():
            utterance.set_display_segments([])
            project.segmentation_cache.pop(str(utterance.id), None)
            return

        existing = list(utterance.display_segments)
        cache_entry = project.segmentation_cache.get(str(utterance.id), {})
        is_manual = (
            cache_entry.get("manual") is True
            or any(seg.manual or seg.segmentation_reason == "manual" for seg in existing)
        )

        if (not is_manual and (not existing or len(existing) == 1)
                and not part_is_hard_valid(source_text, utterance.duration, settings)):
            plan = LocalSegmentationEngine(profile, settings if profile is SegmentationProfile.CUSTOM else None).segment(source_utterance)
            allocated = allocate_display_segments(source_utterance, plan, settings)
            utterance.set_display_segments(list(allocated.segments))
            apply_display_group_qc(utterance.display_segments, settings, source_text, utterance.duration)
            cache_key = self._fingerprint(project, utterance, profile, settings)
            project.segmentation_cache[str(utterance.id)] = {
                "fingerprint": cache_key, "manual": False,
                "timing_source": allocated.timing_source,
                "source_type": getattr(project, "subtitle_text_source", "vi_subtitle"),
            }
        elif not existing or len(existing) == 1:
            if len(existing) == 1:
                seg = existing[0]
                seg.vi_text = source_text
                seg.start = utterance.start
                seg.end = utterance.end
                seg.inherit_speaker(utterance.speaker_id)
                seg.recalculate()
                apply_display_qc(seg, settings, source_text)
                utterance.set_display_segments([seg])
            else:
                fallback = DisplaySegment(
                    f"{utterance.id}.1", utterance.id, utterance.start, utterance.end,
                    source_text, segmentation_reason="utterance_source"
                )
                fallback.inherit_speaker(utterance.speaker_id)
                apply_display_qc(fallback, settings, source_text)
                utterance.set_display_segments([fallback])
            cache_key = self._fingerprint(project, utterance, profile, settings)
            project.segmentation_cache[str(utterance.id)] = {
                "fingerprint": cache_key,
                "manual": is_manual,
                "timing_source": "manual" if is_manual else "utterance",
                "source_type": getattr(project, "subtitle_text_source", "vi_subtitle"),
            }
        elif not is_manual:
            plan = LocalSegmentationEngine(profile, settings if profile is SegmentationProfile.CUSTOM else None).segment(source_utterance)
            allocated = allocate_display_segments(source_utterance, plan, settings)
            utterance.set_display_segments(list(allocated.segments))
            apply_display_group_qc(utterance.display_segments, settings, source_text, utterance.duration)
            cache_key = self._fingerprint(project, utterance, profile, settings)
            project.segmentation_cache[str(utterance.id)] = {
                "fingerprint": cache_key,
                "manual": False,
                "timing_source": allocated.timing_source,
                "source_type": getattr(project, "subtitle_text_source", "vi_subtitle"),
            }
        else:
            parts = reflow_text_to_segments(source_text, existing)
            for seg, part in zip(existing, parts):
                seg.vi_text = part
                seg.inherit_speaker(utterance.speaker_id)
                seg.recalculate()
                apply_display_qc(seg, settings, source_text)
            utterance.set_display_segments(existing)
            cache_key = self._fingerprint(project, utterance, profile, settings)
            project.segmentation_cache[str(utterance.id)] = {
                "fingerprint": cache_key,
                "manual": True,
                "timing_source": cache_entry.get("timing_source", "manual"),
                "source_type": getattr(project, "subtitle_text_source", "vi_subtitle"),
            }

    def display_segments_are_stale(self, project, utterance):
        """Return whether persisted presentation text no longer represents its master source."""
        if not utterance.display_segments:
            return False
        source_text = self.source_text(project, utterance)
        if not source_text.strip():
            return True
        state = project.segmentation_cache.get(str(utterance.id), {})
        return (normalize_text(" ".join(segment.vi_text for segment in utterance.display_segments)) != normalize_text(source_text)
                or (state.get("source_type") is not None
                    and state.get("source_type") != getattr(project, "subtitle_text_source", "vi_subtitle")))

    def sync_stale(self, project):
        """Synchronize only presentation groups whose text diverged from Project.utterances."""
        changed = []
        for utterance in project.utterances:
            if self.display_segments_are_stale(project, utterance):
                self.sync_utterance(project, utterance)
                changed.append(utterance.id)
        return changed

    def reset(self, project, utterance_ids):
        chosen = set(utterance_ids)
        if not chosen:
            raise ValueError("Chọn ít nhất một utterance để reset")
        for utterance in project.utterances:
            if utterance.id in chosen:
                utterance.set_display_segments([])
                project.segmentation_cache.pop(str(utterance.id), None)
        # Reset rebuilds through the same settings-first deterministic policy.
        self.auto_segment(project, chosen, force=True)

    def split_manual(self, project, utterance_id, display_id, word_index):
        utterance = self._utterance(project, utterance_id)
        _, settings = self.settings_for(project)
        source_text = self.source_text(project, utterance)
        rows = presentation_segments(utterance, source_text)
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
            apply_display_qc(item, settings, source_text)
        utterance.set_display_segments(self._reindex(utterance, rows[:index] + replacement + rows[index + 1:]))
        project.segmentation_cache[str(utterance.id)] = {"fingerprint": self._fingerprint(project, utterance, *self.settings_for(project)),
            "manual": True, "timing_source": "manual",
            "source_type": getattr(project, "subtitle_text_source", "vi_subtitle")}

    def merge_manual(self, project, utterance_id, display_ids):
        utterance = self._utterance(project, utterance_id)
        _, settings = self.settings_for(project)
        source_text = self.source_text(project, utterance)
        rows = presentation_segments(utterance, source_text)
        selected = [item for item in rows if item.id in set(display_ids)]
        if len(selected) < 2:
            raise ValueError("Chọn ít nhất hai DisplaySegment cùng utterance để gộp")
        positions = [rows.index(item) for item in selected]
        if positions != list(range(min(positions), max(positions) + 1)):
            raise ValueError("Chỉ gộp các DisplaySegment liền nhau")
        first, last = selected[0], selected[-1]
        merged = DisplaySegment("manual", utterance.id, first.start, last.end, "".join(item.vi_text for item in selected),
            segmentation_reason="manual", manual=True)
        apply_display_qc(merged, settings, source_text)
        utterance.set_display_segments(self._reindex(utterance, rows[:positions[0]] + [merged] + rows[positions[-1] + 1:]))
        project.segmentation_cache[str(utterance.id)] = {"fingerprint": self._fingerprint(project, utterance, *self.settings_for(project)),
            "manual": True, "timing_source": "manual",
            "source_type": getattr(project, "subtitle_text_source", "vi_subtitle")}

    def rows(self, project, warning_filter=None):
        self.sync_stale(project)
        _, settings = self.settings_for(project)
        result = []
        for utterance in project.utterances:
            source_text = self.source_text(project, utterance)
            if not source_text.strip():
                result.append((utterance, []))
                continue
            children = []
            segments = presentation_segments(utterance, source_text)
            reviewed = review_display_segments(segments, settings, source_text, utterance.duration)
            for segment, flags in zip(segments, reviewed):
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
