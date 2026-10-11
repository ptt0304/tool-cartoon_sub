# Development notes

## Layout

- `src/cartoon_sub/`: application code.
- `tests/`: automated tests.
- `docs/`: maintained source documentation.
- `Projects/`: local user projects and generated media; ignored by Git.

## AI and keys

Runtime AI routing is OpenRouter-only. Store the real credential in root `api_key.txt` using `openrouter_key: ...`; the file is ignored. `api_key.example.txt` is safe to commit.

## Test and build

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\build_exe.ps1
```

PyInstaller writes `dist\Cartoon_Sub\Cartoon_Sub.exe` and the local workflow publishes `Cartoon_Sub.exe` and `_internal\`. Do not commit packaged output.

## Local cache paths

Project caches, audio, previews, render output, logs, and Project 1 media are local runtime data. Keep Project 1 available for local regression checks, but never add it to Git.

## Production invariants

- `Utterance` is the canonical dialogue record.
- Display segments are derived presentation data.
- Transcript, translation, mask, TTS, and rendering caches must not contain credentials.
- Do not introduce direct provider clients outside OpenRouter without an explicit architecture change.
