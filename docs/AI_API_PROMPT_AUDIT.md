# AI/API Prompt Audit

## Summary

Scope: production Python paths that can invoke OpenRouter. This audit made no network request and did not read or emit key material. All calls are stateless: no previous provider conversation/history is sent. The shared client uses `POST https://openrouter.ai/api/v1/chat/completions` for JSON/text/vision and `POST https://openrouter.ai/api/v1/audio/transcriptions` for STT.

**Inventory:** 13 distinct AI tasks. Model IDs are resolved by `AIModelResolver` from the relevant tab override/default; every production client is OpenRouter. Current text default in `AISettings` is `google/gemini-2.5-flash`; exact resolved models can differ by saved tab override.

Shared findings:

- `OpenRouterClient._json_body` appends a textual JSON contract containing the schema and, where catalog metadata permits, sends the schema again as strict `response_format.json_schema`. This safely constrains output but duplicates fixed prompt tokens.
- The generic chat/multimodal client sets `temperature=0.3` only where catalog metadata says it is supported. No call sets `max_tokens`, `max_output_tokens`, or a stop sequence.
- `CachedRequests` cache identity contains prompt version, provider, model, system, prompt, schema, and evidence fingerprint. This is safe invalidation.
- `OpenRouterTranscriptionClient.transcribe_json` deliberately discards its `prompt`, `schema`, and `references` arguments. They participate in local cache/validation, but are **not sent** to OpenRouter. Its sent body is only model, base64 WAV, language `zh`, and `verbose_json` response format.
- No task requests chain-of-thought. The shared parser merely has a recovery path for provider reasoning fields.

## Call Inventory

| Task | Feature | Model key | Endpoint/modality | Risk |
|---|---|---|---|---|
| Video OCR | Video Transcript OCR | `VIDEO_SUBTITLE_OCR` | chat/completions, one PNG ROI | HIGH |
| Translation batch | Translate | `TRANSLATION` | chat/completions, text/evidence | HIGH |
| Visual Context (hidden compatibility path) | visual analysis | context resolver | chat/completions, frames/video | HIGH |
| Audio STT | Run Chinese Transcript | configured STT | audio/transcriptions, WAV | MEDIUM |
| STT timing retry | weak acoustic timing | retry model | audio/transcriptions, WAV | MEDIUM |
| STT language retry | foreign-script region | configured STT | audio/transcriptions, WAV | MEDIUM |
| Translation semantic QA | automatic QA | review selection | chat/completions, text | MEDIUM |
| Translation QA rewrite retry | QA failed row | review selection | chat/completions, text | MEDIUM |
| Manual selected QA | selected Translate rows | review selection | chat/completions, text | MEDIUM |
| Dubbing optimization | selected/bulk dubbing | `DUBBING_OPTIMIZE` | chat/completions, text | MEDIUM |
| Duration-fit rewrite | TTS fit failure | `DUBBING_DURATION_REWRITE` | chat/completions, text | MEDIUM |
| Semantic boundary | ambiguous Chinese cuts | `SEMANTIC_BOUNDARY` | chat/completions, text | LOW |
| Transcript AI repair | suspicious regions | `TRANSCRIPT_REPAIR` | chat/completions, text | MEDIUM |

## Shared transport, response and retry controls

**Sources:** `src/cartoon_sub/ai/openrouter_client.py` (`OpenRouterClient._json_body`, `generate_json`, `generate_multimodal_json`, `OpenRouterTranscriptionClient.transcribe_json`) and `src/cartoon_sub/translation/requests.py` (`CachedRequests.request`, `request_multimodal`).

Chat requests use `messages=[system,user]`. The user content is given the fixed suffix: `Chỉ trả về một JSON object hợp lệ, không markdown. JSON phải tuân theo schema sau: {schema}`. If structured outputs are supported, the same schema is also sent as strict `response_format`.

`CachedRequests` uses `AISettings.retry_count` (0–5), retryable provider errors, backoff capped at 30 seconds, and at most one language-contract correction. A retry resends the whole payload; this matters most for Translation and Visual Context. No chat history is retained or resent.

