# Master timeline, speaker review và dubbing

## Cách sử dụng

1. Đóng bản tool cũ, chạy `.\.venv\Scripts\cartoon-sub.exe` từ thư mục Cartoon_Sub và mở project.
2. Trong **Transcript**, mở **Speaker Review**. Với transcript cũ, các dòng chưa biết người nói là `SPK_UNKNOWN`: tạo speaker, lọc/chọn các dòng đang hiển thị rồi **Gán dòng cho SPK**. Nếu một utterance chứa hai lượt nói tuần tự, chọn đúng một dòng và bấm **Tách dòng**, rồi tự đặt ranh giới chữ, timestamp và speaker hai phía; speech đồng thời vẫn dùng overlap, không dùng split tuần tự. Đổi tên chỉ đổi tên hiển thị và giữ stable ID. Các chỉnh sửa chỉ commit khi bấm **Xác nhận Speaker và Lưu**; **Hủy** bỏ thay đổi chưa xác nhận. **Reset** có xác nhận riêng và khôi phục đúng speaker registry/assignment ban đầu sau transcript, không đổi text hoặc timestamp. Speaker là giọng nói; tên nhân vật trong hồ sơ truyện được quản lý riêng.
3. Trong **Translate → Ngữ cảnh & văn phong**, giữ hoặc chỉnh các thể loại, văn phong, glossary và hồ sơ truyện như trước. Phân tích ngữ cảnh hoặc tự áp dụng hồ sơ, rồi dịch bản subtitle. Không bắt buộc gọi phân tích AI nếu đã có hồ sơ phù hợp.
4. Trong **Settings → Translation / Dubbing**, mặc định `balanced_dubbing`, target theo thời lượng, 3.5 âm tiết/giây, giữ riêng hai bản Việt. Cài đặt lưu làm mặc định cho project mới và áp dụng vào các câu project hiện tại qua giao diện.
5. Trong **Translate → Master dialogue timeline**, chọn dòng, sửa nội dung/mode/target hoặc bấm **Optimize selected for dubbing**. Có thể chọn nhiều dòng bằng Ctrl/Shift. Cuộn ngang để xem Target, Δ, Mode và QC. Chế độ Both/Subtitle/Dubbing thay đổi cột hiển thị.
6. Trong **Export**, chọn bản Subtitle hoặc Dubbing và xuất theo speaker. Kết quả là một snapshot mới trong `tts_export/<thời-gian>_<id>/`, mỗi speaker có `speaker.srt`, `segments/000031.txt` và `manifest.json`.

`subtitle/vi.srt` luôn là bản phụ đề; `subtitle/vi_dubbing.srt` là bản dubbing. Mặc định tối ưu dubbing không thay đổi phụ đề. Bỏ chọn giữ riêng hai bản trong Settings là thao tác cho phép ghi cùng nội dung vào cả hai.

## Dữ liệu và migration

`project.json` schema 2 lưu `master_timeline` làm nguồn dữ liệu chính. SRT/TXT là sản phẩm xuất. Mỗi record độc lập giữ ID, start/end, speaker, hai bản Việt, số âm tiết tính local, target, QC và các trường TTS tương lai. Duration được tính từ end − start; khi nạp lại, các giá trị suy ra được tính lại.

Project schema 1 vẫn mở được: `segments` chuyển sang master timeline, `vi` được giữ ở cả hai bản Việt, thể loại/văn phong/glossary/story_context được giữ. Lần lưu nâng cấp tạo `project.v1.backup.json` (hoặc tên có hậu tố nếu đã tồn tại). Bản tool cũ không đọc schema 2; muốn quay lại cần dùng bản sao schema 1. Không ghi đè backup có sẵn.

Không cần gọi lại transcription để dùng project cũ nếu bạn tự gán speaker. Muốn nhận diện speaker bằng Gemini, chủ động chạy lại transcription; prompt/cache mới sẽ tạo request mới. Cache audio vẫn dùng lại. Sau transcription phải duyệt lại speaker. Speaker review không dịch lại hoặc thay timestamp.

## Các chế độ

| Mode | Mục tiêu |
|---|---|
| faithful | Ưu tiên giữ nghĩa đầy đủ |
| balanced_dubbing | Cân bằng nghĩa và độ dài lời đọc |
| syllable_match | Bám số âm tiết, có thể nén sắc thái |
| strict_iso_syllabic | Đúng target local; tối đa số lần rewrite đã đặt, còn lệch thì QC FAILED |
| time_fit | Target theo thời lượng và tốc độ đọc |
| subtitle_natural | Tiếng Việt tự nhiên cho phụ đề |
| short_dub | Bản đọc ngắn, đánh dấu semantic compression |

Target: CHINESE_COUNT lấy số âm tiết Trung; TIME_BASED làm tròn duration × speech_rate; HYBRID trộn hai giá trị theo trọng số. Target riêng của câu được ưu tiên. Tolerance có thể chọn phần trăm hoặc số âm tiết. Strict yêu cầu chênh lệch bằng 0. Việc đạt target không chứng minh bản dịch đúng nghĩa: cần xem QC và nghe/đọc lại.

Prompt thể loại và biên tập gốc vẫn nằm trong `translation/presets.py`, `translation/prompts.py`. Các prompt mode và transcription nằm trong `prompts/*.txt`, được đóng gói cùng ứng dụng. Cache dịch có fingerprint của nguồn, speaker, thời lượng, mode, cấu hình, hồ sơ và phiên bản prompt; cache dubbing tách riêng, có xét nội dung prompt mode. Đổi mode không tự chạy transcription.

## Overlap, QC và giới hạn

- Hai câu 10–13 giây và 11–14 giây giữ nguyên cả hai khoảng thời gian. Export không dồn nối câu, kể cả cùng speaker; manifest giữ master ID trong khi SRT đánh số riêng.
- QC có missing/low-confidence speaker, overlap cùng speaker, chuyển A–B–A nhanh, câu quá ngắn, vượt target, strict thất bại, nén nghĩa và TTS vượt slot. Confidence và meaning_preservation do AI cung cấp là ước lượng, không phải điểm đã hiệu chuẩn.
- Đếm âm tiết là quy tắc văn bản local. Số, viết tắt, từ Latin được chuẩn hóa theo quy tắc và gắn cảnh báo; không bảo đảm trùng mọi cách phát âm. Chưa đo thời lượng giọng TTS thật.
- Gemini nhận diện speaker là đề xuất. Tham chiếu giọng sạch được lấy từ các chunk trước, tối đa 8 speaker; video chia audio thành chunk có thể cắt giữa câu. Speaker review là bước bắt buộc để sửa sai hoặc thiếu giọng, nhất là đoạn chồng tiếng.
- Export vẫn cho phép câu có QC warning/strict failed; manifest giữ trạng thái để người dùng kiểm tra trước khi TTS.
- Hiện có trường đường dẫn/thời lượng/tốc độ/alignment audio để mở rộng. Chưa triển khai sinh TTS, import audio qua UI, mix đa giọng hoặc render video cuối.

## Kiểm tra bản nâng cấp

45 unit/integration tests: migration và backup, đếm/target, overlap, speaker edit, strict retry giới hạn, cache, bảo toàn phụ đề, export master IDs và timestamp. Smoke UI chạy offscreen với FFmpeg/ffprobe và video local thật, Gemini giả lập, gồm speaker review → context → translation → export, save/load và cache. Bản sao project thực tế 34 câu đã round-trip schema 1 → 2, giữ nguyên dữ liệu; file gốc không đổi. Chưa đánh giá chất lượng nhận diện giọng/dịch trên API Gemini thật trong lần kiểm thử này.
