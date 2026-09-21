# Mask, subtitle style và render

## Workflow

1. Mở project và vào tab **Mask**.
2. Chọn mốc thời gian rồi bấm lấy frame.
3. Kéo rectangle trên frame hoặc nhập X/Y/Width/Height theo pixel video nguồn.
4. Bật mask và chọn `Solid` hoặc `Gaussian`.
5. Chỉnh style chữ, logo/watermark rồi lưu hoặc tạo preview 10 giây.

Mask là một rectangle cố định áp dụng cho toàn video; chưa có OCR, tracking, keyframe, nhiều mask hoặc inpainting.

## Mask modes

- `solid`: fill rectangle bằng **Màu khung mask**. Chọn `#FFFFFF` để có nền trắng, `#000000` để có nền đen hoặc chọn màu khác.
- `gaussian`: blur nội dung video trong rectangle. Strength điều khiển mức blur; màu mask không ảnh hưởng Gaussian.

Không có logic đặc biệt “nền trắng thì chữ đen”. Màu chữ và viền luôn lấy từ style người dùng.

## Subtitle style

Preview phản ánh font, size, bold, alignment, outline, shadow, text color và outline color hiện tại. Preview chỉ là presentation, không sửa `VI Subtitle`, `VI Dubbing`, Chinese, speaker hoặc timing.

Renderer burn `VI Subtitle`; `VI Dubbing` dành cho TTS. Khi `DisplaySegment` tồn tại, renderer dùng các segment trình bày đó; nếu không, dùng Utterance. Các câu speaker overlap vẫn là event độc lập và có thể được xếp nhiều lane. Chế độ speaker label gồm overlap-only, always, off và debug timing.

## Logo và watermark

- Logo: file ảnh, vị trí, kích thước/scale theo ảnh gốc, rotation và transparency.
- Watermark: text, font, size, color, opacity và vị trí.
- Drag/resize và các control được hỗ trợ bởi undo stack ở các thao tác đã triển khai.

## Outputs

Mask tab tạo job riêng để không ghi đè kết quả trước:

```text
<ProjectRoot>/
├── preview/<job-id>/
│   ├── preview.mp4
│   ├── subtitle.ass
│   └── render.json
└── output/<job-id>/
    ├── final.mp4
    ├── subtitle.ass
    └── render.json
```

Tab Export là workflow xuất bản chính: nó yêu cầu `audio/final_audio.wav` hợp lệ và ghi `test_30s.mp4` hoặc `final.mp4` tại project root. Preview/render trong tab Mask và Export video là hai entrypoint khác nhau.

## Persistence và compatibility

`project.json` lưu mask rectangle/type/strength/color, subtitle style, logos và watermark. Project cũ không có color fields sẽ dùng default tương thích: chữ trắng, viền đen và mask đen. Style/mask không tham gia fingerprint dịch và không gọi AI.

Video nguồn có thể nằm ngoài project; để backup đầy đủ cần giữ cả project directory và source video. API keys không nằm trong project.
