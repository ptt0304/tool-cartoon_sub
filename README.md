# Cartoon_Sub

Cartoon_Sub is a Windows desktop tool for Chinese-to-Vietnamese subtitle production and local video rendering.

Workflow: **Video → Transcript → Translate → Subtitle → Mask → Audio/Dubbing → Export**.

## What it supports

- Transcript sources: **Audio**, **Video subtitle OCR**, and imported Chinese SRT.
- Vietnamese translation, subtitle segmentation, mask/style placement, Local_TTS dubbing, and FFmpeg preview/final render.
- AI requests use **OpenRouter only**. Model IDs may belong to Google, OpenAI, Anthropic, or other model authors, but requests go through OpenRouter.

## Requirements

- Windows 10 or 11
- Python 3.11+
- FFmpeg and FFprobe available on `PATH`
- An OpenRouter API key for AI features

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python Cartoon_Sub_GUI.pyw
```

## API key setup

Copy `api_key.example.txt` to `api_key.txt`, then replace the placeholder:

```powershell
Copy-Item api_key.example.txt api_key.txt
```

Use the format `openrouter_key: YOUR_KEY`. Never commit `api_key.txt`.

`settings.example.json` is a safe reference only. Local settings and keys remain machine-specific.

## Project data

A local project normally contains `project.json`, source media, and generated folders such as `cache/`, `audio/`, `translation/`, `subtitle/`, `preview/`, and `output/`. These are user/runtime data and are intentionally ignored by Git. Keep a backup before destructive project edits.

## Build and publish locally

The repository uses PyInstaller through the existing PowerShell workflow:

```powershell
.\build_exe.ps1
```

Build artifact: `dist\Cartoon_Sub\Cartoon_Sub.exe`.

The local publish target is `Cartoon_Sub.exe` plus `_internal\`. These packaged artifacts are ignored by source Git; distribute them with GitHub Releases only when desired.

## Tests

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Run a focused test module first when changing one feature area.

## Cache behavior

Transcript, translation, OCR, subtitle, TTS, preview, and render artifacts are cached locally under the project directory. Cache data is not committed and does not travel with the source repository.
