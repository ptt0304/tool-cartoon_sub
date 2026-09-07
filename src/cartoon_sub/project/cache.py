"""Small atomic local stores, with no credentials in their payloads."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from cartoon_sub.media.process import CancelledError


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise CancelledError("Đã hủy tác vụ")


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def file_hash(path, cancel=None):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
    check_cancel(cancel)
    return digest.hexdigest()


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
