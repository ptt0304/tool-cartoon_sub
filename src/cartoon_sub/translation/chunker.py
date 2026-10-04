def source_rows(segments):
    return [{"id": s.id, "zh": s.zh, "speaker_id":s.speaker_id,"speaker_name":s.speaker_name,
             "start":s.start,"end":s.end,"duration":s.duration,"zh_syllables":s.zh_syllables,
             "target_syllables":s.target_syllables,"translation_mode":s.translation_mode,
             "voice_id":s.dubbing_voice_id,"estimated_syllables_per_second":s.dubbing_estimated_rate,
             "available_duration":s.dubbing_budget_duration} for s in segments]


def batches(rows, max_rows, max_chars=16000):
    """Bound both row count and characters; do not silently truncate a long row."""
    batch = []
    chars = 0
    for row in rows:
        size = len(row["zh"])
        if size > max_chars:
            raise ValueError(f"Subtitle {row['id']} quá dài; cần tách trước khi dịch")
        if batch and (len(batch) >= max_rows or chars + size > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(row)
        chars += size
    if batch:
        yield batch


def translation_batches(segments, chunk_size, *, max_duration=45.0, gap_seconds=3.0,
                        max_chars=6000, neighbor_count=5):
    """Adaptive conversation batches; gaps/duration can close a group before row limit."""
    rows = source_rows(segments)
    groups, current, chars = [], [], 0
    for row in rows:
        row_chars = len(row["zh"])
        if row_chars > max_chars:
            raise ValueError(f"Subtitle {row['id']} quá dài; cần tách trước khi dịch")
        gap = (float(row["start"]) - float(current[-1]["end"])) if current else 0.0
        duration = (float(row["end"]) - float(current[0]["start"])) if current else 0.0
        should_close = bool(current) and (
            len(current) >= chunk_size or chars + row_chars > max_chars
            or gap > gap_seconds or duration > max_duration
        )
        if should_close:
            groups.append(current)
            current, chars = [], 0
        current.append(row)
        chars += row_chars
    if current:
        groups.append(current)

    offsets = {row["id"]: index for index, row in enumerate(rows)}
    for target in groups:
        start = offsets[target[0]["id"]]
        end = offsets[target[-1]["id"]] + 1
        yield (target, rows[max(0, start - neighbor_count):start],
               rows[end:end + neighbor_count])
