import re
import unicodedata
from cartoon_sub.syllable.target import DubbingSettings, allowed_delta


def review_translation(project):
    warnings = {}
    terms = dict(project.glossary)
    for row in project.story_context.get("characters", []) + project.story_context.get("terms", []):
        if row["source"] and row["target"]:
            terms.setdefault(row["source"], row["target"])
    for segment in project.segments:
        notes = []
        if not segment.vi.strip():
            notes.append("Chưa có bản dịch")
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", segment.vi):
            notes.append("Còn chữ Trung")
        if len(segment.vi) > 100:
            notes.append("Dòng dài >100 ký tự")
        if segment.duration > 0 and len(segment.vi) / segment.duration > 22:
            notes.append("Tốc độ đọc >22 ký tự/giây")
        vi = unicodedata.normalize("NFC", segment.vi).casefold()
        for source, target in terms.items():
            if source in segment.zh and unicodedata.normalize("NFC", target).casefold() not in vi:
                notes.append(f"Kiểm tra thuật ngữ: {source} → {target}")
        model_note = project.translation_notes.get(str(segment.id), "")
        if model_note:
            notes.append("Biên tập: " + model_note)
        notes.extend(utterance_warnings(segment, project))
        warnings[str(segment.id)] = notes
    return warnings


def utterance_warnings(segment, project):
    notes=[]
    config=DubbingSettings(**project.dubbing_settings)
    if segment.speaker_id=="SPK_UNKNOWN": notes.append("Chưa gán speaker")
    if segment.speaker_confidence is not None and segment.speaker_confidence<0.6: notes.append("Speaker cần nghe lại (ước lượng AI thấp)")
    if segment.duration<0.25: notes.append("Utterance rất ngắn")
    if segment.overlap: notes.append("Overlap hợp lệ: "+str(segment.overlap_group))
    peers=[s for s in project.segments if s.id!=segment.id and s.start<segment.end and s.end>segment.start]
    if any(s.speaker_id==segment.speaker_id for s in peers): notes.append("Kiểm tra overlap cùng speaker")
    ordered=sorted(project.segments,key=lambda s:(s.start,s.id))
    index=next((i for i,s in enumerate(ordered) if s.id==segment.id),-1)
    if 0<index<len(ordered)-1 and segment.duration<0.5:
        a,b=ordered[index-1],ordered[index+1]
        if a.speaker_id==b.speaker_id!=segment.speaker_id and b.start-a.end<1:
            notes.append("Đổi speaker A–B–A rất nhanh; cần nghe lại")
    if segment.semantic_compression: notes.append("Meaning may be compressed — bản dubbing đã nén")
    if segment.translation_mode in ("syllable_match","strict_iso_syllabic","short_dub"):
        notes.append("Mode cho phép nén nghĩa/sắc thái")
    if segment.translation_mode=="strict_iso_syllabic" and segment.syllable_delta:
        notes.append(f"QC FAILED: strict cần {segment.target_syllables}, hiện {segment.vi_syllables}")
    elif segment.translation_mode not in ("faithful","subtitle_natural") and abs(segment.syllable_delta)>allowed_delta(segment.target_syllables,segment.translation_mode,config):
        notes.append(f"Lệch budget dubbing {segment.syllable_delta:+d} âm tiết")
    if segment.vi_syllables>segment.target_syllables: notes.append("Dubbing vượt target")
    if segment.vi_syllables/segment.duration>config.speech_rate*(1+config.tolerance_percent/100):
        notes.append("Ước lượng tốc độ dubbing quá nhanh")
    if segment.tts_duration is not None and segment.tts_duration>segment.duration: notes.append("Audio TTS vượt slot")
    if segment.dubbing_status=="stale": notes.append("Bản dubbing cần tối ưu lại")
    notes.extend(segment.syllable_warnings)
    note=project.translation_notes.get(f"dub:{segment.id}")
    if note: notes.append("Biên tập dubbing: "+note)
    return notes
