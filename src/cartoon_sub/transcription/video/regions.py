"""Validated temporal subtitle regions; legacy single ROI remains readable."""
from pathlib import Path
from cartoon_sub.transcription.mode import normalize_roi


def normalize_regions(rows, duration):
    if not isinstance(rows, list):
        raise ValueError("Danh sách vùng phụ đề Video không hợp lệ")
    duration = float(duration or 0)
    result, ids = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError("Vùng phụ đề Video không hợp lệ")
        identifier = str(row.get("id", "")).strip()
        if not identifier or identifier in ids: raise ValueError("ID vùng phụ đề Video phải duy nhất")
        start, end = row.get("start_time"), row.get("end_time")
        if type(start) not in (int,float) or type(end) not in (int,float) or not 0 <= start < end <= duration:
            raise ValueError("Thời gian vùng phụ đề Video không hợp lệ")
        ids.add(identifier); result.append({"id":identifier,"start_time":float(start),"end_time":float(end),"roi":normalize_roi(row.get("roi"))})
    result.sort(key=lambda row:(row["start_time"],row["end_time"],row["id"]))
    for first, second in zip(result,result[1:]):
        if second["start_time"] < first["end_time"]: raise ValueError("Các vùng phụ đề Video không được chồng thời gian")
    return result


def regions_for_project(project):
    regions = normalize_regions(getattr(project,"video_subtitle_regions",[]), project.metadata.get("duration"))
    if regions: return regions
    roi = normalize_roi(getattr(project,"video_subtitle_roi",{}))
    if roi:
        return [{"id":"region_001","start_time":0.0,"end_time":float(project.metadata.get("duration",0)),"roi":roi}]
    return []


def video_fingerprint(source):
    """Cheap immutable identity used only for Video visual/OCR cache ownership."""
    path = Path(source).resolve()
    if not path.is_file():
        return {"path": str(path), "missing": True}
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