**Safe shared recommendation:** retain strict JSON, but omit the textual schema suffix when strict `response_format.json_schema` is active; measure real outputs and set conservative task-specific output caps. This audit does not implement either change.

### Non-inference OpenRouter operations

Two production OpenRouter calls are operational rather than AI tasks and therefore have no prompt/schema/media cost: `OpenRouterClient.get_models` requests `GET /api/v1/models` for catalog refresh, and `OpenRouterClient.test_connection` (called by `Controller.test_connection` and `test_translation_connection`) requests the authenticated key endpoint. They send authorization only, do not invoke a model, have no response schema/prompt, and are excluded from the 13-task inference count. They should still be regarded as network operations when the user presses refresh/test.

## Task details

### Video OCR

**Feature/source:** Video Transcript OCR — `src/cartoon_sub/transcription/video/ocr_service.py`, `VideoSubtitleOcrService.run` and `_ocr_one`.

**Model/endpoint/input:** `VIDEO_SUBTITLE_OCR`; chat/completions; exactly one `image/png` ROI crop per visual event. FFmpeg creates `crop={width}:{height}:{x}:{y}` at `representative_time`. There is no resize/compression, so vision cost scales directly with the user ROI dimensions.

**System:** `Bạn là OCR phụ đề. Chỉ đọc chữ Trung Quốc đang nhìn thấy trong ảnh crop phụ đề.`

**User:** `Đọc CHỈ phụ đề tiếng Trung nhìn thấy. Giữ dấu câu và thứ tự dòng. Không dịch, không mô tả ảnh, không suy đoán chữ thiếu, không thêm tên người nói. Nếu không có phụ đề, trả has_subtitle=false và text rỗng.` The retry adds only a short JSON/no-description correction.

**Context:** ROI crop is REQUIRED. Event time, region, source hash and visual signature are USEFUL locally/cache-side and are not needlessly injected as prose.

**Schema and consumption:** strict `{has_subtitle:boolean,text:string}`; both are consumed. Normalization and foreign-script status are local. No confidence, explanation, alternatives, or echoed input are requested.

**Cache/retry:** `cache/transcript/video/ocr/{hash}.json`, keyed by source hash, region/ROI/event/timing, visual signature, model, and prompt-schema version. Cache hits validate before reuse. At most two attempts per cache miss; a schema failure, exception, foreign script, or strong visual empty result resends the crop.

**Risk/recommendation:** input HIGH, output LOW, media HIGH, retry MEDIUM, overall HIGH. Keep the minimal schema and one-event/one-crop design. Safely downscale only oversized crops while retaining character legibility and add a small OCR output cap.

### Audio STT, language retry, and timing retry

**Source:** `src/cartoon_sub/transcription/gemini_transcriber.py`, `GeminiTranscriber.transcribe`, `_language_retry`, `_timing_retry`; transport `src/cartoon_sub/ai/openrouter_client.py`, `OpenRouterTranscriptionClient.transcribe_json`.

**Model/endpoint/input:** configured STT model for primary/language retry and configured timing retry model for timing retry; `/audio/transcriptions`; one VAD speech region at a time. Input is PCM WAV mono/16 kHz/16-bit. Region timing is local; no artificial overlap is created here.

**Local prompt/template:** `prompts/transcription_v2.txt` asks for independent Chinese utterances, permits overlap, requires relative timestamps inside this chunk, and forbids translation/invented speech. Local schema expects `segments[id,start,end,zh,transcript_confidence]`.

**Transport reality:** `transcribe_json` deletes `prompt`, `schema`, and `references`. Sent payload is `{model,input_audio:{data:<base64 WAV>,format:"wav"},language:"zh",response_format:"verbose_json"}`. `timestamp_granularities`, a max-output value, and the textual STT prompt are not sent. Parser consumes root/segment text, segments and word timestamps where present.

**Context:** audio and `language:"zh"` are REQUIRED; VAD region and timing-quality/language-drift signals are USEFUL local triggers. Prompt/schema are QUESTIONABLE cache inputs because they do not currently influence the remote call.

