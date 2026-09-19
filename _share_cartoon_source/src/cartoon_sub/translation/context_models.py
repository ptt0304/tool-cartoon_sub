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
        return context


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
    "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["setting", "summary", "narration", "characters", "terms", "address_rules", "uncertainties"]}
