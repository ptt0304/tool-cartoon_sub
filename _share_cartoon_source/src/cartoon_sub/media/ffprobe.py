import json
from fractions import Fraction
from cartoon_sub.media.process import run_process

def probe(video, cancel=None, progress=None):
    if progress:
        progress("Reading video metadata…")
    raw = json.loads(run_process(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", video], cancel))
    streams = raw.get("streams", [])
    video_stream = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video_stream is None:
        raise ValueError("No video stream found")
    audio = next((s for s in streams if s["codec_type"] == "audio"), {})
    try:
        fps = float(Fraction(video_stream.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0
    return {"duration": float(raw.get("format", {}).get("duration", video_stream.get("duration", 0))),
            "width": video_stream.get("width", 0), "height": video_stream.get("height", 0),
            "fps": fps, "video_codec": video_stream.get("codec_name", "unknown"),
            "audio_codec": audio.get("codec_name", "none"),
            "subtitle_tracks": [{"index": s["index"], "codec": s.get("codec_name"), "tags": s.get("tags", {})}
                                for s in streams if s["codec_type"] == "subtitle"]}
