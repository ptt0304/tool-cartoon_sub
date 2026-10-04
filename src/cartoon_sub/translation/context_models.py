from dataclasses import dataclass, field, asdict


@dataclass
class StoryContext:
    setting: str = ""
    summary: str = ""
    narration: str = ""
    characters: list[dict] = field(default_factory=list)
    terms: list[dict] = field(default_factory=list)
    address_rules: list[dict] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    character_profiles: list[dict] = field(default_factory=list)
    speaker_character_mappings: list[dict] = field(default_factory=list)
    visual_contexts: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data, valid_ids=None):
        if not isinstance(data, dict):
            raise ValueError("Hồ sơ truyện phải là object")
        context = cls(**data)
        for name in ("setting", "summary", "narration"):
            if not isinstance(getattr(context, name), str):
                raise ValueError(f"Hồ sơ: {name} phải là văn bản")
        for name, fields in (("characters", ("source", "target", "notes")),
                             ("terms", ("source", "target", "notes")),
                             ("address_rules", ("speaker", "listener", "self_term", "address_term", "condition"))):
            rows = getattr(context, name)
            if not isinstance(rows, list):
                raise ValueError(f"Hồ sơ: {name} phải là danh sách")
            for row in rows:
                if not isinstance(row, dict) or set(row) != {*fields, "evidence_ids"}:
                    raise ValueError(f"Hồ sơ: thiếu trường trong {name}")
                if any(not isinstance(row[key], str) for key in fields):
                    raise ValueError(f"Hồ sơ: {name} phải chứa văn bản")
                ids = row["evidence_ids"]
                if not isinstance(ids, list) or any(type(i) is not int or i < 1 for i in ids):
                    raise ValueError("ID bằng chứng phải là số nguyên dương")
                if valid_ids is not None and not set(ids).issubset(valid_ids):
                    raise ValueError("Hồ sơ viện dẫn ID không có trong transcript")
        if not isinstance(context.uncertainties, list) or any(not isinstance(t, str) for t in context.uncertainties):
            raise ValueError("Nghi vấn phải là danh sách văn bản")
        _validate_visual_context(context, valid_ids)
        return context


SCENE_MODES = {"PRESENT", "FLASHBACK", "MEMORY", "IMAGINED", "NARRATION_VISUAL", "INSERT", "UNKNOWN"}
GENDER_CONTEXTS = {"male", "female", "unknown"}


def _confidence(value, label):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError(f"{label} confidence phải trong [0,1]")


def _validate_visual_context(context, valid_ids):
    if not isinstance(context.character_profiles, list):
        raise ValueError("character_profiles phải là danh sách")
    for row in context.character_profiles:
        required = {"character_id", "name", "role", "gender_context", "relationships",
                    "visual_description", "associated_speakers", "confidence", "evidence_ids"}
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("Character profile thiếu trường")
        if row["gender_context"] not in GENDER_CONTEXTS:
            raise ValueError("gender_context không hợp lệ")
        for key in ("character_id", "name", "role", "visual_description"):
            if not isinstance(row[key], str):
                raise ValueError("Character profile phải chứa văn bản")
        if any(not isinstance(item, str) for item in row["relationships"] + row["associated_speakers"]):
            raise ValueError("Character relationships/speakers phải là danh sách văn bản")
        _confidence(row["confidence"], "Character")
        _evidence_ids(row["evidence_ids"], valid_ids)
    if not isinstance(context.speaker_character_mappings, list):
        raise ValueError("speaker_character_mappings phải là danh sách")
    for row in context.speaker_character_mappings:
        if not isinstance(row, dict) or set(row) != {"spk_id", "character_id", "confidence", "evidence_ids", "notes"}:
            raise ValueError("Speaker-character mapping thiếu trường")
        if any(not isinstance(row[key], str) for key in ("spk_id", "character_id", "notes")):
            raise ValueError("Speaker-character mapping phải chứa văn bản")
        _confidence(row["confidence"], "Speaker mapping")
        _evidence_ids(row["evidence_ids"], valid_ids)
    if not isinstance(context.visual_contexts, list):
        raise ValueError("visual_contexts phải là danh sách")
    seen = set()
    for row in context.visual_contexts:
        required = {"id", "scene_mode", "speaker", "addressee", "visible_characters", "referents",
                    "visible_objects", "notes", "confidence", "analysis_status"}
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("Visual context thiếu trường")
        if type(row["id"]) is not int or row["id"] < 1 or row["id"] in seen:
            raise ValueError("Visual context ID không hợp lệ hoặc bị lặp")
        seen.add(row["id"])
        if valid_ids is not None and row["id"] not in valid_ids:
            raise ValueError("Visual context viện dẫn ID ngoài transcript")
        if row["scene_mode"] not in SCENE_MODES:
            raise ValueError("scene_mode không hợp lệ")
        if row["analysis_status"] not in {"ANALYZED", "LOW_CONFIDENCE", "VISUAL_CONTEXT_UNAVAILABLE", "NEED_REVIEW"}:
            raise ValueError("Visual context status không hợp lệ")
        _confidence(row["confidence"], "Visual context")
        _validate_actor(row["speaker"], True)
        _validate_actor(row["addressee"], False)
        for key in ("visible_characters", "referents"):
            if not isinstance(row[key], list):
                raise ValueError(f"{key} phải là danh sách")
            for item in row[key]:
                if key == "visible_characters":
                    expected = {"character_id", "gender_context", "confidence"}
                else:
                    expected = {"source_expression", "character_id", "gender_context", "confidence"}
                if not isinstance(item, dict) or set(item) != expected or item["gender_context"] not in GENDER_CONTEXTS:
                    raise ValueError(f"{key} không hợp lệ")
                _confidence(item["confidence"], key)
        if not isinstance(row["visible_objects"], list) or any(
                not isinstance(item, dict) or set(item) != {"source_expression", "description", "confidence"}
                for item in row["visible_objects"]):
            raise ValueError("visible_objects không hợp lệ")
        for item in row["visible_objects"]:
            _confidence(item["confidence"], "Visible object")
        if not isinstance(row["notes"], str):
            raise ValueError("Visual context notes phải là văn bản")


