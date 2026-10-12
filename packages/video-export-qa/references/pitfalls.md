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
- ffprobe has no real frame rate for a GIF: for a healthy 24 fps GIF whose delays are 4 and 5 centiseconds it reported an average frame rate of 25/1 (ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1) or 24000/1001 (4.4.2, 5.1 and 5.1.9). So `qa_probe.py` does not use frame rate for a GIF: `video.fps` is NOT MEASURED for a GIF unless the spec also gives `gif.fps` (then `run_qa.py` judges the speed with `qa_gif.py`), and the frame count is compared with the frame count tag only. Use `qa_gif.py` (or `gif.fps` in `run_qa.py`) for speed.

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

Consequences: `max_av_start_offset_s` defaults to 0.05 s when set to true, which tolerates priming and is still under two frames at 24 fps (0.083 s). `duration_s` compares the container duration, so a correct 4.000 s picture with AAC audio in mkv or mpegts reads 4.021 s and needs `duration_tol_s` of 0.05; the default 0.02 s suits mp4 and mov written with an edit list. The report detail names the audio padding for mpegts, where the video stream length is stored. For mkv the detail is bare, because Matroska stores no stream durations (the cause is in the `duration_s` Next steps, which suggest 0.05). The mp4 and mov rows above hold only for files written with an edit list, which is ffmpeg's default. Measured with the same 4.000 s picture at 24 fps (libx264, ffmpeg's aac): an mp4 written with `-use_editlist 0` and no B-frames reads 4.021 s, and 0.05 passes it; the same with B-frames reads 4.083 s, because the picture then starts 0.083 s late and nothing hides it; a fragmented mp4 (`-movflags +frag_keyframe+empty_moov`) also reads 4.083 s. In the last two cases 0.05 does not pass it, and the report detail says the audio runs 0.083 s past the picture. 0.083 s is not a ceiling: a 4.000 s mp4 written without an edit list, and a fragmented one, whose audio had been re-encoded from AAC (`ffmpeg -i src.mp4 -c:v copy -c:a aac -use_editlist 0 out.mp4`) read 4.094 s, and the detail says the audio runs 0.094 s past the picture. Audio deliberately delayed by 0.5 s started at 0.478 s (the delay minus the priming).

## Untagged colour from image sequences (measured)

Encoding an image sequence with no colour options:

```
ffmpeg -framerate 24 -i frames/f%04d.png -c:v libx264 -crf 18 plain.mp4
```

gives, measured for PNG files that carry no colour information: profile High 4:4:4 Predictive, pixel format yuv444p, and color_range, color_space, color_transfer and color_primaries all unknown. (PNG files that ffmpeg 8.1.1 itself wrote from a BT.709 tagged video carry colour chunks. Encoding those the same way kept transfer, primaries and range as tagged, measured as bt709, bt709 and tv, but the matrix stayed unknown and the format stayed yuv444p. So what you get depends on where the PNG files came from; run `qa_probe.py` on your result instead of assuming.) Two separate problems:

1. Nothing is tagged, so players guess. For HD they usually guess BT.709.
2. The pixel data was converted with the BT.601 matrix. A pure red pixel (255, 0, 0) is stored as Y, U, V = 81, 90, 240 (measured, exactly what BT.601 gives), but a player that assumes BT.709 expects 63, 102, 240. Colours shift. For this PNG-to-H.264 case, tagging alone would not repair it. (ffmpeg's lavfi "red" source is not pure red: it decodes to (253, 0, 0) and stores 81, 90, 239.)

The yuv444p result also cannot be played by many phones, TVs and browsers. The fix converts with the BT.709 matrix, limits the range, picks yuv420p and writes the tags:

```
ffmpeg -framerate 24 -i frames/f%04d.png -vf "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" -c:v libx264 -crf 18 fixed.mp4
```

Measured result: profile High, yuv420p, range tv, space, transfer and primaries all bt709, and the pure red pixel is stored as 63, 102, 240, exactly what BT.709 gives. `qa_probe.py` checks the tags, not how the colours look. It cannot see whether the picture content was converted correctly.

An mp4 that is already encoded and untagged is a different case. Best is to export again from the editor with Rec.709 colour tags switched on. If its pixels are known to be BT.709 limited range (the normal HD editor export), the tags can be written without touching the pixels (H.264 only; `hevc_metadata` is the HEVC counterpart, not tested here):

```
ffmpeg -i in.mp4 -c copy -bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0 out.mp4
```

Measured on a 64x64 clip of pure red that was stored with BT.709 values (63, 102, 240) and no tags: after this command `qa_probe.py` reports all four tags and the stored pixel is still 63, 102, 240. Do not run the scale recipe above on such a file. ffmpeg reads untagged input as BT.601, so the same pixel came out as 58, 105, 229 (measured): that recipe is for image sequences. If the matrix of an untagged file is really BT.601, add `in_color_matrix=bt601` to the scale filter (measured: a BT.601 red stored as 81, 90, 240 came out as 62, 102, 239, within one level of the BT.709 values).

## Full-range 4:2:0 is called yuvj420p (H.264, HEVC and MJPEG)

For H.264, HEVC and MJPEG, ffprobe does not call a full-range (`pc`, 0 to 255) 8-bit 4:2:0 file `yuv420p`; it calls it `yuvj420p` (likewise `yuvj422p` and `yuvj444p`). A spec that asks for `video.pix_fmt` `yuv420p` therefore fails a full-range file on `pix_fmt` while `color_tags` passes. Measured: an encode with `-pix_fmt yuv420p` and range `pc` reads `yuvj420p`. To deliver full range, write `yuvj420p` with `color.range` `pc`. To get `yuv420p`, convert the range: `-vf scale=in_range=pc:out_range=tv,format=yuv420p,setparams=range=tv` gave `yuv420p`, range `tv` (measured on ffmpeg 4.4.2, 5.1, 5.1.9, 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1). Do not rely on a plain `-pix_fmt yuv420p` for this, because the builds differ: on a full-range source it left the file `yuvj420p`, range `pc`, with the pixel values unchanged on ffmpeg 7.1, 8.0.1, 8.1.1 and 9.0.1, and it rescaled the file to `yuv420p`, range `tv`, by itself on 4.4.2, 5.1, 5.1.9 and 6.1.1 (measured on a ramp: luma 16 to 235 became 30 to 218 on 4.4.2, 5.1.9 and 6.1.1). `qa_probe.py` does not treat the two names as equal, because a limited-range spec should still fail a full-range file. VP9 and AV1 are different. Measured on ffmpeg 8.1.1 with the same full-range source: libx264, libx265 and mjpeg read `yuvj420p` with range `pc`, while libvpx-vp9 (webm) and libsvtav1 (mp4) read `yuv420p` with range `pc`, so they carry the range only in `color_range`. A spec for such a file writes `yuv420p` with `color.range` `pc` (the other way round it fails on `pix_fmt`). When unsure, run `qa_probe.py` on a sample file and copy the `pix_fmt` it prints; the VP9 and AV1 names were measured on ffmpeg 8.1.1 only.

## Silent exports

An export can contain a normal audio stream that is silent. ffprobe shows an aac stream with the right sample rate and channels, so "audio is present" passes while nothing can be heard.

Measured on a 3 s file of digital silence encoded to aac: `volumedetect` prints `mean_volume: -91.0 dB` (16-bit silence, not -inf) and `ebur128` prints `I: -70.0 LUFS` and `Peak: -inf dBFS`. `qa_audio.py` FAILs a stream as silent when its mean volume is -90 dB or lower, or when the audio is at least 0.4 s long, its integrated loudness is at the -70 LUFS floor and its mean volume is -60 dB or lower. If the loudness is at the floor but the mean volume is above -60 dB it is a WARN instead, because the audio holds sound that BS.1770 loudness does not count (LFE-only or sub-audible content; measured: a 4 s 0.2 Hz sine reads -70.0 LUFS at a mean volume of -20.8 dB). The 0.4 s condition exists because a real 0.3 s tone also reads -70.0 LUFS (loudness needs at least one 400 ms block). A stream that decodes to zero samples is SKIP and NOT MEASURED, never PASS. The same rule is written in references/interpreting-results.md (`not_silent`).

By hand:

```
ffmpeg -i export.mp4 -af volumedetect,ebur128=peak=true -f null -
```

## What the start-offset check cannot see (measured)

`max_av_start_offset_s` compares the start times the container records for the picture and the sound. Measured: an mp4 whose audio was delayed by 400 ms with `adelay` (about ten frames at 24 fps, same total length) still has a start time of 0.000000 for both streams, so the check reads 0.0 and passes, and `av_duration_match` and a `max_leading_silence_s` limit of 0.5 s pass too (silence shorter than 0.5 s is not seen; leading silence read 0.0). Lip-sync is not measured. Check it with a clap or flash test: a visible flash and an audible click on the same frame, then look at the export.

## Loudness and peak are read at one decimal

`lufs_target`, `lufs_tol` and `true_peak_max_db` are compared with the numbers in ffmpeg's `ebur128` summary, which prints one decimal. Measured: a 997 Hz tone with a sample peak of -0.966 dBFS printed `Peak: -1.0 dBFS` and passed a `true_peak_max_db` of -1.0. A value within 0.05 of a limit can pass, so ask for margin on a hard ceiling. The same rounding sets the practical cutoff of `no_clipping` at about -0.15 dBTP.

## no_clipping is a ceiling, not a clip detector (measured)

`no_clipping` fails when the true peak is at or above -0.1 dBTP. A 1 kHz sine at exactly 0 dBFS that was never clipped fails it (true peak -0.0 dBTP, ffmpeg astats Flat factor 0.0); the same sine hard clipped to half scale (flat tops, Flat factor about 24, true peak -5.8 dBTP) passes. Both files were made with ffmpeg 8.1.1: `ffmpeg -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=2" -af volume=8 -c:a pcm_f32le full.wav` and `ffmpeg -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=2" -af "aeval=clip(val(0)*8\,-0.5\,0.5):c=same" -c:a pcm_f32le clipped_half.wav` (the backslashes escape the commas for ffmpeg). Audio that was clipped earlier in the chain and then lowered is not found. Use `true_peak_max_db` for a ceiling of your own choice.

## Raw streams carry no timestamps (measured)

A bare elementary stream has no container. `.h264` and `.hevc` files give the frames no timestamps at all; `.m4v` (MPEG-4), `.m2v` (MPEG-2), `.mjpeg` and `.obu` (AV1) files get evenly spaced timestamps that ffprobe makes up (from a frame rate it assumes or reads from a header inside the stream), and they are not in the file. The frame rate that ffprobe prints for a raw stream is not a measurement either, and which rate it prints depends on the ffmpeg build. Measured on a true 24 fps, 48 frame clip written raw (for example `ffmpeg -i clip.mp4 -an -c:v copy -f h264 raw.h264`): ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1 reported an average frame rate of 25/1 for every format they could write (an assumed rate), while ffmpeg 4.4.2, 5.1 and 5.1.9 reported 24/1 for .h264, .hevc and .m2v (the rate the encoder stored in the stream: a clip written at 30 fps read 30/1), 0/0 (no average rate) for .m4v, and for .mjpeg 25/1 on 5.1 and 5.1.9 and 0/0 on 4.4.2 (.obu: 25/1 on 5.1.9; 4.4.2 and the 5.1 build cannot write it). Without a rule for this (version 1.1 had none), the answer to a spec depended on the build and the format. With a spec of 24 fps, 1.1 FAILed a correct .h264, .hevc, .m4v, .m2v, .mjpeg or .obu file on ffmpeg 6.1.1 and newer, and a spec of 25 fps PASSed a rate that nobody measured; on 4.4.2, 5.1 and 5.1.9 it PASSed a .h264, .hevc or .m2v file by reading the stored rate, and a .m4v file through ffprobe's r_frame_rate (24/1) because its average rate was 0/0, which nobody measured either, and it FAILed .mjpeg (and .obu on 5.1.9). `run_qa.py` therefore reports `cfr`, `fps` (when the spec asks for it) and `frame_count_consistent` as NOT MEASURED for every raw stream, and the overall result is at best WARN. An Apple-style `.m4v` is an MP4 container with a name that looks alike: ffprobe names its format `mov,mp4,m4a,3gp,3g2,mj2` and it is measured normally; only a raw MPEG-4 elementary stream (ffprobe format `m4v`) is NOT MEASURED. A one-frame video is different: its timestamp exists in a container, and its constant-frame-rate check is a plain SKIP because one frame has no rate.

## File length is not speech length

A file that is longer or shorter than expected may differ only in silence at the start or end. `qa_audio.py` reports leading silence, trailing silence and the longest gap inside the audio separately (silence means below -50 dB for at least 0.5 s; shorter gaps are not seen). Look at those numbers before calling a duration difference an audio sync problem, and use `max_av_start_offset_s` for real start offsets. A duration mismatch can be entirely trailing silence, or one AAC frame of padding.

## Frame counts that are silently changed

Some exporters, loaders and video model pipelines accept only certain frame counts (for example a multiple of four plus one) and pad or truncate the rest. That behaviour was reported from real pipelines and is not reproduced by this skill's tests; the tests only prove that a changed count is detected (a clip with six frames removed is reported as 90 frames, not 96). The file still plays. Only counting decoded frames and reading each frame's own index shows it. `qa_probe.py` counts by decoding (`frames`, `frame_count_consistent`), `qa_frames.py` identifies every frame.

## Truncated Matroska and WebM files (measured)

Matroska and WebM files give ffprobe no frame count and no stream duration, so a check that worked out the expected frame count from the decoded frames themselves would agree with itself and pass a truncated file. `frame_count_consistent` therefore compares the decoded count only with a length the file recorded: the frame count tag, the stream duration, the per-stream DURATION tag or the container duration, each times the frame rate. Measured: a 96 frame Matroska file with audio, cut to 60 percent of its bytes, still decodes 58 frames while its DURATION tag still says 4.021 s, and the check FAILs (58 decoded against 97 expected). A file with audio gets 0.07 s of slack because its container can run one audio frame past the picture (the same file uncut reads 96 decoded against 97 expected and passes). A file that records no length at all is SKIP and NOT MEASURED, so the overall result is at best WARN, never PASS: a raw H.264 stream, and a Matroska file that was written to a pipe (measured: ffmpeg wrote one to a pipe, ffprobe gave it no duration, and the copy cut to 60 percent of its bytes still decoded 57 of 96 frames). That truncated file is reported as NOT MEASURED, not caught; only a length recorded in the file lets the check fail it.

## What survives and what breaks the barcode (measured)

The barcode is black and white blocks in the top strip of the frame, so it does not depend on the colour matrix. All 96 frames decoded after each of these: H.264 at quality settings up to crf 51, rescaling from 208x116 up to 3840x2160 and back, mpeg4 at several qualities, mkv remux, ProRes 422 HQ and 4444, full range, brightness plus or minus 0.2 and contrast 0.5. The lowest block margin was about 60 of a possible 127 for the brightness and contrast changes and 77.8 at crf 51 (a margin near 0 would mean a misread is close).

What breaks it, by design: a horizontal flip (every frame reports a marker mismatch), a letterbox bar or crop that moves the strip, and an overlay or logo over the strip. For a bar on top, give the new strip position as fractions of the frame: `--barcode-region x0,y0,x1,y1` with `run_qa.py` (or the spec key `barcode.region`), `--region x0,y0,x1,y1` with `qa_frames.py`; `run_qa.py` has no `--region`. With `bar_px` rows of padding above a picture of height `clip_h` and `total_h = bar_px + clip_h`: `x0 = 0`, `x1 = 1`, `y0 = bar_px / total_h`, `y1 = (bar_px + 0.0833 * clip_h) / total_h`. Measured, 96 of 96 frames read each time: a 60 px bar over a 1920x1080 clip (total 1140 rows) gives `0,0.0526,1,0.1316`; a 60 px bar over a 640x360 clip (total 420 rows) gives `0,0.1429,1,0.2143`. If the scaler blurs the edge of the strip, stay a pixel or two inside it.

Size: 208x48 is the smallest frame `make_test_clip.py` will write. Exports scaled down from a 640x360 clip (96 frames, ffmpeg's default scaler, libx264 at crf 14, 18 and 23, 4:2:0) were still read correctly (96 of 96) at 160x90, 104x58, 78x44 and 52x30, with block margins from 94.5 to 123.1 (the margin falls a little as the crf rises). At 34x20 all 96 frames still read, but a barcode block is then about one pixel wide and the margin was 21.0 to 31.2, so `low_block_margin` warns. At 30x18 in 4:2:0 the strip (8.33 percent of the height) is thinner than one pixel row and ffmpeg refuses to crop it, so `qa_frames.py` stops with exit code 2 and a hint. 4:4:4 frames can be cropped lower: a 26x16 frame in 4:4:4 read 96 of 96 with a margin of 50.0 at crf 14 and 47.5 at crf 18, and 35.8 at crf 23, which is a warning. These margins depend on the scaler and the bitrate.

Blending is the dangerous case. Frame blending, optical flow and motion smoothing mix neighbouring frames, so their barcodes overlap. What the overlap decodes to depends on the program: ffmpeg's `tblend` behaved like a bitwise AND, the third editor's blend like a bitwise OR (output frames read 3 from frames 1 and 2, 7 from 3 and 4, 127 from 63 and 64), and Blender and Resolve average the two frames, which washes the strip out. The 4-bit checksum often survives AND or OR, so a blended frame can decode to a valid but wrong frame number, or to one of the two neighbours, instead of being undecodable. Measured: `tblend=all_mode=average` left 37 of 95 frames decodable (some reading a number that belonged to neither source frame), `minterpolate=mi_mode=blend` 132 of 189, and a plain `framerate=fps=30` conversion decoded all 120 frames. The block margin often gives them away: those exports measured 1.3, 1.0 and 22.9, and half-speed blends in Blender and Resolve 2.2 and 2.1, while every clean transform measured 50.9 or more (crf 51: 77.8, 20 kb/s: 50.9, brightness minus 0.2: 60.1, contrast 0.5: 59.9). But not always: the third editor's half-speed blend measured 58.2, above the warning level, while 58 of 192 frames were unreadable. So do not rely on the margin alone: read `all_frames_decodable` and the declared-edit check (`--barcode-expect`) first, and test retimes with blending off. `qa_frames.py` warns (`low_block_margin`) below 40 and adds a caution to the dropped, duplicate and expect findings. Damage to part of the strip can also produce a valid wrong number; the overall result is still FAIL because other frames become unreadable, but the dropped and duplicate details may describe frames that did not exist.

Rotation metadata: ffmpeg normally turns a frame by its rotation flag before filtering, which would move the strip to the side. `qa_frames.py` reads frames as stored (`-noautorotate`), so a file flagged as rotated still reads 96 of 96. Not tested: non-square pixels, extreme level shifts.

## Old ffmpeg, colour codes, log files and the current folder (measured 2026-10-10)

- **Loudness on ffmpeg 4.4 and 5.1.** Version 1.0 asked the `ebur128` filter for `framelog=quiet`, which exists only from ffmpeg 6.0. On the builds that Ubuntu 22.04 (4.4.2) and Debian 12 (5.1.x) ship, the whole audio measurement failed with "Conversion failed!" and every audio check was SKIP (the test suite showed this on both). With an ffmpeg 5.1 build on Windows, version 1.0 then reported an overall PASS with exit code 0 for a silent export; that end-to-end result was reproduced on that build only. Version 1.1 asks for `framelog=verbose`, which those builds accept, and a requested audio check whose measurement fails now makes the overall result a WARN (NOT MEASURED), never a PASS.
- **Coloured log text.** If the environment sets `AV_LOG_FORCE_COLOR`, ffmpeg writes terminal colour codes into its messages, and the measurement lines no longer matched the patterns that read them (measured on ffmpeg 8.1.1: no loudness was read). The codes are now removed from ffmpeg's messages, in the measurement commands and in the barcode decoder, before the text is read.
- **Log files.** `FFREPORT` makes every ffmpeg and ffprobe run write a log file into the current folder. The scripts read no environment variables except PATH (to find ffmpeg and ffprobe) and change none, and ffmpeg and ffprobe inherit your environment, so unset `FFREPORT` for a clean run.
- **The current folder is never searched for ffmpeg or ffprobe.** On Windows a program in the current folder is found before one on PATH, so a delivery folder that holds its own `ffmpeg.exe` could replace the real tool. Version 1.1 looks only in the absolute folders of PATH, and skips the current folder even when it is listed there, so running from FFmpeg's own folder finds nothing (the message says so: run from the folder that holds the export). Pass `--ffmpeg PATH` to use a tool that sits next to the export on purpose.
