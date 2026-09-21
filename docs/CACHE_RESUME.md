# Project, cache và tiếp tục công việc

Mỗi project có một `project.json`. Đây là nguồn dữ liệu chính cho timeline,
trạng thái workflow, speaker, bản dịch và TTS. File được ghi theo kiểu atomic:
file tạm chỉ thay thế `project.json` sau khi JSON hợp lệ đã được ghi xong.

## Vị trí dữ liệu

Ví dụ project `D:\Documents\MMO\Youtube\Tools\Cartoon_Sub\1`:

```text
1/
  project.json                 trạng thái và dữ liệu timeline
  source/                      tài nguyên source do project sở hữu
  audio/source.wav             audio trích cho transcript
  audio/tts/segments/          WAV TTS từng Utterance
  audio/tts/dubbed_mix.wav     track lồng tiếng theo timeline
  cache/transcription/         phản hồi transcript theo audio chunk
  cache/translation/           phản hồi dịch theo batch
  cache/tts/previews/          preview voice cục bộ
  subtitle/                    SRT và segments đã tạo
  preview/, output/, exports/  preview, render và export
  logs/                        vị trí dành cho log project
```

Video nguồn là input ngoài project và có thể vẫn là đường dẫn tuyệt đối. Mọi
audio WAV mà Cartoon_Sub tạo đều dùng đường dẫn tương đối trong `project.json`;
vì vậy có thể di chuyển nguyên folder project sang vị trí khác. Additional Audio
do người dùng chọn có thể là external reference.

## Resume và stale

- Transcript: kết quả Gemini được cache theo audio, model, prompt/schema và
  speaker references. Chạy lại cùng input dùng lại chunk hoàn tất; chunk lỗi sẽ
  được thử lại.
- Translate: `project.chunk_states.translation` lưu ID của từng batch và cache
  request. Một batch hoàn tất được lưu ngay. Nếu rate-limit, quota hoặc token
  error dừng ở batch sau, các batch trước vẫn còn nguyên.
- TTS: mỗi Utterance lưu fingerprint, segment ID, WAV tương đối, duration và
  trạng thái. **Generate / Resume TTS** chỉ gọi Local_TTS cho item thiếu, lỗi,
  hoặc stale.
- Dubbed/Final audio: chỉ tạo lại khi người dùng bấm build; nếu thao tác lỗi,
  file hoàn chỉnh trước đó không bị thay thế bởi file tạm.

`stale` không phải dữ liệu bị xóa: nó báo input đã thay đổi (ví dụ đổi text,
voice hoặc speed) nên output cũ không được dùng như output hiện hành.

## Khi lỗi API hoặc app đóng

Mở lại đúng folder project và chạy lại nút của bước đang dở. Không xóa
`project.json` hoặc `cache/` nếu muốn resume. Có thể xóa cache khi cần ép gọi
API lại, nhưng việc đó sẽ tốn quota. Không xóa `audio/tts/segments/` nếu muốn
reuse TTS.

Project cũ có đường dẫn WAV tuyệt đối bên trong folder project sẽ được nhận diện
và đổi thành đường dẫn tương đối lúc mở. Đường dẫn absolute trỏ vào Local_TTS
server không được dùng làm resource của project; item đó được đánh dấu stale để
Generate/Resume tải WAV mới vào project.
