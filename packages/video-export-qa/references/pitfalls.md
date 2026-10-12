# Pitfalls that make exports look right and be wrong

Each section says what goes wrong, what was measured, and how the scripts deal with it. "Measured" means observed with ffmpeg and ffprobe 8.1.1 (a gyan.dev full build) on Windows 11. Other builds can print slightly different numbers.

## Constant frame rate (CFR) versus variable frame rate (VFR)

A CFR file has the same gap between every pair of frames. A VFR file does not, and editors, broadcast tools and many players assume CFR, so VFR files drift out of sync or play unevenly.

- `qa_probe.py` reads the timestamps of the decoded frames, which ffprobe lists in presentation order, and sorts them anyway as a safety net. The packets of a B-frame file carry timestamps in decode order, where they are not increasing, so gaps taken in packet order would be nonsense. Measured with libx264's defaults at 24000/1001 fps: the packet timestamps in decode order are 0, 4004, 2002, 1001, 3003, 8008, 6006, 5005, 7007 (ticks of 1/24000 s), while the decoded frames read 0, 1001, 2002, 3003, 4004 and so on. The file is CFR.
- Constant means every gap is within one timebase tick of the others, or within 1 percent of the median gap.
- Measured example of VFR: a clip with every fifth frame removed (timestamps kept) has gaps of 0.0417 s and 0.0833 s, and the `cfr` check fails with exactly those numbers.

## Never seek to count or compare frames

Jumping to a time and reading "the frame there" depends on keyframe positions, edit lists and timestamp rounding, and a one-frame error looks like a pass. The scripts decode from the first frame to the last, in order, and examine every frame.

Related trap, measured: ffmpeg's default frame sync is not passthrough for raw output. On a VFR clip that holds 77 frames, a raw decode returned 96 frames by default (it invents repeats to fill the gaps) and 77 with `-fps_mode passthrough`. The scripts always pass the passthrough option (older ffmpeg: `-vsync passthrough`). If you write your own ffmpeg commands to count frames, add it too.

## Timebase rounding

Timestamps are whole numbers of ticks. mp4 usually uses a fine timebase (for example 1/24000), but Matroska uses 1/1000 s, which cannot hold 1/24 s: the gaps alternate between 41 and 42 ms for a perfectly steady 24 fps file (measured). Comparing gaps for exact equality would call that VFR, so one tick of slack is allowed.

For frame rates write exact fractions: 23.976 fps is 24000/1001 (23.976023976...). In a spec, a fraction string such as "24000/1001" must match exactly or within 0.01 percent, because the container may round the rate it reports: measured, a 59.94 fps file reads 60000/1001 in mp4 and 19001/317 (59.940063) in Matroska and WebM, which is the same rate to within 0.0001 percent. 0.01 percent is still far finer than the 0.1 percent step between 24 and 23.976, so those two are told apart. A plain number such as 23.976 is compared within 0.001 fps. `make_test_clip.py --fps 23.976` is taken to mean 24000/1001 (the decimal 23.976 would be 2997/125, a different rate).

## GIF delays are whole centiseconds

A GIF stores each frame delay as a whole number of hundredths of a second. 24 fps would need 4.1667, so something has to round.

- Uniform round-down trap: every frame written as 4 centiseconds makes 24 frames last 0.96 s instead of 1.00 s, so the GIF plays about 4 percent fast. `qa_gif.py --fps 24` fails this as `rounding_drift` (measured on a 96 frame file patched to 4 centiseconds everywhere: expected 4.00 s, actual 3.84 s).
- ffmpeg 8.1.1 does not fall into it: for 24 fps it writes 4 and 5 centisecond delays in a repeating pattern (measured: 40 frames at 4 plus 8 at 5 for 48 frames, total 2.00 s). 25 fps is exactly 4 centiseconds and is legitimately uniform.
- Delays of 0 or 1 centisecond: common browsers play those as 10 centiseconds. That is browser behaviour, not part of the GIF specification, so `effective_duration` only warns. Frame rates above 50 fps cannot be written faithfully for this reason.
- A GIF with no looping extension plays once. `loop` warns.
- ffprobe has no real frame rate for a GIF: it reported 25/1 for a healthy 24 fps GIF whose delays are 4 and 5 centiseconds. So `qa_probe.py` does not use frame rate for a GIF (the `fps` check is SKIP and the frame count is compared with the frame count tag only). Use `qa_gif.py` for speed.

