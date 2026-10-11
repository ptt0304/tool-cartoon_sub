"""Temporary, secret-safe runtime tracing for AI model routing."""
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


_LOCK = Lock()
_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "ai_runtime_debug.log"


def ai_debug(scope, **values):
    """Append one flushed diagnostic line without accepting credential fields."""
    safe = []
    for key, value in values.items():
        if any(token in key.casefold() for token in ("key", "secret", "token", "prompt")):
            continue
        safe.append(f"{key}={value!r}")
    line = (f"{datetime.now(timezone.utc).isoformat()} [AI-DEBUG][{scope}] "
            + " ".join(safe) + "\n")
    with _LOCK:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
