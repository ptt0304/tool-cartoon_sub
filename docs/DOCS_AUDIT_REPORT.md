# In-app Docs audit

FILES_INSPECTED=

- `src/cartoon_sub/ui/docs_dialog.py`
- `src/cartoon_sub/ui/main_window.py`
- `src/cartoon_sub/ui/settings_dialog.py`
- `src/cartoon_sub/ui/tabs/video_tab.py`
- `src/cartoon_sub/ui/tabs/transcript_tab.py`
- `src/cartoon_sub/ui/tabs/translate_tab.py`
- `src/cartoon_sub/ui/tabs/subtitle_tab.py`
- `src/cartoon_sub/ui/tabs/mask_style_tab.py`
- `src/cartoon_sub/ui/tabs/audio_tab.py`
- `src/cartoon_sub/ui/tabs/export_tab.py`
- `src/cartoon_sub/app/controller.py`
- `src/cartoon_sub/app/settings.py`
- `src/cartoon_sub/project/project_manager.py`
- `src/cartoon_sub/project/cache_status_service.py`
- `src/cartoon_sub/project/stage_reset_service.py`
- `src/cartoon_sub/transcription/mode.py`
- `src/cartoon_sub/transcription/pipeline.py`
- `src/cartoon_sub/transcription/video/pipeline.py`
- `src/cartoon_sub/transcription/video/qaqc.py`
- `src/cartoon_sub/translation/source_modes.py`
- `src/cartoon_sub/translation/pipeline.py`
- `src/cartoon_sub/translation/dubbing_service.py`
- `src/cartoon_sub/translation/qa_service.py`
- `src/cartoon_sub/subtitle/models.py`
- `src/cartoon_sub/subtitle/segmentation_service.py`
- `src/cartoon_sub/subtitle/export_service.py`
- `src/cartoon_sub/tts/generation_service.py`
- `src/cartoon_sub/tts/mix_service.py`
- `src/cartoon_sub/tts/final_mix_service.py`
- `src/cartoon_sub/media/preview.py`
- `src/cartoon_sub/ai/openrouter_client.py`

DOCS_SECTIONS:

1=Giới thiệu: purpose, core terms, local versus AI/API behavior.
2=Project: create/open, persisted data, source-video reference and safe movement.
3=Settings: OpenRouter keys, catalog, default/override models and advanced settings.
4=Transcript: Audio, Video ROI/detection/OCR, Chinese SRT import, cache and video QA/QC undo.
5=Translate: source snapshots, style controls, translation/QA/import and Master Timeline actions.
6=Subtitle: Utterance/DisplaySegment, profiles, local segmentation, manual actions and export.
7=Mask: ROI editor, mask geometry/types/style, logo, watermark, preview/render/save.
8=Audio: Local_TTS, project voice, TTS sources/cache modes, master TTS, mixer and final audio.
9=Export: 30-second test, render prerequisites and SRT ownership.
10=Lỗi thường gặp: OpenRouter, OCR/ROI, Local_TTS/audio, mask/subtitle/project remedies.
11=Quy trình: placeholder only, per requirement.

FEATURES_DOCUMENTED=

- OpenRouter-only AI routing and model selection.
- Audio/Video/Import-SRT transcript acquisition and source-specific caches.
- Video local 10 fps detector, 25 fps boundary refinement and OCR workload semantics.
- Translate source modes, manual/retranslation/dubbing optimization/QA actions.
- DisplaySegment and local-only subtitle segmentation.
- Mask ROI, Gaussian/Solid/No mask, auto-fit/full ROI and overlays.
- One project-level Local_TTS voice, TTS cache reset scopes, dubbed/final audio mixing.
- Final-video rendering and SRT export locations.

OBSOLETE_DOCS_REMOVED=

- Gemini/direct-provider instructions.
- Speaker assignment and per-speaker voice workflow.
- Removed visible AI context-review controls.
- Removed mask modes no longer present in the current UI.

UI_CONTROLS_COVERED=

All visible major tab controls and their persistent/cache/API effects are covered in the corresponding topic. Hidden legacy compatibility controls are intentionally not documented.

KNOWN_UNDOCUMENTED_ITEMS=

NONE

SECTION_11=

PLACEHOLDER_ONLY

TESTS:

pass=3
fail=0
error=0

API:

requests=0
cost=0

BUILD=

PASS

PUBLISH=

PASS

INTERNAL_SYNC=

PASS

CANONICAL_SMOKE=

PASS

FINAL_STATUS=

PASS

CANONICAL_EXE:

path=D:\\Documents\\MMO\\Youtube\\Tools\\Cartoon_Sub\\Cartoon_Sub.exe
size=9450409
timestamp=2026-10-13 01:08:29
sha256=134A92FC6E0C6A2D98271DA2E011DCFBE18B2EC1FA8C32FA228A141DEB18C0EA
