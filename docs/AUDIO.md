# Audio tab

## Tổng quan và workflow

Audio dùng để kết nối Local_TTS, gán giọng cho speaker, nghe thử, tạo hoặc tiếp
tục tạo WAV lời thoại, dựng dubbed timeline và trộn final audio.

```text
Master Dialogue Timeline
        ↓
VI Dubbing
        ↓
Local_TTS
        ↓
TTS WAV theo Utterance
        ↓
Build Dubbed Audio → audio/tts/dubbed_mix.wav
        ↓
Final Audio Mixer
        ↓
audio/final_audio.wav
```

## Local_TTS

- **Local_TTS URL** mặc định là `http://127.0.0.1:8765`.
- **Test connection** kiểm tra health và tải danh sách voice `READY`.
- **Auto Start** khởi động Local_TTS bằng executable đã cấu hình; **Select
  Local_TTS…** chọn executable đó.
- Danh sách voice ưu tiên nhóm **★ Giọng yêu thích**, sau đó hiển thị toàn bộ
  voice sẵn sàng. `FAILED` hoặc `MISSING` nghĩa là voice hiện không dùng được.

Local_TTS chỉ là backend nhận text + `voice_id` và trả WAV. Cartoon_Sub tải,
kiểm tra rồi lưu bản project-owned; không dùng output folder của Local_TTS làm
project storage.

## Bảng speaker và chọn voice

| Cột | Ý nghĩa |
|---|---|
| ✓ | Chọn speaker cho thao tác batch; không quyết định row được Preview. |
| Speaker | ID ổn định, ví dụ `SPK_01`. |
| Character | Tên hiển thị của nhân vật. |
| AI Voice | `voice_id` được gán cho speaker. |
| Engine | Engine/source mà Local_TTS báo cho voice. |
| Speed | Tốc độ TTS của speaker, từ 0.1 đến 3.0. |
| Status | `READY`, `MISSING` hoặc trạng thái voice hiện tại. |

**Select all** và **Clear selection** thay đổi checkbox. Chọn một voice ở ô
batch rồi bấm **Apply voice to checked speakers** để gán cùng voice cho các
speaker đã tick.

**Preview selected voice** luôn nghe voice của row hiện đang active, không dựa
trên checkbox batch. Nếu vừa đổi AI Voice của row, lần Preview sau dùng voice
mới.

## Generate / Resume TTS và timing

**Generate / Resume TTS** dùng đúng nguồn đang chọn (`VI Subtitle` hoặc
`VI Dubbing`), không fallback giữa hai nguồn; mỗi Utterance có một WAV riêng.
Fingerprint bao gồm source, nội dung, voice, speed và cấu hình TTS liên quan.
Segment hợp lệ có fingerprint không đổi được reuse; segment pending/failed có
thể tiếp tục ở lần chạy sau.

Generate chỉ tạo/tiếp tục WAV theo Utterance rồi dừng. Nó không tự chạy Build
Dubbed Audio, audio mixer, final audio hoặc rewrite nội dung. Cache và file WAV
của `VI Subtitle`/`VI Dubbing` độc lập; đổi nguồn có thể reuse lại cache hợp lệ
của chính nguồn đó.

TTS trở thành `stale` khi đổi `VI Dubbing`, `voice_id`, speed hoặc input TTS có
liên quan. Dòng trạng thái tổng hợp các nhóm:

- **Sync OK**: WAV phù hợp slot thời gian.
- **Auto-fit**: WAV dài hơn nhẹ và được tăng tempo khi dựng dubbed mix.
- **Needs review**: timing cần người dùng kiểm tra.
- **Overlap groups**: nhiều speaker thực sự nói chồng nhau; khác với một WAV bị
  overflow khỏi slot của chính nó.

Target ban đầu của VI Dubbing dùng tốc độ đã calibration riêng theo
`voice_id + engine/model + speed`. Calibration chỉ là ước lượng; duration đọc
từ WAV Local_TTS thực tế luôn quyết định bước fit cuối. VI Subtitle vẫn là bản
dịch đầy đủ và không bị ghi đè bởi bản VI Dubbing đã rút gọn.

