# Subtitle Segmentation Contract

## 1. Purpose

Subtitle segmentation separates two concepts:

### Utterance

An Utterance describes:

WHO said WHAT and WHEN.

It is the source-of-truth speech unit produced by the transcription /
speaker-aware pipeline.

It owns:

- speaker identity
- original start/end timestamps
- Chinese transcript
- Vietnamese translation
- dubbing text
- speaker overlap information

### DisplaySegment

A DisplaySegment describes:

HOW part of an Utterance is shown to the viewer.

It is a presentation-layer subtitle unit.

A long Utterance may contain multiple DisplaySegments.

Example:

Utterance 12
SPK_02
00:32.200 → 00:36.200

├── DisplaySegment 12.1
│   00:32.200 → 00:34.100
│
└── DisplaySegment 12.2
    00:34.100 → 00:36.200


## 2. Core Architecture

Required pipeline:

Audio
→ speaker-aware transcription
→ Utterance timeline
→ translation
→ subtitle segmentation
→ DisplaySegments
→ subtitle rendering/export

Do NOT split transcription into small display subtitles before translation.

Speaker detection and transcription remain Utterance-based.

Subtitle segmentation is built on top of the existing master timeline.


## 3. Core Invariants

The following rules must always remain true.

1. Utterance is the source of truth.

2. DisplaySegment must reference exactly one Utterance.

3. DisplaySegment inherits speaker_id from its Utterance.

4. Different speakers must never be merged into one DisplaySegment.

5. Speaker overlap is valid.

6. Segmentation must not modify timestamps merely to remove overlap
   between different speakers.

7. Automatic segmentation must not rewrite the translated text.

8. Joining DisplaySegment texts after whitespace/line-break normalization
   must reproduce the original Utterance subtitle text.

9. Utterance outer start/end timestamps must remain unchanged by
   DisplaySegment generation.

10. Speaker detection must not be executed again for DisplaySegments.

11. Syllable count is a constraint and quality signal.
    It is NOT the primary split boundary.

12. Local deterministic processing must be preferred over AI.

13. Gemini semantic segmentation is a last-resort fallback only.

14. Existing transcription, speaker detection, translation, FFmpeg,
    masking and rendering behavior must not be rewritten unless required
    for compatibility.


## 4. Segmentation Priority

When choosing where to split, use this priority:

1. Speaker boundary
2. Semantic clause boundary
3. Strong punctuation
4. Soft punctuation
5. Duration constraint
6. Syllable / character constraint
7. AI semantic split fallback

Strong punctuation includes:

- .
- ?
- !
- …
- 。
- ？
- ！

Soft punctuation includes:

- ,
- ;
- :
- ，
- ；
- ：

Semantic boundaries may include natural clause transitions such as:

- nhưng
- tuy nhiên
- vì vậy
- do đó
- sau đó
- hôm nay
- ngày mai
- thế nhưng
- rồi
- còn
- trong khi

These are only candidates.

Do not split mechanically on these words.


## 5. Do Not Split Blindly

Do NOT implement:

split(",")

or:

split(".")

Punctuation provides candidate boundaries only.

Do not create many tiny subtitles merely because commas exist.

Do not split an already acceptable subtitle only because punctuation
is present.


## 6. Protected Semantic Units

Avoid breaking:

- named entities
- character names
- geographical names
- titles
- ranks
- dates
- numbers
- idioms
- compound names
- tightly connected semantic phrases

Example:

"Cao Câu Ly"

must not become:

"Cao"

"Câu Ly"

when a safe alternative boundary exists.


## 7. Default Limits

Default BALANCED profile:

minimum duration:
1.0 second

preferred duration:
2.0–4.0 seconds

maximum duration:
5.0 seconds

preferred Vietnamese syllables:
8–16

maximum Vietnamese syllables:
18

maximum lines:
2

preferred characters per line:
36

hard maximum characters per line:
44

These are defaults, not universal hard linguistic rules.

The segmentation engine should use them as constraints and scoring
signals.


## 8. Segmentation Modes

Supported modes:

### BALANCED

Default.

Balance:

- meaning
- reading comfort
- duration
- visual length

### READING_COMFORT

Prefer easier reading and allow more segments.

Suggested:

preferred duration:
2.5–4.5 seconds

preferred syllables:
7–14

maximum syllables:
16

### FAST_DIALOGUE

For fast conversations.

Suggested:

preferred duration:
1.2–2.8 seconds

preferred syllables:
5–11

maximum syllables:
14

Shorter DisplaySegments are allowed.

### PRESERVE_SENTENCES

Prefer keeping complete sentences.

Split only when limits such as duration, syllables, line length or
visual readability require it.

### CUSTOM

Allow user-defined segmentation settings.


## 9. High-Level Processing

Processing should follow:

Vietnamese Utterance
↓
measure duration
↓
count syllables
↓
measure visual/text length
↓
already acceptable?
├── YES → keep as one DisplaySegment
└── NO
    ↓
    find candidate boundaries
    ↓
    score candidates
    ↓
    choose best segmentation
    ↓
    allocate timestamps
    ↓
    rebalance
    ↓
    validate
    ↓
    QC
    ↓
    if no safe result exists
    ↓
    Gemini semantic fallback
    ↓
    validate AI result
    ↓
    DisplaySegments


## 10. Speaker Overlap

Example:

SPK_01:
10.000 → 14.000

SPK_02:
11.300 → 13.500

This is VALID.

SPK_01 may be segmented into:

10.000 → 12.000
12.000 → 14.000

while SPK_02 remains:

11.300 → 13.500

Do NOT serialize speakers merely to remove overlap.


## 11. Source Text Rule

Keep:

utterance.vi_subtitle

separate from:

display_segment.vi_text

For normal automatic segmentation:

normalize(join(display_segment.vi_text))
==
normalize(utterance.vi_subtitle)

Segmentation may change:

- boundaries
- timestamps
- line breaks
- whitespace normalization

Segmentation must not silently:

- translate again
- paraphrase
- shorten
- add words
- remove words

Text rewriting must be a separate explicit feature.


## 12. Development Principle

UTTERANCE:

WHO SAID WHAT AND WHEN.

DISPLAY SEGMENT:

HOW THAT UTTERANCE IS SHOWN TO THE VIEWER.

Never confuse these concepts.

Implement segmentation incrementally.

Preserve backward compatibility with existing project files where
reasonably possible.


## 13. Related Specifications

Data structures and relationships:

SEGMENTATION_MODEL.md

Local segmentation algorithm, scoring, timing, QC and Gemini fallback:

SEGMENTATION_ENGINE.md

UI, editing, export and cache behavior:

SEGMENTATION_UI_EXPORT.md

Examples and acceptance tests:

SEGMENTATION_TESTS.md