# Video Render Performance Audit — Project 1

**Audit date:** 2026-10-14  
**Scope:** Read-only inspection of `Projects/1`, current renderer source, existing final output, and prior local benchmark artifacts. No source, project setting, media, API, or executable was modified by this audit.

## SOURCE_MEDIA

| Property | Source `1.mp4` |
|---|---:|
| Duration | 574.578639 s |
| Video | AV1 Main, level 8, yuv420p |
| Resolution / FPS | 1920×1080 / 29.9999995 fps |
| Video bitrate | 843,070 bps |
| Audio | AAC-LC, 145,659 bps |
| Total bitrate | 996,798 bps |
| File size | 71,592,399 bytes (68.27 MiB) |

## FINAL_MEDIA

| Property | Current `final.mp4` |
|---|---:|
| Duration | 574.578 s |
| Video | H.264 High, level 4.0, yuv420p |
| Resolution / FPS | 1920×1080 / 30 fps |
| Video bitrate | 3,007,866 bps |
| Audio | AAC-LC, 191,786 bps |
| Total bitrate | 3,208,353 bps |
| File size | 230,431,188 bytes (219.76 MiB) |

## CURRENT_FFMPEG_COMMAND

The production path is `Export tab → VideoRenderer.render() → save_ass() → mask_events_from_ass() → mask_filter_chain() → run_process()`.

Effective full-render argument shape:

```text
ffmpeg -nostdin -n -i Projects/1/1.mp4 -i Projects/1/audio/final_audio.wav
-filter_complex_script <temporary-export-folder>/fc.txt
-map [v] -map 1:a:0
-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p
-c:a aac -b:a 192k -movflags +faststart <temporary-rendering.mp4>
```

| Setting | Effective value |
|---|---|
| FFmpeg path | System `ffmpeg` resolved by the renderer |
| Input video | `Projects/1/1.mp4` |
| Input audio | `Projects/1/audio/final_audio.wav` |
| Output | `Projects/1/final.mp4` after atomic move |
| Video encoder | `libx264` |
| Preset | `veryfast` |
| Rate control | CRF |
| CRF | 20 |
| Fixed video bitrate / maxrate / bufsize | Not set |
| Pixel format | `yuv420p` |
| FPS option | Not set; source timing is preserved through the filter pipeline |
| Audio encoder / bitrate | AAC / 192k |
| Threads | Not explicitly set; FFmpeg defaults apply |
| Filter transport | `-filter_complex_script <fc.txt>` |

## OUTPUT_BITRATE_CAUSE

`libx264 -crf 20` is content-adaptive quality control, not source-bitrate preservation and not fixed bitrate. Blur, re-rendered subtitles, and H.264 encoding can require more bits than the low-bitrate AV1 input to satisfy CRF 20. The observed 3.008 Mbps video bitrate is therefore expected under the current CRF target; it does not indicate an extra encode pass.

Expected size from the measured final aggregate bitrate is approximately:

```text
3.208 Mbps × 574.578 s ÷ 8 = 230.4 MB decimal
```

This matches the actual 230.43 MB final file.

## FILTER_GRAPH_SUMMARY

The current full graph has one ASS subtitle filter and Gaussian mask branches derived from ASS events. Project 1 has 244 display events, no temporal overlaps, and 57 unique auto-fit ROI geometries.

| Full graph property | Current value |
|---|---:|
| Display segments / mask events | 244 / 244 |
| ASS subtitle filters | 1 |
| Gaussian branches | 57 |
| Crop branches | 57 |
| Overlay branches | 57 |
| Split filters | 1 (`split=58`) |
| Serial overlay chain | 57 |
| `enable` expressions | 114 |
| Estimated filter nodes | 1,207 |
| Filter script bytes | 22,510 |

The current renderer groups non-overlapping events with identical ROI geometry. It preserves every original timing interval; overlap timelines deliberately retain the legacy one-branch-per-event graph to preserve composition ordering.

Time gating status:

- `gblur`: gated
- `overlay`: gated
- `crop`: not gated
- `split`: not gated

Thus inactive blur work is avoided, but each frame still traverses the grouped split/crop/overlay graph.

## TEST30_VS_FULL

