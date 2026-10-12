# Example session: is this export what I asked for?

_The report block under "Complete output of run_qa.py" is verbatim output, except that its SHA-256 values are shortened to 12 characters (the real report prints all 64) so they are not mistaken for credentials. The request and the answer under "What the agent tells the user" are written to illustrate the format in SKILL.md, from the numbers in that report; the version lines under "What the agent ran" show only the first line of each tool's output._

## The request

> I made a 4 second barcode test clip with your `make_test_clip.py` (1920x1080, 24 fps, 96 frames) and put it in my editor. I deleted frames 30 to 35 and rejoined the clip, and exported it as `input.mp4`. The delivery spec for the real job is in `spec_h264_1080p24.json`. Can you check that this export has exactly the frames I meant and that the export settings meet the spec?

_How this example was made: there is no editor in the loop. `input.mp4` was produced with ffmpeg from the barcode clip to contain four typical faults: the out point treated as exclusive (frames 30 to 34 cut instead of 30 to 35), untagged colour, yuv444p pixels and a silent audio track. The report below is the real output of `run_qa.py` on that file._

## What the agent ran

Python 3.9 or newer, ffmpeg and ffprobe are needed; the skill installs nothing. The agent confirms all three:

```
$ ffmpeg -version
ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
$ ffprobe -version
ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers
$ python --version
Python 3.14.6
```

The barcode clip's tone measures about -20.7 LUFS, so loudness cannot be judged on the export of the clip. For this barcode run the agent uses `spec_barcode_run.json`, copied from the skill's `examples/` folder next to `input.mp4`: the delivery spec `spec_h264_1080p24.json` without `lufs_target`, `lufs_tol`, `true_peak_max_db` and `no_clipping` (the `audio` keys stay). The export, that spec and the barcode expectation "0-29,36-95" (frames 0 to 29, then 36 to 95: six frames removed) go into one command, run from the folder that holds `input.mp4`:

```
python -I -B <skill-dir>/scripts/run_qa.py input.mp4 --spec spec_barcode_run.json --barcode-expect "0-29,36-95" --out-dir qa_out
```

Exit code: 1 (1 means at least one check failed).

## Complete output of run_qa.py

