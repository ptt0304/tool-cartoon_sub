from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit


def normalize_local_tts_base_url(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise ValueError("Local_TTS URL must be text")
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("Local_TTS URL must use http:// or https:// with a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Local_TTS URL has an invalid port") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    authority = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, authority, path, "", ""))


def build_tts_fingerprint(utterance, speaker, server_base_url: str) -> str:
    value = {
        "version": 2,
        "server": normalize_local_tts_base_url(server_base_url),
        "utterance_id": utterance.id,
        "speaker_id": utterance.speaker_id,
        "text": utterance.vi_dubbing,
        "voice_id": speaker.tts_voice_id,
        "speed": float(speaker.tts_speed),
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_tts_segment_id(utterance, fingerprint: str) -> str:
    return f"utt_{utterance.id:06d}_{fingerprint[:12]}"
