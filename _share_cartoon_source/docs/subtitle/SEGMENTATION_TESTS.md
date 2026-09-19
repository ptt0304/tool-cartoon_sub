# Subtitle Segmentation Examples and Acceptance Tests

## 1. Purpose

This document contains behavioral examples and acceptance tests for
subtitle segmentation.

These examples clarify expected behavior.

Core rules remain defined in:

SEGMENTATION.md

Algorithm rules remain defined in:

SEGMENTATION_ENGINE.md


# EXAMPLES


## 2. Example — Long Narrative

Input Utterance:

Start:
0.000

End:
15.200

Vietnamese:

Các khanh, năm Trẫm 28 tuổi, huynh đệ bị sát hại,
phụ hoàng bị giam lỏng, một mình Trẫm chống đỡ cả
bầu trời Đại Đường. Đi đến ngày hôm nay, cũng coi như
đạt được chút thành tựu nho nhỏ. Trẫm quyết định phong
thiện Thái Sơn, mọi người thấy có được không?

Expected behavior:

The Utterance should remain one source speech record.

The presentation layer may create multiple DisplaySegments.

A reasonable result may resemble:

SEGMENT 1

0.0 → ~4.3

Các khanh, năm Trẫm 28 tuổi,
huynh đệ bị sát hại, phụ hoàng bị giam lỏng.

SEGMENT 2

~4.3 → ~8.1

Một mình Trẫm chống đỡ
cả bầu trời Đại Đường.

SEGMENT 3

~8.1 → ~11.2

Đi đến ngày hôm nay,
cũng coi như có chút thành tựu.

SEGMENT 4

~11.2 → 15.2

Trẫm quyết định phong thiện Thái Sơn,
mọi người thấy có được không?

Exact timestamps are not required to match this example.

The segmentation logic should produce a semantically similar result.


## 3. Example — Short Subtitle

Input:

跟你没关系

Vietnamese:

Chuyện này không liên quan đến cô.

Duration:

2.4 seconds

Syllables:

7

Expected:

ONE DisplaySegment.

Do NOT split into:

Chuyện này

Không liên quan

Đến cô


## 4. Example — Long Sentence with Commas

Input:

Hôm nay Bệ hạ dám sửa cung điện, ngày mai sẽ dám đào
kênh đào, ngày mốt sẽ dám ba lần chinh phạt Cao Câu Ly.

Possible segmentation:

Hôm nay Bệ hạ dám sửa cung điện,

ngày mai sẽ dám đào kênh đào,

ngày mốt sẽ dám ba lần
chinh phạt Cao Câu Ly.

Reason:

Each comma separates a relatively complete semantic clause.

This does NOT mean every comma should always create a split.


## 5. Example — Named Entity

Input:

Hoàng đế quyết định chinh phạt Cao Câu Ly lần thứ ba.

Invalid:

Hoàng đế quyết định chinh phạt Cao

Câu Ly lần thứ ba.

Expected:

Preserve:

Cao Câu Ly

when a safe alternative exists.


## 6. Example — Title

Input:

Tần Vương điện hạ hôm nay sẽ vào cung.

Avoid:

Tần Vương

điện hạ hôm nay sẽ vào cung.

If the parser recognizes:

Tần Vương điện hạ

as a connected title/name phrase, preserve it when possible.


## 7. Example — Overlapping Speakers

SPK_01:

10.0 → 14.0

Vietnamese:

Anh nghe tôi giải thích, chuyện này không phải như anh nghĩ!

SPK_02:

11.5 → 13.8

Vietnamese:

Tôi không muốn nghe nữa!

Possible SPK_01 segmentation:

10.0 → 12.1

Anh nghe tôi giải thích,

12.1 → 14.0

chuyện này không phải như anh nghĩ!

SPK_02 remains:

11.5 → 13.8

Tôi không muốn nghe nữa!

Expected:

Overlap remains valid.


## 8. Example — Syllable Trigger

Input:

Anh thật sự cho rằng hôm nay tôi đến đây chỉ vì muốn
xin anh tha thứ cho những chuyện trước kia sao

Assume:

29 syllables

Max:

18

Expected:

Engine should search for a semantic boundary near the target size.

Possible:

Anh thật sự cho rằng hôm nay tôi đến đây

chỉ vì muốn xin anh tha thứ
cho những chuyện trước kia sao?

Do not blindly cut at exactly syllable 18.

If local rules cannot find a safe boundary, Gemini semantic fallback may
be used.


# ACCEPTANCE TESTS


## TEST A — Short Acceptable Subtitle

Input:

Chuyện này không liên quan đến cô.

Duration:

2.4 seconds

Expected:

