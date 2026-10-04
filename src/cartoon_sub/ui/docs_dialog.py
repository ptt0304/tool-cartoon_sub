"""Non-modal, detachable help windows for people new to Cartoon Sub."""
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QTextBrowser, QLabel


TOPICS = (
    ("1. Giới thiệu", """
<h1>Cartoon Sub là gì?</h1><p><b>PHẠM THANH TÙNG - 0866891380</b></p>
<p>Cartoon Sub chuyển video tiếng Trung thành video có phụ đề Việt. Tool lưu toàn bộ công việc theo một project để có thể đóng/mở và tiếp tục.</p>
<h2>Khái niệm</h2><ul><li><b>Transcript</b>: lời thoại tiếng Trung và thời điểm nói.</li><li><b>Translate</b>: bản dịch Việt theo thể loại, nhân vật và xưng hô.</li><li><b>Subtitle</b>: cách chia bản dịch thành các đoạn ngắn để người xem đọc.</li><li><b>Dubbing</b>: bản Việt tối ưu riêng cho thời lượng đọc/giọng, không thay bản subtitle.</li><li><b>Mask</b>: vùng che chữ Trung trên ảnh; <b>Style</b>: font, viền, bóng và vị trí chữ Việt.</li><li><b>Export</b>: render/export video; SRT được export tại tab sở hữu dữ liệu.</li></ul>
<p>Ví dụ: câu tiếng Trung dài 8 giây được Transcript ghi lại; Translate dịch thành một câu Việt; Subtitle tách thành hai DisplaySegment dễ đọc; Mask che chữ gốc rồi render MP4.</p>
<p><b>Undo:</b> nhấn Ctrl+Z để hoàn tác edit đã commit; Ctrl+Y hoặc Ctrl+Shift+Z để redo. Undo đổi project state và không gửi lại API hay tự render.</p>"""),
    
    ("2. Project", """
<h1>Thư mục project</h1><p>Tạo project mới tại tab <b>Video</b>: chọn video nguồn và chọn thư mục project. Video không được tự sao chép, vì vậy đừng đổi vị trí video nguồn.</p>
<ul><li><code>project.json</code>: file chính chứa timeline, speaker, bản dịch, mask, style, logo, watermark và trạng thái công việc.</li><li><code>audio/source.wav</code>: audio trích từ video khi cần AI transcription.</li><li><code>subtitle/zh.srt</code>, <code>subtitle/vi.srt</code>: phụ đề Trung và Việt.</li><li><code>cache/</code>: kết quả AI từng bước để bấm tiếp tục sau lỗi; không nên tự sửa.</li><li><code>preview/</code>: preview 10 giây; <code>output/</code>: MP4 render hoàn chỉnh.</li></ul>
<p>Mở project có sẵn: chọn <b>Project → Open project</b>, rồi mở đúng file <code>project.json</code>. Để sao lưu, giữ nguyên toàn bộ thư mục project và video nguồn.</p>"""),
    ("3. Settings", """
<h1>Settings và AI API</h1><p>Mở <b>Settings → AI</b>. OpenRouter keys được lưu theo thứ tự trong Windows Credential Manager/keyring, không nằm trong project.json.</p>
<h2>OpenRouter-first</h2><p>Speech-to-Text, Translation, Vision/Speaker và Review/QA được gán model theo từng nhiệm vụ. Bấm Edit Keys để nhập mỗi OpenRouter key trên một dòng, Test All Keys, chọn model tương thích rồi Save.</p>
<p>Catalog general và transcription được đồng bộ nền một lần khi ứng dụng khởi động, đồng thời cache cục bộ để Settings vẫn dùng được khi offline. Model Author là phần trước dấu / của model ID, không phải inference provider.</p>
<p><b>Test All Keys</b> xác thực từng key bằng endpoint tài khoản, không gọi inference. Translation chunk size và Retry count nằm trong Advanced. Gemini/OpenAI transcription và Gemini direct-video cũ chỉ được giữ nội bộ để project cũ không bị hỏng.</p>
<p>AI đề xuất nội dung; người dùng vẫn kiểm tra transcript, speaker, glossary, xưng hô và câu dịch trước khi render.</p>"""),
    ("4. Transcript", """
<h1>Transcript tiếng Trung</h1><p>Transcript xác định <b>ai nói gì, vào lúc nào</b>. Có hai cách:</p><ol><li><b>Import Chinese SRT</b>: chọn SRT Trung có sẵn, không gọi AI.</li><li><b>Gemini: tạo / tiếp tục</b>: tool trích audio cục bộ, gửi từng đoạn audio sang Gemini và nhận JSON thời điểm + tiếng Trung.</li></ol>
<p><b>Export Transcript SRT</b> tạo <code>exports/transcript/transcript_N.srt</code> từ transcript nguồn đã commit hiện tại. Mỗi lần export tăng N và không ghi đè file cũ.</p>
<p>Nếu lỗi hoặc dừng giữa chừng, bấm lại cùng nút để tiếp tục cache. AI có thể sai từ hoặc lệch mốc; kiểm tra các câu quan trọng bằng nghe audio.</p>
<h2>Speaker Review</h2><p><code>SPK_UNKNOWN</code> là giọng chưa đủ tin cậy để tự gán. Lọc speaker nếu cần; <b>Chọn tất cả</b> chỉ chọn các dòng đang hiển thị. Dùng <b>Speaker mới</b>, <b>Đổi tên</b>, <b>Gán dòng cho SPK</b> và <b>Nghe dòng chọn</b> để duyệt. Speaker chưa có câu vẫn được giữ và stable ID không đổi khi đổi tên.</p>
<p>Thay đổi chỉ được lưu khi bấm <b>Xác nhận Speaker và Lưu</b>; <b>Hủy</b> bỏ thay đổi chưa xác nhận. <b>Reset</b> có hộp xác nhận riêng, khôi phục speaker ban đầu sau transcript nhưng không đổi text/timestamp. Phải xử lý hết <code>SPK_UNKNOWN</code> trước khi xác nhận.</p>"""),
    ("5. Translate", """
<h1>Dịch Trung–Việt</h1><p>Chọn thể loại và văn phong trước khi dịch. Ví dụ phim tu tiên hài: chọn <b>Tu tiên/huyền huyễn</b> và <b>Hài/châm biếm</b>; glossary có thể ghi <code>筑基 -&gt; Trúc Cơ</code>.</p>
<p><b>Phân tích ngữ cảnh bằng AI</b> đối chiếu transcript với video proxy theo chunk để đề xuất nhân vật, người nói/người nghe/referent, scene mode, thuật ngữ và quy tắc xưng hô. Context theo ID được cache; bước dịch dùng bản đã duyệt và không gửi lại video. Bấm <b>Duyệt &amp; lưu ngữ cảnh AI</b> hoặc <b>Hồ sơ đang áp dụng / tự nhập</b>, kiểm tra rồi Áp dụng.</p>
<p><b>Dịch / tiếp tục</b> gửi từng nhóm câu sang provider text đã chọn. Tool kiểm tra JSON/ID; khi AI lỗi, bản dịch cũ được giữ.</p>
<h2>Master dialogue timeline</h2><p>Chọn <b>Both</b> để xem cả hai bản Việt; <b>Subtitle</b> để ưu tiên bản hiển thị; <b>Dubbing</b> để ưu tiên bản đọc/lồng tiếng. Kéo tiêu đề cột để đổi độ rộng; chữ dài tự xuống dòng và tăng chiều cao hàng.</p>
<ul><li><b>ID</b>: số câu Utterance.</li><li><b>Start / End / Duration</b>: mốc bắt đầu, kết thúc và thời lượng câu theo giây.</li><li><b>Speaker</b>: ID và tên giọng đã duyệt.</li><li><b>Chinese</b>: transcript tiếng Trung nguồn; không tự đổi khi sửa bản Việt.</li><li><b>ZH Syl</b>: số đơn vị/âm tiết ước lượng của nguồn, dùng tham khảo.</li><li><b>VI Subtitle</b>: bản Việt ưu tiên người xem đọc, tự nhiên và đủ nghĩa.</li><li><b>VI Dubbing</b>: bản Việt ưu tiên thời lượng đọc/TTS; có thể khác Subtitle.</li><li><b>VI Syl</b>: số âm tiết Việt local của bản đang xem.</li><li><b>Target</b>: mục tiêu âm tiết do Settings tính từ thời lượng; Target override trong hộp sửa sẽ thay giá trị này.</li><li><b>Δ target</b>: VI Syl trừ Target. Ví dụ <code>+3</code> là dài hơn 3 âm tiết; <code>-5</code> là ngắn hơn 5 âm tiết.</li><li><b>Mode</b>: chiến lược dịch từng câu.</li><li><b>QC</b>: danh sách cảnh báo, không phải lỗi tự động sửa.</li></ul>
<h2>Màu sắc và QC</h2><p>Các ô VI Syl, Target và Δ target có màu: <b>xanh</b> = khớp đúng target; <b>vàng</b> = lệch nhỏ trong dung sai; <b>đỏ</b> = lệch lớn hoặc mode nghiêm ngặt không đạt. QC có thể báo: dòng &gt;100 ký tự, tốc độ đọc &gt;22 ký tự/giây, còn chữ Trung, thuật ngữ glossary chưa xuất hiện, speaker cần nghe lại, overlap, dubbing vượt target hoặc tốc độ TTS dự đoán quá nhanh.</p>
<h2>Mode</h2><ul><li><b>Ưu tiên sát nghĩa</b>: giữ ý nghĩa tối đa, ít quan tâm độ dài.</li><li><b>Cân bằng nghĩa / thời lượng</b>: mặc định, cân bằng tự nhiên và nhịp đọc.</li><li><b>Ưu tiên/khớp âm tiết</b>: ưu tiên budget âm tiết; có thể nén sắc thái.</li><li><b>Khớp âm tiết nghiêm ngặt</b>: phải khớp target, lệch sẽ đỏ/QC failed.</li><li><b>Ưu tiên khớp thời lượng</b>: điều chỉnh cho slot audio.</li><li><b>Phụ đề tự nhiên</b>: ưu tiên dễ đọc trên màn hình.</li><li><b>Lồng tiếng ngắn</b>: rút gọn cho slot hẹp; cần kiểm tra mất sắc thái.</li></ul>
<h2>Sửa câu chọn / mode / target</h2><p>Chọn một dòng rồi bấm nút này. <b>Chinese</b> chỉ đọc để bảo toàn transcript. <b>Vietnamese Subtitle</b> sửa câu hiển thị; <b>Vietnamese Dubbing</b> sửa câu đọc, không làm đổi Subtitle. Dòng <b>Âm tiết local</b> cập nhật ngay khi gõ. Chọn Mode phù hợp; <b>Target override = 0 / Tự tính theo Settings</b> dùng target tự động, nhập số dương để ép target riêng cho câu. Save lưu chỉnh sửa, timestamp vẫn giữ nguyên.</p>
<h2>Optimize selected for dubbing</h2><p>Chọn một hoặc nhiều dòng rồi bấm nút. AI chỉ tối ưu bản Dubbing theo mode/target/hồ sơ truyện; không đổi Chinese, Subtitle hay timestamp. Xem lại ô QC và sửa thủ công nếu câu bị nén nghĩa hoặc phát âm không tự nhiên.</p>
<p><b>Export Translate SRT</b> tạo <code>exports/translate/translate_N.srt</code> từ VI Subtitle đã Apply trong master timeline. Export không gọi Gemini và không dùng cache/file import cũ. Export SRT + TXT/manifest theo speaker cũng nằm tại tab này.</p>"""),
    ("6. Subtitle", """
<h1>Chia phụ đề Việt</h1><p><b>Utterance</b> là lời thoại nguồn, không bị thay đổi. <b>DisplaySegment</b> là một phần hiển thị của Utterance.</p>
<p><b>Auto Segment All</b> chia câu dài theo nghĩa, dấu câu, độ dài, số âm tiết và tốc độ đọc. <b>Auto Segment Selected</b> chỉ chạy câu chọn. <b>Split Manually</b>, <b>Merge Selected</b> và <b>Reset To Utterance</b> dành cho biên tập thủ công.</p>
<p>QC: OK là đạt; TOO_LONG/TOO_MANY_SYLLABLES là quá dài; HIGH_READING_SPEED là khó đọc; BAD_SPLIT/MANUAL_REVIEW cần xem lại. Auto Segment luôn chạy local theo cài đặt hiện tại và không gọi AI.</p>
<p><b>Export Subtitle SRT</b> đồng bộ phần presentation bị stale rồi tạo <code>exports/subtitle/subtitle_N.srt</code> từ DisplaySegment hiện tại, gồm split/merge/timing đã lưu.</p>"""),
    ("7. Mask", """
<h1>Mask, phụ đề, logo và watermark</h1><p>Nhập mốc thời gian, bấm <b>Lấy khung hình</b>, kéo vùng chữ gốc. Chọn <b>solid</b>, <b>blur</b>, <b>gaussian</b>, <b>pixelate</b> hoặc <b>frosted</b>. Độ nhòe 10–14 thường đủ; 15–18 cho nền nhiều chi tiết.</p>
<p>Chỉnh font, cỡ chữ, đậm, viền và bóng. Số dòng do tab Subtitle quyết định; Mask giữ nguyên cấu trúc DisplaySegment và luôn căn giữa khối chữ trong vùng mask hiện tại. Canvas là preview nhanh; Tạo preview 10 giây là kiểm tra FFmpeg trước khi render toàn bộ.</p>
<p><b>Logo</b>: load nhiều ảnh, chọn ảnh trên canvas hoặc danh sách rồi kéo để di chuyển. Có X/Y, kích thước, xoay và trong suốt. Logo Scale là phần trăm so với kích thước gốc: 50% = một nửa, 100% = gốc, 250% = 2,5 lần, 1000% = 10 lần. Giá trị âm dùng trị tuyệt đối làm độ lớn (ví dụ -504% = 5,04 lần); renderer hiện không mirror ảnh.</p>
<p><b>Watermark</b>: nhập text để chữ chạy và phản xạ ở mép video. Font, viền, bóng, trong suốt và tốc độ được xem trực tiếp trên canvas và dùng khi render.</p>"""),
    ("8. Audio", """
<h1>Audio và Local_TTS</h1>
<h2>Quy trình Audio</h2>
<p><b>Master Dialogue Timeline</b> → VI Dubbing → Speaker → AI Voice → Generate / Resume TTS → TTS Segments → Build Dubbed Audio → Audio Mixer → Build Final Audio → <code>audio/final_audio.wav</code>.</p>

<h2>Local_TTS</h2>
<p><b>Local_TTS URL</b> là địa chỉ backend. <b>Test connection</b> kiểm tra kết nối và tải voice READY; <b>Auto Start</b> khởi động executable đã cấu hình; <b>Select Local_TTS…</b> chọn executable. Trạng thái READY cho biết backend và voice dùng được; FAILED cần kiểm tra URL, process và cấu hình.</p>
<p>Local_TTS chỉ tạo WAV từ text + voice_id. Cartoon_Sub tải và giữ WAV trong project, không phụ thuộc thư mục <code>Local_TTS/outputs</code>.</p>

<h2>Bảng Speaker / Voice</h2>
<ul><li><b>✓</b>: chọn speaker cho batch assignment.</li><li><b>Speaker</b>: ID ổn định như SPK_01.</li><li><b>Character</b>: tên hiển thị nhân vật.</li><li><b>AI Voice</b>: voice_id gán cho speaker.</li><li><b>Engine</b>: engine/source của voice.</li><li><b>Speed</b>: tốc độ TTS của speaker.</li><li><b>Status</b>: READY, MISSING hoặc trạng thái voice.</li></ul>
<p>Mỗi AI Voice selector và dropdown batch có hai section: <b>★ Giọng yêu thích</b> lấy trực tiếp từ Local_TTS và <b>Tất cả giọng</b> chứa toàn bộ voice READY theo thứ tự API. Cartoon_Sub đồng bộ mỗi 3 giây; đổi dấu ★, clone hoặc xóa voice bên Local_TTS sẽ tự phản ánh mà không cần khởi động lại.</p>
<p>Đổi favorite không thay voice_id đang chọn. Nếu voice bị xóa, selector chuyển sang voice hợp lệ đầu tiên; Local_TTS offline thì giữ danh sách gần nhất và tự kết nối lại.</p>
<p><b>Select all</b> và <b>Clear selection</b> quản lý checkbox. Chọn voice ở dropdown batch rồi bấm <b>Apply voice to checked speakers</b>. Checkbox chỉ phục vụ batch; <b>Preview selected voice</b> luôn nghe voice của row đang active.</p>

<h2>Generate / Resume TTS</h2>
<p>Dùng <b>VI Dubbing</b> và tạo một WAV cho mỗi Utterance. Segment hoàn tất có fingerprint hợp lệ được reuse; lần Resume chỉ xử lý phần pending, failed hoặc stale. VI Dubbing, voice_id, speed hay cấu hình TTS liên quan thay đổi sẽ làm segment stale.</p>
<ul><li><b>Generated</b>: số WAV đã generated/cached.</li><li><b>Sync OK</b>: WAV phù hợp slot thời gian.</li><li><b>Auto-fit</b>: WAV dài hơn nhẹ và được chỉnh tempo khi mix.</li><li><b>Needs review</b>: timing cần kiểm tra.</li><li><b>Overlap groups</b>: speaker thật sự nói chồng nhau.</li><li><b>Stale</b>: input đổi, cần Generate / Resume lại.</li></ul>

<h2>Build Dubbed Audio</h2>
<p>Nút này <b>không tạo voice mới</b>. Nó lấy WAV đã generate, đặt mỗi WAV tại <code>Utterance.start</code>, giữ overlap hợp lệ rồi mix thành <code>audio/tts/dubbed_mix.wav</code>. Diagnostics sau build hiển thị số segment, phase timing, số process và kích thước graph. Dubbed mix hiện rebuild mỗi lần bấm, chưa có cache hit riêng.</p>

<h2>Audio Mixer</h2>
<ul><li><b>Original Audio Volume</b>: ô số 0–100%; 0 mute tiếng gốc, 100 là mức chuẩn.</li><li><b>Dubbed Audio Volume</b>: 0–100%, điều khiển <code>dubbed_mix.wav</code>, không thay TTS generation.</li><li><b>Additional Audio</b>: tùy chọn. Browse chọn file, Clear bỏ file, Volume 0–100%, Start Offset tính bằng giây; để trống thì skipped.</li></ul>
<p>Ba volume nhập bằng bàn phím; mouse wheel không thay đổi giá trị.</p>

<h2>Build và Play Final Audio</h2>
<p><b>Build Final Audio</b> trộn Original + Dubbed + Optional Additional theo volume hiện tại thành <code>audio/final_audio.wav</code>. <b>Play Final Audio</b> phát file này; nút đổi Play/Stop, Stop hoặc phát hết sẽ dừng và lần phát sau bắt đầu lại từ đầu.</p>

<h2>File trong project</h2>
<pre>&lt;ProjectRoot&gt;/
  audio/
    source.wav
    tts/
      segments/          WAV theo Utterance, có thể reuse
      dubbed_mix.wav
    final_audio.wav</pre>

<h2>Ví dụ</h2>
<ol><li><b>Chỉ dubbing:</b> Original 0%, Dubbed 100%, không chọn Additional.</li><li><b>Giữ tiếng gốc nhỏ:</b> Original 20%, Dubbed 100%.</li><li><b>Thêm BGM:</b> Original 0%, Dubbed 100%, Additional 10–20%.</li><li><b>Nhiều speaker cùng voice:</b> tick speaker → chọn voice batch → Apply voice to checked speakers.</li></ol>

<h2>Troubleshooting</h2>
<ul><li>Local_TTS không kết nối: kiểm tra URL, Test connection, Auto Start hoặc chọn lại executable.</li><li>Voice không READY/MISSING: chọn voice READY và Generate / Resume lại.</li><li>Preview WAV lỗi: kiểm tra row đang chọn, voice và trạng thái Local_TTS.</li><li><code>TTS_AUDIO_STALE</code> hoặc thiếu segment: Generate / Resume TTS.</li><li><code>DUBBED_AUDIO_NOT_FOUND</code>: Build Dubbed Audio trước.</li><li><code>FINAL_AUDIO_STALE</code> hoặc thiếu file: Build Final Audio lại.</li><li><code>FINAL_AUDIO_FILE_LOCKED</code> / WinError 5: dừng player và đóng ứng dụng đang giữ file.</li><li><code>ADDITIONAL_AUDIO_NOT_FOUND</code> / <code>ADDITIONAL_AUDIO_INVALID</code>: chọn file audio hợp lệ hoặc Clear.</li></ul>"""),
    ("9. Export", """
<h1>Export video</h1><p>Tab Export chỉ dành cho Test 30 seconds, Full Video và Render Video. SRT Transcript, Translate và Subtitle được export trong chính tab tương ứng.</p>
<p>Chọn test 30 giây trước khi render full. Video dùng <code>audio/final_audio.wav</code> và trạng thái mask/style/subtitle hiện tại; output nằm trong project.</p>"""),
    ("10. Lỗi thường gặp", """
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
