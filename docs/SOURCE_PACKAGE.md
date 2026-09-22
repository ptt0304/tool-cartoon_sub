# Source-only ZIP / Developer Transfer Package

Tài liệu này mô tả gói **source-only** để chuyển mã nguồn Cartoon_Sub sang một máy Windows khác. Đây không phải binary portable và không chứa Python, virtual environment, FFmpeg, thư viện pip, project hay media người dùng.

## Why source-only ZIP? / Vì sao dùng source-only ZIP?

- Gói nhỏ, dễ kiểm tra và không mang cache/môi trường máy build sang máy khác.
- Dependencies được cài lại từ `pyproject.toml`, đúng với Python trên PC đích.
- Tránh đóng gói secrets, dữ liệu project, media, log và file generated.
- Phù hợp cho developer transfer; PC đích phải cài Python và các dependency hệ thống.

## Nội dung ZIP

### Phân loại A–H

| Nhóm | Cartoon_Sub |
| --- | --- |
| A. REQUIRED SOURCE | `pyproject.toml`, `Cartoon_Sub_GUI.pyw`, toàn bộ `src/cartoon_sub/` |
| B. REQUIRED RUNTIME RESOURCE | `src/cartoon_sub/assets/`, `src/cartoon_sub/prompts/`; chúng phải giữ nguyên vị trí trong package |
| C. OPTIONAL | Tài liệu ngoài `docs/SOURCE_PACKAGE.md`; chỉ copy khi developer cần đọc offline |
| D. DEV ONLY | `tests/`, `.env.example`, tooling/lint/coverage files |
| E. GENERATED | `.venv/`, `build/`, `dist/`, `*.egg-info`, cache, logs, temp, project và mọi media/output |
| F. EXTERNAL DEPENDENCY | Python/pip packages từ `pyproject.toml`, FFmpeg/ffprobe, Windows fonts, Internet/API cloud, Local_TTS khi dubbing |
| G. LARGE ASSET — INSTALL SEPARATELY | Local_TTS model/voice assets; Cartoon_Sub không sở hữu model TTS trong source ZIP |
| H. PRIVATE — NEVER PACKAGE | `.env`, API keys/tokens/credentials, keyring export, user projects/media và dữ liệu voice riêng |

### REQUIRED source/core

| Path | Vai trò |
| --- | --- |
| `pyproject.toml` | Metadata, Python `>=3.11`, dependencies và console script |
| `Cartoon_Sub_GUI.pyw` | Launcher GUI từ source |
| `src/cartoon_sub/` | Toàn bộ application package, gồm `assets/` và `prompts/` |
| `README.md` | Hướng dẫn cài/chạy và liên kết tài liệu |
| `docs/SOURCE_PACKAGE.md` | Hướng dẫn transfer này |

```text
Cartoon_Sub_Source/
├── pyproject.toml
├── Cartoon_Sub_GUI.pyw
├── README.md
├── docs/SOURCE_PACKAGE.md
└── src/cartoon_sub/              # gồm assets/ và prompts/
```

`docs/` còn lại và `tests/` hữu ích cho development nhưng không bắt buộc để chạy ứng dụng. Nếu chuyển cả tài liệu hoặc test, copy có chủ đích; không đưa chúng vào minimal ZIP mặc định.

Không copy `.venv`: thư mục này lớn, chứa dependency/native wheel theo Python và máy cũ, có thể lưu absolute path và không bảo đảm hoạt động trên PC khác. PC đích phải tạo venv mới từ `pyproject.toml`; cần Internet hoặc một pip wheel cache hợp lệ để cài dependencies.

### Không đưa vào ZIP

