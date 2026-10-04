"""Conservative run-scoped character identity registry for Visual Context."""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata


UNKNOWN_CHARACTER = "UNKNOWN"
REGISTRY_VERSION = 1
_CANONICAL_NUMERIC = re.compile(r"^CHAR_(\d+)$")


def _identity(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


class CharacterRegistry:
    def __init__(self, entries=None):
        self.entries = {}
        for entry in entries or []:
            self._store(entry)

    @classmethod
    def from_project(cls, project):
        registry = cls()
        # Human-approved Context has priority. An unapproved candidate is only
        # a seed when no approved registry exists.
        approved = project.story_context if project.context_source_hash else {}
        registry.absorb_context(approved or project.context_proposal or {})
        return registry

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get("version") != REGISTRY_VERSION:
            return cls()
        return cls(value.get("characters", []))

    def to_dict(self):
        return {"version": REGISTRY_VERSION, "characters": self.snapshot()}

    def snapshot(self):
        return [deepcopy(self.entries[key]) for key in sorted(self.entries)]

    @property
    def allowed_ids(self):
        return set(self.entries) | {UNKNOWN_CHARACTER}

    def _store(self, entry):
        character_id = str(entry.get("character_id") or "").strip()
        if not character_id.startswith("CHAR_"):
            return
        current = self.entries.setdefault(character_id, {
            "character_id": character_id,
            "name": "",
            "aliases": [],
            "description": "",
            "gender_context": "unknown",
            "confidence": 0.0,
            "evidence_ids": [],
            "associated_speakers": [],
            "source": "visual_candidate",
        })
        for key in ("name", "description", "source"):
            if entry.get(key):
                current[key] = str(entry[key])
        if entry.get("gender_context") in {"male", "female", "unknown"}:
            current["gender_context"] = entry["gender_context"]
        current["confidence"] = max(float(current.get("confidence", 0)),
                                    float(entry.get("confidence", 0)))
        for key in ("aliases", "evidence_ids", "associated_speakers"):
            current[key] = list(dict.fromkeys([*current.get(key, []), *entry.get(key, [])]))

    def absorb_context(self, context):
        profiles = {row.get("character_id"): row
                    for row in context.get("character_profiles", []) if isinstance(row, dict)}
        aliases = {}
        for row in context.get("characters", []):
            if isinstance(row, dict):
                for value in (row.get("source"), row.get("target")):
                    if value:
                        aliases.setdefault(row.get("target"), []).append(value)
        for character_id, profile in profiles.items():
            self._store({
                "character_id": character_id,
                "name": profile.get("name", ""),
                "aliases": aliases.get(profile.get("name"), []),
                "description": profile.get("visual_description", ""),
                "gender_context": profile.get("gender_context", "unknown"),
                "confidence": profile.get("confidence", 0),
                "evidence_ids": profile.get("evidence_ids", []),
                "associated_speakers": profile.get("associated_speakers", []),
                "source": "approved_context" if context else "visual_candidate",
            })
        for mapping in context.get("speaker_character_mappings", []):
            character_id = mapping.get("character_id")
            if character_id in self.entries:
                self._store({
                    "character_id": character_id,
                    "associated_speakers": [mapping.get("spk_id")]
                                           if mapping.get("spk_id") else [],
                    "evidence_ids": mapping.get("evidence_ids", []),
                    "confidence": mapping.get("confidence", 0),
                })

    def _next_id(self):
        reserved = {int(match.group(1)) for key in self.entries
                    if (match := _CANONICAL_NUMERIC.match(key))}
        number = 1
        while number in reserved:
            number += 1
        return f"CHAR_{number:02d}"

    def _match_candidate(self, candidate):
        aliases = {_identity(value) for value in candidate.get("aliases", []) if _identity(value)}
        description = _identity(candidate.get("description"))
        gender = candidate.get("gender_context", "unknown")
        for character_id, entry in self.entries.items():
            entry_aliases = {_identity(value) for value in [entry.get("name"), *entry.get("aliases", [])]
                             if _identity(value)}
            genders_compatible = (gender == "unknown" or entry.get("gender_context") == "unknown"
                                  or gender == entry.get("gender_context"))
            if aliases & entry_aliases and genders_compatible:
                return character_id
            # Exact visual-description continuity is evidence; fuzzy name
            # similarity alone is intentionally never used.
            if (description and len(description) >= 8
                    and description == _identity(entry.get("description"))
                    and genders_compatible):
                return character_id
        return None

    def reconcile_candidate(self, candidate):
        if (candidate.get("uncertain") is True
                or float(candidate.get("confidence", 0)) < .70
                or not candidate.get("evidence_ids")):
            return UNKNOWN_CHARACTER
        matched = self._match_candidate(candidate)
        character_id = matched or self._next_id()
        self._store({
            "character_id": character_id,
            "name": next(iter(candidate.get("aliases", [])), ""),
            "aliases": candidate.get("aliases", []),
            "description": candidate.get("description", ""),
            "gender_context": candidate.get("gender_context", "unknown"),
            "confidence": candidate.get("confidence", 0),
            "evidence_ids": candidate.get("evidence_ids", []),
            "source": "visual_candidate",
        })
        return character_id

    @staticmethod
    def _replace_id(value, mapping, allowed):
        if value in mapping:
            return mapping[value]
        return value if value in allowed else UNKNOWN_CHARACTER

    def reconcile_response(self, value, target_ids):
        """Canonicalize model output before strict StoryContext validation."""
        result = deepcopy(value)
        candidates = result.pop("new_character_candidates", []) or []
        candidate_mapping = {}
        bindings = []
        for candidate in candidates:
            temporary_id = str(candidate.get("temporary_id") or "").strip()
            canonical = self.reconcile_candidate(candidate)
            if temporary_id:
                candidate_mapping[temporary_id] = canonical
            for binding in candidate.get("bindings", []):
                bindings.append((binding.get("id"), binding.get("role"), canonical,
                                 candidate.get("gender_context", "unknown"),
                                 float(candidate.get("confidence", 0))))
        allowed = self.allowed_ids
        by_id = {row.get("id"): row for row in result.get("visual_contexts", [])}
        for row in result.get("visual_contexts", []):
            changed = False
            speaker = row.get("speaker", {})
            old = speaker.get("character_id")
            speaker["character_id"] = self._replace_id(old, candidate_mapping, allowed)
            changed |= (speaker["character_id"] != old and old not in candidate_mapping
                        and old not in (None, ""))
            addressee = row.get("addressee", {})
            old = addressee.get("character_id")
            addressee["character_id"] = self._replace_id(old, candidate_mapping, allowed)
            changed |= (addressee["character_id"] != old and old not in candidate_mapping
                        and old not in (None, ""))
            for key in ("visible_characters", "referents"):
                for item in row.get(key, []):
                    old = item.get("character_id")
                    item["character_id"] = self._replace_id(old, candidate_mapping, allowed)
                    changed |= item["character_id"] != old and old not in candidate_mapping
            if changed:
                row["analysis_status"] = "NEED_REVIEW"
                note = "ID nhân vật do model tự đặt đã được registry chuẩn hóa."
                row["notes"] = " ".join(filter(None, (row.get("notes", ""), note)))

        for utterance_id, role, character_id, gender, confidence in bindings:
            row = by_id.get(utterance_id)
            if row is None or utterance_id not in target_ids:
                continue
            if role == "speaker":
                row["speaker"]["character_id"] = character_id
                row["speaker"]["confidence"] = confidence
            elif role == "addressee":
                row["addressee"]["character_id"] = character_id
                row["addressee"]["confidence"] = confidence
            elif role == "visible" and character_id != UNKNOWN_CHARACTER:
                row["visible_characters"].append({
                    "character_id": character_id, "gender_context": gender,
                    "confidence": confidence,
                })
            if character_id == UNKNOWN_CHARACTER:
                row["analysis_status"] = "NEED_REVIEW"

        profiles = []
        for profile in result.get("character_profiles", []):
            character_id = self._replace_id(
                profile.get("character_id"), candidate_mapping, allowed)
            if character_id == UNKNOWN_CHARACTER:
                continue
            profile["character_id"] = character_id
            profile["relationships"] = [candidate_mapping.get(item, item)
                                        for item in profile.get("relationships", [])]
            profiles.append(profile)
        represented = {profile["character_id"] for profile in profiles}
        for character_id in set(candidate_mapping.values()) - {UNKNOWN_CHARACTER} - represented:
            entry = self.entries[character_id]
            profiles.append({
                "character_id": character_id,
                "name": entry.get("name", "") or "Chưa xác định",
                "role": "Chưa xác định",
                "gender_context": entry.get("gender_context", "unknown"),
                "relationships": [],
                "visual_description": entry.get("description", ""),
                "associated_speakers": entry.get("associated_speakers", []),
                "confidence": entry.get("confidence", 0),
                "evidence_ids": entry.get("evidence_ids", []),
            })
        result["character_profiles"] = profiles
        for mapping in result.get("speaker_character_mappings", []):
            mapping["character_id"] = self._replace_id(
                mapping.get("character_id"), candidate_mapping, allowed)
        self.absorb_context(result)
        return result

    def normalize_final_context(self, context):
        """Final defensive canonicalization after all windows are merged."""
        value = deepcopy(context)
        allowed = self.allowed_ids
        profiles = {}
        for row in value.get("character_profiles", []):
            character_id = row.get("character_id")
            if character_id not in allowed or character_id == UNKNOWN_CHARACTER:
                continue
            current = profiles.get(character_id)
            if current is None:
                profiles[character_id] = row
            else:
                for key in ("relationships", "associated_speakers", "evidence_ids"):
                    current[key] = list(dict.fromkeys(current.get(key, []) + row.get(key, [])))
                current["confidence"] = max(current.get("confidence", 0), row.get("confidence", 0))
        value["character_profiles"] = list(profiles.values())
        known_aliases = {row.get("source") for row in value.get("characters", [])}
        for entry in self.entries.values():
            for alias in entry.get("aliases", []):
                if alias and alias not in known_aliases:
                    value["characters"].append({
                        "source": alias,
                        "target": entry.get("name", "") or alias,
                        "notes": f"Alias candidate của {entry['character_id']}",
                        "evidence_ids": entry.get("evidence_ids", []),
                    })
                    known_aliases.add(alias)
        for row in value.get("speaker_character_mappings", []):
            if row.get("character_id") not in allowed:
                row["character_id"] = UNKNOWN_CHARACTER
        for row in value.get("visual_contexts", []):
            for actor in (row.get("speaker", {}), row.get("addressee", {})):
                if actor.get("character_id") not in allowed:
                    actor["character_id"] = UNKNOWN_CHARACTER
                    row["analysis_status"] = "NEED_REVIEW"
            for key in ("visible_characters", "referents"):
                for item in row.get(key, []):
                    if item.get("character_id") not in allowed:
                        item["character_id"] = UNKNOWN_CHARACTER
                        row["analysis_status"] = "NEED_REVIEW"
        return value
