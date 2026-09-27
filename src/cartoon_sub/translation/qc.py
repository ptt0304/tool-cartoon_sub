import re
import unicodedata
from cartoon_sub.project.cache import content_hash
from cartoon_sub.syllable.target import DubbingSettings, allowed_delta


HAN_PATTERN = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\U00020000-\U0002FA1F]"
)
HAN_RUN_PATTERN = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\U00020000-\U0002FA1F]{2,}"
)
VIETNAMESE_LETTER = r"A-Za-zÀ-ỹ"
TRUNCATED_END = re.compile(r"\b(là|của|và|với|để|nhưng|hoặc|rằng)\s*[.!?…]*$", re.IGNORECASE)
PLACEHOLDER = re.compile(r"(?:\\u[0-9a-fA-F]{4}|\b(?:TODO|FIXME|undefined|null)\b|<[^>]{1,40}>)")


def _issue(issue_type, detail, severity="FAIL"):
    return {"type": issue_type, "detail": detail, "severity": severity}


def translation_qa_fingerprint(segment, project=None):
    value = {
        "source_hash": content_hash(segment.zh),
        "target_hash": content_hash(segment.vi_subtitle),
    }
    if project is not None:
        visual = next((row for row in project.story_context.get("visual_contexts", [])
                       if row.get("id") == segment.id), None)
        value["visual_context_hash"] = content_hash({
            "status": project.visual_context_status,
            "visual": visual,
        })
    return value


def qa_entry_is_current(segment, entry, project=None):
    fingerprint = translation_qa_fingerprint(segment, project)
    return (isinstance(entry, dict)
            and entry.get("source_hash") == fingerprint["source_hash"]
            and entry.get("target_hash") == fingerprint["target_hash"]
            and (project is None
                 or entry.get("visual_context_hash") == fingerprint["visual_context_hash"]))


def store_qa_result(project, segment, status, issues=None, attempts=0, last_failed_reason=""):
    entry = {"status": status, "issues": list(issues or []), "attempts": int(attempts),
             "last_failed_reason": str(last_failed_reason)}
    entry.update(translation_qa_fingerprint(segment, project))
    project.translation_qa[str(segment.id)] = entry
    return entry


def invalidate_qa(project, segment):
    return store_qa_result(project, segment, "UNKNOWN", [], 0, "Source Chinese đã thay đổi")


def _allowed_han_values(project):
    values = [value for value in project.glossary.values() if isinstance(value, str) and HAN_PATTERN.search(value)]
    context_rows = project.story_context.get("characters", []) + project.story_context.get("terms", [])
    values.extend(row.get("target", "") for row in context_rows
                  if isinstance(row, dict) and isinstance(row.get("target"), str)
                  and HAN_PATTERN.search(row["target"]))
    return sorted(set(filter(None, values)), key=len, reverse=True)


def _mask_allowed(text, allowed):
    for value in allowed:
        text = text.replace(value, "")
    return text


