# Đóng gói bản Portable ZIP / Chuyển sang PC khác

## Trạng thái build hiện tại

Cartoon_Sub hiện là ứng dụng **chạy từ source**, chưa có pipeline binary portable:

- package Python được khai báo trong `pyproject.toml` (`cartoon-sub` v0.3.0);
- GUI chạy bằng console script `cartoon-sub`, module `cartoon_sub.app.main` hoặc `Cartoon_Sub_GUI.pyw`;
- repository không có PyInstaller `.spec`, build script hoặc artifact `Cartoon_Sub.exe` được định nghĩa;
- `ffmpeg` và `ffprobe` được gọi bằng tên executable, tức phải có trong `PATH`;
- font không được bundle; renderer dùng font đã cài trên Windows.

Vì vậy hiện chưa thể tạo `Cartoon_Sub_Portable_v0.3.0.zip` dạng binary standalone chỉ bằng các file có sẵn trong repo. Không dùng lệnh PyInstaller tự đoán. Cho đến khi có build pipeline được kiểm thử, PC đích vẫn cần Python 3.11+, cài dependencies và FFmpeg/ffprobe.

## Các file cần cho source-transfer hiện tại

| Loại | Có đưa vào ZIP? | Lý do |
| --- | --- | --- |
| `pyproject.toml` | REQUIRED | Dependencies, Python version và GUI entrypoint |
| `Cartoon_Sub_GUI.pyw` | REQUIRED/OPTIONAL | Launcher Windows thuận tiện; module entrypoint vẫn dùng được |
| `src/cartoon_sub/` | REQUIRED | Application code, embedded docs UI, icon assets và prompts |
| `README.md`, `docs/` | OPTIONAL nhưng khuyến nghị | Hướng dẫn setup/troubleshooting |
| `tests/` | DEV ONLY — DO NOT PACKAGE cho end user | Không cần để chạy app |
| `.venv/`, `__pycache__/`, `*.egg-info/` | DEV ONLY — DO NOT PACKAGE | Environment/cache theo máy build |
| `.git/`, `.github/`, `_inventory/`, `_share_*/` | DEV ONLY — DO NOT PACKAGE | Metadata/audit copies |
| `build/`, `dist/`, `.tmp-tests/` | GENERATED — DO NOT PACKAGE | Staging/test artifacts |
| project folders, `project.json`, source video, WAV/MP3/MP4, cache, preview, output, logs | PRIVATE — DO NOT PACKAGE | Dữ liệu người dùng/project |
| `.env`, API keys, keyring export, `%USERPROFILE%\.cartoon_sub` | PRIVATE — DO NOT PACKAGE | Secrets và settings cá nhân |

`src/cartoon_sub/assets/cartoon_sub.png` và `.ico`, cùng `src/cartoon_sub/prompts/*.txt`, là package data bắt buộc. Khi có binary build trong tương lai, build script phải bundle chúng và smoke test icon/prompts từ staging; task hiện tại không thêm pipeline đó.

## FFmpeg và fonts

Cartoon_Sub **không bundle** `ffmpeg.exe` hoặc `ffprobe.exe`, cũng không có resolver cho thư mục `tools/ffmpeg`. PC đích phải cài FFmpeg và thêm cả hai executable vào `PATH` trước khi mở app:

```powershell
ffmpeg -version
ffprobe -version
```

Không chỉ copy FFmpeg vào một thư mục tự đặt rồi kỳ vọng app tìm thấy. Sau khi sửa `PATH`, đóng và mở lại Cartoon_Sub.

Subtitle dùng font Windows đã cài. Nếu font đã chọn không tồn tại trên PC mới, FFmpeg/libass có thể dùng font thay thế. Logo do project tham chiếu phải được copy cùng project và giữ/cập nhật path.

## Local_TTS discovery

Endpoint mặc định là `http://127.0.0.1:8765`. Source-mode autodiscovery kiểm tra một số layout workspace tương đối, trong đó có Local_TTS nằm cạnh Cartoon_Sub và các layout dev `TTS_Sub/Local_TTS`. Đây không phải bằng chứng cho một binary Cartoon_Sub frozen vì binary build chưa tồn tại.

Workflow ổn định trên PC mới:

1. Giải nén/chạy Local_TTS và chờ `/api/health` READY.
2. Mở Cartoon_Sub.
3. Vào **Audio → Select Local_TTS…** và chọn `Local_TTS.exe` nếu Auto Start chưa tìm thấy.
4. Kiểm tra URL `http://127.0.0.1:8765` rồi bấm **Test connection**.

Đường dẫn executable đã chọn được lưu trong `%USERPROFILE%\.cartoon_sub\local_tts.json`; khi di chuyển bundle sang vị trí khác cần Select lại. API key nằm trong Windows keyring hoặc được nhập trên máy mới, không đưa vào ZIP.

## Source-transfer workflow hiện được hỗ trợ

Đây là gói chuyển source sạch, **không phải binary portable**.

Trên máy build:

```powershell
$release = 'C:\release'
$stage = Join-Path $release 'Cartoon_Sub_Source_v0.3.0'
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null

Copy-Item .\pyproject.toml,.\Cartoon_Sub_GUI.pyw,.\README.md -Destination $stage
Copy-Item .\src -Destination $stage -Recurse
Copy-Item .\docs -Destination $stage -Recurse
```

Không copy `.env`, `.venv`, numeric/user project folders, media, logs hoặc cache.

Tạo ZIP tại release directory riêng, không tạo bên trong source hoặc staging app:

```powershell
Compress-Archive `
  -Path 'C:\release\Cartoon_Sub_Source_v0.3.0\*' `
  -DestinationPath 'C:\release\Cartoon_Sub_Source_v0.3.0.zip' `
  -Force
