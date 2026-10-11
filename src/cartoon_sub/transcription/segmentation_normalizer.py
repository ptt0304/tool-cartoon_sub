"""Provider-neutral normalization of coarse transcript rows into utterances."""
from dataclasses import dataclass
import re

from cartoon_sub.subtitle.models import Segment
from cartoon_sub.transcription.contracts import normalize_word_timestamps


TRANSCRIPT_SEGMENTATION_VERSION = 4

# Partial word alignment is useful only when it is substantially complete.
# Sparse STT word timestamps must never be mixed with estimated fragments: that
# can place a missing character in a multi-second silence while its neighbours
# retain 100 ms word times.
PARTIAL_ALIGNMENT_MIN_COVERAGE = 0.85
PARTIAL_ALIGNMENT_MAX_GAP = 2.5
PARTIAL_ALIGNMENT_MIN_WORD_DURATION = 0.10


@dataclass(frozen=True)
class TranscriptSegmentationConfig:
    soft_pause: float = 0.45
    strong_pause: float = 0.70
    preferred_min_duration: float = 2.0
    preferred_max_duration: float = 5.0
    soft_max_duration: float = 7.0
    hard_max_duration: float = 10.0
    preferred_min_chars: int = 8
    preferred_max_chars: int = 30
    soft_max_chars: int = 40
    hard_max_chars: int = 60


@dataclass(frozen=True)
class _TimedToken:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class _WordAlignment:
    """Monotonic canonical-character to provider-token evidence mapping."""
    canonical_to_word: dict
    canonical_count: int
    word_count: int

    @property
    def matched_count(self):
        return len(self.canonical_to_word)


_STRONG_END = re.compile(r"(?:[。！？?!]|……|…)[\"'”’」』】）)]*$")
_MEDIUM_END = re.compile(r"[；;][\"'”’」』】）)]*$")
_WEAK_END = re.compile(r"[，,][\"'”’」』】）)]*$")
_SENTENCE_PARTS = re.compile(r".*?(?:……|…|[。！？?!]+|$)", re.S)
_MEDIUM_PARTS = re.compile(r".*?(?:[；;]+|$)", re.S)
_SPACE_OR_ATOM = re.compile(r"\s+|[A-Za-z0-9]+(?:[.'’:/-][A-Za-z0-9]+)*|.", re.S)
_FALLBACK_CLAUSES = re.compile(
    r".*?(?:\s+|[吗吧啊呢嘛呀哇](?=[\u3400-\u9fff])|$)", re.S)


def _spoken_weight(text):
    han = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_or_numbers = re.findall(r"[A-Za-z0-9]+(?:[.'’:/-][A-Za-z0-9]+)*", text)
    return max(0.2, han + sum(max(1.0, len(token) * 0.55) for token in latin_or_numbers))


def _char_count(text):
    return len(re.sub(r"\s|[。！？?!；;，,…\"'“”‘’「」『』【】（）()]", "", text))


def _significant(text):
    return "".join(ch for ch in text if ch.isalnum() or "\u3400" <= ch <= "\u9fff")


def _copy_segment(parent, segment_id, start, end, text,
                  timing_provenance="ESTIMATED", timing_confidence=0.0):
    return Segment(
        segment_id, start, end, text.strip(),
        transcript_confidence=parent.transcript_confidence,
        transcript_timing_provenance=timing_provenance,
        transcript_timing_confidence=timing_confidence,
        transcript_language_status=parent.transcript_language_status,
        transcript_language_retry_count=parent.transcript_language_retry_count,
        transcript_raw_stt_text=parent.transcript_raw_stt_text,
        transcript_language_retry_text=parent.transcript_language_retry_text,
        translation_mode=parent.translation_mode,
    )


