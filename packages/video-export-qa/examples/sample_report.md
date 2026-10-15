# Video export QA report

> SHA-256 values below are shortened to 12 characters (the real report prints all 64). Everything else is verbatim output.
>
> How it was made: `input.mp4` is the unmodified output of `make_test_clip.py barcode_clip.mp4 --frames 96 --fps 24 --size 1920x1080`, standing in for a straight export of the barcode clip. It was checked with `examples/spec_barcode_run.json` (the delivery spec `spec_h264_1080p24.json` without its four loudness keys, because the test tone measures about -20.7 LUFS), copied next to `input.mp4`, and `--barcode-expect "0-95"`: `python -I -B <skill-dir>/scripts/run_qa.py input.mp4 --spec spec_barcode_run.json --barcode-expect "0-95" --out-dir qa_out`.

**Overall: PASS** - 20 PASS, 0 WARN, 0 FAIL, 3 SKIP (SKIP means no claim is made; it is never a pass).

## Next steps

Nothing to fix: no check failed or warned. Checks marked SKIP made no claim and prove nothing.

## What was checked

| Item | Value |
| --- | --- |
| File | input.mp4 |
| Size | 125,715 bytes |
| Modified (UTC) | 2026-10-15T06:37:06Z |
| SHA-256 of the file | 81314f3382b1... |
| Spec | spec_barcode_run.json (sha256 d86de81a8019...) |
| Parts run | qa_probe.py, qa_audio.py, qa_frames.py (expect 0-95) |
| Report made (UTC) | 2026-10-15T06:37:07Z |
| Tool | run_qa.py 1.2.1 on Python 3.14.6 |
| ffmpeg | ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers |
| ffprobe | ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers |

## Summary

| PASS | WARN | FAIL | SKIP |
| --- | --- | --- | --- |
| 20 | 0 | 0 | 3 |

## Checks

| Status | Check | From | Expected | Measured | Detail |
| --- | --- | --- | --- | --- | --- |
| PASS | `video_stream` | qa_probe.py | at least one video stream | 1 | h264 1920x1080 |
| PASS | `cfr` | qa_probe.py | constant frame rate | min 0.0417 s, max 0.0417 s | 95 frame intervals, median 0.0417 s, one tick = 0.000081 s |
| PASS | `frame_count_consistent` | qa_probe.py | nb_frames=96, duration*fps=96 | 96 | decoded 96; references: nb_frames 96, duration*fps 96 |
| PASS | `color_tags` | qa_probe.py | space=bt709, transfer=bt709, primaries=bt709, range=tv | space=bt709, transfer=bt709, primaries=bt709, range=tv | matches the spec |
| PASS | `codec` | qa_probe.py | h264 | h264 | - |
| PASS | `pix_fmt` | qa_probe.py | yuv420p | yuv420p | - |
| PASS | `width` | qa_probe.py | 1920 | 1920 | - |
| PASS | `height` | qa_probe.py | 1080 | 1080 | - |
| PASS | `even_dimensions` | qa_probe.py | width and height both even | 1920x1080 | - |
| PASS | `fps` | qa_probe.py | 24 | 24/1 (24.000000) | within 0.001 fps |
| PASS | `audio.present` | qa_probe.py | true | true | 1 audio stream(s) |
| PASS | `audio.sample_rate` | qa_probe.py | 48000 | 48000 | - |
| PASS | `audio.channels` | qa_probe.py | 2 | 2 | - |
| PASS | `audio.codec` | qa_probe.py | aac | aac | - |
| PASS | `max_av_start_offset_s` | qa_probe.py | 0.05 | 0.0 | video start minus audio start = +0.0000 s (positive means audio starts first) |
| PASS | `not_silent` | qa_audio.py | mean volume above -90 dB and integrated loudness above -70 LUFS | mean_volume_db=-23.0, integrated_lufs=-20.7 | mean volume -23.0 dB, integrated loudness -20.7 LUFS |
| PASS | `all_frames_decodable` | qa_frames.py | 0 undecodable frames | 0 of 96 undecodable | every frame decoded (lowest block margin 124.2 of a possible 127) |
| PASS | `low_block_margin` | qa_frames.py | lowest block margin at least 40 of a possible 127 | 124.2 | every barcode block is clearly black or white |
| SKIP | `no_dropped_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `no_duplicate_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `monotonic` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| PASS | `expect` | qa_frames.py | 0-95 (96 frames) | 0-95 (96 frames) | all 96 output frames carry the expected source index |
| PASS | `av_duration_match` | run_qa.py | sound and picture within 0.1 s of each other | picture 4.000 s, sound 4.011 s | the sound is 0.011 s longer than the picture |

Long values are shortened in this table. The complete text of every FAIL, WARN and NOT MEASURED row is repeated under Next steps (above), and report.json (with --out-dir) keeps everything whole.

## Barcode runs

Which source frame sits on which output frame, read from the barcode on every frame (output frame numbers and source frame numbers both start at 0):

```
  output 0-95 -> source 0-95  (96 frames)
```

## How this report was made

Commands that were run (tool and file paths shortened; the exact argument lists are in report.json):

- `ffprobe -v error -protocol_whitelist file -show_format -show_streams -of json input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams 0 -count_frames -show_entries stream=nb_read_frames:frame=pts,best_effort_timestamp -of json=compact=1 input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams a -show_entries stream=index,codec_name,sample_rate,channels -of json input.mp4`
- `ffmpeg -hide_banner -nostdin -nostats -protocol_whitelist file -i input.mp4 -map 0:a:0 -af asetpts=PTS-STARTPTS,ebur128=peak=true:framelog=verbose,volumedetect,silencedetect=noise=-50dB:d=0.5 -f null -`
- `ffmpeg -hide_banner -nostdin -loglevel error -noautorotate -protocol_whitelist file -i input.mp4 -map 0:v:0 -an -sn -dn -vf "crop=w=iw*1.000000:h=ih*0.083300:x=iw*0.000000:y=ih*0.000000,scale=208:4:flags=area,format=gray" -fps_mode passthrough -f rawvideo -`
