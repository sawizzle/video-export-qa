# Example session: is this export what I asked for?

_SHA-256 values in this example are shortened to 12 characters (the real report prints all 64) so they are not mistaken for credentials; every other line is verbatim output._

## The request

> I made a 4 second barcode test clip with your `make_test_clip.py` (1920x1080, 24 fps, 96 frames) and put it in my editor. I deleted frames 30 to 35 and rejoined the clip, exported it as `input.mp4`, and I have to deliver it to the spec in `spec_h264_1080p24.json`. Can you check that the export has exactly the frames I meant and meets the spec?

_How this example was made: there is no editor in the loop. `input.mp4` was produced with ffmpeg from the barcode clip to contain four typical faults: the out point treated as exclusive (frames 30 to 34 cut instead of 30 to 35), untagged colour, yuv444p pixels and a silent audio track. The report below is the real, unedited output of `run_qa.py` on that file._

## What the agent ran

Python 3.9 or newer and ffmpeg are needed; the skill installs nothing. The agent confirms both are there:

```
$ ffmpeg -version
ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
$ python --version
Python 3.14.6
```

The export, the spec and the barcode expectation "0-29,36-95" (frames 0 to 29, then 36 to 95: six frames removed) go into one command, run from the folder that holds `input.mp4`:

```
python -I -B <skill-dir>/scripts/run_qa.py input.mp4 --spec spec_h264_1080p24.json --barcode-expect "0-29,36-95" --out-dir qa_out
```

Exit code: 1 (1 means at least one check failed).

## Complete output of run_qa.py

```
# Video export QA report

**Overall: FAIL** - 18 PASS, 0 WARN, 5 FAIL, 3 SKIP (SKIP means not measured; it is never a pass).

## What was checked

| Item | Value |
| --- | --- |
| File | input.mp4 |
| Size | 338,776 bytes |
| Modified (UTC) | 2026-10-11T17:24:19Z |
| SHA-256 of the file | 574f4fe55165... |
| Spec | spec_h264_1080p24.json (sha256 f89a1c31462d...) |
| Parts run | qa_probe.py, qa_audio.py, qa_frames.py (expect 0-29,36-95) |
| Report made (UTC) | 2026-10-11T17:24:20Z |
| Tool | run_qa.py 1.1.0 on Python 3.14.6 |
| ffmpeg | ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers |
| ffprobe | ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers |

## Summary

| PASS | WARN | FAIL | SKIP |
| --- | --- | --- | --- |
| 18 | 0 | 5 | 3 |

## Checks

| Status | Check | From | Expected | Measured | Detail |
| --- | --- | --- | --- | --- | --- |
| PASS | `video_stream` | qa_probe.py | at least one video stream | 1 | h264 1920x1080 |
| PASS | `cfr` | qa_probe.py | constant frame rate | min 0.0417 s, max 0.0417 s | 90 frame intervals, median 0.0417 s, one tick = 0.000081 s |
| PASS | `frame_count_consistent` | qa_probe.py | nb_frames=91, duration*fps=91 | 91 | decoded 91; references: nb_frames 91, duration*fps 91 |
| FAIL | `color_tags` | qa_probe.py | space=bt709, transfer=bt709, primaries=bt709, range=tv | space=unknown, transfer=bt709, primaries=bt709, range=tv | space is unknown, spec wants bt709 |
| PASS | `codec` | qa_probe.py | h264 | h264 | - |
| FAIL | `pix_fmt` | qa_probe.py | yuv420p | yuv444p | - |
| PASS | `width` | qa_probe.py | 1920 | 1920 | - |
| PASS | `height` | qa_probe.py | 1080 | 1080 | - |
| PASS | `even_dimensions` | qa_probe.py | width and height both even | 1920x1080 | - |
| PASS | `fps` | qa_probe.py | 24 | 24/1 (24.000000) | within 0.001 fps |
| PASS | `audio.present` | qa_probe.py | true | true | 1 audio stream(s) |
| PASS | `audio.sample_rate` | qa_probe.py | 48000 | 48000 | - |
| PASS | `audio.channels` | qa_probe.py | 2 | 2 | - |
| PASS | `audio.codec` | qa_probe.py | aac | aac | - |
| PASS | `max_av_start_offset_s` | qa_probe.py | 0.05 | 0.0 | video start minus audio start = +0.0000 s (positive means audio starts first) |
| FAIL | `not_silent` | qa_audio.py | mean volume above -90 dB and integrated loudness above -70 LUFS | mean_volume_db=-91.0, integrated_lufs=-70.0 | audio stream present but silent: mean volume -91.0 dB is digital silence |
| FAIL | `lufs_target` | qa_audio.py | target=-23.0, tol=1.0 | -70.0 | measured -70.0 LUFS, 47.0 LU from the target |
| PASS | `true_peak_max_db` | qa_audio.py | -1.0 | -inf | true peak within the limit |
| PASS | `no_clipping` | qa_audio.py | true peak below -0.1 dBTP | -inf | true peak -inf dBTP |
| PASS | `all_frames_decodable` | qa_frames.py | 0 undecodable frames | 0 of 91 undecodable | every frame decoded (lowest block margin 126.5 of a possible 127) |
| PASS | `low_block_margin` | qa_frames.py | lowest block margin at least 40 of a possible 127 | 126.5 | every barcode block is clearly black or white |
| SKIP | `no_dropped_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `no_duplicate_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `monotonic` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| FAIL | `expect` | qa_frames.py | 0-29,36-95 (90 frames) | 0-29,35-95 (91 frames) | decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s) |
| PASS | `av_duration_match` | run_qa.py | sound and picture within 0.1 s of each other | picture 3.792 s, sound 3.776 s | the sound is 0.016 s shorter than the picture |

Long values are shortened in this table. The complete text of every FAIL and WARN is repeated under Next steps, and report.json (with --out-dir) keeps everything whole.

## Barcode runs

Which source frame sits on which output frame, read from the barcode on every frame (output frame numbers and source frame numbers both start at 0):

```
  output 0-29 -> source 0-29  (30 frames)
  output 30-90 -> source 35-95  (61 frames)
