# Subtitle Segmentation Data Model

## 1. Purpose

This document defines the data structures used by subtitle segmentation.

The core hierarchy is:

Project
└── Utterance
    └── DisplaySegment


## 2. Utterance

Utterance is the source-of-truth speech record.

Conceptual model:

{
    "id": 12,

    "speaker_id": "SPK_02",

    "start": 32.2,
    "end": 36.2,

    "zh": "...",

    "vi_subtitle": "...",
    "vi_dubbing": "...",

    "display_segments": []
}

An Utterance represents one natural speech unit from one speaker.


## 3. Utterance Responsibilities

Utterance owns:

- id
- speaker_id
- speaker metadata/reference
- original start
- original end
- Chinese transcript
- Vietnamese subtitle translation
- Vietnamese dubbing text
- overlap metadata when applicable
- DisplaySegment children

Speaker detection remains associated with Utterance.

Translation context remains associated with Utterance.


## 4. DisplaySegment

DisplaySegment is a presentation-layer subtitle record.

Conceptual model:

{
    "id": "12.1",

    "utterance_id": 12,

    "start": 32.2,
    "end": 34.1,

    "zh_text": "...",
    "vi_text": "...",

    "vi_syllables": 11,

    "line_count": 2,

    "segmentation_reason": "comma_boundary"
}


## 5. Relationship

Example:

Utterance 12
speaker_id = SPK_02

├── DisplaySegment 12.1
├── DisplaySegment 12.2
└── DisplaySegment 12.3

All DisplaySegments belong to Utterance 12.

All inherit:

speaker_id = SPK_02

Speaker detection must not run again for these DisplaySegments.


## 6. DisplaySegment Identity

DisplaySegment ID must remain traceable to its parent Utterance.

Examples:

12.1
12.2
12.3

Exact internal representation may differ if the existing architecture
has stronger requirements.

The invariant is:

DisplaySegment → Utterance mapping must always be recoverable.


## 7. Segmentation Reason

Supported segmentation_reason values should include:

- sentence_boundary
- comma_boundary
- semantic_boundary
- duration_limit
- syllable_limit
- manual
- ai_fallback

Additional internal reasons may be added if needed.

Do not silently change the meaning of existing values.


## 8. Manual Segmentation

When the user manually changes a segmentation boundary:

segmentation_reason = manual

Manual editing must remain distinguishable from automatic segmentation.


## 9. Timestamp Rules

Every DisplaySegment must satisfy:

start >= 0

end > start

DisplaySegments belonging to the same continuous Utterance should
normally be chronologically ordered.

The first generated DisplaySegment should begin at the Utterance start.

The last generated DisplaySegment should end at the Utterance end.

Example:

Utterance:

0.0 → 10.0

Generated:

0.0 → 3.1
3.1 → 6.7
6.7 → 10.0

No negative duration is allowed.

No accidental gaps should be introduced when the source Utterance
represents continuous speech.


## 10. Cross-Speaker Overlap

Timestamp overlap between different speakers is valid.

Example:

Utterance A:

speaker = SPK_01
10 → 14

Utterance B:

speaker = SPK_02
11 → 13

VALID.

DisplaySegments generated from A must not change B.

DisplaySegments generated from B must not change A.


## 11. Speaker Mapping

DisplaySegment inherits its speaker from the parent Utterance.

Example:

Utterance:

id = 100
speaker_id = SPK_03

DisplaySegments:

100.1 → SPK_03
100.2 → SPK_03
100.3 → SPK_03

Do not create new speaker identities during subtitle segmentation.


## 12. Text Fields

Keep source-level and presentation-level text separate.

Utterance:

vi_subtitle

DisplaySegment:

vi_text

The Utterance translation remains the source text.

DisplaySegment text represents a slice of that translation.


## 13. Text Immutability

For automatic segmentation:

normalize(
    join(all DisplaySegment.vi_text ordered by segment)
)
==
normalize(
    Utterance.vi_subtitle
)

Whitespace and line-break normalization may differ.

Actual language rewriting is not part of segmentation.


## 14. Vietnamese Dubbing

Utterance may contain:

vi_subtitle
vi_dubbing

These remain separate.

Subtitle segmentation normally operates on:

vi_subtitle

unless the caller explicitly requests segmentation of another text
variant.

Do not overwrite vi_subtitle when working with vi_dubbing.


## 15. QC Metadata

DisplaySegment should support QC state.

Required statuses include:

- OK
- TOO_LONG
- TOO_SHORT
- TOO_MANY_SYLLABLES
- TOO_MANY_LINES
- HIGH_READING_SPEED
- BAD_SPLIT
- MANUAL_REVIEW

Implementation may represent these as:

- enum
- list of flags
- structured QC result

Prefer compatibility with the existing project model.


## 16. Optional Derived Fields

Useful derived fields may include:

duration

vi_syllables

character_count

characters_per_second

syllables_per_second

line_count

These values should be calculated locally.

Do not use Gemini for deterministic calculations.


## 17. Serialization

Project save/load must preserve:

- Utterance identity
- speaker mapping
- timestamps
- source translation
- DisplaySegments
- segmentation reason
- relevant QC/manual state

New project data must remain deterministic after save/load.


## 18. Backward Compatibility

Existing projects may not contain DisplaySegments.

When loading an older project:

- do not destroy existing timeline data
- treat existing source subtitle record as the Utterance/source record
- initialize DisplaySegment state safely
- generate segmentation only when explicitly required by current workflow

Do not silently rewrite old project files merely because they were
loaded.


## 19. Migration Principle

If the current code uses names such as:

Segment

or:

SubtitleSegment

for the master timeline record, migration must be incremental.

Prefer compatibility aliases/adapters where necessary.

Do not perform a repository-wide destructive rename unless explicitly
required.

The semantic target is:

Utterance = master/source speech record

DisplaySegment = subtitle presentation record


## 20. Source of Truth

The hierarchy must always preserve:

Utterance
    ↓
DisplaySegments

Never invert the relationship.

DisplaySegments are derived presentation data.

They must not become the authoritative source for:

- speaker detection
- original transcript
- original speech timestamps
- translation context