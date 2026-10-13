"""Non-modal, detachable end-user documentation for Cartoon Sub."""
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QTextBrowser, QLabel


TOPICS = (
    ("1. Giới thiệu", """
<h1>Cartoon Sub là gì?</h1><p><b>PHẠM THANH TÙNG - 0866891380</b></p>
<p>Cartoon Sub giúp làm video tiếng Trung có phụ đề Việt, lồng tiếng Việt và video xuất bản. Mỗi công việc nằm trong một <b>project</b>, vì vậy có thể đóng ứng dụng rồi mở lại để tiếp tục.</p>
<h2>Các khái niệm chính</h2><ul>
<li><b>Transcript</b>: lời tiếng Trung và mốc thời gian; đây là nguồn Master Timeline.</li>
<li><b>Utterance / Master Timeline</b>: từng câu nguồn có ID, Start/End, Chinese, VI Subtitle và VI Dubbing.</li>
<li><b>VI Subtitle</b>: câu Việt để người xem đọc. <b>VI Dubbing</b>: câu Việt cho Local_TTS đọc; có thể ngắn hơn để vừa thời lượng nhưng không thay Subtitle.</li>
<li><b>DisplaySegment</b>: phần hiển thị của một Utterance, dùng để chia câu dài mà không sửa nguồn.</li>
<li><b>Mask</b>: vùng che chữ gốc. <b>Style</b>: font, màu, viền, bóng và vị trí subtitle.</li>
<li><b>Dubbed Audio</b>: toàn bộ WAV TTS theo timeline. <b>Final Audio</b>: tiếng gốc, dubbed audio và audio bổ sung đã trộn.</li></ul>
<p><b>Cục bộ</b>: segmentation, mask, mix và render chạy trên máy. <b>AI/API</b>: STT, Video OCR, dịch, QA/QC AI và tối ưu dubbing dùng OpenRouter. Local_TTS là dịch vụ cục bộ, không phải OpenRouter.</p>
<p>Video render dùng subtitle hiện hành và <code>audio/final_audio.wav</code>. Chỉnh font/mask không đổi transcript hay bản dịch; chỉnh text nguồn có thể làm cache phía sau thành <b>STALE</b>.</p>"""),

    ("2. Project", """
<h1>Project</h1><p>Tại tab <b>Video</b>, bấm <b>Mở video / Tạo project</b> để chọn video nguồn và thư mục project. Mở lại qua <b>Project → Open project</b> rồi chọn đúng <code>project.json</code>.</p>
<h2>Dữ liệu project</h2><ul><li><code>project.json</code>: timeline, cấu hình và trạng thái.</li><li><code>audio/</code>: audio nguồn, WAV TTS, dubbed mix và final audio.</li><li><code>cache/</code>: kết quả STT/OCR/dịch/QA để Resume.</li><li><code>subtitle/</code>, <code>exports/</code>, <code>preview/</code>, <code>output/</code>: phụ đề và file tạo ra.</li></ul>
<p>Video nguồn được project <b>tham chiếu</b>, không sao chép. Đừng đổi tên/di chuyển video sau khi tạo project. Để sao lưu hoặc chuyển máy, sao chép cả thư mục project và giữ video nguồn có thể truy cập. API key không nằm trong <code>project.json</code>.</p>
<p>Mở project không tự gọi AI/render. Cache cũ vẫn hỗ trợ Resume; <b>STALE</b> nghĩa là dữ liệu còn tồn tại nhưng không còn khớp input hiện tại.</p>"""),

    ("3. Settings", """
<h1>Settings → AI</h1><p>Mọi AI production dùng <b>OpenRouter</b>. Tên trước dấu <code>/</code> trong Model ID là tác giả model, không phải provider phải chọn riêng.</p>
<h2>OpenRouter API Keys</h2><ul><li><b>Edit Keys…</b>: nhập một key mỗi dòng. Key không hiện đầy đủ trên UI.</li><li><b>Test All Keys</b>: kiểm tra key dùng được; không chạy STT, OCR hoặc dịch.</li><li>Key sai, không có quyền model hoặc hết quota sẽ làm tác vụ AI báo lỗi trước khi thay dữ liệu.</li></ul>
<h2>Catalog và model</h2><p><b>Search author</b>/<b>Author</b> lọc catalog; Search trong selector lọc theo tên/Model ID. Metadata bên dưới cho biết capability, context và thông tin model.</p>
<ul><li><b>Speech-to-Text / Transcription</b>: model mặc định cho Audio Transcript; chỉ dùng model từ catalog transcription.</li><li><b>Model AI mặc định</b>: dùng khi tab AI không có model override riêng.</li><li>Transcript và Translate có selector riêng. <b>Model hiệu lực</b> cho biết tab đang dùng override hay default.</li><li><b>Advanced</b>: Translation chunk size là số dòng mục tiêu một request; Retry count là số lần thử lỗi tạm thời.</li></ul>
<p>Đổi model có thể làm cache AI không khớp. Dữ liệu cũ không bị xóa tự động; chỉ chạy lại/xóa cache đúng phạm vi khi muốn kết quả mới.</p>"""),

    ("4. Transcript", """
<h1>Transcript: Chinese Master Timeline</h1><p><b>Nguồn transcript</b> chỉ chọn cách lấy dữ liệu; chỉ bấm Run hoặc Import mới thay Master Timeline.</p>
<h2>Audio</h2><p><b>Model STT</b> chọn model transcription. <b>Run Chinese Transcript</b> trích/chia audio cục bộ, gọi OpenRouter STT khi cache không dùng được, rồi tạo timeline Chinese.</p>
<ul><li><b>AI xử lý ranh giới câu mơ hồ</b>: bước sau STT chỉ cho vùng chưa rõ. Status nói rõ không có vùng mơ hồ, số vùng đã xử lý, hoặc cache/API đã dùng.</li><li><b>Xóa dữ liệu AI / Chạy lại</b>: xóa dữ liệu semantic-boundary. <b>Xóa cache Audio Transcript / Chạy lại</b>: xóa cache STT/semantic Audio, không xóa ROI Video.</li><li><b>Transcript QA</b>: kiểm tra kết quả. <b>NEEDS_MANUAL_REVIEW</b> nghĩa là cần nghe/đối chiếu, không phải transcript đã được AI bảo đảm đúng.</li></ul>
<h2>Video</h2><p>Chọn <b>Video</b> để đọc phụ đề trong hình. <b>Chọn vùng phụ đề Video…</b> mở ROI editor: kéo để di chuyển, kéo cạnh/góc để resize. ROI lưu theo tỉ lệ khung hình nên không lệch khi preview đổi kích thước.</p>
<ul><li>Detector local quét <b>10 fps</b>, refine ranh giới <b>25 fps</b>; bước này <b>không gọi AI</b>.</li><li><b>Model Video / OCR</b> đọc một frame đại diện cho mỗi sự kiện; OCR <b>gọi OpenRouter</b>. Timing refined từ detector sở hữu Start/End.</li><li><b>Video Detection cache</b> lưu event local; <b>Video OCR cache</b> lưu OCR. <b>PENDING</b> là đã có workload nhưng OCR chưa hoàn tất.</li><li><b>Xóa cache Video / Chạy lại</b> xóa dữ liệu Video tạo ra nhưng giữ ROI người dùng. <b>QA/QC transcript từ video</b> gộp exact/near duplicate hợp lệ, đánh lại ID liên tiếp và lưu snapshot. <b>Hủy QA/QC</b> khôi phục snapshot.</li></ul>
<h2>Import Chinese SRT</h2><p>Dùng khi đã có SRT Trung đáng tin cậy. <b>Import Chinese SRT</b> không gọi AI/STT/OCR; text/timestamp SRT trở thành Master Timeline.</p>
<h2>Cache và export</h2><p><b>NONE</b>: chưa có; <b>CACHED</b>: có thể reuse; <b>PENDING</b>: chờ OCR; <b>STALE</b>: còn file nhưng không khớp input. <b>Export Transcript SRT</b> xuất timeline Chinese hiện hành, không gọi AI.</p>"""),

    ("5. Translate", """
<h1>Translate</h1><h2>Ngữ cảnh văn phong</h2><p><b>Model AI cho tab</b> là model override Translate; nếu không có thì dùng Default AI Model. <b>Nguồn translate</b> chọn snapshot <b>Audio</b>, <b>Video</b> hoặc <b>SRT</b>; đổi selector không tự dịch lại và không đổi canonical Transcript. <b>Xóa cache Translate Audio</b> xóa cache nguồn đang chọn để lần sau gọi AI lại.</p>
<ul><li><b>Thể loại chính — chọn tối đa 3</b>: hướng dẫn AI về thế giới truyện/thuật ngữ/cách xưng hô.</li><li><b>Văn phong dịch</b>: cách viết tiếng Việt; mô tả dưới selector giải thích từng lựa chọn.</li><li><b>Quy tắc tên riêng</b>: Hán Việt, giữ theo nguồn hoặc tùy chỉnh. Mapping người dùng được ưu tiên.</li><li><b>Người dùng tự định nghĩa</b>: Thể loại chính, Văn phong chính, Quy tắc tên riêng là hướng dẫn bổ sung.</li><li><b>Dịch</b>: AI/OpenRouter dịch nguồn đang chọn, reuse cache khi input không đổi.</li><li><b>QA/QC bản dịch</b>: kiểm tra local toàn bộ; chỉ có thể gọi AI cho dòng lỗi/đáng nghi.</li><li><b>Import Vietnamese SRT</b>: ghép SRT Việt theo timestamp; không gọi AI. Dòng không ghép được/mơ hồ được báo thay vì đoán.</li></ul>
<p>Đổi genre/style/rule/model/text nguồn làm Translation/QA có thể STALE, nhưng không đổi Chinese Master Timeline.</p>
<h2>Master dialogue timeline</h2><p>View <b>Both</b>/<b>Subtitle</b>/<b>Dubbing</b> đổi cách xem. Bảng hỗ trợ tìm kiếm, cuộn, kéo cột và xuống dòng.</p>
<p><b>ID</b>: câu nguồn; <b>Start/End/Duration</b>: mốc giây; <b>Chinese</b>: transcript; <b>ZH Syl</b>: đếm nguồn; <b>VI Subtitle</b>: text màn hình; <b>VI Dubbing</b>: text TTS; <b>VI Syl</b>/<b>Target</b>/<b>Δ target</b>: độ dài đọc; <b>Mode</b>: chiến lược; <b>QC</b>: cảnh báo. Màu xanh = khớp target, vàng = lệch nhỏ, đỏ = lệch lớn/mode nghiêm ngặt không đạt.</p>
<h2>Thao tác theo dòng</h2><ul><li><b>Sửa câu chọn / mode / target</b>: sửa nội dung/mode/target override. Timestamp chỉ đổi khi tự sửa.</li><li><b>Dịch lại dòng đã chọn</b>: sau xác nhận xóa VI Subtitle/VI Dubbing/cache đúng ID, rồi dịch lại; dòng khác/timestamp giữ nguyên.</li><li><b>Tối ưu dubbing đã chọn</b> và <b>Tối ưu dubbing hàng loạt</b>: AI rút/gọt <b>VI Dubbing</b> theo target; không đổi Chinese, VI Subtitle hoặc timestamp.</li><li><b>QA/QC AI dòng đã chọn</b>: AI review các dòng chọn; có thể sửa VI Subtitle nếu kết quả hợp lệ.</li><li><b>Revert selected dubbing optimization</b>: khôi phục VI Dubbing trước tối ưu khi có snapshot.</li><li>Nhấp đúp ô editable, bấm <b>Áp dụng bản sửa tay</b> để commit; <b>Hoàn tác sửa tay</b> hoàn tác batch sửa tay.</li><li><b>Export Translate SRT</b>: xuất VI Subtitle hiện hành, không gọi AI.</li></ul>
<p>Đổi VI Subtitle/Dubbing làm DisplaySegment và TTS/audio downstream liên quan STALE.</p>"""),

    ("6. Subtitle", """
<h1>Subtitle: cách hiển thị bản Việt</h1><p><b>Utterance</b> là câu nguồn. <b>DisplaySegment</b> là phần hiển thị rút từ câu đó. Segmentation không sửa transcript, Chinese, VI Subtitle/VI Dubbing nguồn, hay timestamp overlap giữa các câu.</p>
<h2>Nguồn/cấu hình</h2><ul><li><b>Nguồn nội dung phụ đề</b>: VI Subtitle hoặc VI Dubbing làm text presentation.</li><li><b>Cấu hình</b>: Balanced, Reading Comfort, Fast Dialogue, Preserve Sentences, Custom. <b>Áp dụng cài đặt</b> lưu profile/giới hạn Custom.</li><li>Preferred/Max duration, syllables, lines, chars per line là tín hiệu đọc/QC; không tự viết lại bản dịch.</li><li><b>Lọc</b>: Tất cả QC hoặc Có cảnh báo.</li></ul>
<h2>Thao tác</h2><ul><li><b>Auto Segment All</b>: chia toàn bộ câu dài local.</li><li><b>Auto Segment Selected</b>: chỉ Utterance được chọn.</li><li><b>Split Manually</b>: chọn vị trí chia.</li><li><b>Merge Selected</b>: gộp segment hợp lệ cùng Utterance.</li><li><b>Reset To Utterance</b>: bỏ các split của câu chọn, tạo một DisplaySegment từ text nguồn hiện hành.</li><li><b>Export Subtitle SRT</b>: xuất DisplaySegment; đồng bộ presentation stale trước khi xuất.</li></ul>
<p><b>AI/API: NO.</b> QC có thể là OK, TOO_LONG, TOO_SHORT, TOO_MANY_SYLLABLES, TOO_MANY_LINES, HIGH_READING_SPEED, BAD_SPLIT hoặc MANUAL_REVIEW. Cảnh báo không tự sửa text.</p>"""),

    ("7. Mask", """
<h1>Mask và style subtitle</h1><p>Mask che chữ gốc và đặt subtitle Việt. <b>AI/API: NO.</b> Preview/render dùng FFmpeg local, không sửa Master Timeline.</p>
<h2>Vùng mask</h2><p>Nhập thời điểm → <b>Lấy khung hình</b> → kéo rectangle. Kéo vùng để di chuyển, kéo cạnh/góc để resize. X/Y/Width/Height là pixel video gốc; ROI chuẩn hóa giữ tương quan khi preview đổi kích thước.</p>
<ul><li><b>Bật vùng che</b>: bật/tắt khi render.</li><li><b>Kiểu mask</b>: <b>Solid</b> tô vùng; <b>Gaussian</b> làm mờ; <b>No mask</b> không che.</li><li><b>Mask color</b> dùng cho Solid; <b>Blur strength</b> điều khiển Gaussian.</li><li><b>Mask geometry</b>: <b>Auto-fit theo text</b> = khung text + padding ngang/dọc; <b>Toàn bộ ROI</b> = cả rectangle. Mask chỉ hiện khi subtitle active.</li></ul>
<h2>Style, logo, watermark</h2><p>Chỉnh Font, size, Đậm, text color, Viền/outline color, Bóng, vị trí/lề dọc và số dòng tối đa. Tick căn giữa vùng mask để đặt khối subtitle ở giữa ROI active.</p>
<ul><li><b>Load logo ảnh</b>: thêm nhiều logo; chọn logo rồi chỉnh X/Y/rộng/cao/xoay/scale/trong suốt hoặc kéo trực tiếp. <b>Logo Scale</b> là phần trăm kích thước gốc: 100% là gốc, 200% là gấp đôi. Giá trị âm hiện dùng độ lớn tuyệt đối; không tạo hiệu ứng lật ảnh. <b>Xóa logo chọn</b> chỉ xóa logo hiện chọn; phím Delete trên canvas cũng xóa logo đang chọn.</li><li><b>Watermark text</b>: chữ chạy và phản xạ ở cạnh khung hình. Font/size/đậm/viền/bóng/trong suốt/tốc độ cập nhật preview.</li></ul>
<p><b>Tạo preview 10 giây</b> kiểm tra nhanh; <b>Render toàn bộ MP4</b> render Mask workflow; <b>Lưu Mask / Style</b> lưu vào project.</p>"""),

    ("8. Audio", """
<h1>Audio và Local_TTS</h1><h2>Local_TTS</h2><ul><li><b>Local_TTS URL</b>: địa chỉ dịch vụ TTS local.</li><li><b>Test connection</b>: kiểm tra dịch vụ/tải voice READY.</li><li><b>Auto Start</b>: thử khởi động TTS đã cấu hình. <b>Select Local_TTS…</b>: chọn executable.</li><li><b>Giọng đọc</b>: một voice cho toàn project. <b>Preview selected voice</b>: nghe thử.</li><li><b>Nguồn TTS</b>: VI Subtitle hoặc VI Dubbing; Dubbing thường hợp thời lượng hơn.</li></ul>
<h2>Tạo/build audio</h2><ul><li><b>Generate / Resume TTS</b>: tạo/reuse WAV từng Utterance. Gọi Local_TTS, không gọi OpenRouter.</li><li><b>Build Dubbed Audio</b>: đặt WAV tại Start, giữ overlap, tạo <code>audio/tts/dubbed_mix.wav</code>. Sau khi gom segment, master TTS gain/limiter chạy đúng một lần.</li><li><b>Build Final Audio</b>: trộn original + dubbed + additional thành <code>audio/final_audio.wav</code>.</li><li><b>Play Final Audio</b>: phát/dừng final audio.</li></ul>
<p>Generated = WAV generated/cached; Sync OK = vừa slot; Auto-fit = chỉnh tempo nhẹ khi mix; Needs review = cần kiểm tra timing; overlap vẫn được mix theo timestamp.</p>
<h2>Xóa cache</h2><ul><li><b>TTS — VI Subtitle</b>: chỉ cache/WAV dùng Subtitle.</li><li><b>TTS — VI Dubbing</b>: chỉ cache/WAV dùng Dubbing.</li><li><b>Tất cả TTS</b>: tất cả WAV và downstream.</li><li><b>Dubbed Audio</b>: dubbed mix/final audio, giữ WAV TTS.</li><li><b>Toàn bộ Audio downstream</b>: TTS, dubbed mix, final audio và preview liên quan.</li></ul>
<p><b>NONE</b> chưa tạo; <b>CACHED</b> khớp input; <b>STALE</b> là file còn nhưng text/voice/speed/source đã đổi. Đổi volume/additional audio không cần Generate lại, nhưng cần <b>Build Final Audio</b>.</p>
<h2>Audio Mixer</h2><p><b>Original Audio Volume</b>, <b>Dubbed Audio Volume</b> và <b>Additional Audio Volume</b> là 0–100%. <b>Browse</b> chọn additional audio, <b>Clear</b> bỏ nó; <b>Start Offset (seconds)</b> là số giây từ đầu timeline.</p>"""),

    ("9. Export", """
<h1>Export</h1><p>Export dùng style/mask/subtitle hiện hành và <code>audio/final_audio.wav</code>. <b>AI/API: NO.</b></p><ul><li><b>Vị trí bắt đầu (giây)</b> và <b>Test 30 seconds</b>: render đoạn tối đa 30 giây để kiểm tra.</li><li><b>Render Video</b>: render toàn bộ video. Nếu Final Audio missing/stale, quay lại Audio và Build Final Audio.</li><li>Status dưới nút hiển thị output. Render không cần chạy STT/dịch lại.</li></ul><p>SRT được export tại tab dữ liệu sở hữu: Transcript, Translate hoặc Subtitle.</p>"""),

    ("10. Lỗi thường gặp", """
<h1>Lỗi thường gặp</h1><h2>OpenRouter key/model</h2><p><b>Triệu chứng:</b> 401/403/404/429. <b>Nguyên nhân:</b> key sai, không có quyền model, ID unavailable hoặc quota/rate limit. <b>Cách xử lý:</b> Settings → Edit Keys → Test All Keys; chọn model compatible; chờ khi 429.</p>
<h2>Video OCR/ROI</h2><p><b>Triệu chứng:</b> OCR PENDING, ít phụ đề, timing sai. <b>Nguyên nhân:</b> ROI/time range sai hoặc OCR chưa chạy. <b>Cách xử lý:</b> chỉnh ROI/time range, chạy Video Transcript; chỉ xóa Video cache khi muốn phát hiện/OCR lại. QA/QC Video có thể Hủy QA/QC.</p>
<h2>Local_TTS/audio</h2><p><b>Triệu chứng:</b> không có voice, Generate lỗi, TTS stale, DUBBED_AUDIO_NOT_FOUND/FINAL_AUDIO_STALE. <b>Nguyên nhân:</b> Local_TTS không chạy, voice chưa READY, source/voice/speed đổi hoặc chưa build bước trước. <b>Cách xử lý:</b> Test connection/Auto Start, chọn voice READY, Generate → Build Dubbed → Build Final.</p>
<h2>Mask/subtitle/project</h2><p><b>Triệu chứng:</b> sub dài/mask lệch/không thấy, project/video/SRT không load. <b>Nguyên nhân:</b> source Subtitle khác dự kiến, chưa segment, không có subtitle active, video bị di chuyển hoặc timestamp SRT không khớp. <b>Cách xử lý:</b> kiểm tra nguồn/DisplaySegment, Auto Segment hoặc sửa tay; lấy frame đúng mốc/chỉnh ROI; khôi phục source video và mở đúng project.json.</p>"""),

    ("11. Quy trình", """<p>Phần này sẽ được bổ sung sau.</p>"""),
)


class DocsTopicWindow(QMainWindow):
    def __init__(self, title, html, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Cartoon Sub Docs — {title}")
        self.resize(760, 650)
        browser = QTextBrowser()
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)
        self.setCentralWidget(browser)


class DocsWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cartoon Sub — Docs")
        self.resize(620, 520)
        self.topic_windows = []
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addWidget(QLabel("Bấm một tab để mở tài liệu trong cửa sổ riêng. Có thể mở nhiều chủ đề song song với tool."))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(host)
        for title, html in TOPICS:
            preview = QTextBrowser()
            preview.setHtml(html)
            preview.setOpenExternalLinks(True)
            self.tabs.addTab(preview, title)
        self.tabs.tabBarClicked.connect(self.open_topic)

    def open_topic(self, index):
        if index < 0:
            return
        title, html = TOPICS[index]
        window = DocsTopicWindow(title, html, self.parent())
        window.show()
        window.raise_()
        window.activateWindow()
        self.topic_windows.append(window)
        window.destroyed.connect(lambda *_: self.topic_windows.remove(window) if window in self.topic_windows else None)