**Cache/retry:** primary `cache/transcription/{hash}.json`; language `cache/transcription/language_retry/{hash}.json`; timing `cache/transcription/timing_retry/{hash}.json`. Identities include audio hash, model, region/VAD and relevant version. Primary uses configured retries for retryable errors; language and timing are each one bounded extra transcription path when eligible.

**Risk/recommendation:** input MEDIUM, output LOW (verbose timing is used), media MEDIUM, retry MEDIUM, overall MEDIUM. Preserve verbose JSON. Before depending on prompt-driven behavior, clarify an endpoint-supported STT prompt/schema mechanism; separately evaluate supported timestamp controls.

### Semantic Boundary

**Source:** `src/cartoon_sub/transcription/semantic_boundary_resolver.py`, `ChineseSemanticBoundaryResolver.resolve` / `_apply`.

**Model/endpoint:** `SEMANTIC_BOUNDARY`; chat/completions; text only; only ambiguous long/multi-child parents qualify.

**System:** `You identify boundaries in recognized Mandarin speech. Return JSON only. Never rewrite, correct, punctuate, translate, or repeat source text. Operate only on the supplied target IDs and source character indices. KEEP/REMOVE applies to existing candidate boundaries; ADD applies to a missing boundary. Prefer natural sentence, clause, vocative, question/answer, and likely dialogue-turn boundaries. Be conservative: do not create tiny fragments.`

**User template:** `{targets:[{target_id,source,indexed_source,existing_boundaries,previous,next}]}` with a short boundary directive.

**Context:** source/indexed source and candidate boundaries are REQUIRED; immediate neighbours are USEFUL. `indexed_source` repeats the full Chinese text character-by-character, creating medium input bloat. Speaker data is absent.

**Schema/consumption:** target/boundary `after_index`, `action`, `type`, `confidence`. Target ID, index, action and confidence are used; `type` is validated but not applied, so it is a safe candidate for later removal.

**Cache/retry/risk:** `cache/transcription_semantic/{hash}.json`, version/model/system/prompt/schema identity; at most two attempts, deterministic result retained on failure. Input MEDIUM, output LOW, media NONE, overall LOW. Keep target gating; consider compact index mapping only after boundary regression coverage.

### Transcript AI Repair

**Source:** `src/cartoon_sub/transcription/qa_repair.py`, `TranscriptAIRepairService.repair` / `_request_rows`.

**Model/endpoint:** `TRANSCRIPT_REPAIR`; chat/completions; text; one request for locally selected suspicious regions.

**System:** `You review only suspicious Mandarin transcript regions. Context is read-only. Return JSON only. Never edit context rows or invent content. Use KEEP unless a repair is clearly justified. Every proposal must copy source_ids exactly from affected_ids, in the same order. For MERGE, SPLIT, or BOUNDARY_REPAIR, parts must preserve every source character in the affected region in order. For TEXT_CORRECTION, correct only an obvious recognition error and retain nearly all source characters. Do not provide timestamps; Cartoon_Sub rebuilds timing locally.`

**User template:** `{regions:[{region_id,affected_ids,range,violations,rows:[{id,text}],context_before,context_after}]}`; neighbour windows are two rows each side.

**Context:** affected IDs/rows/violations REQUIRED; range and bounded neighbours USEFUL. No media and no full project transcript.

**Schema/consumption:** `region_id,source_ids,action,parts,confidence`; all are validated and consumed. No explanatory prose.

**Cache/retry/risk:** transcript QA-repair cache keyed by QA/prompt/model/schema identity; one repair pass and failure preserves source/manual review. Input MEDIUM, output LOW, overall MEDIUM. If suspicious regions grow, batch deterministically while preserving ID-level validation.

### Translation Batch

**Source:** `src/cartoon_sub/translation/pipeline.py`, `TranslationPipeline.run`; `src/cartoon_sub/translation/prompts.py`, `translation_prompt`, `editorial`; batching `translation/chunker.py`.

**Model/endpoint:** `TRANSLATION`; chat/completions; text or conversation evidence media. Batches use configured size 30–50, character cap 6000, duration cap 45 seconds and local neighbour windows.

