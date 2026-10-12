# Cartoon_Sub Transcript Handoff

Written by Kiro when work was stopped mid-Phase 1. Facts below come from the source and tool output seen in this session. Anything not proven is labelled unverified.

## Current Goal

Split Transcript into two user-selectable acquisition modes:

1. Audio Transcript (mode value `audio`, UI text "Lấy transcript từ Audio"). Existing behavior must remain unchanged.
2. Video Subtitle Transcript (mode value `video`, UI text "Lấy transcript từ Video"). Future flow: extract hardcoded Chinese subtitles from a user-selected ROI. Local frame analysis (ffmpeg-only, no OpenCV/NumPy/Pillow) decides subtitle start/end. OpenRouter Vision only reads Chinese text from representative cropped frames.

Decisions already made by the user:
- Frame analysis: ffmpeg-only first. Report before adding any image dependency.
- Vision model key: independent `transcript_video` (do not reuse `transcript`).
- Timing provenance for Video rows: `VIDEO_SUBTITLE`.
- Persistent config lives in Project fields (`transcript_source_mode`, `video_subtitle_roi`), ROI normalized (`x_pct`, `y_pct`, `w_pct`, `h_pct`). Generated Video data goes under `cache/transcript/video/`.

## Current Phase

Phase 1 (routing only) was started and is NOT complete.
- Phase 0 audit is complete: `logs/transcript_video/phase_0_audit.log`.
- `logs/transcript_video/phase_1_routing.log` has NOT been written (it was to be written only after stabilization).

## Changes Already Implemented

Phase 1 edits cannot be isolated with `git diff`: the working tree already contained many uncommitted changes before Phase 1 (HEAD `72eac81` is behind the working tree). Do not use HEAD as a baseline.

| Path | What changed | Why | State |
|---|---|---|---|
| `src/cartoon_sub/transcription/mode.py` (new) | `TranscriptSourceMode` enum (`audio`, `video`), `DEFAULT_MODE="audio"`, `MODE_LABELS`, `normalize_mode()`, `normalize_roi()`, `ROI_KEYS` | Single place for mode and ROI validation; no model imports so `models.py` can use it | Complete for Phase 1 |
| `src/cartoon_sub/transcription/video/__init__.py` (new) | Empty package marker | Package for future Video code | Complete |
| `src/cartoon_sub/transcription/video/pipeline.py` (new) | `VideoTranscriptNotImplemented(ValueError)`; `VideoTranscriptionPipeline(settings_store).run(project, directory, *, cancel=None, progress=None)` stub | Controlled Video route. Raises for status `imported`, for empty ROI, otherwise raises `VideoTranscriptNotImplemented`. No processing | Stub, intentionally partial |
| `src/cartoon_sub/subtitle/models.py` | `"VIDEO_SUBTITLE"` added to the allowed `transcript_timing_provenance` set; `Project.transcript_source_mode: str = "audio"`; `Project.video_subtitle_roi: dict = {}`; `from_dict` normalizes/validates both | Persistent mode/ROI and Video timing provenance | Complete, regression unverified |
| `src/cartoon_sub/app/settings.py` | `"transcript_video"` added to `allowed_tabs` in `AISettings.validate()` | Independent Vision model override key | Partial: no UI selector, no consumer yet |
| `src/cartoon_sub/project/paths.py` | `ProjectPaths.transcript_video_cache_dir` = `<root>/cache/transcript/video` | Video cache location. Not created by `ensure()` | Complete |
| `src/cartoon_sub/project/stage_reset_service.py` | `reset_transcript` also removes `cache/transcript/video` (only inside the `clear_stt_cache` branch) | Reset must clear generated Video data | Complete, test status unverified |
| `src/cartoon_sub/app/controller.py` | Imports; `self.video_pipeline = VideoTranscriptionPipeline(...)`; new `transcribe_video`, `set_transcript_source_mode`, `run_transcript`. `transcribe` unchanged | Routing and persistence | Complete for Phase 1 |
| `src/cartoon_sub/ui/tabs/transcript_tab.py` | `mode_combo` (2 items), `video_mode_hint`, stored labels `stt_label`/`ai_label`, `apply_mode()`, `set_mode()` | Mode selector; Audio widgets hidden in Video mode | Complete, UI untested by hand |
| `src/cartoon_sub/ui/main_window.py` | `mode_combo.currentIndexChanged` -> `_on_transcript_mode_changed`; `transcribe()` now calls `controller.run_transcript`; `refresh()` calls `transcript_tab.set_mode(...)` | Persist mode, route the button | Complete, UI untested by hand |
| `tests/test_transcript_mode_routing.py` (new) | 11 tests: Project fields, round trip, invalid values, provenance, `transcript_video` key, routing, mode persistence, tab widgets | Cover Phase 1 | See Tests |