- `.git/`, `.github/`, `.venv/`, `venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- `env/`, `tests/`, `*.pyc`, `*.pyo`
- `build/`, `dist/`, `*.egg-info/`, coverage và package cache
- `.env`, API keys, credentials, keyring export hoặc file cấu hình chứa secret
- Python interpreter, pip cache/site-packages, FFmpeg/ffprobe, Windows runtime hoặc system DLL
- project người dùng, source video/audio, subtitle, transcript, output render, TTS output, logs và temp files
- model weights, voice libraries hoặc Local_TTS runtime; Local_TTS được chuyển bằng ZIP riêng
- CUDA Toolkit, GPU driver, Visual Studio Build Tools, Visual C++ runtime và mọi system dependency khác

## Tạo `Cartoon_Sub_Source.zip` bằng Git Bash

Chạy từ root repo Cartoon_Sub. Lệnh dùng staging sạch tại `C:\release`; thay biến `RELEASE` nếu muốn vị trí khác.

```bash
cd /d/Documents/MMO/Youtube/Tools/Cartoon_Sub

RELEASE="/c/release"
STAGE="$RELEASE/Cartoon_Sub_Source"
ZIP="$RELEASE/Cartoon_Sub_Source.zip"

rm -rf "$STAGE"
mkdir -p "$STAGE/docs"
cp pyproject.toml Cartoon_Sub_GUI.pyw README.md "$STAGE/"
tar -cf - --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' --exclude='*.egg-info' src | tar -xf - -C "$STAGE"
cp docs/SOURCE_PACKAGE.md "$STAGE/docs/"

rm -f "$ZIP"
STAGE_WIN="$(cygpath -w "$STAGE")"
ZIP_WIN="$(cygpath -w "$ZIP")"
powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::CreateFromDirectory('$STAGE_WIN', '$ZIP_WIN')"
certutil.exe -hashfile "$ZIP_WIN" SHA256
```

Kiểm tra staging trước khi ZIP:

```bash
find "$STAGE" -type f | sort
du -sh "$STAGE" "$ZIP"
```

Không chạy lệnh trên nếu `STAGE` đã được đổi thành một thư mục chứa dữ liệu cần giữ: bước đầu tiên xóa toàn bộ staging đó.

## Cài trên PC đích

Layout khuyến nghị:

```text
C:\CartoonSuite\
├── Cartoon_Sub\
├── Local_Sub\
└── Projects\
```

### 1. Chuẩn bị hệ thống

1. Cài Python 3.11 x64. Manifest chấp nhận Python `>=3.11`; các lệnh dưới đây cố định interpreter 3.11 để dễ tái tạo.
2. Cài FFmpeg build có cả `ffmpeg.exe` và `ffprobe.exe`, rồi thêm thư mục `bin` vào Windows `PATH`.
3. Git chỉ cần nếu muốn Git Bash; ứng dụng không cần Git để chạy.
4. Giải nén `Cartoon_Sub_Source.zip` vào `C:\CartoonSuite\Cartoon_Sub`.
5. Đặt project tại `C:\CartoonSuite\Projects`, không đặt trong source hoặc `.venv`.

### 2. Tạo môi trường và cài package

#### FIRST INSTALL

Trong Git Bash:

```bash
cd /c/CartoonSuite/Cartoon_Sub
py -3.11 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install --upgrade setuptools wheel
python -m pip install .
python -m pip check
```

Mỗi app phải có `.venv` riêng; không dùng chung venv với Local_TTS.

#### REINSTALL ENVIRONMENT

Chỉ xóa `.venv` nằm ngay trong source root; không xóa source, settings hoặc project:

```bash
cd /c/CartoonSuite/Cartoon_Sub
test "$(pwd)" = "/c/CartoonSuite/Cartoon_Sub" || exit 1
rm -rf .venv
py -3.11 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install .
python -m pip check
```

### 3. Kiểm tra dependency hệ thống

```bash
python --version
which ffmpeg
which ffprobe
ffmpeg -version
ffprobe -version
```

Nếu `which` không tìm thấy executable, mở lại terminal sau khi sửa `PATH`. Font render lấy từ Windows; cài font mong muốn trên PC đích nếu project dùng font không có sẵn.

### 4. Khởi động và cấu hình

```bash
cd /c/CartoonSuite/Cartoon_Sub
source .venv/Scripts/activate
python -m cartoon_sub.app.main
```

Sau khi mở app:

1. Cấu hình Gemini/API provider trong Settings; không copy `.env` hoặc API key vào ZIP.
2. Khởi động Local_TTS từ package riêng.
3. Trong tab Audio, dùng **Select Local_TTS…** nếu auto-discovery không tìm đúng `C:\CartoonSuite\Local_Sub`, rồi **Test connection** với `http://127.0.0.1:8765`.
4. Tạo project test trong `C:\CartoonSuite\Projects`, import một video ngắn có audio, chạy smoke test transcript/voice theo nhu cầu.