**System:** `EDITORIAL_RULES`, a long Vietnamese editorial contract requiring natural dialogue and preservation of meaning, relationship, names, user-facing language, and untrusted-transcript handling.

**User template:** `{editorial,reference_before,reference_after,previous_translation,targets,audiovisual_evidence}` and requested `translations`, `continuity_updates`, `uncertainties`.

**Context:** Chinese, IDs and timing are REQUIRED; five previous/five next rows, last five translations and available evidence are USEFUL. Editorial contains genre, style, custom rules, glossary/proper-name rules and up to 64 continuity records. Custom rules and glossary/proper-name mappings appear both inside `context_instruction` and separately: HIGH, QUESTIONABLE duplication. Target voice/rate/syllable fields are QUESTIONABLE for subtitle-only translation.

**Schema/consumption:** `translations[{id,vi,confidence,review_note,meaning_preservation,compressed}]`, continuity updates and uncertainties. `vi`, note, preservation, compression, continuity and uncertainties are persisted. Per-row `confidence` is validated/returned but not used in the visible update path, so it is QUESTIONABLE. No free-form prose outside bounded notes/uncertainties.

**Cache/retry/risk:** `cache/translation/{translate_source}` plus durable chunk checkpoints; identity covers prompt/model/system/schema/targets/neighbours/continuity/evidence. Input HIGH, output MEDIUM, media MEDIUM when evidence is active, retry MEDIUM, overall HIGH. Highest-value safe optimization: eliminate duplicate editorial fields, relevance-bound glossary/continuity and add a batch-aware output cap.

### Selected Retranslation

**Source/function:** `TranslationPipeline.retranslate_selected`.

Uses the Translation system/schema for one row with two neighbours each side and `{mode:"text_only"}` evidence. It consumes `vi`, note, preservation, compression and continuity updates, then clears only that row’s dubbing state. `cache/translation/retranslate` is called with `force=True`, intentionally making an explicit retranslation fresh. Input MEDIUM due full editorial repetition; output MEDIUM; overall MEDIUM. A lean single-row schema/context is safe only after preserving the persisted fields.

### Translation Semantic QA and automatic rewrite retry

**Source:** `src/cartoon_sub/translation/qa_service.py`, `TranslationQAService.run`, `_semantic_check`, `_retry_failed_row`.

**System:** `SEMANTIC_QA_RULES`: `Bạn là QA hội thoại Trung–Việt theo góc nhìn khán giả Việt... Không dịch lại và không sửa row PASS...` plus user-facing language requirements.

**User template:** semantic check sends full `editorial`, five rows before/after, target and current Vietnamese. Retry sends translation retry context plus local issues. Local QA runs first, avoiding AI calls for direct PASS/FAIL; semantic AI applies only to SUSPECT rows. A failure queue has at most one translation rewrite.

**Schema/consumption:** QA `{id,status,issues:[{type,detail}]}`; every field is shown/stored. Retry consumes normal translation output.

**Cache/risk:** `cache/translation_qa/{translate_source}` and current-QA checkpoints. Input MEDIUM/HIGH due repeated editorial; output LOW/MEDIUM; retry MEDIUM; overall MEDIUM. Use a QA-specific lean editorial projection, but retain issue details.

### Manual selected Translation QA

**Source:** `TranslationQAService.run_selected_manual`.

One selected row per call, two attempts maximum. User template is `{editorial,target,before[3],after[3],current_vi_subtitle,current_vi_dubbing,attempt,previous_issues}`. Strict output `{id,status,issues,corrected_vi_subtitle,reason}` is wholly consumed: correction updates subtitle and reason/issues are stored or shown. `cache/manual_translation_qa/{source}` is forced fresh by design. Input MEDIUM, output MEDIUM, overall MEDIUM. Keep correction/reason; cap issue-detail length and use lean editorial context.

### Dubbing optimization

**Source:** `src/cartoon_sub/translation/dubbing_service.py`, `DubbingService.optimize`; helpers `dubbing_prompt`, `dubbing_system`.