```

## Next steps

1. **color_tags** (FAIL, from qa_probe.py)
   - Measured: space=unknown, transfer=bt709, primaries=bt709, range=tv
   - Expected: space=bt709, transfer=bt709, primaries=bt709, range=tv
   - Detail: space is unknown, spec wants bt709
   - Likely causes: An image sequence turned into H.264 with no colour flags, an export preset without colour metadata. The pixels may also be in the BT.601 matrix while players assume BT.709.
   - What to do: Convert and tag in one step, see references/pitfalls.md (untagged colour). Re-run and check the tags. A GIF cannot carry tags and is SKIP.
2. **pix_fmt** (FAIL, from qa_probe.py)
   - Measured: yuv444p
   - Expected: yuv420p
   - Likely causes: Image sequence encoded without a pixel format, 10-bit or 4:2:2 preset used for web or phone delivery.
   - What to do: Export again with the required pixel format (ffmpeg: -pix_fmt yuv420p).
3. **not_silent** (FAIL, from qa_audio.py)
   - Measured: mean_volume_db=-91.0, integrated_lufs=-70.0
   - Expected: mean volume above -90 dB and integrated loudness above -70 LUFS
   - Detail: audio stream present but silent: mean volume -91.0 dB is digital silence
   - Likely causes: Audio muted or disabled, a silent placeholder track, the wrong source exported, an audio render that failed. For the WARN: sound only in the LFE channel.
   - What to do: Play the file, then re-export with the audio enabled. A stream that exists is not proof of sound. If the sound is only in the LFE channel, ignore the loudness numbers: BS.1770 does not count that channel.
4. **lufs_target** (FAIL, from qa_audio.py)
   - Measured: -70.0
   - Expected: target=-23.0, tol=1.0
   - Detail: measured -70.0 LUFS, 47.0 LU from the target
   - Likely causes: No loudness normalisation, mix level changed after the meter check.
   - What to do: Normalise the mix to the target (or pick the correct target for the recipient) and export again.
5. **expect** (FAIL, from qa_frames.py)
   - Measured: 0-29,35-95 (91 frames)
   - Expected: 0-29,36-95 (90 frames)
   - Detail: decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s)
   - Likely causes: The edit is not what you meant (off by one at an in or out point, wrong order), or the exporter changed the frames.
   - What to do: Read the runs (listed under Barcode runs in report.md, or qa_frames.py --show-runs) to see what the export really contains, then fix the edit or the expectation.

## How this report was made

Commands that were run (tool and file paths shortened; the exact argument lists are in report.json):

- `ffprobe -v error -protocol_whitelist file -show_format -show_streams -of json input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams 0 -count_frames -show_entries stream=nb_read_frames:frame=pts,best_effort_timestamp -of json=compact=1 input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams a -show_entries stream=index,codec_name,sample_rate,channels -of json input.mp4`
- `ffmpeg -hide_banner -nostdin -nostats -protocol_whitelist file -i input.mp4 -map 0:a:0 -af asetpts=PTS-STARTPTS,ebur128=peak=true:framelog=verbose,volumedetect,silencedetect=noise=-50dB:d=0.5 -f null -`
- `ffmpeg -hide_banner -nostdin -loglevel error -noautorotate -protocol_whitelist file -i input.mp4 -map 0:v:0 -an -sn -dn -vf "crop=w=iw*1.000000:h=ih*0.083300:x=iw*0.000000:y=ih*0.000000,scale=208:4:flags=area,format=gray" -fps_mode passthrough -f rawvideo -`
```