## New Data/Enums

- `Project.transcript_source_mode`: `str`, default `"audio"`. Allowed: `"audio"`, `"video"`. Any other value (including `None` and `""`) raises `ValueError` in `Project.from_dict`.
- `Project.video_subtitle_roi`: `dict`, default `{}` (not selected). When set it must have exactly keys `x_pct`, `y_pct`, `w_pct`, `h_pct`: finite int/float, `x_pct>=0`, `y_pct>=0`, `w_pct>0`, `h_pct>0`, `x_pct+w_pct<=1`, `y_pct+h_pct<=1` (epsilon 1e-9). Stored as floats. Otherwise `ValueError`.
- `TranscriptSourceMode` (`str` Enum): `AUDIO="audio"`, `VIDEO="video"`.
- `Utterance.transcript_timing_provenance` allows `"VIDEO_SUBTITLE"`. All earlier values remain allowed. Nothing sets it yet.
- Settings key `transcript_video` in `AISettings.tab_model_overrides`. With no override, `tab_selection("transcript_video")` falls back to the default AI selection. Not consumed anywhere yet.
- Cache path `cache/transcript/video/` (`ProjectPaths.transcript_video_cache_dir`). Nothing writes to it yet.
- Note: `VIDEO_SUBTITLE` is only a timing provenance value. The route/mode value is `"video"`.

## Current Routing

```
Transcript button
  -> MainWindow.transcribe (ui/main_window.py)      # sync_options, controller.save, start_job
  -> Controller.run_transcript (app/controller.py)
       project.transcript_source_mode == "audio" (default, also when no project)
         -> Controller.transcribe -> TranscriptionPipeline.run     (unchanged Audio pipeline)
       project.transcript_source_mode == "video"
         -> Controller.transcribe_video -> VideoTranscriptionPipeline.run  (STUB)
```

- Changing the combo calls `Controller.set_transcript_source_mode` (validates, saves via `ProjectManager.save`, no pipeline run). With no project open it only toggles the UI.
- Video stub (`transcription/video/pipeline.py`): raises `ValueError` for status `imported`; raises `ValueError("Chưa chọn vùng phụ đề ...")` when ROI is empty; otherwise raises `VideoTranscriptNotImplemented`. There is no ROI UI, so today the ROI message is what a user would see. The stub text is Vietnamese; the English wording "Video Transcript is not implemented yet" was requested later and is NOT used.
- Errors propagate through `Worker` (catches `Exception`, emits `error`) to `MainWindow.error`. Unverified by running the GUI.

## Audio Mode Status

- Audio routes to the original code: `Controller.transcribe` -> `TranscriptionPipeline.run` (`transcription/pipeline.py`). `Controller.transcribe` and `TranscriptionPipeline` were not edited in Phase 1 (this is from the edits made, not from a diff, because of the dirty tree).
- `cartoon_sub/app/main.py` `--runtime-transcribe-project` still calls `controller.transcribe` directly.
- No Audio regression PASS is claimed. Audio regression is NOT VERIFIED.

## Video Mode Status

Exists: mode selector, persistence in `project.json`, `Controller.run_transcript` / `transcribe_video` / `set_transcript_source_mode`, stub, reset of `cache/transcript/video`, `transcript_video` key accepted by settings, `VIDEO_SUBTITLE` provenance accepted.

Does NOT exist: ROI selector/UI and any code that sets `video_subtitle_roi`; ffmpeg frame scanning (including a binary-safe helper, since `media.process.run_process` returns text); subtitle detection; start/end boundary refinement; OpenRouter Vision OCR; vision model selector UI for `transcript_video`; final integration into `project.segments`, `zh.srt`, exports; Video-specific manual_split guard.

## Tests

`tests/test_transcript_mode_routing.py` (11 tests):
- First run: 10 passed, 1 failed. The failure was a bad assertion in the test (it compared the default model to the same id it set).
- The assertion was then corrected. The corrected file has NOT been re-run, so its current status is unverified.

