"""Derived snapshots only; exporting never edits or serializes the master timeline."""
import json
import re
import unicodedata
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from cartoon_sub.subtitle.models import Project
from cartoon_sub.subtitle.parser import export_srt
from cartoon_sub.syllable.vietnamese import count_syllables
from cartoon_sub.speaker.service import refresh_timeline, review_complete
from cartoon_sub.project.cache import atomic_json, check_cancel


def safe_name(name):
    name=unicodedata.normalize("NFKD",name).encode("ascii","ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+","_",name).strip("_")[:48] or "Unknown"


def export_speakers(project, directory, text_type="vi_dubbing", cancel=None, progress=None):
    if text_type not in ("vi_subtitle","vi_dubbing"): raise ValueError("Chọn Subtitle hoặc Dubbing")
    project=Project.from_dict(project.to_dict())
    refresh_timeline(project)
    if not review_complete(project): raise ValueError("Cần duyệt speaker trước khi export")
    if any(not getattr(s,text_type).strip() for s in project.segments): raise ValueError("Còn câu chưa có bản Việt; chưa export")
    root=Path(directory)/"tts_export"/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8])
    root.mkdir(parents=True,exist_ok=False)
    # Versioned snapshots avoid stale utterance TXT files after speaker reassignment.
    atomic_json(root/"export_status.json",{"status":"running","text_type":text_type})
    try:
        groups={}
        for segment in project.segments: groups.setdefault(segment.speaker_id,[]).append(segment)
        for speaker_id,segments in groups.items():
            check_cancel(cancel)
            name=project.speakers[speaker_id]["name"]
            folder=root/(speaker_id+"_"+safe_name(name))
            folder.mkdir()
            segment_folder=folder/"segments"
            segment_folder.mkdir()
            segments=sorted(segments,key=lambda s:(s.start,s.id))
            export_srt(segments,folder/"speaker.srt",text_type)
            records=[]
            for local_id,s in enumerate(segments,1):
                check_cancel(cancel)
                text=getattr(s,text_type)
                filename=f"{s.id:06d}.txt"
                (segment_folder/filename).write_text(text,encoding="utf-8")
                records.append({"id":s.id,"srt_index":local_id,"start":s.start,"end":s.end,"duration":s.duration,
                    "text":text,"text_file":"segments/"+filename,"target_syllables":s.target_syllables,
                    "actual_syllables":count_syllables(text),"overlap":s.overlap,"overlap_group":s.overlap_group,
                    "translation_mode":s.translation_mode,"dubbing_status":s.dubbing_status,
                    "semantic_compression":s.semantic_compression})
            atomic_json(folder/"manifest.json",{"speaker_id":speaker_id,"speaker_name":name,"text_type":text_type,"segments":records})
        atomic_json(root/"export_status.json",{"status":"completed","text_type":text_type,"speaker_count":len(groups)})
        if progress: progress(f"Đã export {len(groups)} speaker: {root}")
        return root
    except Exception:
        atomic_json(root/"export_status.json",{"status":"incomplete","text_type":text_type})
        raise
