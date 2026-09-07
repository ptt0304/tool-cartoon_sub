import re
import unicodedata


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
        warnings[str(segment.id)] = notes
    return warnings
