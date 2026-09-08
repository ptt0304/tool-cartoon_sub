# Cartoon Sub — Master Timeline / Dubbing

Phase 5 đã có **Mask & Style → kéo rectangle solid/blur → preview 10 giây → render MP4** chạy local. Xem [hướng dẫn Phase 5 và đánh giá mức đáp ứng](docs/PHASE5_MASK_STYLE.md).

Bản nâng cấp thêm speaker review, master timeline, hai bản Việt riêng và export theo speaker. **Bắt đầu với [hướng dẫn Master Timeline](docs/MASTER_TIMELINE.md)**; các thể loại, văn phong và hồ sơ ngữ cảnh Phase 3 được giữ nguyên. Project cũ cần gán/xác nhận speaker trước khi dịch.

Desktop Python/PySide6, xử lý media local. Gemini dùng cho Chinese audio transcription và dịch Trung–Việt có hồ sơ ngữ cảnh. Translation đã nối API thật ở Phase 3.

## Dịch tiếng Việt — Phase 3

Đọc [hướng dẫn và thiết kế biên tập Phase 3](docs/PHASE3_TRANSLATION.md). Trong Settings kiểm tra **Translation / context model**, đổi model 2.5 cũ sang `gemini-3.5-flash` nếu cần. Translate → chọn thể loại/văn phong → Phân tích ngữ cảnh → Duyệt và Áp dụng hồ sơ → Dịch. Tự lưu `subtitle/vi.srt`.

## Chạy ứng dụng

Môi trường trên máy này đã cài. Trong PowerShell tại thư mục dự án:

```powershell
.\.venv\Scripts\cartoon-sub.exe
```

Cài mới: Python 3.11+, FFmpeg/ffprobe có trong PATH:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\cartoon-sub.exe
```

## Thiết lập Gemini API key

1. Mở [Google AI Studio → API keys](https://aistudio.google.com/apikey), đăng nhập Google và chọn **Create API key**. Chọn/tạo project theo giao diện của Google rồi copy key. Xem [hướng dẫn chính thức](https://ai.google.dev/gemini-api/docs/api-key).
2. Trong tool mở **Settings → AI…**.
3. Dán key vào **Gemini API key**. Ô nhập được che ký tự, không tự hiển thị lại key đã lưu.
4. Giữ **Transcription model = gemini-3.5-flash**, **Retry count = 2** để bắt đầu. Có thể nhập model khác hỗ trợ audio + structured JSON qua generateContent; tên model phải đúng và key có quyền truy cập.
5. Bấm **Test Connection**. Kiểm tra này đọc metadata model bằng key đang nhập (hoặc key đã lưu); không gửi audio, không tạo transcript. Thành công chưa đảm bảo còn quota inference.
6. Bấm **Save** để lưu. Test Connection không tự lưu key. Để trống ô key ở những lần sau để giữ key hiện tại.

Key được lưu trong OS keyring (Windows Credential Manager), không nằm trong `project.json` hay `settings.json`. Preferences nằm ở `%USERPROFILE%/.cartoon_sub/settings.json`. Log ứng dụng: `%USERPROFILE%/.cartoon_sub/app.log`.

Fallback cho dev khi keyring không hoạt động: copy `.env.example` thành `.env` tại thư mục chạy tool, tự điền `GEMINI_API_KEY=...`. Tool ưu tiên keyring, sau đó biến môi trường, cuối cùng `.env`. Không tự ghi key xuống .env; .env đã được gitignore. Không cần gửi key vào cuộc trò chuyện.

Nếu Test Connection báo 400/401/403: kiểm tra key/quyền API và tên model. 404: kiểm tra model. 429: kiểm tra quota/tốc độ tại AI Studio; tool retry lỗi tạm thời theo cài đặt. Retry count là số lần thử lại sau lần đầu, không phải tổng số request.

## Chạy Phase 2

1. Video → **Mở video / Tạo project**, chọn MP4/MKV/MOV rồi thư mục project mới. Hoặc Project → Open project.
2. Transcript → **Gemini: tạo / tiếp tục Chinese transcript**.
3. Worker tính hash video, FFmpeg trích `audio/source.wav` (PCM mono 16-bit, 16 kHz).
4. Mỗi đoạn tối đa 180 giây được gửi dưới dạng audio WAV tới Google Gemini; không gửi nguyên video. Gemini trả JSON gồm ID/start/end/zh. Backend validate kết quả, cộng offset theo đoạn và gán ID liên tục.
5. Khi hoàn tất: bảng Transcript có tiếng Trung; tool tự lưu `project.json`, `subtitle/zh.srt`, `subtitle/segments.json`.
6. Nếu lỗi, bấm lại cùng nút để tiếp tục. Đoạn đã hoàn thành dùng cache, chỉ đoạn lỗi/chưa chạy gọi Gemini. Chuyển model hoặc thay nội dung audio sẽ tạo cache khác. Chỉnh style không gọi lại AI.

Import Chinese SRT bỏ qua Gemini hoàn toàn và vô hiệu nút transcription cho project đó. Import tự lưu zh.srt/segments.json. Video gốc được tham chiếu tại vị trí hiện tại, chưa copy vào source/.

Nút Cancel dừng FFmpeg và kiểm tra hủy giữa các bước. Request Gemini đang gửi phải chờ phản hồi/timeout tối đa 120 giây mỗi request; thao tác hủy không hoàn lại quota đã dùng. Khi hủy/lỗi, subtitle trước đó được giữ lại. Không có phần trăm upload; thanh trạng thái báo bước và số đoạn.

Timestamp do Gemini ước lượng, cần kiểm tra trước khi render. Đoạn audio chia cố định 180 giây nên câu tại điểm cắt có thể cần sửa sau này. Editor/QC/playback sẽ làm ở phase sau. Audio không có speech có thể trả danh sách rỗng và trạng thái no_speech.

## Cấu trúc

- `app/settings.py`: AISettings, OS keyring + env fallback; không lưu secret trong JSON.
- `ai/gemini_client.py`: adapter SDK duy nhất gửi request Google, timeout/error mapping, structured JSON.
- `transcription/gemini_transcriber.py`: chia audio, validate, offset/ID, cache từng đoạn và retry lỗi tạm thời.
- `transcription/pipeline.py`: hash/extract audio local, điều phối transcription, lưu project và zh.srt/segments.json.
- `project/cache.py`: hash và ghi JSON nguyên tử; cache tại `cache/transcription/` trong mỗi project.
- `app/controller.py`: service orchestration cho UI.
- `ui/settings_dialog.py`, `ui/worker.py`, `ui/main_window.py`, `ui/tabs/`: Settings, background worker, sáu tab.
- `subtitle/models.py`, `parser.py`: models và SRT.
- `media/ffprobe.py`, `ffmpeg.py`, `process.py`: metadata/extraction, logging/error/cancel.

Project JSON là nguồn dữ liệu chính. Ghi từng file nguyên tử; nếu xuất artifact bị lỗi, Save project để xuất lại từ dữ liệu đã lưu. Các cache chạy dở/lỗi không được dùng làm transcript hoàn chỉnh. Cache là local theo từng project, không phải Google context caching.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\smoke_local.py "duong-dan-video.mp4"
```