**Model/endpoint:** `DUBBING_OPTIMIZE`; chat/completions; text. Selected/bulk eligible rows are regrouped by translation batches.

**User template:** `{task,editorial,modes,budget_settings,reference_before,reference_after,targets}`. Targets contain Chinese source, current VI dubbing, VI Subtitle, syllable/delta limits and timing/budget fields. The fixed task protects meaning, negation, names, numbers, terminology, roles and causality; local semantic-preservation checks reject unsafe changes.

**Context:** source/current dubbing/subtitle/budget REQUIRED; neighbours USEFUL; broad source-row voice/rate values QUESTIONABLE if duplicated at target level; full editorial useful but broad.

**Schema/consumption:** translation schema; `vi`, `compressed`, `meaning_preservation`, `review_note` consumed. `confidence` is QUESTIONABLE. Up to two shortening passes resend only failed IDs with authoritative local counts.

**Cache/risk:** `cache/dubbing` plus segment fingerprint. Input MEDIUM/HIGH, output MEDIUM, retry HIGH for stubborn rows, overall MEDIUM. Keep semantic safeguards; shrink duplicated row/editorial fields before touching core constraints.

### Duration-fit rewrite

**Source:** `DubbingService.rewrite_duration_failures`.

One utterance per request only after local TTS fit requires a rewrite. Prompt target contains Chinese, full subtitle, current dubbing, voice, available/actual duration, reduction percentage and rewrite attempt, plus three neighbours each side. The translation schema output is applied to dubbing after validation. Cache is `cache/dubbing_duration`; shared retries plus bounded semantic rewrite count apply. Input MEDIUM, output MEDIUM, retry MEDIUM/HIGH, overall MEDIUM. Preserve actual-duration/reduction fields; reduce duplicated editorial context.

### Visual Context — hidden compatibility path

**Source:** `src/cartoon_sub/translation/visual_context.py`, `VisualContextAnalyzer.analyze` / `_request`. Translate UI controls are hidden compatibility controls, but the callable service remains production code and is included.

**Input:** chat/completions with adaptive JPEG frames (480px high, quality 4; bounded/adaptive frame set) or one proxy MP4. Prompt includes analysis pass, language contract, video range, target IDs, canonical character registry, contracts, target/neighbour rows, user requirements, glossary, approved context and sampling.

**Schema/consumption:** broad `CONTEXT_SCHEMA` StoryContext. Its setting/summary/narration/character/term/address-rule/uncertainty/profile/visual-context/registry fields are used in validation and merge; broad output follows the broad context-analysis feature rather than accidental prose.

**Cache/risk:** `cache/visual_context/{hash}` and registry cache, keyed by video/targets/references/window/model/mode/registry/prompt version. Input HIGH, output MEDIUM/HIGH, media HIGH, overall HIGH. Keep hidden unless needed; future safe optimization is target-relevant registry/context and an explicit frame cap.

## Conclusions

**Top context cost:** Translation, QA and dubbing use `editorial(project)` repeatedly. Its user instructions and glossary/proper-name mappings are duplicated; continuity memory can contain 64 records. This is the first source-level optimization target.

**Top output bloat:** Translation-family `confidence` is the clearest questionable output; Semantic Boundary `type` is validated but unused by application. OCR fields and QA/manual QA correction/reason/issues are all consumed. No feature requests explanations beyond necessary QA details.

**Cache:** no unsafe reuse was established. Manual retranslate/manual QA intentionally force new requests. OCR identity correctly includes ROI/event information.

## Priority order

1. Translation/QA/Dubbing: remove duplicated editorial representations and relevance-bound glossary/continuity.
2. Shared client: avoid textual-schema duplication when strict structured outputs are active; add measured caps.
3. Video OCR: preserve one crop/event; downscale only excessive crops.
4. STT: reconcile local prompt expectations with the actual transcription payload before changing quality logic.
5. Visual Context: reduce broad registry/context/frame payload before enabling broadly.

## Audit limits

No live request was made. Provider token accounting, effective catalog capabilities and exact model-side image tokenization were not measured; all risk levels are conservative source-level assessments.
