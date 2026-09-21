# Hướng dẫn dùng Cartoon Sub

## Flow chuẩn

1. **Video** — Mở video / tạo project. Project chỉ tham chiếu video gốc; audio
   trích và các output sinh ra nằm trong folder project.
2. **Transcript** — Import SRT tiếng Trung hoặc chạy Gemini. Media ngắn dùng
   request cũ; media dài được chia audio chunk, gọi tuần tự và có retry/cache.
3. **Transcript / Speaker review** — Kiểm tra `SPK_UNKNOWN`, đặt tên, gộp/tách
   speaker và xác nhận review trước khi dịch/TTS.
4. **Translate** — Chọn profile/ngữ cảnh, áp dụng profile rồi dịch. Master
   dialogue timeline là nơi chỉnh Start, End, Speaker, Chinese, VI Subtitle và
   VI Dubbing. Bấm **Áp dụng bản sửa tay** sau khi sửa ô. **Import Vietnamese
   SRT** map theo timestamp và có ưu tiên cao hơn AI Translation.
5. **Subtitle** — Auto Segment tạo DisplaySegment để hiển thị; không thay đổi
   Utterance nguồn. Có thể Split, Merge, Reset To Utterance hoặc căn timing.
6. **Mask** — Chọn vùng che chữ gốc, style subtitle, logo/watermark và
   render preview trước. Mode **Nền trắng / chữ đen** che trắng và render chữ
   đen; frame chọn mask không hiển thị subtitle giả.
7. **Audio** — Kết nối Local_TTS, chọn voice/speed cho từng speaker, Generate /
   Resume TTS, Build Dubbed Audio, rồi Build Final Audio.
8. **Export** — Render test hoặc full export sau khi output cần thiết hợp lệ.

## VI Subtitle và VI Dubbing

`VI Subtitle` ưu tiên dễ đọc trên màn hình, có thể xuống dòng qua DisplaySegment.
`VI Dubbing` ưu tiên lời nói tự nhiên và giới hạn thời lượng cho TTS. Hai trường
có thể giống nhau ở đầu workflow nhưng được tối ưu cho hai mục tiêu khác nhau.

## Ngữ cảnh dịch và tên riêng

Tab **Translate → Ngữ cảnh & văn phong** có 27 profile dựng sẵn và cho phép
chọn nhiều profile cùng lúc, ví dụ **Cổ trang + Xuyên không + Hài hước**. Mỗi
profile có mô tả chi tiết ngay trong UI; API chỉ nhận chỉ dẫn rút gọn của những
profile đang được chọn. Nhập yêu cầu đặc thù của truyện vào **Bối cảnh bổ sung**.

Chế độ tên riêng gồm **Hán Việt (mặc định)**, **Giữ nguyên theo nguồn** và
**Theo Mapping của user**. Mapping chấp nhận cả `Xuanyi = Huyền Nhất` và
`Qingyun Sect -> Thanh Vân Tông`; dòng trống hoặc bắt đầu bằng `#` được bỏ qua.
Thứ tự ưu tiên là Mapping → bối cảnh bổ sung → profile đã chọn → quy tắc nền →
kiến thức chung của model. Bấm **Áp dụng ngữ cảnh / văn phong** để lưu lựa chọn;
thay đổi này làm bản dịch AI cũ trở nên stale nhưng không chạy lại transcript.

## Audio

Local_TTS chỉ là backend tạo WAV. Cartoon_Sub tải `audio_url`, kiểm tra WAV và
lưu bản sao vào `audio/tts/segments/`; không phụ thuộc thư mục output của server.

- **Preview selected voice** chỉ nghe thử voice, không thay thế WAV timeline.
- **Generate / Resume TTS** reuse item hợp lệ theo fingerprint.
- Checkbox speaker và **Apply voice to checked speakers** gán cùng một voice;
  Preview vẫn dùng row đang active.
- **Build Dubbed Audio** đặt toàn bộ WAV vào timestamp Utterance, gồm cả overlap.
- **Build Final Audio** trộn dubbed mix với audio gốc và Additional Audio theo
  volume/offset. Vì vậy cần Build Dubbed Audio trước Final Audio.

## Giao diện, tiến độ và hủy

Window fit trong vùng màn hình khả dụng; tab có thanh cuộn dọc khi cần. Wheel
trên ô số không đổi value ngoài ý muốn và được chuyển thành cuộn trang cha.
Ctrl+Z hoàn tác edit đã commit; Ctrl+Y hoặc Ctrl+Shift+Z redo. Undo không gọi
AI/TTS và không tự render lại artifact.
Tác vụ nền hiển thị status cùng progress `X/Y` khi có tổng, hoặc busy indicator
khi chưa biết tổng. Bấm Cancel đổi ngay thành `Cancelling...`; checkpoint đã
hoàn tất vẫn được giữ để Resume.

## Export video

Test render nằm ở `<ProjectRoot>/test_30s.mp4`, full render ở
`<ProjectRoot>/final.mp4`. File trung gian nằm trong
`<ProjectRoot>/.tmp/export/<job-id>/`, được dọn khi xong/lỗi/hủy. Output cũ chỉ
bị thay thế sau khi file mới được render hợp lệ.

## Lỗi thường gặp

- `429` / `503`: chờ retry hoặc chạy lại; cache đã hoàn tất được reuse.
- `GEMINI_QUOTA_EXHAUSTED`: quota ngày hết, cần chờ quota hoặc đổi project/key
  có quyền; chạy lại sau đó sẽ resume cache.
- Local_TTS chưa READY: khởi động Local_TTS, bấm Test connection, kiểm tra voice
  có trạng thái READY.
- `TTS_AUDIO_STALE`: Generate/Resume các câu bị stale trước khi build mix.
- `FINAL_AUDIO_STALE`: Build Final Audio lại trước khi export.
- FFmpeg missing: cài FFmpeg/ffprobe vào `PATH` rồi mở lại ứng dụng.
- WinError 5: file output đang bị player/Explorer khóa; đóng chương trình đang
  dùng file và chạy lại.

## Use cases

- Video ngắn: Transcript → review speaker → Translate → Subtitle → Export.
- Video dài/429/503: chạy lại Transcript; chunk hoàn tất không gọi API lại.
- App dừng giữa Translate: mở project, xác nhận context nếu được yêu cầu, rồi
  Translate lại; batch hoàn tất được dùng từ cache.
- Đổi voice: chỉ các Utterance dùng speaker đó trở nên stale và cần tạo TTS lại.
- Hai người nói chồng: giữ Utterance riêng; Subtitle hỗ trợ multi-lane.
- Di chuyển project: copy cả folder project; các TTS paths tương đối vẫn hoạt
  động. Giữ video nguồn ở vị trí cũ hoặc chọn lại video nếu external path đổi.