Smoke test dùng video MP4 có sẵn trong thư mục, FFmpeg thật và Qt offscreen; thay adapter Google bằng fake, không gửi audio lên mạng. Unit tests kiểm tra settings/key isolation, response validation, chunk offsets/IDs, cache/resume/retry/cancel, preservation khi lỗi, imported SRT bypass và SDK request mapping. Chưa kiểm chứng live Gemini với key người dùng hoặc độ chính xác transcript trên video mẫu.

## Chưa triển khai

Editor và QC timestamp đầy đủ (Phase 4), preview/mask/style/render (Phase 5), các format TTS thủ công (Phase 6); cache translation đã có ở Phase 3. Translation model dùng cho phân tích ngữ cảnh và dịch; chunk size có hiệu lực khi dịch. Không có TTS tự động, OCR, stem separation, cloud hay multi-user.

## Tài liệu SDK

- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)
- [Gemini 3.5 Flash: audio input và structured outputs](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)

## Sửa lỗi 404 khi dùng gemini-2.5-flash

Google thông báo hạn chế quyền dùng model 2.5 với project mới. Key có thể đọc metadata nhưng gọi generateContent vẫn trả 404. [Thông báo Google ngày 31/08/2026](https://discuss.ai.google.dev/t/auth-key-can-list-models-but-generatecontent-returns-http-404-not-found-for-gemini-2-5-flash/180197/2).

Đóng/mở lại tool để nạp bản cập nhật. Settings → AI → Transcription model chọn `gemini-3.5-flash` → Save. Giữ key hiện tại (để trống ô key), mở lại project video cũ và bấm tạo/tiếp tục transcript. Không phải trích audio thủ công hoặc tạo project mới. Bản cập nhật giữ nguyên model đã lưu, không tự đổi model tính phí. Test Connection chỉ đọc metadata, không xác nhận quyền inference.
