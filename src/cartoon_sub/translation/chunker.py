def source_rows(segments):
    return [{"id": s.id, "zh": s.zh, "speaker_id":s.speaker_id,"speaker_name":s.speaker_name,
             "start":s.start,"end":s.end,"duration":s.duration,"zh_syllables":s.zh_syllables,
             "target_syllables":s.target_syllables,"translation_mode":s.translation_mode} for s in segments]


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


def translation_batches(segments, chunk_size):
    rows = source_rows(segments)
    offset = 0
    for target in batches(rows, chunk_size, 8000):
        end = offset + len(target)
        yield target, rows[max(0, offset - 5):offset], rows[end:end + 5]
        offset = end
