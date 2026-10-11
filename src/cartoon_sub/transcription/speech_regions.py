"""Local energy-VAD speech regions for the Transcript pipeline."""
from dataclasses import dataclass
import audioop
import wave


VAD_VERSION = "energy-vad-v1"
FRAME_SECONDS = .03
MIN_SPEECH_SECONDS = .30
MIN_SILENCE_SECONDS = 1.00
PADDING_BEFORE_SECONDS = .40
PADDING_AFTER_SECONDS = .60
MERGE_GAP_SECONDS = 8.00
MAX_REGION_SECONDS = 40.0
FALLBACK_REGION_SECONDS = 30.0


@dataclass(frozen=True)
class SpeechRegion:
    start: float
    end: float
    source: str = "vad"


def detect_speech_regions(path):
    """Return padded, merged local speech-like regions; no network/model dependency."""
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Audio phải là WAV PCM 16-bit mono 16 kHz")
        rate, frames = audio.getframerate(), audio.getnframes()
        data = audio.readframes(frames)
    duration = frames / rate
    width = max(1, int(rate * FRAME_SECONDS))
    rms = [audioop.rms(data[index * width * 2:(index + 1) * width * 2], 2)
           for index in range((frames + width - 1) // width)]
    if not rms:
        return []
    floor = sorted(rms)[max(0, len(rms) // 5 - 1)]
    threshold = max(120, floor * 3)
    speech = [value >= threshold for value in rms]
    raw, start, silence = [], None, 0
    for index, active in enumerate(speech + [False]):
        if active and start is None:
            start = index
        if start is not None:
            silence = 0 if active else silence + 1
            if silence * FRAME_SECONDS >= MIN_SILENCE_SECONDS:
                end = index - silence + 1
                if (end - start) * FRAME_SECONDS >= MIN_SPEECH_SECONDS:
                    raw.append((start * FRAME_SECONDS, min(duration, end * FRAME_SECONDS)))
                start, silence = None, 0
    padded = [(max(0.0, left - PADDING_BEFORE_SECONDS), min(duration, right + PADDING_AFTER_SECONDS))
              for left, right in raw]
    merged = []
    for left, right in padded:
        if merged and left - merged[-1][1] <= MERGE_GAP_SECONDS:
            merged[-1] = (merged[-1][0], right)
        else:
            merged.append((left, right))
    result = []
    for left, right in merged:
        cursor = left
        while right - cursor > MAX_REGION_SECONDS:
            result.append(SpeechRegion(cursor, cursor + MAX_REGION_SECONDS))
            cursor += MAX_REGION_SECONDS
        if right > cursor:
            result.append(SpeechRegion(cursor, right))
    return result


def fallback_regions(duration):
    return [SpeechRegion(start, min(duration, start + FALLBACK_REGION_SECONDS), "vad_fallback")
            for start in range(0, int(duration), int(FALLBACK_REGION_SECONDS))]
