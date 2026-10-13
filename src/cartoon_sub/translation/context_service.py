"""Translation readiness and compatibility helpers after visual inference removal."""
from cartoon_sub.project.cache import content_hash
from .prompts import effective_custom_rules


def translation_readiness(project):
    if not project.segments or any(not row.zh.strip() for row in project.segments):
        return False, "Cần transcript tiếng Trung trước khi dịch."
    return True, "Transcript sẵn sàng — có thể dịch trực tiếp."


def source_fingerprint(project):
    """Legacy-safe deterministic timeline fingerprint; no AI or video input."""
    return content_hash([{"id": row.id, "start": row.start, "end": row.end, "zh": row.zh}
                         for row in project.segments])


def context_config_fingerprint(project):
    """Legacy-safe fingerprint of user-owned translation guidance."""
    return content_hash({
        "source": source_fingerprint(project), "genres": list(project.translation_genres),
        "translation_style": project.translation_preset, "proper_name_rule": project.proper_name_mode,
        "user_mappings": project.glossary, "supplemental_requirements": project.translation_prompt.strip(),
        "active_custom_rules": effective_custom_rules(project),
    })
