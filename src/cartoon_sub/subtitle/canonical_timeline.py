"""Derived multilingual subtitle timeline; source utterances remain untouched."""

from dataclasses import dataclass


OVERLAP_TOLERANCE_SECONDS = 1e-6


def _join_text(rows, attribute):
    values = [" ".join(str(getattr(row, attribute, "") or "").split()) for row in rows]
    return " ".join(value for value in values if value)


@dataclass(frozen=True)
class CanonicalTimelineEntry:
    canonical_id: int
    source_utterance_ids: tuple[int, ...]
    start: float
    end: float
    speaker_ids: tuple[str, ...]
    chinese: str
    vi_translation: str
    vi_subtitle: str
    vi_dubbing: str

    @property
    def id(self):
        return self.canonical_id

    @property
    def zh(self):
        return self.chinese

    @property
    def vi(self):
        return self.vi_translation


def canonical_timeline(utterances, tolerance=OVERLAP_TOLERANCE_SECONDS):
    """Merge transitive, genuinely intersecting intervals in chronological order."""
    indexed = list(enumerate(utterances))
    ordered = [row for _, row in sorted(indexed, key=lambda item: (
        item[1].start, item[1].end, item[0]
    ))]
    groups = []
    active = []
    active_end = None
    for row in ordered:
        if active and row.start >= active_end - tolerance:
            groups.append(active)
            active = []
            active_end = None
        active.append(row)
        active_end = row.end if active_end is None else max(active_end, row.end)
    if active:
        groups.append(active)

    entries = []
    for sequence, rows in enumerate(groups, 1):
        speakers = tuple(dict.fromkeys(row.speaker_id for row in rows))
        entries.append(CanonicalTimelineEntry(
            canonical_id=sequence,
            source_utterance_ids=tuple(row.id for row in rows),
            start=min(row.start for row in rows),
            end=max(row.end for row in rows),
            speaker_ids=speakers,
            chinese=_join_text(rows, "zh"),
            vi_translation=_join_text(rows, "vi_subtitle"),
            vi_subtitle=_join_text(rows, "vi_subtitle"),
            vi_dubbing=_join_text(rows, "vi_dubbing"),
        ))
    return entries