Get-FileHash 'C:\release\Cartoon_Sub_Source_v0.3.0.zip' -Algorithm SHA256
```

## Chuẩn bị máy Windows mới

Với source-transfer hiện tại:

- Windows 10/11;
- Python `>=3.11`;
- FFmpeg và ffprobe trong `PATH`;
- internet khi dùng Gemini/provider dịch hoặc khi Local_TTS cần tải model;
- font dùng cho subtitle đã cài trên Windows;
- Local_TTS tùy chọn cho dubbing.

Không có requirement GPU/NVIDIA/CUDA trong Cartoon_Sub. AI transcript/dịch gọi remote API; Local_TTS dùng CPU/ONNX theo guide riêng. Repository chưa chứng minh requirement Visual C++ riêng cho source-mode Cartoon_Sub; dependencies native có thể mang yêu cầu riêng của Python/PySide/FFmpeg.

Khuyến nghị giải nén vào thư mục ngắn, writable như `C:\CartoonSuite\Cartoon_Sub`, không phải `Program Files`. Project phải nằm ngoài `.venv`, `src` và mọi `_internal` tương lai:

```text
C:\CartoonSuite\
├── Cartoon_Sub\
├── Local_TTS\
└── Projects\
```

Tại PC mới:

```powershell
cd C:\CartoonSuite\Cartoon_Sub
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\cartoon-sub.exe
```

Sau khi mở app, vào **Settings → AI…** và nhập lại API key. Không mang `.env` hay credential vault từ máy cũ.

## Separate ZIP và combined transfer

### Option A — Separate

```text
C:\release\
├── Cartoon_Sub_Source_v0.3.0.zip
└── Local_TTS_Portable_v0.1.0.zip
```

### Option B — Combined transfer hiện tại

```text
CartoonSuite_Transfer/
├── Cartoon_Sub/          # source-transfer; PC đích vẫn cài Python/dependencies
├── Local_TTS/            # PyInstaller one-folder đầy đủ
└── Projects/             # tạo sau khi giải nén, không chứa project cá nhân trong release
```

Combined được khuyến nghị cho vận hành dubbing, nhưng chưa được gọi là standalone portable vì Cartoon_Sub chưa có binary package. Không duplicate Local_TTS model files trong Cartoon_Sub.

Không có version chung cho CartoonSuite; dùng `CartoonSuite_Transfer.zip`, không tự tạo version suite. Hai ZIP riêng có thể dùng version từ `pyproject.toml` của từng repo.

## Smoke test tại đường dẫn khác

Giải nén vào `C:\PortableTest\CartoonSuite`, không chạy ngay trong source dev path.

Cartoon_Sub source-transfer:

1. tạo `.venv` mới và cài `-e .` như trên;
2. xác nhận icon/Docs mở được;
3. chạy `ffmpeg -version` và `ffprobe -version` trong cùng môi trường;
4. tạo/mở một project test ngoài app folder;
5. mở Local_TTS từ folder staging, chờ READY;
6. **Audio → Select Local_TTS… → Test connection**;
7. preview một READY voice.

Smoke này chứng minh source-transfer ở đường dẫn khác, không chứng minh binary portable. Source-tree-independent binary smoke chỉ có thể làm sau khi Cartoon_Sub có build pipeline.

## Update/chuyển project

- Backup toàn bộ project directory và source video trước khi update app.
- Không đặt project trong app folder; thay app/source folder mà không overwrite project.
- `project.json` giữ TTS artifacts nội bộ theo relative path, nhưng source video, logo hoặc additional audio bên ngoài có thể vẫn là absolute path. Copy các file ngoài đó và cập nhật/chọn lại nếu path đổi.
- Settings/API keys nằm trong user profile/keyring, không nằm trong app ZIP. Sau khi chuyển máy cần cấu hình lại.
- Sau update, test FFmpeg và Local_TTS connection trước khi tiếp tục job dài.

## Portability blockers

1. Chưa có PyInstaller `.spec`/build script/`Cartoon_Sub.exe` được kiểm thử.
2. FFmpeg/ffprobe là external `PATH` dependency, chưa có bundled resolver.
3. `local_tts_executable` có thể là absolute user setting và cần chọn lại sau khi di chuyển.
4. Project có thể tham chiếu absolute source video/logo/additional audio ở ngoài project.

## Redistribution warning

Trước khi public ZIP, xác minh quyền phân phối của logo/icon, fonts, FFmpeg build, Python/PySide dependencies và mọi third-party assets. Không kèm project/media/API key cá nhân. Tài liệu này không tuyên bố quyền sử dụng thương mại.

## Checklist

### BEFORE ZIP

- [ ] Build Release (hiện chỉ Local_TTS có binary Release)
- [ ] Remove secrets và `.env`
- [ ] Remove cache/log/temp/project/media cá nhân
- [ ] Verify Local_TTS assets
- [ ] Verify FFmpeg/ffprobe requirement
- [ ] Verify resource và project paths
- [ ] Smoke test staging folder tại đường dẫn khác
- [ ] Create ZIP ngoài repo/app folder
- [ ] Calculate SHA256
- [ ] Test ZIP after extraction

### TARGET PC

- [ ] Extract to writable folder
- [ ] Install Python 3.11+ cho Cartoon_Sub source-transfer
- [ ] Install required system runtime nếu dependency yêu cầu
- [ ] Install FFmpeg/ffprobe vào PATH
- [ ] Configure API key trong Settings
- [ ] Start/Test Local_TTS
- [ ] Start Cartoon_Sub
- [ ] Test FFmpeg
- [ ] Create a test project ngoài app folder
- [ ] Preview one TTS voice
