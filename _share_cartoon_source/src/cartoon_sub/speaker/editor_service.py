from dataclasses import asdict
from .models import Speaker
from .service import refresh_timeline


def new_speaker(project, name="Unknown"):
    number=1
    while f"SPK_{number:02d}" in project.speakers: number+=1
    key=f"SPK_{number:02d}"
    project.speakers[key]=asdict(Speaker(key,name))
    return key


def rename(project, key, name):
    if not name.strip(): raise ValueError("Tên speaker không được rỗng")
    project.speakers[key]["name"]=name.strip()
    refresh_timeline(project)


def assign(project, ids, key):
    if key not in project.speakers: raise ValueError("Speaker không tồn tại")
    selected=set(ids)
    if not selected or not selected.issubset({s.id for s in project.segments}): raise ValueError("Chọn ít nhất một utterance hợp lệ")
    for segment in project.segments:
        if segment.id in selected:
            segment.speaker_id=key
            segment.speaker_confidence=None
    refresh_timeline(project)


def merge(project, source, target):
    if source==target or source not in project.speakers or target not in project.speakers:
        raise ValueError("Chọn hai speaker khác nhau")
    ids=[s.id for s in project.segments if s.speaker_id==source]
    if ids: assign(project,ids,target)
    del project.speakers[source]
    refresh_timeline(project)


def split(project, ids):
    if not ids: raise ValueError("Chọn các dòng cần tách thành speaker mới")
    key=new_speaker(project)
    assign(project,ids,key)
    return key