```
# Video export QA report

**Overall: FAIL** - 16 PASS, 0 WARN, 4 FAIL, 3 SKIP (SKIP means no claim is made; it is never a pass).

## Next steps

1. **color_tags** (FAIL, from qa_probe.py)
   - Measured: space=unknown, transfer=unknown, primaries=unknown, range=unknown
   - Expected: space=bt709, transfer=bt709, primaries=bt709, range=tv
   - Detail: space is unknown, spec wants bt709; transfer is unknown, spec wants bt709; primaries is unknown, spec wants bt709; range is unknown, spec wants tv
   - Likely causes: An image sequence turned into H.264 with no colour flags, an export preset without colour metadata. The pixels may also be in the BT.601 matrix while players assume BT.709.
   - What to do: Best: export again from the editor with Rec.709 colour tags switched on. If the pixels are known to be BT.709 limited range (the normal HD editor export) the tags can be written without re-encoding: ffmpeg -i in.mp4 -c copy -bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0 out.mp4 (H.264 only; hevc_metadata is the HEVC counterpart, not tested here). Do not run the scale recipe in references/pitfalls.md on an untagged mp4: ffmpeg reads untagged input as BT.601 and shifts the colours; that recipe is for image sequences. Re-run and check the tags.
2. **pix_fmt** (FAIL, from qa_probe.py)
   - Measured: yuv444p
   - Expected: yuv420p
   - Likely causes: Image sequence encoded without a pixel format, 10-bit or 4:2:2 preset used for web or phone delivery, or a full-range export checked against a yuv420p spec.
   - What to do: Export again with the required pixel format (ffmpeg: -pix_fmt yuv420p). If the actual value starts with yuvj: to deliver full range, write yuvj420p with color.range pc in the spec; to get yuv420p, convert the range, for example -vf scale=in_range=pc:out_range=tv,format=yuv420p,setparams=range=tv (do not rely on a plain -pix_fmt yuv420p: on a full-range source ffmpeg 7.1, 8.0.1, 8.1.1 and 9.0.1 keep it yuvj420p, range pc, while ffmpeg 4.4.2, 5.1 and 6.1.1 rescale it to yuv420p, range tv, by themselves).
3. **not_silent** (FAIL, from qa_audio.py)
   - Measured: mean_volume_db=-91.0, integrated_lufs=-70.0
   - Expected: mean volume above -90 dB and integrated loudness above -70 LUFS
   - Detail: audio stream present but silent: mean volume -91.0 dB is digital silence
   - Likely causes: Audio muted or disabled, a silent placeholder track, the wrong source exported, an audio render that failed. For the WARN: sound only in the LFE channel.
   - What to do: Play the file, then re-export with the audio enabled. A stream that exists is not proof of sound. If the sound is only in the LFE channel, ignore the loudness numbers: BS.1770 does not count that channel.
4. **expect** (FAIL, from qa_frames.py)
   - Measured: 0-29,35-95 (91 frames)
   - Expected: 0-29,36-95 (90 frames)
   - Detail: decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s)
   - Likely causes: The edit is not what you meant (off by one at an in or out point, wrong order), or the exporter changed the frames.
   - What to do: Read the runs (listed under Barcode runs in report.md, or qa_frames.py --show-runs) to see what the export really contains, then fix the edit or the expectation.

## What was checked

| Item | Value |
| --- | --- |
| File | input.mp4 |
| Size | 214,087 bytes |
| Modified (UTC) | 2026-10-12T16:10:11Z |
| SHA-256 of the file | 86b9dce87e21... |
| Spec | spec_barcode_run.json (sha256 d86de81a8019...) |
| Parts run | qa_probe.py, qa_audio.py, qa_frames.py (expect 0-29,36-95) |
| Report made (UTC) | 2026-10-12T16:10:11Z |
| Tool | run_qa.py 1.2.0 on Python 3.14.6 |
| ffmpeg | ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers |
| ffprobe | ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c) 2007-2026 the FFmpeg developers |

## Summary

| PASS | WARN | FAIL | SKIP |
| --- | --- | --- | --- |
| 16 | 0 | 4 | 3 |

## Checks

| Status | Check | From | Expected | Measured | Detail |
| --- | --- | --- | --- | --- | --- |
| PASS | `video_stream` | qa_probe.py | at least one video stream | 1 | h264 1920x1080 |
| PASS | `cfr` | qa_probe.py | constant frame rate | min 0.0417 s, max 0.0417 s | 90 frame intervals, median 0.0417 s, one tick = 0.000081 s |
| PASS | `frame_count_consistent` | qa_probe.py | nb_frames=91, duration*fps=91 | 91 | decoded 91; references: nb_frames 91, duration*fps 91 |
| FAIL | `color_tags` | qa_probe.py | space=bt709, transfer=bt709, primaries=bt709, range=tv | space=unknown, transfer=unknown, primaries=unknown, range=unknown | space is unknown, spec wants bt709; transfer is unknown, spec wants bt709; primaries is unknown, spec wants bt709; range is unknown, spec wants tv |
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
| PASS | `all_frames_decodable` | qa_frames.py | 0 undecodable frames | 0 of 91 undecodable | every frame decoded (lowest block margin 126.5 of a possible 127) |
| PASS | `low_block_margin` | qa_frames.py | lowest block margin at least 40 of a possible 127 | 126.5 | every barcode block is clearly black or white |
| SKIP | `no_dropped_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `no_duplicate_frames` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| SKIP | `monotonic` | qa_frames.py | - | - | --expect given: the declared edit is judged by the expect check; facts are in info |
| FAIL | `expect` | qa_frames.py | 0-29,36-95 (90 frames) | 0-29,35-95 (91 frames) | decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s) |
| PASS | `av_duration_match` | run_qa.py | sound and picture within 0.1 s of each other | picture 3.792 s, sound 3.776 s | the sound is 0.016 s shorter than the picture |

Long values are shortened in this table. The complete text of every FAIL, WARN and NOT MEASURED row is repeated under Next steps (above), and report.json (with --out-dir) keeps everything whole.

## Barcode runs

Which source frame sits on which output frame, read from the barcode on every frame (output frame numbers and source frame numbers both start at 0):

```
  output 0-29 -> source 0-29  (30 frames)
  output 30-90 -> source 35-95  (61 frames)
```

## How this report was made

Commands that were run (tool and file paths shortened; the exact argument lists are in report.json):

