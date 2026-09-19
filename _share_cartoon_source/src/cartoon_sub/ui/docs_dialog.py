"""Non-modal, detachable help windows for people new to Cartoon Sub."""
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QTextBrowser, QLabel


TOPICS = (
    ("1. Giới thiệu", """
<h1>Cartoon Sub là gì?</h1><p><b>PHẠM THANH TÙNG - 0866891380</b></p>
<p>Cartoon Sub chuyển video tiếng Trung thành video có phụ đề Việt. Tool lưu toàn bộ công việc theo một project để có thể đóng/mở và tiếp tục.</p>
<h2>Khái niệm</h2><ul><li><b>Transcript</b>: lời thoại tiếng Trung và thời điểm nói.</li><li><b>Translate</b>: bản dịch Việt theo thể loại, nhân vật và xưng hô.</li><li><b>Subtitle</b>: cách chia bản dịch thành các đoạn ngắn để người xem đọc.</li><li><b>Dubbing</b>: bản Việt tối ưu riêng cho thời lượng đọc/giọng, không thay bản subtitle.</li><li><b>Mask</b>: vùng che chữ Trung trên ảnh; <b>Style</b>: font, viền, bóng và vị trí chữ Việt.</li><li><b>Export</b>: xuất dữ liệu theo speaker để làm TTS hoặc biên tập ngoài.</li></ul>
<p>Ví dụ: câu tiếng Trung dài 8 giây được Transcript ghi lại; Translate dịch thành một câu Việt; Subtitle tách thành hai DisplaySegment dễ đọc; Mask che chữ gốc rồi render MP4.</p>"""),
    ("2. Project", """
<h1>Thư mục project</h1><p>Tạo project mới tại tab <b>Video</b>: chọn video nguồn và chọn thư mục project. Video không được tự sao chép, vì vậy đừng đổi vị trí video nguồn.</p>
<ul><li><code>project.json</code>: file chính chứa timeline, speaker, bản dịch, mask, style, logo, watermark và trạng thái công việc.</li><li><code>audio/source.wav</code>: audio trích từ video khi cần AI transcription.</li><li><code>subtitle/zh.srt</code>, <code>subtitle/vi.srt</code>: phụ đề Trung và Việt.</li><li><code>cache/</code>: kết quả AI từng bước để bấm tiếp tục sau lỗi; không nên tự sửa.</li><li><code>preview/</code>: preview 10 giây; <code>output/</code>: MP4 render hoàn chỉnh.</li></ul>
<p>Mở project có sẵn: chọn <b>Project → Open project</b>, rồi mở đúng file <code>project.json</code>. Để sao lưu, giữ nguyên toàn bộ thư mục project và video nguồn.</p>"""),
    ("3. Settings & AI", """
<h1>Settings và AI API</h1><p>Mở <b>Settings → AI</b>. API key được lưu trong Windows Credential Manager/keyring, không nằm trong project.json.</p>
<h2>Gemini cho audio</h2><p><b>Gemini API key</b> và <b>Transcription model</b> dùng cho tạo transcript và căn timing audio. Lấy key từ Google AI Studio, dán key, bấm Test Gemini transcription rồi Save. Request audio có thể dùng quota.</p>
<h2>AI cho text</h2><p>Provider dịch/ngữ cảnh áp dụng cho phân tích ngữ cảnh, dịch, tối ưu dubbing text và semantic fallback. Chọn provider, nhập Translation API key, chọn model rồi Save.</p>
<p>Tool có model gợi ý cho: Google Gemini, OpenAI, Anthropic Claude, OpenRouter, DeepSeek, Groq, Mistral, Together, Fireworks và xAI Grok. Có thể gõ model khác nếu key của bạn có quyền.</p>
<p><b>Test provider dịch/ngữ cảnh</b> gửi một request text nhỏ. Translation chunk size 30–50: giảm về 30 nếu AI trả JSON thiếu; Retry count là số lần thử lại sau lỗi mạng/429/5xx.</p>
<p>AI đề xuất nội dung; người dùng vẫn kiểm tra transcript, speaker, glossary, xưng hô và câu dịch trước khi render.</p>"""),
    ("4. Transcript", """
<h1>Transcript tiếng Trung</h1><p>Transcript xác định <b>ai nói gì, vào lúc nào</b>. Có hai cách:</p><ol><li><b>Import Chinese SRT</b>: chọn SRT Trung có sẵn, không gọi AI.</li><li><b>Gemini: tạo / tiếp tục</b>: tool trích audio cục bộ, gửi từng đoạn audio sang Gemini và nhận JSON thời điểm + tiếng Trung.</li></ol>
<p>Nếu lỗi hoặc dừng giữa chừng, bấm lại cùng nút để tiếp tục cache. AI có thể sai từ hoặc lệch mốc; kiểm tra các câu quan trọng bằng nghe audio.</p>
<h2>Speaker review</h2><p><code>SPK_UNKNOWN</code> là giọng chưa đủ tin cậy để tự gán. Chọn các dòng cùng giọng, chọn speaker đích rồi <b>Gán dòng chọn</b>. Dùng <b>Nghe dòng chọn</b> để kiểm tra. Xác nhận speaker trước khi dịch vì thông tin speaker giúp giữ xưng hô nhất quán.</p>"""),
    ("5. Translate", """
<h1>Dịch Trung–Việt</h1><p>Chọn thể loại và văn phong trước khi dịch. Ví dụ phim tu tiên hài: chọn <b>Tu tiên/huyền huyễn</b> và <b>Hài/châm biếm</b>; glossary có thể ghi <code>筑基 -&gt; Trúc Cơ</code>.</p>
<p><b>Phân tích ngữ cảnh bằng AI</b> đọc transcript chữ, đề xuất nhân vật, thuật ngữ, quan hệ và quy tắc xưng hô. AI không nghe lại audio ở bước này. Bấm <b>Duyệt đề xuất AI</b> hoặc <b>Hồ sơ đang áp dụng / tự nhập</b>, kiểm tra rồi Áp dụng.</p>
<p><b>Dịch / tiếp tục</b> gửi từng nhóm câu sang provider text đã chọn. Tool kiểm tra JSON/ID; khi AI lỗi, bản dịch cũ được giữ.</p>
<h2>Master dialogue timeline</h2><p>Chọn <b>Both</b> để xem cả hai bản Việt; <b>Subtitle</b> để ưu tiên bản hiển thị; <b>Dubbing</b> để ưu tiên bản đọc/lồng tiếng. Kéo tiêu đề cột để đổi độ rộng; chữ dài tự xuống dòng và tăng chiều cao hàng.</p>
<ul><li><b>ID</b>: số câu Utterance.</li><li><b>Start / End / Duration</b>: mốc bắt đầu, kết thúc và thời lượng câu theo giây.</li><li><b>Speaker</b>: ID và tên giọng đã duyệt.</li><li><b>Chinese</b>: transcript tiếng Trung nguồn; không tự đổi khi sửa bản Việt.</li><li><b>ZH Syl</b>: số đơn vị/âm tiết ước lượng của nguồn, dùng tham khảo.</li><li><b>VI Subtitle</b>: bản Việt ưu tiên người xem đọc, tự nhiên và đủ nghĩa.</li><li><b>VI Dubbing</b>: bản Việt ưu tiên thời lượng đọc/TTS; có thể khác Subtitle.</li><li><b>VI Syl</b>: số âm tiết Việt local của bản đang xem.</li><li><b>Target</b>: mục tiêu âm tiết do Settings tính từ thời lượng; Target override trong hộp sửa sẽ thay giá trị này.</li><li><b>Δ target</b>: VI Syl trừ Target. Ví dụ <code>+3</code> là dài hơn 3 âm tiết; <code>-5</code> là ngắn hơn 5 âm tiết.</li><li><b>Mode</b>: chiến lược dịch từng câu.</li><li><b>QC</b>: danh sách cảnh báo, không phải lỗi tự động sửa.</li></ul>
<h2>Màu sắc và QC</h2><p>Các ô VI Syl, Target và Δ target có màu: <b>xanh</b> = khớp đúng target; <b>vàng</b> = lệch nhỏ trong dung sai; <b>đỏ</b> = lệch lớn hoặc mode nghiêm ngặt không đạt. QC có thể báo: dòng &gt;100 ký tự, tốc độ đọc &gt;22 ký tự/giây, còn chữ Trung, thuật ngữ glossary chưa xuất hiện, speaker cần nghe lại, overlap, dubbing vượt target hoặc tốc độ TTS dự đoán quá nhanh.</p>
<h2>Mode</h2><ul><li><b>Ưu tiên sát nghĩa</b>: giữ ý nghĩa tối đa, ít quan tâm độ dài.</li><li><b>Cân bằng nghĩa / thời lượng</b>: mặc định, cân bằng tự nhiên và nhịp đọc.</li><li><b>Ưu tiên/khớp âm tiết</b>: ưu tiên budget âm tiết; có thể nén sắc thái.</li><li><b>Khớp âm tiết nghiêm ngặt</b>: phải khớp target, lệch sẽ đỏ/QC failed.</li><li><b>Ưu tiên khớp thời lượng</b>: điều chỉnh cho slot audio.</li><li><b>Phụ đề tự nhiên</b>: ưu tiên dễ đọc trên màn hình.</li><li><b>Lồng tiếng ngắn</b>: rút gọn cho slot hẹp; cần kiểm tra mất sắc thái.</li></ul>
<h2>Sửa câu chọn / mode / target</h2><p>Chọn một dòng rồi bấm nút này. <b>Chinese</b> chỉ đọc để bảo toàn transcript. <b>Vietnamese Subtitle</b> sửa câu hiển thị; <b>Vietnamese Dubbing</b> sửa câu đọc, không làm đổi Subtitle. Dòng <b>Âm tiết local</b> cập nhật ngay khi gõ. Chọn Mode phù hợp; <b>Target override = 0 / Tự tính theo Settings</b> dùng target tự động, nhập số dương để ép target riêng cho câu. Save lưu chỉnh sửa, timestamp vẫn giữ nguyên.</p>
<h2>Optimize selected for dubbing</h2><p>Chọn một hoặc nhiều dòng rồi bấm nút. AI chỉ tối ưu bản Dubbing theo mode/target/hồ sơ truyện; không đổi Chinese, Subtitle hay timestamp. Xem lại ô QC và sửa thủ công nếu câu bị nén nghĩa hoặc phát âm không tự nhiên.</p>"""),
    ("6. Subtitle", """
<h1>Chia phụ đề Việt</h1><p><b>Utterance</b> là lời thoại nguồn, không bị thay đổi. <b>DisplaySegment</b> là một phần hiển thị của Utterance.</p>
<p><b>Auto Segment All</b> chia câu dài theo nghĩa, dấu câu, độ dài, số âm tiết và tốc độ đọc. <b>Auto Segment Selected</b> chỉ chạy câu chọn. <b>Split Manually</b>, <b>Merge Selected</b> và <b>Reset To Utterance</b> dành cho biên tập thủ công.</p>
<p>QC: OK là đạt; TOO_LONG/TOO_MANY_SYLLABLES là quá dài; HIGH_READING_SPEED là khó đọc; BAD_SPLIT/MANUAL_REVIEW cần xem lại. AI semantic fallback chỉ được gọi khi engine local không tìm được điểm ngắt an toàn và không được phép sửa chữ dịch.</p>"""),
    ("7. Mask", """
<h1>Mask, phụ đề, logo và watermark</h1><p>Nhập mốc thời gian, bấm <b>Lấy khung hình</b>, kéo vùng chữ gốc. Chọn <b>solid</b>, <b>blur</b>, <b>gaussian</b>, <b>pixelate</b> hoặc <b>frosted</b>. Độ nhòe 10–14 thường đủ; 15–18 cho nền nhiều chi tiết.</p>
<p>Chỉnh font, cỡ chữ, đậm, viền, bóng, vị trí và số dòng. Bật <b>Căn phụ đề giữa vùng mask</b> để đặt chữ vào giữa vùng che. Canvas là preview nhanh; Tạo preview 10 giây là kiểm tra FFmpeg trước khi render toàn bộ.</p>
<p><b>Logo</b>: load nhiều ảnh, chọn ảnh trên canvas hoặc danh sách rồi kéo để di chuyển. Có X/Y, kích thước, xoay, trong suốt; scale 0% giữ nguyên, 100% gấp đôi.</p>
<p><b>Watermark</b>: nhập text để chữ chạy và phản xạ ở mép video. Font, viền, bóng, trong suốt và tốc độ được xem trực tiếp trên canvas và dùng khi render.</p>"""),
    ("8. Export", """
<h1>Export</h1><p>Export xuất dữ liệu theo speaker để chuẩn bị TTS hoặc biên tập bằng phần mềm khác. Chọn loại văn bản cần xuất theo giao diện tab Export.</p>
<p>Để có video hoàn chỉnh có subtitle, dùng <b>Mask</b> → Tạo preview 10 giây → Render toàn bộ MP4. File MP4 nằm trong thư mục <code>output</code> của project. SRT Việt nằm tại <code>subtitle/vi.srt</code>.</p>
<p>Tool chưa tự clone giọng hoặc mix TTS vào video. Export speaker là đầu vào an toàn cho bước TTS bên ngoài.</p>"""),
    ("9. Lỗi thường gặp", """
<h1>Lỗi thường gặp</h1><ul><li><b>401/403</b>: key sai hoặc chưa có quyền API/model.</li><li><b>404 model</b>: model gõ sai hoặc key không được provider cho dùng; chọn model gợi ý khác.</li><li><b>429</b>: hết quota/vượt tốc độ; chờ rồi thử lại.</li><li><b>AI trả JSON không hợp lệ</b>: giảm chunk size, đổi model, rồi chạy lại. Tool giữ dữ liệu cũ.</li><li><b>SPK_UNKNOWN</b>: vào Speaker review, nghe dòng và gán speaker trước khi dịch.</li><li><b>Không lấy khung/render</b>: kiểm tra FFmpeg/ffprobe trong PATH, video nguồn còn tồn tại và logo nằm trong khung.</li><li><b>Chữ không đúng font</b>: cài font trên Windows hoặc chọn font có hỗ trợ tiếng Việt.</li><li><b>Sub lệch lời nói</b>: kiểm tra Transcript; việc chia Subtitle không sửa timestamp nguồn.</li></ul>"""),
)