Test30 and full export use the same ASS renderer, Gaussian-mask renderer, `libx264 veryfast CRF 20`, AAC 192k, and filter-script architecture. The only material differences are selected time range/events and final-audio mapping.

| Range | Display events | Filter nodes | Script bytes |
|---:|---:|---:|---:|
| 30 s | 14 | 107 | 2,717 |
| 60 s | 27 | 195 | 4,866 |
| 120 s | 50 | 331 | 7,879 |
| 300 s | 134 | 743 | 15,571 |
| Full 574.579 s | 244 | 1,207 | 22,510 |

Graph complexity grows approximately linearly with event/geometry count. It is not a full-244-event graph for a short test range.

## BENCHMARKS

No network/API calls were made. The 120s and 300s samples were intentionally stopped after roughly 90 seconds; the final FFmpeg speed is still valid for estimating throughput.

| Range | Wall time | Media reached | Final speed |
|---:|---:|---:|---:|
| 30 s | 68.905 s | complete | 0.436x |
| 60 s | 130.916 s | complete | 0.458x |
| 120 s | 90.635 s | 40.80 s | 0.452x |
| 300 s | 90.727 s | 29.66 s | 0.329x |

Mask-off diagnostic from the earlier current-code measurement: 23.40 s / 1.29x for 30 seconds. No-subtitle diagnostic: 28.02 s / 1.07x. ASS is a single filter and is not the material bottleneck; dynamic Gaussian mask composition is.

## SCALING_CAUSE

The renderer is filter-bound. Performance reduces as range length accumulates more distinct mask geometries: each still requires a split branch, crop, time-gated Gaussian blur, and serial overlay. The 300s sample has 51 such branches; the full plan has 57. Encoder settings and filter-script transport are unchanged between test and full paths.

A conservative full-time estimate from 0.33–0.44x is 22–29 minutes. An observed 80-minute run is slower than this estimate and should be checked against system contention/thermal state or a stale executable/process, but the render architecture still remains mask-filter dominated.

## WINERROR206_DIFF

The WinError 206 change replaced only:

```text
-filter_complex <large graph>
```

with:

```text
-filter_complex_script <fc.txt>
```

It did not change event selection, ASS generation, mask semantics, encoder, preset, CRF, audio settings, or test/full mode logic. The performance behavior is graph computation, not argument transport.

## MULTI_PASS_CHECK

Full Video performs **one** full H.264 video encode. It writes one temporary `rendering.mp4`, validates it, then moves it to `final.mp4`. There is no intermediate-video re-encode or separately rendered mask-video pass.

## MASK_COST

Mask is the dominant cost. Gaussian branches are ROI-only—not full-frame blur—but the split/crop/overlay topology still costs per unique geometry. The current branch grouping is present and reduced Project 1 from a one-per-event full graph (244 branches) to 57 branches.

## ASS_COST

ASS contributes one `ass=filename=subtitle.ass` filter. It does not create 244 `drawtext` filters. The diagnostics do not show ASS as a material source of render time.

## BITRATE_RECOMMENDATIONS

These are future audit recommendations only; no setting was changed.

| Candidate | Expected tradeoff |
|---|---|
| Current CRF 20 | Highest current quality target; observed final about 230 MB decimal |
| CRF 22 | Usually smaller output with modest quality reduction; exact rate requires encode measurement |
| CRF 23 | Further size reduction; artifacts may become more visible around blur/text edges |
| Fixed 1.0 Mbps video + 192 kbps audio | Approx. 85.6 MB decimal total; can constrain complex scenes |
| Fixed 1.5 Mbps video + 192 kbps audio | Approx. 121.5 MB decimal total; safer than 1.0 Mbps but still below current CRF result |

CRF is content dependent, so no exact CRF22/23 file size is claimed without controlled encodes.

## SAFE_OPTIMIZATION_ORDER

1. Measure a fresh full render from the canonical EXE without competing FFmpeg/system load.
2. If full remains materially slower than the 22–29 minute estimate, profile the remaining 57-branch serial mask graph.
3. Only then evaluate a mask-architecture change with frame-level visual parity tests.
4. Separately, offer an explicit quality/size profile only after visual comparisons at CRF22/23 or a selected bitrate target.

No production changes are included in this audit.
