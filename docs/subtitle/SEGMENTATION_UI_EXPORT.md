# Subtitle Segmentation UI, Export and Cache

## 1. Purpose

This document defines user-facing segmentation controls, editor behavior,
export behavior and segmentation cache dependencies.

It does not define the core segmentation algorithm.


## 2. Settings

Provide Subtitle Segmentation settings.

Suggested UI:

Subtitle Segmentation

Mode:
[ Balanced ▼ ]

Preferred Duration:
[ 3.0 sec ]

Max Duration:
[ 5.0 sec ]

Preferred Syllables:
[ 12 ]

Max Syllables:
[ 18 ]

Max Lines:
[ 2 ]

Preferred Chars / Line:
[ 36 ]

Hard Max Chars / Line:
[ 44 ]


## 3. Options

Provide options equivalent to:

[✓] Prefer punctuation

[✓] Preserve semantic phrases

[✓] Rebalance timestamps

[✓] Avoid orphan words

[✓] Avoid one-word subtitle

[✓] Keep speaker mapping

[✓] Preserve overlaps


## 4. Segmentation Modes

UI must expose:

BALANCED

READING_COMFORT

FAST_DIALOGUE

PRESERVE_SENTENCES

CUSTOM

BALANCED should be the default unless project/user settings specify
otherwise.


## 5. Main Actions

Provide actions equivalent to:

Auto Segment All

Auto Segment Selected

Merge Selected

Split Manually

Reset To Utterance


## 6. Editor Hierarchy

The editor should make the relationship visible.

Example:

Utterance 12
SPK_02
32.2 → 36.2

    12.1
    32.2 → 34.0
    ...

    12.2
    34.0 → 36.2
    ...

The user should be able to understand which DisplaySegments belong to
which Utterance.


## 7. DisplaySegment Actions

Support:

- Split here
- Merge with previous
- Merge with next
- Reset segmentation
- Auto segment utterance
- Auto segment selected
- Edit text
- Edit timestamp


## 8. Manual Changes

Manual segmentation changes must be marked:

segmentation_reason = manual

Do not silently treat manually edited boundaries as automatically
generated boundaries.


## 9. Reset Behavior

Reset To Utterance should remove derived segmentation for the selected
Utterance and return it to one presentation unit based on the source
Utterance.

It must not:

- rerun transcription
- rerun speaker detection
- delete source translation
- modify Utterance outer timestamps


## 10. Auto Segment Selected

Only selected Utterances should be re-segmented.

Do not modify unrelated Utterances.


## 11. Auto Segment All

Run segmentation over eligible Utterances.

Do not rerun transcription or translation merely because segmentation
is regenerated.


## 12. QC Presentation

DisplaySegment QC should be visible/filterable.

Required states include:

OK

TOO_LONG

TOO_SHORT

TOO_MANY_SYLLABLES

TOO_MANY_LINES

HIGH_READING_SPEED

BAD_SPLIT

MANUAL_REVIEW


## 13. QC Filtering

Subtitle UI should allow filtering or locating warning segments.

Exact visual implementation may follow the existing UI architecture.


## 14. Standard SRT Export

Standard Vietnamese subtitle SRT should export:

DisplaySegments

not the long source Utterance when DisplaySegments exist.

DisplaySegment timestamps become SRT timestamps.


## 15. SRT Source Text

For normal Vietnamese subtitle export use:

DisplaySegment.vi_text

derived from:

Utterance.vi_subtitle

Do not export unrelated dubbing text unless the user explicitly chooses
that text type.


## 16. Speaker/TTS Export

Speaker/TTS workflow may allow:

A. Utterance-level export

B. DisplaySegment-level export

Default subtitle export:

DisplaySegment level.

Default TTS behavior:

configurable according to the TTS workflow.


## 17. Speaker Mapping During Export

DisplaySegment export must preserve parent speaker identity.

Example:

Utterance 12
speaker = SPK_02

DisplaySegment 12.1
DisplaySegment 12.2

Both remain associated with SPK_02.


## 18. Overlap During Export

Do not modify timestamps merely because two speakers overlap.

If:

SPK_01 DisplaySegment:
10 → 12

SPK_02 DisplaySegment:
11 → 13

both timestamps remain valid.


## 19. Segmentation Cache

Segmentation cache should depend on inputs that can change segmentation.

Include:

- Utterance text
- Utterance start/end
- segmentation mode
- segmentation settings
- syllable counter version
- semantic segmentation prompt version when AI fallback is used


## 20. Cache Invalidation

Changing:

max syllables

should invalidate relevant segmentation.

Changing:

segmentation mode

should invalidate relevant segmentation.

Changing:

Utterance subtitle text

should invalidate that Utterance's segmentation.

Changing:

Utterance timing

should invalidate timing-dependent segmentation.


## 21. Visual-Only Changes

Changing subtitle font must NOT invalidate segmentation.

Other purely visual style changes should not invalidate segmentation
unless they directly change segmentation constraints such as measured
line width in an explicitly supported visual-layout mode.


## 22. Scoped Invalidation

Prefer scoped invalidation.

Example:

editing Utterance 12

should not invalidate segmentation for every unrelated Utterance unless
a global setting changed.


## 23. Manual Editing and Cache

Manual DisplaySegment changes should not be silently overwritten by
background cache regeneration.

Respect the existing project/manual-edit workflow.

Automatic regeneration should occur only through an explicit workflow
or a clearly defined invalidation path.


## 24. Source Text Editing

Editing DisplaySegment text is a user action.

Automatic segmentation itself must not rewrite source translation.

If DisplaySegment text diverges intentionally from the Utterance source,
the application should preserve enough state to identify/manual-review
that divergence rather than pretending it came from normal automatic
segmentation.


## 25. UI Architecture

Follow the existing UI architecture.

Do not place:

- Gemini calls
- syllable calculations
- segmentation scoring
- timestamp allocation

directly inside UI widgets.

UI should call services/controllers.


## 26. Error Handling

Segmentation failure must not destroy the current Utterance.

If auto segmentation fails:

- keep existing valid data
- show/report the error according to existing UI patterns
- allow manual review


## 27. Compatibility

Do not redesign unrelated:

- translation UI
- transcription UI
- speaker editor
- mask/style editor
- rendering UI
- TTS workflow

unless required to expose DisplaySegments safely.


## 28. Development Principle

UI is a consumer of segmentation state.

It must not become the source of truth for:

- speaker identity
- transcription
- original Utterance timing
- original translation