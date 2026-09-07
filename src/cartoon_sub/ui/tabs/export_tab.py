from .common import page

def build():
    widget, layout = page("Phase 3 tự lưu subtitle/zh.srt, subtitle/vi.srt, subtitle/segments.json và translation_review.json trong project. "
                          "Bản dịch chưa hoàn tất/cần cập nhật được ghi trạng thái trong project và review JSON. "
                          "Các phase sau bổ sung TXT/JSON cho TTS thủ công và render final.mp4. Không có TTS tự động.")
    layout.addStretch()
    return widget