1 DisplaySegment.


## TEST B — Long Semantic Subtitle

Input:

Các khanh, năm Trẫm 28 tuổi, huynh đệ bị sát hại,
phụ hoàng bị giam lỏng, một mình Trẫm chống đỡ cả
bầu trời Đại Đường.

Duration:

8 seconds

Expected:

Multiple semantic DisplaySegments.

Must NOT split every comma blindly.


## TEST C — Named Entity Preservation

Input:

Hoàng đế quyết định chinh phạt Cao Câu Ly lần thứ ba.

Expected:

"Cao Câu Ly" must not be broken when a safe alternative exists.


## TEST D — Speaker Overlap

SPK_01:

10 → 14

SPK_02:

11 → 13

Expected:

Valid overlap.

Auto segmentation must not modify speaker timing merely to remove
overlap.


## TEST E — Timestamp Coverage

Utterance:

0 → 10

Generated:

3 DisplaySegments.

Expected:

first.start == 0

last.end == 10

All boundaries ordered.

No negative duration.

No accidental gaps unless source timing explicitly contains one.


## TEST F — Do Not Oversplit

Text:

7 syllables

max_syllables:

18

duration:

3 seconds

Expected:

Do not split merely because punctuation exists.


## TEST G — Long Syllable Count

Text:

30 syllables

max_syllables:

18

Expected:

Must attempt semantic segmentation.


## TEST H — Source Text Preservation

Run automatic segmentation.

Then:

normalize(join(DisplaySegment.vi_text))
==
normalize(Utterance.vi_subtitle)

Expected:

TRUE.


# ADDITIONAL TESTS


## TEST I — Different Speaker Mapping

Utterance:

id = 100

speaker_id = SPK_03

Generated:

100.1
100.2
100.3

Expected:

Every DisplaySegment maps to:

SPK_03

Speaker detection must not run again.


## TEST J — Minimum Duration

Generated candidate:

A:
0 → 0.6

B:
0.6 → 4.0

Default minimum duration:

1.0 second

Expected:

Engine attempts semantic merge/rebalance.

Utterance outer timestamps remain unchanged.


## TEST K — AI Text Integrity

Original:

Anh thật sự không muốn gặp lại cô nữa.

Gemini fallback returns parts.

Expected:

normalize(join(parts))
==
normalize(original)

If Gemini adds/removes/rewrites words:

reject result.


## TEST L — AI Not Called for Easy Case

Input:

Chuyện này không liên quan đến cô.

Duration:

2.4 seconds

Expected:

Local segmentation succeeds.

Gemini semantic fallback is NOT called.


## TEST M — Reset

Utterance has:

12.1
12.2
12.3

User chooses:

Reset To Utterance

Expected:

Derived segmentation is reset.

Must NOT:

- rerun transcription
- rerun speaker detection
- delete translation
- modify Utterance outer timestamps


## TEST N — Cache Visual Change

Existing valid segmentation.

User changes:

subtitle font

Expected:

Segmentation cache remains valid.


## TEST O — Cache Constraint Change

Existing valid segmentation.

User changes:

max_syllables

Expected:

Relevant segmentation cache becomes invalid.


## TEST P — Manual Segmentation

User manually splits a DisplaySegment.

Expected:

segmentation_reason = manual


## TEST Q — Deterministic QC

Given:

12 syllables
2 seconds

Expected:

syllables_per_second = 6

Calculated locally.

Gemini must not be used for this calculation.


# IMPLEMENTATION ACCEPTANCE


## Required implementation order

Implement incrementally:

1. identify/migrate existing source subtitle model toward
   Utterance + DisplaySegment

2. add segmentation settings/model

3. integrate Vietnamese syllable counter

4. add candidate boundary parser

5. add segmentation scorer

6. add timestamp allocator

7. add overlap-safe behavior

8. add DisplaySegment QC

9. update UI

10. update SRT export

11. add optional Gemini semantic fallback LAST


## Regression requirements

Do not rewrite working:

- transcription
- speaker detection
- translation
- FFmpeg processing
- masking
- rendering

unless compatibility requires a targeted change.


## Completion requirements

After implementation:

- run targeted tests
- run broader tests when shared/core models changed
- smoke-test the application when UI/runtime behavior changed
- report backward compatibility issues
- report any remaining mock/fallback behavior


## Final invariant

UTTERANCE describes:

WHO SAID WHAT AND WHEN.

DISPLAY SEGMENT describes:

HOW THAT UTTERANCE IS SHOWN TO THE VIEWER.

Speaker-aware transcription remains Utterance-based.

Subtitle segmentation remains a presentation layer built on top of the
Utterance/master timeline.