class DocsTopicWindow(QMainWindow):
    def __init__(self, title, html, parent=None):
        super().__init__(parent);self.setWindowTitle(f"Cartoon Sub Docs — {title}");self.resize(760,650)
        browser=QTextBrowser();browser.setHtml(html);browser.setOpenExternalLinks(True);self.setCentralWidget(browser)


class DocsWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent);self.setWindowTitle("Cartoon Sub — Docs");self.resize(620,520);self.topic_windows=[]
        host=QWidget();layout=QVBoxLayout(host);layout.addWidget(QLabel("Bấm một tab để mở tài liệu trong cửa sổ riêng. Có thể mở nhiều chủ đề song song với tool."))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1);self.setCentralWidget(host)
        for title,html in TOPICS:
            preview=QTextBrowser();preview.setHtml(html);preview.setOpenExternalLinks(True);self.tabs.addTab(preview,title)
        self.tabs.tabBarClicked.connect(self.open_topic)

    def open_topic(self,index):
        if index < 0:return
        title,html=TOPICS[index];window=DocsTopicWindow(title,html,self.parent())
        window.show();window.raise_();window.activateWindow();self.topic_windows.append(window)
        window.destroyed.connect(lambda *_:self.topic_windows.remove(window) if window in self.topic_windows else None)