## Inclusive and exclusive out points

Editors disagree on whether the out point frame is part of the clip, and some number frames from 1 while the barcode counts from 0 (timecode 00:00:00:00 is frame 0). One frame of difference is the usual result. `--expect` segments include both ends: "30-35" is six frames (30, 31, 32, 33, 34, 35). Cutting frames 30 to 35 out of a 96 frame clip is "0-29,36-95". If the export shows "0-29,35-95", the cut removed only five frames: the out point was treated as exclusive.

## 23.976 versus 24, and timecode

23.976 fps is really 24000/1001. One hour of 23.976 frames (86,400 frames) lasts 3,603.6 s, which is 3.6 s longer than an hour of 24 fps. Timecode for 23.976 is non-drop (drop frame exists only for 29.97 and 59.94), so its labels drift from the wall clock by the same 0.1 percent. Delivering "24" from a 23.976 project, or the reverse, makes picture and untouched audio drift apart unless one of them is conformed. State the frame rate as an exact fraction in the spec.

## AAC encoder priming and start times (measured)

ffmpeg's native aac encoder starts with 1024 samples of priming, 0.0213 s at 48 kHz (other AAC encoders use other amounts). Containers deal with it differently. Measured for a 4.000 s tone plus picture exported with libx264 and ffmpeg's aac:

| Container | Video start | Audio start | Container duration | Note |
| --- | --- | --- | --- | --- |
| mp4, mov | 0.000000 | 0.000000 | 4.000000 | An edit list hides the priming. |
| mkv | 0.000000 | 0.000000 | 4.021 | One AAC frame (0.021 s) past the picture. Stream durations are not stored. |
| mpegts | 1.483333 | 1.462000 | 4.021333 | Audio starts 0.021333 s earlier (1024 samples at 48 kHz). The audio stream duration reads 3.8 to 4.0 s depending on bitrate, so it is never used for a verdict. |

Consequences: `max_av_start_offset_s` defaults to 0.05 s when set to true, which tolerates priming and is still under two frames at 24 fps (0.083 s). `duration_s` compares the container duration, so a correct 4.000 s picture in mkv or mpegts reads 4.021 s and needs `duration_tol_s` of 0.05. The report detail says when that is the reason. Audio deliberately delayed by 0.5 s started at 0.478 s (the delay minus the priming).

## Untagged colour from image sequences (measured)

Encoding an image sequence with no colour options:

```
ffmpeg -framerate 24 -i frames/f%04d.png -c:v libx264 -crf 18 plain.mp4
```

gives, measured for PNG files that carry no colour information: profile High 4:4:4 Predictive, pixel format yuv444p, and color_range, color_space, color_transfer and color_primaries all unknown. (PNG files that ffmpeg 8.1.1 itself wrote from a BT.709 tagged video carry colour chunks. Encoding those the same way kept transfer, primaries and range as tagged, measured as bt709, bt709 and tv, but the matrix stayed unknown and the format stayed yuv444p. So what you get depends on where the PNG files came from; run `qa_probe.py` on your result instead of assuming.) Two separate problems:

1. Nothing is tagged, so players guess. For HD they usually guess BT.709.
2. The pixel data was converted with the BT.601 matrix. A pure red pixel (255, 0, 0) is stored as Y, U, V = 81, 90, 240 (measured, exactly what BT.601 gives), but a player that assumes BT.709 expects 63, 102, 240. Colours shift. Tagging alone would not repair this. (ffmpeg's lavfi "red" source is not pure red: it decodes to (253, 0, 0) and stores 81, 90, 239.)

The yuv444p result also cannot be played by many phones, TVs and browsers. The fix converts with the BT.709 matrix, limits the range, picks yuv420p and writes the tags:

```
ffmpeg -framerate 24 -i frames/f%04d.png \
  -vf "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" \
  -c:v libx264 -crf 18 fixed.mp4
```

Measured result: profile High, yuv420p, range tv, space, transfer and primaries all bt709, and the pure red pixel is stored as 63, 102, 240, exactly what BT.709 gives. `qa_probe.py` checks the tags, not how the colours look. It cannot see whether the picture content was converted correctly.

## Silent exports

An export can contain a normal audio stream that is silent. ffprobe shows an aac stream with the right sample rate and channels, so "audio is present" passes while nothing can be heard.

Measured on a 3 s file of digital silence encoded to aac: `volumedetect` prints `mean_volume: -91.0 dB` (16-bit silence, not -inf) and `ebur128` prints `I: -70.0 LUFS` and `Peak: -inf dBFS`. `qa_audio.py` calls a stream silent when the mean is -90 dB or lower, or when the audio is at least 0.4 s long and the integrated loudness is -70 LUFS or lower. The 0.4 s condition exists because a real 0.3 s tone also reads -70.0 LUFS (loudness needs at least one 400 ms block). A stream that decodes to zero samples is SKIP, never PASS.

By hand:

```
ffmpeg -i export.mp4 -af volumedetect,ebur128=peak=true -f null -
```

## File length is not speech length

A file that is longer or shorter than expected may differ only in silence at the start or end. `qa_audio.py` reports leading silence, trailing silence and the longest gap inside the audio separately (silence means below -50 dB for at least 0.5 s; shorter gaps are not seen). Look at those numbers before calling a duration difference an audio sync problem, and use `max_av_start_offset_s` for real start offsets. A duration mismatch can be entirely trailing silence, or one AAC frame of padding.

## Frame counts that are silently changed

Some exporters, loaders and video model pipelines accept only certain frame counts (for example a multiple of four plus one) and pad or truncate the rest. That behaviour was reported from real pipelines and is not reproduced by this skill's tests; the tests only prove that a changed count is detected (a clip with six frames removed is reported as 90 frames, not 96). The file still plays. Only counting decoded frames and reading each frame's own index shows it. `qa_probe.py` counts by decoding (`frames`, `frame_count_consistent`), `qa_frames.py` identifies every frame.

## Truncated Matroska and WebM files (measured)

Matroska and WebM files give ffprobe no frame count and no stream duration, so a check that worked out the expected frame count from the decoded frames themselves would agree with itself and pass a truncated file. `frame_count_consistent` therefore compares the decoded count only with a length the file recorded: the frame count tag, the stream duration, the per-stream DURATION tag or the container duration, each times the frame rate. Measured: a 96 frame Matroska file with audio, cut to 60 percent of its bytes, still decodes 58 frames while its DURATION tag still says 4.021 s, and the check FAILs (58 decoded against 97 expected). A file with audio gets 0.07 s of slack because its container can run one audio frame past the picture (the same file uncut reads 96 decoded against 97 expected and passes). A file that records no length at all (a raw H.264 stream) is SKIP, not PASS.

## What survives and what breaks the barcode (measured)

The barcode is black and white blocks in the top strip of the frame, so it does not depend on the colour matrix. All 96 frames decoded after each of these: H.264 at quality settings up to crf 51, rescaling from 208x116 up to 3840x2160 and back, mpeg4 at several qualities, mkv remux, ProRes 422 HQ and 4444, full range, brightness plus or minus 0.2 and contrast 0.5. The lowest block margin was about 60 of a possible 127 for the brightness and contrast changes and 77.8 at crf 51 (a margin near 0 would mean a misread is close).

What breaks it, by design: a horizontal flip (every frame reports a marker mismatch), a letterbox bar or crop that moves the strip (use `--region`; with a 60 px bar on top, `--region 0,0.15,1,0.21` read 96 of 96), and an overlay or logo over the strip.

Size: 208x48 is the smallest frame `make_test_clip.py` will write. Exports scaled down from a 640x360 clip were still read correctly (96 of 96) at 160x90, 104x58, 78x44 and 52x30 in 4:2:0 video, with block margins from 120.9 down to 86.8. At 34x20 all 96 frames still read, but a barcode block is then about one pixel wide and the margin is 14.6, so `low_block_margin` warns. At 30x18 the strip (8.33 percent of the height) is thinner than one pixel row and ffmpeg refuses to crop it, so `qa_frames.py` stops with exit code 2 and a hint. In 4:4:4 a 26x16 frame read 96 of 96 with a margin of 24.2 (also a warning).

Blending is the dangerous case. Frame blending, optical flow and motion smoothing mix neighbouring frames, so their barcodes overlap. What the overlap decodes to depends on the program: ffmpeg's `tblend` behaved like a bitwise AND, FilmCraft's blend like a bitwise OR (output frames read 3 from frames 1 and 2, 7 from 3 and 4, 127 from 63 and 64), and Blender and Resolve average the two frames, which washes the strip out. The 4-bit checksum often survives AND or OR, so a blended frame can decode to a valid but wrong frame number, or to one of the two neighbours, instead of being undecodable. Measured: `tblend=all_mode=average` left 37 of 95 frames decodable (some reading a number that belonged to neither source frame), `minterpolate=mi_mode=blend` 132 of 189, and a plain `framerate=fps=30` conversion decoded all 120 frames. The block margin often gives them away: those exports measured 1.3, 1.0 and 22.9, and half-speed blends in Blender and Resolve 2.2 and 2.1, while every clean transform measured 50.9 or more (crf 51: 77.8, 20 kb/s: 50.9, brightness minus 0.2: 60.1, contrast 0.5: 59.9). But not always: FilmCraft's half-speed blend measured 58.2, above the warning level, while 58 of 192 frames were unreadable. So do not rely on the margin alone: read `all_frames_decodable` and the declared-edit check (`--barcode-expect`) first, and test retimes with blending off. `qa_frames.py` warns (`low_block_margin`) below 40 and adds a caution to the dropped, duplicate and expect findings. Damage to part of the strip can also produce a valid wrong number; the overall result is still FAIL because other frames become unreadable, but the dropped and duplicate details may describe frames that did not exist.

Rotation metadata: ffmpeg normally turns a frame by its rotation flag before filtering, which would move the strip to the side. `qa_frames.py` reads frames as stored (`-noautorotate`), so a file flagged as rotated still reads 96 of 96. Not tested: non-square pixels, extreme level shifts.

## Old ffmpeg, colour codes, log files and the current folder (measured 2026-10-10)

- **Loudness on ffmpeg 4.4 and 5.1.** Version 1.0 asked the `ebur128` filter for `framelog=quiet`, which exists only from ffmpeg 6.0. On the builds that Ubuntu 22.04 (4.4.2) and Debian 12 (5.1.x) ship, the whole audio measurement failed with "Conversion failed!" and every audio check was SKIP (the test suite showed this on both). With an ffmpeg 5.1 build on Windows, version 1.0 then reported an overall PASS with exit code 0 for a silent export; that end-to-end result was reproduced on that build only. Version 1.1 asks for `framelog=verbose`, which those builds accept, and a requested check that still cannot be measured now makes the overall result a WARN (NOT MEASURED), never a PASS.
- **Coloured log text.** If the environment sets `AV_LOG_FORCE_COLOR`, ffmpeg writes terminal colour codes into its messages, and the measurement lines no longer matched the patterns that read them (measured on ffmpeg 8.1.1: no loudness was read). The codes are now removed from ffmpeg's messages, in the measurement commands and in the barcode decoder, before the text is read.
- **Log files.** `FFREPORT` makes every ffmpeg and ffprobe run write a log file into the current folder. The scripts do not read or change the environment, so unset it for a clean run.
- **The current folder is never searched for ffmpeg or ffprobe.** On Windows a program in the current folder is found before one on PATH, so a delivery folder that holds its own `ffmpeg.exe` could replace the real tool. Version 1.1 looks only in the absolute folders of PATH. Pass `--ffmpeg PATH` to use a tool that sits next to the export on purpose.