- `ffprobe -v error -protocol_whitelist file -show_format -show_streams -of json input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams 0 -count_frames -show_entries stream=nb_read_frames:frame=pts,best_effort_timestamp -of json=compact=1 input.mp4`
- `ffprobe -v error -protocol_whitelist file -select_streams a -show_entries stream=index,codec_name,sample_rate,channels -of json input.mp4`
- `ffmpeg -hide_banner -nostdin -nostats -protocol_whitelist file -i input.mp4 -map 0:a:0 -af asetpts=PTS-STARTPTS,ebur128=peak=true:framelog=verbose,volumedetect,silencedetect=noise=-50dB:d=0.5 -f null -`
- `ffmpeg -hide_banner -nostdin -loglevel error -noautorotate -protocol_whitelist file -i input.mp4 -map 0:v:0 -an -sn -dn -vf "crop=w=iw*1.000000:h=ih*0.083300:x=iw*0.000000:y=ih*0.000000,scale=208:4:flags=area,format=gray" -fps_mode passthrough -f rawvideo -`
```

## What the agent tells the user

**Verdict: FAIL.** 4 of 23 checks failed, 0 warned, 0 NOT MEASURED, 3 skipped by design (the declared edit is judged by `expect`). The export does not match the spec or the edit you described. (Frame numbers count from 0, as the barcode does. The spec has no length key and you gave no "about N s", so no tolerance was chosen.)

| Check | Status | Measured | Expected |
| --- | --- | --- | --- |
| `color_tags` | FAIL | space=unknown, transfer=unknown, primaries=unknown, range=unknown | space=bt709, transfer=bt709, primaries=bt709, range=tv |
| `pix_fmt` | FAIL | yuv444p | yuv420p |
| `not_silent` | FAIL | mean_volume_db=-91.0, integrated_lufs=-70.0 | mean volume above -90 dB and integrated loudness above -70 LUFS |
| `expect` | FAIL | 0-29,35-95 (91 frames) | 0-29,36-95 (90 frames) |

Next steps:

1. `color_tags`: all four tags are unknown, the spec wants bt709 for space, transfer and primaries and tv for range. Likely cause: the export preset writes no colour metadata.
   Do: Export again from the editor with Rec.709 colour tags switched on. (The report also gives an ffmpeg command that writes the tags without re-encoding, for the case where the pixels are known to be BT.709 limited range.) Re-run and check the tags.
2. `pix_fmt`: measured yuv444p, expected yuv420p. Likely cause: an image-sequence encode or a preset that kept full 4:4:4 colour.
   Do: Export again with the required pixel format (ffmpeg: -pix_fmt yuv420p).
3. `not_silent`: audio stream present but silent: mean volume -91.0 dB is digital silence. Likely cause: the audio track is muted or a silent placeholder.
   Do: Play the file, then re-export with the audio enabled. A stream that exists is not proof of sound.
4. `expect`: decoded 91 frames, expected 90; first mismatch at output frame 30: expected 36, got 35; 61 mismatching output frame(s).
   Do: Read the runs (listed under Barcode runs in report.md, or qa_frames.py --show-runs) to see what the export really contains, then fix the edit or the expectation.

Notes:

- Reading the `expect` row: the export holds source frames `0-29,35-95`, so the cut removed frames 30 to 34 (five frames) and frame 35 is still there. The out point was treated as exclusive. The edit you described is `0-29,36-95`. Size, frame rate, codec and channel count all matched the spec; the problems are the cut, the colour tags, the pixel format and an audio track that is digital silence.
- Skipped by design (SKIP, so no claim is made): `no_dropped_frames`, `no_duplicate_frames`, `monotonic`. They were skipped because the frame expectation was declared with --barcode-expect, which makes `expect` the verdict for dropped, repeated and re-ordered frames. Nothing is NOT MEASURED.
- Loudness (`lufs_target`, `true_peak_max_db`, `no_clipping`) was not part of this run: the barcode clip's tone measures about -20.7 LUFS, so it cannot meet the -23 LUFS target whatever your edit did, and those four keys are left out of `spec_barcode_run.json`. Check loudness on the real deliverable with the full `spec_h264_1080p24.json` in a second `run_qa.py` call.

Full report: `qa_out/report.md` and `qa_out/report.json`. `report.json` holds full local paths, so look through it before sharing it. Your export was not modified.
