# Delivery specs: what to ask for and why

A delivery spec is a short JSON file that states what the export must be. Put only the keys the recipient really requires in it: a key that is not in the spec is not checked, and the scripts never guess a requirement. Two starting points are in `examples/`.

## Turning a delivery document into a spec

| The document says | Spec key | Note |
| --- | --- | --- |
| H.264, ProRes, and so on | `video.codec` | ffprobe names: `h264`, `hevc`, `prores`, `mjpeg`, `gif`. |
| 8-bit 4:2:0 | `video.pix_fmt` | `yuv420p`. 10-bit 4:2:2 is `yuv422p10le`. |
| 1920x1080 | `video.width`, `video.height` | Whole numbers. |
| 24, 25, 29.97, 23.976 fps | `video.fps` | Write 23.976 as "24000/1001" and 29.97 as "30000/1001"; a fraction must match exactly or within 0.01 percent (containers may round the rate), which still tells 24 from 23.976 apart. |
| Constant frame rate | `video.cfr` | Checked even when absent, true is the default. Write false to allow variable rate. |
| Even dimensions | `video.even_dimensions` | true. |
| Exact length | `video.frames` and/or `duration_s` | `frames` counts decoded frames. Use `duration_tol_s` of 0.05 for mkv and mpegts files with audio. |
| BT.709, limited range | `color` | `{"space": "bt709", "transfer": "bt709", "primaries": "bt709", "range": "tv"}`. Any subset can be given. |
| 48 kHz stereo AAC | `audio` | `{"present": true, "sample_rate": 48000, "channels": 2, "codec": "aac"}`. |
| Audio in sync | `max_av_start_offset_s` | true means 0.05 s. A number sets the limit. |
| Integrated loudness | `lufs_target`, `lufs_tol` | Default tolerance 1.0 LU. |
| True peak limit | `true_peak_max_db` | In dBTP, for example -1.0. |
| No clipping | `no_clipping` | true fails when the true peak reaches -0.1 dBTP. |
| No dead air | `max_leading_silence_s`, `max_trailing_silence_s`, `max_silence_s` | Silence means below -50 dB for at least 0.5 s. |
| GIF speed and length | `gif` | `{"fps": 15, "expect_duration_s": 3.0, "expect_frames": 45, "tol_s": 0.02}`. |
| Which source frames, in what order | `barcode` | `{"expect": "0-29,36-95"}`, needs a barcode clip. See references/editor-recipes.md. |

## Common targets and the reason for each

**Constant frame rate.** Editors, broadcast tools and many players assume every frame lasts the same time. A variable rate file drifts against its audio or plays unevenly. Phone and screen recordings are the usual source of variable rate.

**Even width and height.** 4:2:0 video stores colour at half resolution in both directions, so the picture needs an even number of pixels each way. Odd sizes make encoders refuse the job or silently crop a pixel.

**8-bit yuv420p, High profile.** The most widely decodable combination: phones, browsers, TVs, social platforms. Image sequences encoded without options often come out as yuv444p (High 4:4:4 Predictive), which many devices cannot play. 10-bit and 4:2:2 are for mastering and exchange, not for casual delivery.

**Standard frame rates.** 23.976 (24000/1001), 24, 25, 29.97 (30000/1001), 30, 50, 59.94 (60000/1001), 60. Pick the one the project was cut at. Converting between 23.976 and 24 changes the length by 0.1 percent (3.6 s per hour), see references/pitfalls.md.

**Tagged colour.** Players read four tags, not the picture: colour space (the matrix), transfer, primaries and range.
- HD (720p and above): BT.709 for all three, limited range ("tv", 16 to 235). This is what HD players assume when tags are missing, but missing tags are still a risk, and the pixel data must have been converted with the same matrix.
- SD: BT.601. ffprobe names it smpte170m (525 line) or bt470bg (625 line).
- UHD and HDR: BT.2020 primaries and matrix with an HDR transfer (PQ is smpte2084, HLG is arib-std-b67). The scripts only compare the tag values you write in the spec.
- Full range ("pc", 0 to 255) is correct only when the recipient asks for it.

**48 kHz audio.** 48 kHz is the sample rate of video and broadcast (44.1 kHz belongs to CD music). A mismatch forces a resample somewhere in the chain. Web delivery usually wants AAC-LC stereo. Masters are normally uncompressed PCM at 24 bit.

**Loudness.** These are public conventions. Check the recipient's current document before using a number.
- EBU R 128 (European broadcast): programme loudness -23.0 LUFS with a tolerance of plus or minus 0.5 LU, maximum true peak -1 dBTP. Spec: `"lufs_target": -23, "lufs_tol": 0.5, "true_peak_max_db": -1`.
- ATSC A/85 (US broadcast): -24 LKFS with plus or minus 2 dB, true peak not above -2 dBTP. LKFS and LUFS are the same unit from ITU-R BS.1770.
- Online platforms often normalise somewhere around -14 to -16 LUFS. They differ and change, so use the platform's own current guidance.
- `qa_audio.py` measures integrated loudness of the whole file as ffmpeg's ebur128 filter computes it (ITU-R BS.1770 gating). It does not isolate dialogue, so a recipient that specifies dialogue-gated loudness needs a different meter.

**GIF.** Frame delays are whole centiseconds, so only a frame rate of exactly 100 / n for a whole number n (50, 25, 20, 10 and so on) can use one delay for every frame; any other rate needs mixed delays (24 fps needs a pattern of 4 and 5 centiseconds). Delays of 0 or 1 centisecond are played as 10 by common browsers. Loop forever unless told otherwise. Keep width and height modest, because file size grows quickly. Use `gif.fps` so the drift check knows the intended speed.

## Tolerances

Defaults are chosen so a correct export passes: `duration_tol_s` 0.02 s (one AAC frame is 0.021 s, see pitfalls), `lufs_tol` 1.0 LU, `max_av_start_offset_s` true = 0.05 s, GIF `tol_s` 0.02 s. Tighten them when the recipient does, and say so in the report.