def _validate_actor(value, speaker):
    expected = {"spk_id", "character_id", "confidence"} if speaker else {"character_id", "confidence"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Speaker/addressee visual context không hợp lệ")
    _confidence(value["confidence"], "Speaker/addressee")


def _evidence_ids(ids, valid_ids):
    if not isinstance(ids, list) or any(type(item) is not int or item < 1 for item in ids):
        raise ValueError("ID bằng chứng phải là số nguyên dương")
    if valid_ids is not None and not set(ids).issubset(valid_ids):
        raise ValueError("ID bằng chứng ngoài transcript")


def _rows(fields):
    return {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        **{name: {"type": "STRING"} for name in fields},
        "evidence_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
        "required": [*fields, "evidence_ids"]}}


CONTEXT_SCHEMA = {"type": "OBJECT", "properties": {
    "setting": {"type": "STRING"}, "summary": {"type": "STRING"}, "narration": {"type": "STRING"},
    "characters": _rows(("source", "target", "notes")),
    "terms": _rows(("source", "target", "notes")),
    "address_rules": _rows(("speaker", "listener", "self_term", "address_term", "condition")),
    "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
    "character_profiles": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "character_id": {"type": "STRING"}, "name": {"type": "STRING"}, "role": {"type": "STRING"},
        "gender_context": {"type": "STRING", "enum": sorted(GENDER_CONTEXTS)},
        "relationships": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_description": {"type": "STRING"},
        "associated_speakers": {"type": "ARRAY", "items": {"type": "STRING"}},
        "confidence": {"type": "NUMBER"}, "evidence_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
        "required": ["character_id", "name", "role", "gender_context", "relationships", "visual_description", "associated_speakers", "confidence", "evidence_ids"]}},
    "speaker_character_mappings": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "spk_id": {"type": "STRING"}, "character_id": {"type": "STRING"}, "confidence": {"type": "NUMBER"},
        "evidence_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}, "notes": {"type": "STRING"}},
        "required": ["spk_id", "character_id", "confidence", "evidence_ids", "notes"]}},
    "new_character_candidates": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "temporary_id": {"type": "STRING"}, "description": {"type": "STRING"},
        "aliases": {"type": "ARRAY", "items": {"type": "STRING"}},
        "gender_context": {"type": "STRING", "enum": sorted(GENDER_CONTEXTS)},
        "confidence": {"type": "NUMBER"},
        "uncertain": {"type": "BOOLEAN"},
        "evidence_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "bindings": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER"},
            "role": {"type": "STRING", "enum": ["speaker", "addressee", "visible"]}},
            "required": ["id", "role"]}}},
        "required": ["temporary_id", "description", "aliases", "gender_context", "confidence",
                     "uncertain", "evidence_ids", "bindings"]}},
    "visual_contexts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "INTEGER"}, "scene_mode": {"type": "STRING", "enum": sorted(SCENE_MODES)},
        "speaker": {"type": "OBJECT", "properties": {"spk_id": {"type": "STRING"}, "character_id": {"type": "STRING"}, "confidence": {"type": "NUMBER"}}, "required": ["spk_id", "character_id", "confidence"]},
        "addressee": {"type": "OBJECT", "properties": {"character_id": {"type": "STRING"}, "confidence": {"type": "NUMBER"}}, "required": ["character_id", "confidence"]},
        "visible_characters": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"character_id": {"type": "STRING"}, "gender_context": {"type": "STRING", "enum": sorted(GENDER_CONTEXTS)}, "confidence": {"type": "NUMBER"}}, "required": ["character_id", "gender_context", "confidence"]}},
        "referents": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"source_expression": {"type": "STRING"}, "character_id": {"type": "STRING"}, "gender_context": {"type": "STRING", "enum": sorted(GENDER_CONTEXTS)}, "confidence": {"type": "NUMBER"}}, "required": ["source_expression", "character_id", "gender_context", "confidence"]}},
        "visible_objects": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"source_expression": {"type": "STRING"}, "description": {"type": "STRING"}, "confidence": {"type": "NUMBER"}}, "required": ["source_expression", "description", "confidence"]}},
        "notes": {"type": "STRING"}, "confidence": {"type": "NUMBER"},
        "analysis_status": {"type": "STRING", "enum": ["ANALYZED", "LOW_CONFIDENCE", "NEED_REVIEW"]}},
        "required": ["id", "scene_mode", "speaker", "addressee", "visible_characters", "referents", "visible_objects", "notes", "confidence", "analysis_status"]}}},
    "required": ["setting", "summary", "narration", "characters", "terms", "address_rules", "uncertainties", "character_profiles", "speaker_character_mappings", "visual_contexts"]}
