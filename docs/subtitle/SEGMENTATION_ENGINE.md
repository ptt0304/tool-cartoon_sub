# Subtitle Segmentation Engine

## 1. Purpose

This document defines the local segmentation algorithm, boundary scoring,
timestamp allocation, QC and optional Gemini fallback.

The engine must be LOCAL-FIRST.

Gemini is not the normal segmentation engine.


## 2. Processing Pipeline

Vietnamese Utterance
↓
count syllables
↓
measure duration
↓
measure characters / visual constraints
↓
already acceptable?
├── YES → create one DisplaySegment
└── NO
    ↓
    detect candidate boundaries
    ↓
    score candidates
    ↓
    choose best segmentation
    ↓
    allocate timestamps
    ↓
    rebalance timestamps
    ↓
    validate
    ↓
    calculate QC
    ↓
    acceptable?
    ├── YES → return
    └── NO
        ↓
        Gemini semantic fallback if appropriate
        ↓
        validate again
        ↓
        return or mark MANUAL_REVIEW


## 3. Acceptability Check

Do not split an Utterance unnecessarily.

Example:

Text:

Chuyện này không liên quan đến cô.

Duration:

2.4 seconds

Syllables:

7

With default limits this should remain ONE DisplaySegment.

Punctuation alone is not a reason to split.


## 4. Candidate Boundary Detection

Candidate boundaries should be collected before choosing the final
segmentation.

Boundary classes:

### Strong punctuation

- .
- ?
- !
- …
- 。
- ？
- ！

Highest punctuation preference.

### Soft punctuation

- ,
- ;
- :
- ，
- ；
- ：

Useful when the surrounding clauses are semantically separable.

### Semantic boundaries

Natural clause transitions may be candidates.

Examples:

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

Do not split mechanically on these words.


## 5. Protected Boundaries

Avoid candidate boundaries that break:

- named entities
- names
- titles
- ranks
- dates
- numbers
- idioms
- compound expressions
- tightly connected phrases

Example:

Hoàng đế quyết định chinh phạt Cao Câu Ly lần thứ ba.

Do not split:

Hoàng đế quyết định chinh phạt Cao

Câu Ly lần thứ ba.

Preserve:

Cao Câu Ly

when possible.


## 6. Syllable Constraint

Vietnamese syllable count is a constraint and scoring signal.

It is NOT the primary split mechanism.

Do not implement:

split every N syllables

Instead:

1. detect that a segment exceeds the preferred/hard limit
2. search nearby semantic/punctuation boundaries
3. choose the best natural boundary

Example:

30-syllable text with max_syllables = 18

should trigger segmentation.

A 7-syllable text with max_syllables = 18 should not.


## 7. Candidate Scoring

Use scoring instead of one hardcoded split rule.

Conceptual signals:

semantic_score

punctuation_score

duration_fit

syllable_fit

visual_fit

line_length_fit

orphan_penalty

named_entity_penalty

bad_phrase_break_penalty


## 8. Example Candidate Scores

Conceptually:

after full stop:

semantic_score = high
punctuation_score = high

after comma:

semantic_score = medium/high
punctuation_score = medium

random word boundary:

semantic_score = low

A conceptual total may resemble:

score =
semantic_weight
+ punctuation_weight
+ duration_fit
+ syllable_fit
+ visual_fit
- orphan_penalty
- entity_break_penalty

The implementation does not have to use this exact mathematical
formula.

The architecture should support weighted scoring.


## 9. Avoid Orphan Segments

Avoid creating:

- one-word subtitles
- extremely short fragments
- fragments containing only discourse filler
- visually unbalanced fragments

If a candidate creates an orphan segment, penalize it.


## 10. Segmentation Profiles

The engine should consume settings from the selected profile.

BALANCED:

min_duration = 1.0
preferred_duration = 2.0–4.0
max_duration = 5.0
preferred_syllables = 8–16
max_syllables = 18
max_lines = 2
preferred_chars_per_line = 36
hard_max_chars_per_line = 44

READING_COMFORT:

preferred_duration = 2.5–4.5
preferred_syllables = 7–14
max_syllables = 16

FAST_DIALOGUE:

preferred_duration = 1.2–2.8
preferred_syllables = 5–11
max_syllables = 14

PRESERVE_SENTENCES:

prefer complete sentences and split only when constraints require it.

CUSTOM:

use user-configured values.


## 11. Timestamp Allocation

When one Utterance becomes multiple DisplaySegments, each DisplaySegment
must receive its own timestamp range.

Example:

Utterance:

0.0 → 15.2

Possible result:

0.0 → 4.3
4.3 → 8.1
8.1 → 11.2
11.2 → 15.2

Do not assign the entire 0.0 → 15.2 range to every child.


## 12. Timestamp Evidence Priority

Use this priority:

1. word-level timestamps
2. source clause position
3. syllable proportional estimate
4. character proportional estimate

Word-level timestamps are preferred when reliable source data exists.


## 13. Proportional Fallback

If precise timing information is unavailable, estimate child timing
based on text proportion.

Prefer syllable proportion over raw character proportion for Vietnamese
when appropriate.

Example:

Utterance duration:

10 seconds

Display text proportions roughly:

30%
40%
30%

Possible allocation:

0 → 3
3 → 7
7 → 10

This is a fallback, not a substitute for real word timestamps.


## 14. Timestamp Rebalancing

After initial allocation, rebalance poor timing.

Avoid:

Segment A:
0.0 → 0.6

Segment B:
0.6 → 6.0

when a semantically safe adjustment can produce more readable timing.

Default minimum duration:

1.0 second

If a generated segment is below minimum duration:

- merge with previous if semantically appropriate
- merge with next if semantically appropriate
- or shift the boundary

Do not modify the outer Utterance start/end.


## 15. Coverage

For a continuous Utterance:

first_display.start == utterance.start

last_display.end == utterance.end

DisplaySegment boundaries should be ordered.

Do not create:

- negative durations
- accidental gaps
- accidental reversed timestamps


## 16. Cross-Speaker Overlap

Overlap between speakers is valid.

Example:

SPK_01:

10 → 14

SPK_02:

11 → 13

If SPK_01 becomes:

10 → 12
12 → 14

SPK_02 remains:

11 → 13

Do not shift SPK_02.

Do not serialize independent speakers.


## 17. Reading Speed

Calculate locally:

characters_per_second

and:

syllables_per_second

Example:

12 syllables
duration = 2 seconds

syllables_per_second = 6

Use configurable thresholds to generate QC warnings.

Do not call Gemini for these calculations.


## 18. QC Status

Required DisplaySegment QC statuses:

OK

TOO_LONG

TOO_SHORT

TOO_MANY_SYLLABLES

TOO_MANY_LINES

HIGH_READING_SPEED

BAD_SPLIT

MANUAL_REVIEW

Multiple warnings may coexist if the implementation supports QC flags.


## 19. Validation

Before accepting generated DisplaySegments verify:

- every child references the correct Utterance
- speaker mapping remains correct
- timestamps are valid
- outer Utterance timing is preserved
- source text is preserved
- segments are ordered
- no accidental gaps were introduced for continuous speech
- no invalid empty segment exists


## 20. Source Text Validation

For normal segmentation:

normalize(join(parts))
==
normalize(original_text)

Normalization may account for:

- whitespace
- line breaks
- safe punctuation spacing normalization

It must not hide actual word additions/removals.


## 21. Local-First Requirement

Do not call Gemini for every long subtitle.

The normal path is:

local rules
→ candidate scoring
→ segmentation
→ validation

Gemini is only used when deterministic/local rules cannot find an
acceptable semantic boundary.


## 22. Gemini Semantic Fallback

Create a service conceptually equivalent to:

SemanticSegmentationService

Interface:

split(
    text,
    language,
    target_syllables,
    max_syllables,
    max_segments
)


## 23. Gemini Restrictions

Gemini may:

- identify semantic split positions
- return text parts corresponding to the original text

Gemini must NOT:

- translate
- paraphrase
- rewrite
- add words
- remove words
- change word order


## 24. Gemini Input

Conceptual input:

{
    "text": "...",
    "preferred_syllables": 12,
    "max_syllables": 18
}


## 25. Gemini Output

Structured output:

{
    "parts": [
        "...",
        "...",
        "..."
    ]
}


## 26. AI Result Validation

Backend must verify:

normalize(join(parts))
==
normalize(original_text)

If validation fails:

reject AI result.

Do not silently accept rewritten text.


## 27. AI Failure

If Gemini fails or returns invalid segmentation:

- preserve the source Utterance
- do not corrupt current DisplaySegments
- return/mark MANUAL_REVIEW where appropriate

AI failure must not destroy valid local data.


## 28. Deterministic Calculations

The following must remain local:

- duration
- syllable count
- character count
- reading speed
- timestamp validation
- source-text equality validation
- speaker inheritance
- overlap validity
- QC thresholds

Do not outsource deterministic business rules to Gemini.


## 29. Engine Output

The engine returns zero or more validated DisplaySegments associated
with one Utterance.

For an acceptable short Utterance, normally return exactly one
DisplaySegment.

For a long Utterance, return the minimum reasonable number of
semantically coherent DisplaySegments required for readability.


## 30. Implementation Principle

Prefer:

semantic correctness
+
reading comfort
+
timing quality
+
visual readability

over blindly reaching an exact syllable count.

Syllables help determine whether and how strongly segmentation is
needed.

Semantic boundaries determine where the split should occur.