Khi WAV dài hơn source slot, hệ thống tạo `allowed_audio_start/end` riêng và
mượn gap thật phía sau trước, rồi mới tới phía trước, luôn chừa guard 80 ms.
Canonical Start/End của subtitle không đổi. Tỉ lệ nhẹ chỉ dùng tempo tối đa
1.12; câu dài hơn được rewrite semantic tối đa 2 lần. Ba câu dài/dày liên tiếp
không mượn dây chuyền mà được đánh dấu `LONG_DENSE_CHAIN / NEED_REVIEW`.

## Build Dubbed Audio

Nút này không sinh voice mới. Nó xác minh các WAV đã generated/cached, đặt mỗi
WAV tại `Utterance.start`, giữ overlap hợp lệ rồi mix thành:

`<ProjectRoot>/audio/tts/dubbed_mix.wav`

Mỗi lần bấm hiện rebuild toàn bộ; chưa có cache hit cho dubbed mix. Diagnostics
sau build ghi số segment, thời gian validate/graph/FFmpeg/finalize, số process,
số input/filter và kích thước filter script. Mixer hiện dùng một FFmpeg process,
không gọi ffprobe hoặc FFmpeg riêng cho từng segment.

## Final Audio Mixer

Ba volume là ô nhập số bằng bàn phím, range `0–100%`; wheel không đổi giá trị.

- **Original Audio Volume**: `0%` mute tiếng gốc; `100%` giữ mức chuẩn.
- **Dubbed Audio Volume**: điều chỉnh `dubbed_mix.wav`, không ảnh hưởng quá
  trình tạo TTS.
- **Additional Audio Volume**: điều chỉnh file bổ sung tùy chọn.

Additional Audio hỗ trợ **Browse**, **Clear**, volume `0–100%` và **Start
Offset** tính bằng giây từ đầu timeline. File có thể là định dạng audio FFmpeg
đọc được; nếu không chọn thì mixer bỏ qua nguồn này.

**Build Final Audio** trộn original + dubbed + optional additional theo volume
hiện tại và tạo:

`<ProjectRoot>/audio/final_audio.wav`

**Play Final Audio** phát file này và đổi thành Stop trong lúc phát. Stop hoặc
phát hết sẽ dừng, giải phóng source và lần phát sau bắt đầu lại từ đầu.

## File và cache

```text
<ProjectRoot>/
  audio/
    source.wav
    tts/
      segments/          # WAV từng Utterance, có fingerprint và có thể reuse
      dubbed_mix.wav     # rebuildable, hiện chưa cache theo fingerprint
    final_audio.wav      # rebuildable từ các nguồn và volume hiện tại
```

## Troubleshooting

- Local_TTS không kết nối: kiểm tra URL, chạy **Test connection**, dùng **Auto
  Start** hoặc chọn lại executable.
- Voice `MISSING`/không `READY`: chọn voice sẵn sàng rồi Generate/Resume lại.
- Preview lỗi/không có audio: kiểm tra voice đang chọn và trạng thái Local_TTS.
- `TTS_AUDIO_STALE` hoặc thiếu WAV: chạy **Generate / Resume TTS**.
- `DUBBED_AUDIO_NOT_FOUND`: chạy **Build Dubbed Audio** trước Final Audio.
- `FINAL_AUDIO_STALE`: chạy lại **Build Final Audio** trước khi export.
- `ADDITIONAL_AUDIO_NOT_FOUND` / `ADDITIONAL_AUDIO_INVALID`: chọn file tồn tại
  và có audio stream FFmpeg đọc được, hoặc bấm Clear.
- `FINAL_AUDIO_FILE_LOCKED` / WinError 5: dừng player và đóng ứng dụng khác đang
  giữ `final_audio.wav`, sau đó build lại.

## Ví dụ

1. Chọn đúng **Nguồn TTS**, chạy **Generate / Resume TTS**, sau đó bấm riêng
   **Build Dubbed Audio**.
2. Giữ tiếng gốc nhỏ: Original `20%`, Dubbed `100%`.
3. Thêm nhạc nền: Original `0%`, Dubbed `100%`, Additional `15%`.
4. Nhiều speaker dùng một voice: tick các speaker, chọn voice batch, bấm
   **Apply voice to checked speakers**, rồi Generate / Resume TTS.
