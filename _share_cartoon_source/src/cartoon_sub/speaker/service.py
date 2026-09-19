from dataclasses import asdict
from .models import Speaker
from cartoon_sub.syllable.target import DubbingSettings
from cartoon_sub.project.cache import content_hash


def detect_overlaps(segments):
    """Connected interval components; never change times or merge utterances."""
    for s in segments:
        s.overlap, s.overlap_group = False, None
    group, end, number = [], -1, 0
    def finish(rows, number):
        if len(rows) > 1:
            number += 1
            for row in rows:
                row.overlap = True
                row.overlap_group = f"OVL_{number:03d}"
        return number
    for segment in sorted(segments, key=lambda s:(s.start,s.end,s.id)):
        if group and segment.start >= end:
            number=finish(group,number)
            group=[]
        group.append(segment)
        end=segment.end if len(group)==1 else max(end,segment.end)
    finish(group,number)


def refresh_timeline(project):
    config=DubbingSettings(**project.dubbing_settings).validate()
    project.dubbing_settings=config.to_dict()
    for key, value in project.speakers.items():
        speaker=Speaker(**value)
        if speaker.id != key: raise ValueError("Speaker registry ID mismatch")
    for segment in project.segments:
        segment.validate()
        if segment.speaker_id not in project.speakers:
            project.speakers[segment.speaker_id]=asdict(Speaker(segment.speaker_id,segment.speaker_name))
        segment.speaker_name=project.speakers[segment.speaker_id]["name"]
        segment.recalculate(config)
    detect_overlaps(project.segments)


def review_hash(project):
    return content_hash([{"id":s.id,"start":s.start,"end":s.end,"zh":s.zh,
                         "speaker_id":s.speaker_id,"speaker_name":s.speaker_name} for s in project.segments])


def review_complete(project):
    return bool(project.segments) and all(s.speaker_id != "SPK_UNKNOWN" for s in project.segments) and project.speaker_review_hash==review_hash(project)


def approve_review(project):
    refresh_timeline(project)
    if any(s.speaker_id=="SPK_UNKNOWN" for s in project.segments):
        raise ValueError("Còn dòng chưa gán speaker. Chọn các dòng và gán SPK_01… trước khi xác nhận.")
    project.speaker_review_hash=review_hash(project)


def speaker_counts(project):
    return {key:sum(s.speaker_id==key for s in project.segments) for key in project.speakers}
