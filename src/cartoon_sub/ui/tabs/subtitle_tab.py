from .common import page, table

def build():
    widget, layout = page("Bản dịch song ngữ. Cảnh báo thuật ngữ/xưng hô/nghĩa chưa chắc cần người kiểm tra; "
                          "không có cảnh báo không có nghĩa bản dịch chắc chắn đúng. Rê chuột lên ô để xem đầy đủ. Editor/timestamp thuộc phase 4.")
    widget.table = table(["ID", "Chinese", "Vietnamese", "Duration (s)", "QC"])
    layout.addWidget(widget.table)
    return widget