class TranscriptSegmentationNormalizer:
    """Deterministic domain service; contains no provider/model knowledge."""

    def __init__(self, config=None):
        self.config = config or TranscriptSegmentationConfig()

    def normalize(self, segments, words=None, language="zh", source="stt"):
        del language  # Reserved for language-specific strategies beyond V1 Chinese rules.
        parents = sorted(list(segments), key=lambda row: (row.start, row.end, row.id))
        if not parents:
            return []
        max_end = max(row.end for row in parents)
        safe_words = normalize_word_timestamps(words, max_end) if words else []
        word_buckets = [[] for _ in parents]
        for word in safe_words:
            candidates = []
            for index, parent in enumerate(parents):
                overlap = max(0.0, min(word["end"], parent.end) - max(word["start"], parent.start))
                if overlap:
                    candidates.append((overlap, -abs((word["start"] + word["end"]) / 2
                                                     - (parent.start + parent.end) / 2), index))
            if candidates:
                word_buckets[max(candidates)[2]].append(word)
        output = []
        for parent, parent_words in zip(parents, word_buckets):
            tokens = self._align_words(parent, parent_words)
            if tokens:
                children = self._from_timed_tokens(parent, tokens)
            else:
                alignment = self._partial_word_alignment(parent, parent_words)
                estimated = self._without_word_times(parent, source)
                children = (self._apply_partial_word_alignment(parent, estimated, alignment, parent_words)
                            if self._partial_alignment_is_safe(parent, alignment, parent_words)
                            else estimated)
            output.extend(children)
        for index, row in enumerate(output, 1):
            row.id = index
        return output

    def normalize_region(self, segments, words=None, language="zh", source="vad"):
        """Normalize one speech region without turning coarse fragments into characters."""
        segments = list(segments)
        if not segments:
            return []
        safe_words = normalize_word_timestamps(words, max(row.end for row in segments)) if words else []
        # Provider word arrays accompanying several coarse fragments are not
        # trustworthy enough to mix with inferred characters. Keep region text
        # sentence-level unless one parent has complete word evidence.
        if safe_words and not self.region_segmentation_untrusted(segments):
            return self.normalize(segments, safe_words, language, source)
        # New production runs never partition a VAD window by character weight.
        # Without complete acoustic word evidence, preserve one coarse evidence
        # row and let timing QA/retry decide whether it can be finalized.
        if not safe_words:
            # Provider segment boundaries are still acoustic evidence.  Group
            # only at an existing sentence boundary; never manufacture an
            # internal timestamp from text/character weight.
            grouped, pending = [], []
            for row in segments:
                pending.append(row)
                if _STRONG_END.search(row.zh.rstrip()):
                    grouped.append(pending); pending = []
            if pending:
                grouped.append(pending)
            return [_copy_segment(group[0], 1, group[0].start, group[-1].end,
                                  "".join(item.zh for item in group),
                                  timing_provenance=group[0].transcript_timing_provenance,
                                  timing_confidence=group[0].transcript_timing_confidence)
                    for group in grouped]
        text = "".join(row.zh for row in segments)
        parent = _copy_segment(segments[0], 1, min(row.start for row in segments),
                               max(row.end for row in segments), text,
                               timing_provenance="VAD_SENTENCE", timing_confidence=0.45)
        return self._without_word_times(parent, source)

    @staticmethod
    def region_segmentation_untrusted(segments):
        rows = list(segments)
        if not rows:
            return False
        counts = [_char_count(row.zh) for row in rows]
        single = sum(count <= 1 for count in counts) / len(rows)
        micro = sum(count <= 2 for count in counts) / len(rows)
        punctuation = any(not _char_count(row.zh) and row.zh.strip() for row in rows)
        chain = max((len(list(group)) for value, group in __import__('itertools').groupby(
            count <= 1 for count in counts) if value), default=0)
        durations = [row.end - row.start for row in rows if row.end > row.start]
        uniform = (len(durations) >= 4 and max(durations) - min(durations) <= .15
                   and micro > .5)
        return single > .30 or micro > .50 or punctuation or chain >= 3 or uniform

    def split_at_source_indices(self, parent, after_indices):
        """Slice only the original text, then reconstruct conservative timing."""
        boundaries = sorted(set(after_indices))
        if any(type(index) is not int or index < 0 or index >= len(parent.zh) - 1
               for index in boundaries):
            raise ValueError("Semantic boundary index is outside the source text")
        pieces = []
        cursor = 0
        for index in boundaries:
            piece = parent.zh[cursor:index + 1]
            if not piece.strip():
                raise ValueError("Semantic boundary creates an empty child")
            pieces.append(piece)
            cursor = index + 1
        tail = parent.zh[cursor:]
        if not tail.strip():
            raise ValueError("Semantic boundary creates an empty child")
        pieces.append(tail)
        if "".join(pieces) != parent.zh:
            raise ValueError("Semantic boundaries do not preserve source text")
        return self._allocate_weighted(parent, pieces)

    def _align_words(self, parent, words):
        if not words:
            return []
        words = sorted(words, key=lambda row: (row["start"], row["end"]))
        parent_sig = _significant(parent.zh)
        word_sigs = [_significant(row["word"]) for row in words]
        if not parent_sig or any(not value for value in word_sigs) or "".join(word_sigs) != parent_sig:
            return []

        significant_positions = [index for index, ch in enumerate(parent.zh)
                                 if ch.isalnum() or "\u3400" <= ch <= "\u9fff"]
        tokens = []
        source_cursor = 0
        sig_cursor = 0
        for index, (word, word_sig) in enumerate(zip(words, word_sigs)):
            sig_cursor += len(word_sig)
            # Attribute inter-word punctuation/spacing to the preceding timed
            # token so a sentence mark becomes a boundary at its spoken word.
            source_end = (len(parent.zh) if index == len(words) - 1
                          else significant_positions[sig_cursor])
            text = parent.zh[source_cursor:source_end]
            source_cursor = source_end
            tokens.append(_TimedToken(text, max(parent.start, word["start"]),
                                      min(parent.end, word["end"])))
        return tokens if all(token.end > token.start for token in tokens) else []

    @staticmethod
    def _alignment_characters(text):
        """Characters used for timing comparison, retaining source positions."""
        return [(index, char) for index, char in enumerate(text)
                if char.isalnum() or "\u3400" <= char <= "\u9fff"]

    def _partial_word_alignment(self, parent, words):
        """Globally align canonical text with timed token characters.

        Exact equality is intentionally not required.  This is a deterministic
        edit-distance alignment, so repeated phrases retain their sequence
        position instead of being independently fuzzy-matched.
        """
        if not words:
            return None
        canonical = self._alignment_characters(parent.zh)
        timed = []
        for word_index, word in enumerate(sorted(words, key=lambda row: (row["start"], row["end"]))):
            timed.extend((char, word_index) for _, char in self._alignment_characters(word["word"]))
        if not canonical or not timed:
            return None
        rows, cols = len(canonical), len(timed)
        # Unit-cost global edit alignment; a character match is always preferred
        # during backtracking, which gives stable anchors around repeated text.
        score = [[0] * (cols + 1) for _ in range(rows + 1)]
        for i in range(1, rows + 1): score[i][0] = i
        for j in range(1, cols + 1): score[0][j] = j
        for i in range(1, rows + 1):
            left = canonical[i - 1][1]
            for j in range(1, cols + 1):
                diagonal = score[i - 1][j - 1] + (left != timed[j - 1][0])
                score[i][j] = min(diagonal, score[i - 1][j] + 1, score[i][j - 1] + 1)
        mapping = {}
        i, j = rows, cols
        while i and j:
            left, right = canonical[i - 1][1], timed[j - 1][0]
            diagonal = score[i - 1][j - 1] + (left != right)
            if score[i][j] == diagonal:
                if left == right:
                    mapping[i - 1] = timed[j - 1][1]
                i -= 1; j -= 1
            elif score[i][j] == score[i - 1][j] + 1:
                i -= 1
            else:
                j -= 1
        if not mapping:
            return None
        return _WordAlignment(mapping, rows, len(timed))

    @staticmethod
    def _partial_alignment_is_safe(parent, alignment, words):
        """Reject incomplete or temporally sparse word evidence as a whole.

        The fallback keeps deterministic weighted timing for the complete
        parent.  In particular, it intentionally avoids combining exact word
        timings with inferred text when that inference spans a long silence.
        """
        if alignment is None or not words or not alignment.canonical_count:
            return False
        if alignment.matched_count / alignment.canonical_count < PARTIAL_ALIGNMENT_MIN_COVERAGE:
            return False
        ordered = sorted(words, key=lambda row: (row["start"], row["end"]))
        if any(row["end"] - row["start"] <= PARTIAL_ALIGNMENT_MIN_WORD_DURATION
               for row in ordered):
            return False
        if any(right["start"] < left["end"] - 0.001
               for left, right in zip(ordered, ordered[1:])):
            return False
        matched = sorted(set(alignment.canonical_to_word.values()))
        if any(ordered[right]["start"] - ordered[left]["end"] > PARTIAL_ALIGNMENT_MAX_GAP
               for left, right in zip(matched, matched[1:])):
            return False
        return all(parent.start <= row["start"] < row["end"] <= parent.end for row in ordered)

    def _apply_partial_word_alignment(self, parent, estimated, alignment, words):
        """Replace only each child timing with its local word evidence."""
        words = sorted(words, key=lambda row: (row["start"], row["end"]))
        significant = self._alignment_characters(parent.zh)
        cursor = 0
        entries = []
        for child in estimated:
            count = len(_significant(child.zh))
            indices = range(cursor, min(cursor + count, len(significant)))
            matched_words = [alignment.canonical_to_word[index] for index in indices
                             if index in alignment.canonical_to_word]
            cursor += count
            entries.append((child, count, matched_words))
        children = []
        entry_index = 0
        canonical_cursor = 0
        while entry_index < len(entries):
            child, count, matched_words = entries[entry_index]
            if matched_words:
                first, last = min(matched_words), max(matched_words)
                start = max(parent.start, words[first]["start"])
                end = min(parent.end, words[last]["end"])
                ratio = len(matched_words) / max(1, count)
                provenance = "WORD_TIMESTAMP" if ratio == 1 else "PARTIAL_WORD_ALIGNMENT"
                confidence = ratio
                if end > start:
                    children.append(_copy_segment(parent, 1, start, end, child.zh,
                                                  timing_provenance=provenance,
                                                  timing_confidence=confidence))
                else:
                    children.append(_copy_segment(parent, 1, child.start, child.end, child.zh))
                canonical_cursor += count
                entry_index += 1
                continue

            # Consecutive unmatched child rows share only the local interval
            # between their nearest aligned neighbours.  Partitioning that
            # interval avoids assigning the same evidence to multiple rows.
            run_start, run_cursor = entry_index, canonical_cursor
            while entry_index < len(entries) and not entries[entry_index][2]:
                run_cursor += entries[entry_index][1]
                entry_index += 1
            before = [word_index for index, word_index in alignment.canonical_to_word.items()
                      if index < canonical_cursor]
            after = [word_index for index, word_index in alignment.canonical_to_word.items()
                     if index >= run_cursor]
            left = words[max(before)]["end"] if before else parent.start
            right = words[min(after)]["start"] if after else parent.end
            run = entries[run_start:entry_index]
            if right > left:
                weights = [_spoken_weight(item[0].zh) for item in run]
                total = sum(weights)
                local_cursor = left
                for offset, ((row, _, _), weight) in enumerate(zip(run, weights)):
                    end = right if offset == len(run) - 1 else local_cursor + (right - left) * weight / total
                    children.append(_copy_segment(parent, 1, local_cursor, end, row.zh,
                                                  timing_provenance="LOCAL_ESTIMATION",
                                                  timing_confidence=0.5))
                    local_cursor = end
            else:
                for row, _, _ in run:
                    children.append(_copy_segment(parent, 1, row.start, row.end, row.zh))
            canonical_cursor = run_cursor
        return children

    def _from_timed_tokens(self, parent, tokens):
        cfg = self.config
        groups = []
        group_start = 0
        last_candidate = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            text = "".join(item.text for item in tokens[group_start:index + 1])
            duration = token.end - tokens[group_start].start
            chars = _char_count(text)
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            gap = max(0.0, next_token.start - token.end) if next_token else 0.0
            strong = bool(_STRONG_END.search(text.rstrip()))
            medium = bool(_MEDIUM_END.search(text.rstrip()))
            weak = bool(_WEAK_END.search(text.rstrip()))
            if medium or weak or gap >= cfg.soft_pause:
                last_candidate = index
            forced = strong or gap >= cfg.strong_pause
            over_hard = duration >= cfg.hard_max_duration or chars >= cfg.hard_max_chars
            over_soft = duration >= cfg.soft_max_duration or chars >= cfg.soft_max_chars
            split_at = None
            if forced:
                split_at = index
            elif over_hard:
                split_at = last_candidate if last_candidate is not None else index
            elif over_soft and last_candidate is not None:
                split_at = last_candidate
            elif (medium and duration >= cfg.preferred_min_duration
                  and chars >= cfg.preferred_min_chars):
                split_at = index
            if split_at is not None:
                groups.append((group_start, split_at))
                group_start = split_at + 1
                last_candidate = None
                index = group_start
                continue
            index += 1
        if group_start < len(tokens):
            groups.append((group_start, len(tokens) - 1))

        children = []
        for start_index, end_index in groups:
            group = tokens[start_index:end_index + 1]
            children.append(_copy_segment(parent, 1, group[0].start, group[-1].end,
                                          "".join(item.text for item in group),
                                          timing_provenance="WORD_TIMESTAMP",
                                          timing_confidence=1.0))
        return children

    def _without_word_times(self, parent, source):
        cfg = self.config
        text = parent.zh
        chars = _char_count(text)
        duration = parent.end - parent.start
        strong_count = len(re.findall(r"……|…|[。！？?!]+", text))
        if source == "srt":
            pathological = (duration > cfg.hard_max_duration or chars > cfg.hard_max_chars
                            or (duration > cfg.soft_max_duration and strong_count > 1))
            if not pathological:
                return [parent]
        elif (duration <= cfg.soft_max_duration and chars <= cfg.soft_max_chars
              and strong_count <= 1):
            return [parent]

        pieces = [part for part in _SENTENCE_PARTS.findall(text) if part]
        if not pieces:
            pieces = [text]
        if source == "vad":
            # A VAD region bounds real speech. Preserve sentence boundaries;
            # duration alone must never turn Mandarin into character fragments.
            return self._allocate_weighted(parent, pieces)
        refined = []
        for piece in pieces:
            piece_duration = duration * _spoken_weight(piece) / _spoken_weight(text)
            if (_char_count(piece) > cfg.soft_max_chars
                    or piece_duration > cfg.soft_max_duration):
                medium = [part for part in _MEDIUM_PARTS.findall(piece) if part]
                refined.extend(medium or [piece])
            else:
                refined.append(piece)
        pieces = self._split_oversize_parts(refined, duration, text)
        return self._allocate_weighted(parent, pieces)

    def _split_oversize_parts(self, pieces, parent_duration, parent_text):
        cfg = self.config
        result = []
        parent_weight = _spoken_weight(parent_text)
        for piece in pieces:
            estimated = parent_duration * _spoken_weight(piece) / parent_weight
            if _char_count(piece) <= cfg.soft_max_chars and estimated <= cfg.soft_max_duration:
                result.append(piece)
                continue
            # Whitespace from STT and common Chinese utterance-final particles
            # are safer fallbacks than arbitrary character cuts. They are only
            # consulted after punctuation and only for an oversized row.
            clauses = [part for part in _FALLBACK_CLAUSES.findall(piece) if part]
            groups = []
            current = ""
            for clause in clauses:
                trial = current + clause
                trial_duration = parent_duration * _spoken_weight(trial) / parent_weight
                if current and (_char_count(trial) > cfg.preferred_max_chars
                                or trial_duration > cfg.preferred_max_duration):
                    groups.append(current)
                    current = clause
                else:
                    current = trial
                hint_boundary = bool(re.search(r"(?:\s|[吗吧啊呢嘛呀哇])$", clause))
                current_duration = parent_duration * _spoken_weight(current) / parent_weight
                if (hint_boundary and (_char_count(current) >= cfg.preferred_min_chars
                                       or current_duration >= cfg.preferred_min_duration)):
                    groups.append(current)
                    current = ""
            if current:
                groups.append(current)

            safe_groups = []
            for group in groups or [piece]:
                group_duration = parent_duration * _spoken_weight(group) / parent_weight
                if (_char_count(group) <= cfg.soft_max_chars
                        and group_duration <= cfg.soft_max_duration):
                    safe_groups.append(group)
                    continue
                safe_groups.extend(self._split_at_atoms(group, parent_duration, parent_weight))
            result.extend(safe_groups)
        return [piece for piece in result if piece.strip()]

    def _split_at_atoms(self, piece, parent_duration, parent_weight):
        cfg = self.config
        atoms = _SPACE_OR_ATOM.findall(piece)
        groups = []
        current = ""
        for atom in atoms:
            trial = current + atom
            trial_duration = parent_duration * _spoken_weight(trial) / parent_weight
            should_cut = current and (_char_count(trial) > cfg.preferred_max_chars
                                      or trial_duration > cfg.preferred_max_duration)
            if should_cut:
                groups.append(current)
                current = atom
            else:
                current = trial
        if current:
            groups.append(current)
        return groups or [piece]

    @staticmethod
    def _allocate_weighted(parent, pieces):
        weights = [_spoken_weight(piece) for piece in pieces]
        total = sum(weights)
        duration = parent.end - parent.start
        cursor = parent.start
        children = []
        for index, (piece, weight) in enumerate(zip(pieces, weights)):
            end = (parent.end if index == len(pieces) - 1
                   else cursor + duration * weight / total)
            children.append(_copy_segment(parent, 1, cursor, end, piece))
            cursor = end
        return children
