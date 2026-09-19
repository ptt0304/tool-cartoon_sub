# Phase 5 — Mask / Style và render local

## Cách dùng

1. Mở project, vào **Mask & Style**. Nhập thời gian (giây), bấm **Lấy khung hình**.
2. Trên ảnh, kéo từ góc này đến góc đối diện của vùng chữ Trung cần che. Tool quy đổi vị trí hiển thị sang pixel video gốc, bỏ phần viền đen của khung xem. Có thể kéo lại hoặc chỉnh X/Y/Width/Height. Một rectangle cố định áp dụng suốt video.
3. Chọn `solid` (đen) hoặc `blur`. Bỏ **Bật vùng che** để giữ ảnh gốc.
4. Chỉnh font, cỡ chữ, đậm, viền, bóng, vị trí, lề dọc và số dòng tối đa. Cỡ chữ và lề tính theo pixel video nguồn, không theo kích thước cửa sổ.
5. **Lưu Mask / Style** hoặc Ctrl+S. **Tạo preview 10 giây** lưu cấu hình trước khi render. Khi xong, chuyển sang Video preview và bấm **Phát preview**. Không tự phát âm thanh; đổi tab/đóng trang sẽ dừng. Chỉnh mask/style cần tạo lại preview để thấy kết quả.
6. Khi vừa ý, bấm **Render toàn bộ MP4**. Đường dẫn kết quả hiện dưới tab. Nút Cancel ở thanh trạng thái hủy job FFmpeg.

Preview từ mốc được chọn dài tối đa 10 giây, ngắn hơn nếu gần cuối video. Final render toàn bộ video. Mỗi lần tạo thư mục mới để không ghi đè kết quả trước:

- `preview/<id>/preview.mp4`, `subtitle.ass`, `render.json`
- `output/<id>/final.mp4`, `subtitle.ass`, `render.json`

`render.json` ghi trạng thái và cấu hình của lần render; file tạm không được coi là kết quả hoàn tất. Video H.264, âm thanh gốc đầu tiên được mã hóa lại AAC để tương thích MP4; không thay bằng TTS. Không giữ nguyên bitstream audio hoặc tất cả track audio.

## Những gì được giữ nguyên

- Dùng `vi_subtitle` để burn chữ, không lấy `vi_dubbing`.
- Không đổi master IDs, timestamp, speaker, hồ sơ truyện hoặc hai bản dịch.
- Các câu overlap vẫn là các sự kiện ASS độc lập; libass có thể bố trí để tránh đè chữ, cần xem preview.
- Style/mask không tham gia fingerprint dịch và không gọi Gemini.
- Mask được áp dụng trước chữ Việt. Chỉ che hình theo rectangle, không OCR, không phục hồi nền.

Ngắt dòng được tính gần đúng theo cỡ chữ/chiều rộng, không cắt mất từ để đạt số dòng. Câu dài vẫn có thể tràn ngang; chỉnh cỡ chữ hoặc biên tập câu. Font phải có trên máy; FFmpeg/libass có thể dùng font thay thế nếu thiếu. Một mask không tự di chuyển theo phụ đề ở các vị trí khác nhau. Chưa có nhiều mask, keyframe hoặc inpainting.

## Project lưu ở đâu?

`project.json` chứa nguồn video (đường dẫn), metadata, master timeline, speaker và trạng thái duyệt, hồ sơ ngữ cảnh, glossary/văn phong, trạng thái dịch, cấu hình dubbing, mask/style và các hash/trạng thái chunk. Nó không phải file chứa toàn bộ tiến trình vật lý.

Audio trích, cache phản hồi AI, SRT/TXT/manifest, ASS và MP4 nằm ở các thư mục bên cạnh. Video nguồn có thể nằm ngoài thư mục project; tạo project không tự sao chép video vào `source`. API key nằm trong keyring hoặc cấu hình môi trường, không nằm trong project. Để sao lưu đầy đủ: giữ cả thư mục project và video nguồn; nếu đổi vị trí nguồn phải cập nhật đường dẫn.

## Đánh giá ngày 08/09/2026

Project `4` có 23 câu, 4 speaker (13/1/8/1 câu), transcription/translation completed, context applied và 23 câu có phụ đề Việt. Dubbing status của cả 23 câu là not_started: nội dung ở cột dubbing chưa chứng minh đã tối ưu riêng. Mask ban đầu tắt. Đây là kiểm tra dữ liệu, không phải chấm chất lượng dịch hay xác nhận mọi giọng AI đều đúng.

| Phần | Mức đáp ứng |
|---|---|
| Project, ffprobe, import SRT | Đã có |
| Gemini transcript, speaker review | Đã có; vẫn cần nghe kiểm tra |
| Dịch theo thể loại/ngữ cảnh, glossary | Đã có |
| Hai bản Việt, 7 mode dubbing, đếm/QC | Đã có; đánh giá văn phong/giọng cần kiểm tra thực tế |
| Editor | Sửa bản Việt/mode/target đã có; sửa Chinese, split/merge utterance và timestamp chưa hoàn chỉnh |
| Mask / Style / preview / final MP4 | Đã triển khai trong bản này |
| Cache/retry và export theo speaker | Đã có |
| Embedded subtitle | Có wrapper FFmpeg, chưa có luồng chọn track hoàn chỉnh trong UI |
| TTS/import/mix audio | Chưa triển khai; hiện xuất file để dùng công cụ ngoài |

Không coi tool là hoàn tất toàn bộ V1: phần editor Chinese/timestamp còn thiếu, chất lượng nhận diện giọng và dịch không được đảm bảo chỉ bằng test tự động.

Kiểm thử Phase 5 gồm ASS giữ overlap và cắt/đổi mốc preview, save/load style, tọa độ canvas, mask ngoài khung, fingerprint dịch không đổi, FFmpeg thật với solid/blur và audio, full render, cancel. Đã render preview 10 giây từ bản sao project `4` tại mốc 55 giây và kiểm tra ảnh chữ Việt/solid; không thay file project gốc. Video thử nằm trong `docs/phase5_check/` và chỉ dùng mask mẫu.