## What the agent tells the user

**Verdict: FAIL.** 5 of 26 checks failed, 0 warned, 3 were not measured. The export does not match the spec or the edit you described. (Frame numbers count from 0, as the barcode does. No tolerance had to be chosen: every length in the spec is exact.)

| Check | Status | Measured | Expected |
| --- | --- | --- | --- |
| `color_tags` | FAIL | space=unknown, transfer=bt709, primaries=bt709, range=tv | space=bt709, transfer=bt709, primaries=bt709, range=tv |
| `pix_fmt` | FAIL | yuv444p | yuv420p |
| `not_silent` | FAIL | mean_volume_db=-91.0, integrated_lufs=-70.0 | mean volume above -90 dB and integrated loudness above -70 LUFS |
| `lufs_target` | FAIL | -70.0 | target=-23.0, tol=1.0 |
| `expect` | FAIL | 0-29,35-95 (91 frames) | 0-29,36-95 (90 frames) |

Reading the `expect` row: the export holds source frames `0-29,35-95`, so the cut removed frames 30 to 34 (five frames) and frame 35 is still there. The out point was treated as exclusive. The edit you described is `0-29,36-95`. Size, frame rate, codec and channel layout all matched the spec; the problems are the cut, the colour tags, the pixel format and an audio track that is digital silence.

Next steps:

1. `color_tags`: space is unknown, spec wants bt709
   Do: Convert and tag in one step, see references/pitfalls.md (untagged colour). Re-run and check the tags. A GIF cannot carry tags and is SKIP.
2. `pix_fmt`: measured yuv444p, expected yuv420p
   Do: Export again with the required pixel format (ffmpeg: -pix_fmt yuv420p).
3. `not_silent`: audio stream present but silent: mean volume -91.0 dB is digital silence
   Do: Play the file, then re-export with the audio enabled. A stream that exists is not proof of sound. If the sound is only in the LFE channel, ignore the loudness numbers: BS.1770 does not count that channel.
4. `lufs_target`: measured -70.0 LUFS, 47.0 LU from the target
   Do: Normalise the mix to the target (or pick the correct target for the recipient) and export again.
5. `expect`: decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s)
   Do: Read the runs (listed under Barcode runs in report.md, or qa_frames.py --show-runs) to see what the export really contains, then fix the edit or the expectation.

Not measured (SKIP, so no claim is made): `no_dropped_frames`, `no_duplicate_frames`, `monotonic`. They were skipped because the frame expectation was declared with --barcode-expect, which makes `expect` the verdict for dropped, repeated and re-ordered frames.

Full report: `qa_out/report.md` and `qa_out/report.json`. Your export was not modified.
