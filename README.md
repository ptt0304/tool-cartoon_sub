# Cartoon Sub

Desktop tool hỗ trợ quy trình **video tiếng Trung → transcript → dịch Trung–Việt → phụ đề → MP4**. Ứng dụng chạy bằng Python/PySide6; FFmpeg xử lý media tại máy. Gemini chỉ được dùng khi người dùng cấu hình API key cho transcript, phân tích ngữ cảnh hoặc dịch.

**Bản quyền: PHẠM THANH TÙNG - 0866891380**

## Tính năng

- Mở video và tạo project cục bộ.
- Import SRT tiếng Trung hoặc tạo/tiếp tục transcript bằng Gemini.
- Review speaker, gán/đổi tên/gộp/tách speaker và nghe từng dòng.
- Dịch Trung–Việt theo thể loại, văn phong, glossary, nhân vật và quy tắc xưng hô.
- Tách câu Việt dài thành DisplaySegment để dễ đọc; giữ nguyên câu nguồn và timestamp overlap giữa speaker.
- Che chữ gốc với solid, blur, gaussian, pixelate hoặc frosted; chỉnh style subtitle, logo và watermark chạy.
- Tạo preview 10 giây, render MP4 và export dữ liệu theo speaker.

## Yêu cầu

- Windows 10/11.
- Python 3.11 trở lên.
- FFmpeg và ffprobe có trong biến môi trường `PATH`.
- Gemini API key cần cho transcription audio; provider text cần key riêng khi dùng dịch/ngữ cảnh.

Kiểm tra FFmpeg trong PowerShell:

```powershell
ffmpeg -version
ffprobe -version
```

## Cài đặt

Mở PowerShell tại thư mục source code:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

Chạy ứng dụng:

```powershell
.\.venv\Scripts\cartoon-sub.exe
```

Lệnh trên mở trực tiếp giao diện Windows, không mở terminal. Sau khi cập nhật source, chạy lại lệnh cài đặt để tạo lại launcher GUI:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

Nếu môi trường Python chưa tạo lại launcher, nhấp đúp [Cartoon_Sub_GUI.pyw](Cartoon_Sub_GUI.pyw). File này luôn mở GUI mà không hiện terminal.

Hoặc:

```powershell
.\.venv\Scripts\python.exe -m cartoon_sub.app.main
```

## Thiết lập Gemini

1. Tạo API key tại [Google AI Studio](https://aistudio.google.com/apikey).
2. Mở tool, chọn **Settings → AI…**.
3. Dán key, chọn model và bấm **Test Connection**.
4. Bấm **Save**.

Gemini xử lý audio transcription. Dịch/ngữ cảnh hỗ trợ Google Gemini, OpenAI, Anthropic Claude, OpenRouter, DeepSeek, Groq, Mistral AI, Together AI, Fireworks AI và xAI Grok. Chọn provider trong **Settings → AI**, nhập Translation API key và model đúng của provider. Key được lưu trong Windows Credential Manager/keyring, không nằm trong `project.json`. Có thể dùng biến môi trường như `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` hoặc `.env` cho môi trường phát triển.

## Cách dùng cho người mới

1. Trong tab **Video**, chọn **Mở video / Tạo project**.
2. Trong tab **Transcript**, import SRT tiếng Trung hoặc chạy Gemini transcription.
3. Bấm **Speaker review**, gán các dòng `SPK_UNKNOWN`, rồi xác nhận speaker.
4. Trong tab **Translate**, chọn thể loại/văn phong; thêm glossary nếu cần; phân tích ngữ cảnh, duyệt hồ sơ và dịch.
5. Trong tab **Subtitle**, dùng **Auto Segment All** để chia các câu dài; xem QC và chỉnh tay nếu cần.
6. Trong **Mask & Style**, lấy khung hình, kéo vùng che chữ gốc, chỉnh chữ/logo/watermark và tạo preview.
7. Bấm **Render toàn bộ MP4** khi preview đã đạt yêu cầu.

Nút **Docs** ở góc phải phía trên ứng dụng giải thích từng tab, option và ví dụ sử dụng.

## Cấu trúc source code

```text
src/cartoon_sub/
  app/           Điều phối project, cấu hình, entry point
  ai/            Gemini client
  transcription/ Transcript tiếng Trung và cache
  translation/   Hồ sơ ngữ cảnh, dịch và QC
  subtitle/      Utterance, DisplaySegment, segmentation, ASS/SRT
  speaker/       Speaker review và timeline
  media/         FFmpeg, preview và render
  ui/            Giao diện PySide6, các tab và dialog
tests/           Unit/integration tests
docs/            Tài liệu thiết kế theo phase
```

`project.json` là dữ liệu project chính. Video nguồn không được sao chép tự động; hãy giữ file video ở vị trí ban đầu. Audio trích, cache, SRT, preview và video render nằm trong thư mục project.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Tài liệu nội bộ

- [Mục lục tài liệu](docs/INDEX.md)
- [Master Timeline](docs/MASTER_TIMELINE.md)
- [Dịch theo ngữ cảnh](docs/PHASE3_TRANSLATION.md)
- [Mask, style và render](docs/PHASE5_MASK_STYLE.md)