A 17-module targeted run (before the correction) gave 121 tests, 6 failures, 6 errors. Names captured (11 of 12; one error was not captured by name):
- `test_unified_ai_model_routing`: `test_missing_default_blocks` (error), `test_legacy_migration_once_and_exact_persistence` (fail), `test_tab_override_persists_as_application_preference` (fail)
- `test_stage_reset_service`: `test_buttons_exist_and_cancel_changes_nothing` (error), `test_confirmed_reset_refreshes_ui_without_restart` (error)
- `test_canonical_timeline`: `test_renderer_uses_one_overlap_event_without_speaker_prefix` (error)
- `test_manual_edits`: `test_apply_manual_edits_timing_and_overlap` (fail), `test_dirty_tracking_in_timeline_table` (fail)
- `test_transcript_segmentation_normalizer`: `test_completed_raw_cache_is_renormalized_without_network` (error), `test_downstream_invalidation_only_when_signature_changes` (fail), `test_word_pause_and_speaker_change_are_boundaries` (fail; observed `['SPK_UNKNOWN','SPK_UNKNOWN']` vs expected `['SPK_01','SPK_02']`)

Known facts only: the cause of each is not classified. A baseline attempt using a temporary worktree of HEAD was invalid (HEAD is behind the working tree; that run produced 14 errors with "settings.json không hợp lệ"). It was removed. No failure is proven to be pre-existing or caused by Phase 1.

## Known Unverified Areas

- Full Phase 1 regression and the cause of the 12 failures/errors above.
- `test_transcript_mode_routing.py` after the assertion fix.
- Project 1 end-to-end (its `project.json` has `transcription_status` "failed", 34 rows; it was only read, never modified).
- Build/publish; canonical EXE smoke.
- GUI behavior (mode switch, hide/show of Audio widgets, error popup on Video run) was never exercised manually.
- Whether `Project` files with the new fields can be read by older builds (inferred: `Project.from_dict` ends in `cls(**data)`, so unknown keys would raise `TypeError`; not run).

## Known Risks

- `Utterance.validate()` whitelists provenance values; any further new value needs a models.py edit.
- `transcript_video` is accepted by `AISettings.validate()` but nothing resolves it; `set_tab_model_override` uses the translation provider for non-STT tabs. No UI selector exists.
- Reset: `cache/transcript/video` is removed only when `clear_stt_cache` is true. Mode and ROI are Project fields and are not cleared by reset (intended).
- `Project.from_dict` raises on an invalid mode or ROI, so a hand-edited bad value makes the project fail to load.
- Video stub: the Video path always ends in a controlled `ValueError` subclass; the GUI error path was not run. Message wording differs from the later English request.
- `set_transcript_source_mode` saves with `ProjectManager.save` directly (not `Controller.save`, no undo entry).
- Working tree is far ahead of HEAD (about 61 modified, 7 deleted, 22 untracked before this handoff). Never reset, stash or checkout to "get a baseline".
- Git warned that LF will be replaced by CRLF for `paths.py`, `stage_reset_service.py`, `transcript_tab.py`. Mixed line endings possible; unverified.
- `.kiro/skills` does not exist in this workspace.

## Project Rules

- ONLY real regression project: `D:\Documents\MMO\Youtube\Tools\Cartoon_Sub\Projects\1`. Do not use Project 2. Do not create another numbered project.
- All AI inference must remain OpenRouter-only.
- Follow `AGENTS.md`: small patches, targeted tests first, do not touch Translation, VI Subtitle, dubbing/TTS, VAD, segmentation algorithm or QA thresholds.
- Do not change Audio Transcript behavior.

## Canonical Paths

Project root:
`D:\Documents\MMO\Youtube\Tools\Cartoon_Sub`

Project 1:
`D:\Documents\MMO\Youtube\Tools\Cartoon_Sub\Projects\1`

Canonical EXE:
`D:\Documents\MMO\Youtube\Tools\Cartoon_Sub\Cartoon_Sub.exe`

Build EXE:
`D:\Documents\MMO\Youtube\Tools\Cartoon_Sub\dist\Cartoon_Sub\Cartoon_Sub.exe`

(Existence of the two EXE files was seen in the session file tree; they were not run.)

## Recommended Next Step For Codex

Do NOT implement ROI yet.

First:

1. Inspect the current git diff of the Phase 1 files (listed above).
2. Rerun `tests/test_transcript_mode_routing.py` (`PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`, `.venv\Scripts\python.exe -m unittest tests.test_transcript_mode_routing`).
3. Stabilize Phase 1.
4. Classify the 12 earlier failures/errors one module at a time (decide per test whether Phase 1 caused it, e.g. by temporarily isolating the Phase 1 edits, not by checking out HEAD).
5. Only then start the Video ROI phase.