def local_translation_qa(project, segment):
    """Fast deterministic QA. It never mutates translation text or project state."""
    source = unicodedata.normalize("NFC", segment.zh or "").strip()
    target = unicodedata.normalize("NFC", segment.vi_subtitle or "").strip()
    issues = []
    if source and not target:
        return {"status": "FAIL", "issues": [
            _issue("EMPTY_TRANSLATION", "Source có nội dung nhưng bản dịch tiếng Việt đang rỗng")
        ]}
    if not source:
        return {"status": "PASS", "issues": []}

    if "\ufffd" in target or any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in target):
        issues.append(_issue("INVALID_UNICODE", "Bản dịch chứa ký tự Unicode/control bất thường"))
    if PLACEHOLDER.search(target):
        issues.append(_issue("MALFORMED_OUTPUT", "Bản dịch chứa escape, placeholder hoặc artifact không hợp lệ"))

    # Preserve-source is an explicit user decision. Otherwise only exact locked
    # mapping/context targets may retain Han characters.
    inspect_target = target
    if project.proper_name_mode == "preserve_source":
        inspect_target = ""
    else:
        inspect_target = _mask_allowed(inspect_target, _allowed_han_values(project))
    han = "".join(dict.fromkeys(HAN_PATTERN.findall(inspect_target)))
    if han:
        issues.append(_issue("UNTRANSLATED_HAN", f"Còn ký tự Trung Quốc trong bản dịch: {han}"))
        if (re.search(rf"[{VIETNAMESE_LETTER}]{{1,}}\s*{HAN_PATTERN.pattern}", inspect_target)
                or re.search(rf"{HAN_PATTERN.pattern}\s*[{VIETNAMESE_LETTER}]{{1,}}", inspect_target)):
            issues.append(_issue("MIXED_SCRIPT", "Bản dịch chứa chuỗi Trung–Việt bất thường"))

    if project.proper_name_mode != "preserve_source":
        masked_target = _mask_allowed(target, _allowed_han_values(project))
        fragments = [fragment for fragment in HAN_RUN_PATTERN.findall(masked_target) if fragment in source]
        if fragments:
            issues.append(_issue("UNTRANSLATED_SOURCE_FRAGMENT",
                                 "Còn nguyên cụm Chinese từ source: " + ", ".join(dict.fromkeys(fragments))))

    terms = dict(project.glossary)
    for row in project.story_context.get("characters", []) + project.story_context.get("terms", []):
        if isinstance(row, dict) and row.get("source") and row.get("target"):
            terms.setdefault(row["source"], row["target"])
    folded_target = target.casefold()
    for term_source, term_target in terms.items():
        if term_source in source and str(term_target).strip() and str(term_target).casefold() not in folded_target:
            issues.append(_issue("TERM_MISMATCH", f"Thuật ngữ chưa theo mapping/context: {term_source} → {term_target}"))

    visual = next((row for row in project.story_context.get("visual_contexts", [])
                   if row.get("id") == segment.id), None)
    ambiguous_source = any(token in source for token in ("他", "她", "它", "这个", "那个", "这东西"))
    if visual is None:
        if ambiguous_source and project.visual_context_status == "VISUAL_CONTEXT_UNAVAILABLE":
            issues.append(_issue("VISUAL_CONTEXT_LOW_CONFIDENCE",
                                 "Video context chưa khả dụng cho đại từ/referent mơ hồ", "SUSPECT"))
    else:
        if visual["speaker"].get("spk_id") != segment.speaker_id:
            issues.append(_issue("SPEAKER_CHARACTER_MISMATCH",
                                 f"Visual context gắn {visual['speaker'].get('spk_id')} nhưng timeline là {segment.speaker_id}"))
        if visual.get("confidence", 0) < 0.70 or visual.get("analysis_status") in {"LOW_CONFIDENCE", "NEED_REVIEW"}:
            issues.append(_issue("VISUAL_CONTEXT_LOW_CONFIDENCE",
                                 "Visual context chưa đủ chắc chắn; giữ cách diễn đạt trung tính", "SUSPECT"))
        male_pronouns = re.compile(r"\b(hắn|anh ấy|ông ấy|cậu ấy|chàng)\b", re.IGNORECASE)
        female_pronouns = re.compile(r"\b(nàng|cô ấy|chị ấy|em ấy|bà ấy)\b", re.IGNORECASE)
        for referent in visual.get("referents", []):
            expression = referent.get("source_expression", "")
            if expression and expression not in source:
                continue
            if referent.get("confidence", 0) < 0.80:
                continue
            gender = referent.get("gender_context")
            if gender == "female" and male_pronouns.search(target):
                issues.append(_issue("PRONOUN_CONTEXT_MISMATCH",
                                     f"{expression or 'Referent'} được duyệt là nữ nhưng bản dịch dùng đại từ nam"))
            elif gender == "male" and female_pronouns.search(target):
                issues.append(_issue("PRONOUN_CONTEXT_MISMATCH",
                                     f"{expression or 'Referent'} được duyệt là nam nhưng bản dịch dùng đại từ nữ"))
        matching_referents = [item for item in visual.get("referents", [])
                              if item.get("confidence", 0) >= 0.80
                              and item.get("source_expression") in source
                              and item.get("character_id")]
        if ambiguous_source and visual.get("confidence", 0) >= 0.80 and not matching_referents:
            issues.append(_issue("REFERENT_CONTEXT_MISMATCH",
                                 "Visual context chưa xác định được referent chắc chắn cho biểu thức mơ hồ",
                                 "SUSPECT"))

    suspect = []
    bracket_pairs = (("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ("‘", "’"))
    if any(target.count(left) != target.count(right) for left, right in bracket_pairs) or target.count('"') % 2:
        suspect.append(_issue("SUSPECT_TRUNCATION", "Câu có dấu ngoặc hoặc dấu nháy chưa khép", "SUSPECT"))
    if len(HAN_PATTERN.findall(source)) >= 20 and TRUNCATED_END.search(target):
        suspect.append(_issue("SUSPECT_TRUNCATION", "Câu có dấu hiệu bị cắt giữa chừng", "SUSPECT"))
    if len(HAN_PATTERN.findall(source)) >= 30 and len(re.findall(r"\w+", target, re.UNICODE)) <= 3:
        suspect.append(_issue("SUSPECT_OMISSION", "Bản dịch có thể thiếu ý so với câu gốc", "SUSPECT"))

    unique = []
    seen = set()
    for item in issues + suspect:
        if item["type"] not in seen:
            unique.append(item)
            seen.add(item["type"])
    if any(item["severity"] == "FAIL" for item in unique):
        return {"status": "FAIL", "issues": unique}
    if unique:
        return {"status": "SUSPECT", "issues": unique}
    return {"status": "PASS", "issues": []}


def review_translation(project):
    warnings = {}
    terms = dict(project.glossary)
    for row in project.story_context.get("characters", []) + project.story_context.get("terms", []):
        if row["source"] and row["target"]:
            terms.setdefault(row["source"], row["target"])
    for segment in project.segments:
        notes = []
        saved = project.translation_qa.get(str(segment.id), {})
        if qa_entry_is_current(segment, saved, project):
            qa_status, qa_issues = saved.get("status", "UNKNOWN"), saved.get("issues", [])
        else:
            current = local_translation_qa(project, segment)
            qa_status = "UNKNOWN" if current["status"] == "PASS" else current["status"]
            qa_issues = current["issues"]
        notes.append("QA: " + qa_status)
        notes.extend(f"{item.get('type', 'QA')}: {item.get('detail', '')}" for item in qa_issues)
        if any(item.get("type") == "UNTRANSLATED_HAN" for item in qa_issues):
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