Gemini và các cloud provider cần Internet. FFmpeg/ffprobe là bắt buộc cho media workflow. Cartoon_Sub không yêu cầu NVIDIA/CUDA; yêu cầu tăng tốc của model local thuộc Local_TTS.

## Quy trình PC đích đầy đủ

1. Tạo `C:\CartoonSuite` và `C:\CartoonSuite\Projects`.
2. Cài Python 3.11 x64.
3. Cài FFmpeg/ffprobe và thêm vào `PATH`.
4. Giải nén `Local_Sub_Source.zip` vào `C:\CartoonSuite\Local_Sub`.
5. Tạo `.venv` riêng cho Local_Sub và cài `pip install -e .`.
6. Provision model/voice external theo `docs/SOURCE_PACKAGE.md` hoặc README đi kèm `Local_Sub_Source.zip`.
7. Chạy `python -m pip check` trong venv Local_Sub.
8. Start Local_TTS và chờ trạng thái READY.
9. Kiểm tra `http://127.0.0.1:8765/api/health`.
10. Giải nén `Cartoon_Sub_Source.zip` vào `C:\CartoonSuite\Cartoon_Sub`.
11. Tạo `.venv` riêng cho Cartoon_Sub và cài `pip install -e .`.
12. Chạy `python -m pip check`, `ffmpeg -version` và `ffprobe -version`.
13. Start Cartoon_Sub, cấu hình API key và chọn Local_TTS nếu cần.
14. Tạo project test ngoài hai thư mục app.
15. Smoke test import video có audio, kết nối TTS và preview một voice READY.

## Chạy hai app

Terminal 1:

```bash
cd /c/CartoonSuite/Local_Sub
source .venv/Scripts/activate
python -m local_tts
```

Terminal 2:

```bash
cd /c/CartoonSuite/Cartoon_Sub
source .venv/Scripts/activate
python -m cartoon_sub.app.main
```

Trong Cartoon_Sub: **Audio → Local_TTS URL → Test connection**. Auto Start có thể dùng executable/source path đã cấu hình; sau khi transfer, chọn lại Local_TTS nếu đường dẫn máy cũ không còn hợp lệ.

## Checklist

### Trước khi ZIP

- [ ] Source changes đã commit hoặc được review rõ ràng
- [ ] `pyproject.toml`, entrypoint và `src/` có trong staging
- [ ] Staging mới chỉ chứa danh sách REQUIRED
- [ ] `src/cartoon_sub/assets/` và `src/cartoon_sub/prompts/` có trong staging
- [ ] `README.md` và source guide có trong staging
- [ ] Không có `.venv`, `.git`, tests, build/dist/cache/log, `.env` hoặc secrets
- [ ] Không có project, media, transcript, output, logs hoặc temp
- [ ] Không có model weights, FFmpeg hoặc system runtime
- [ ] Tên ZIP là `Cartoon_Sub_Source.zip`
- [ ] ZIP được tạo ngoài repo
- [ ] SHA256 đã được ghi lại

### Trên PC đích

- [ ] Giải nén vào `C:\CartoonSuite\Cartoon_Sub`
- [ ] Python 3.11 x64 và venv riêng hoạt động
- [ ] `python -m pip install .` và `python -m pip check` thành công
- [ ] `ffmpeg` và `ffprobe` có trong `PATH`
- [ ] API key được nhập trên PC đích, không lấy từ ZIP
- [ ] Local_TTS venv/dependencies/model/voices đã setup; health READY và connection thành công
- [ ] App mở được, Docs tải được và tạo được project mới
- [ ] Import video có audio thành công; không cần render full video
- [ ] Project test nằm trong `C:\CartoonSuite\Projects`

Binary portable là quy trình khác; xem [Portable ZIP](PORTABLE.md). Source-only ZIP luôn yêu cầu cài Python và dependencies trên máy đích.
