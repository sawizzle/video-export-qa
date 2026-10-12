# Delivery specs: what to ask for and why

A delivery spec is a short JSON file that states what the export must be. Put only the keys the recipient really requires in it. A key that is not in the spec adds no check beyond the always-on ones (video stream, constant frame rate, frame count consistency, colour tags, not silent, and in `run_qa.py` the sound-versus-picture length match), and the scripts never invent a numeric target such as a codec, size or loudness. Save the file as UTF-8; a byte order mark is accepted, and so is UTF-16 with a byte order mark (what Windows PowerShell can write). Three starting points are in `examples/`: the full delivery spec, the same spec without the loudness keys for a barcode run, and a GIF spec.

## Turning a delivery document into a spec

| The document says | Spec key | Note |
| --- | --- | --- |
| H.264, ProRes, and so on | `video.codec` | ffprobe names: `h264`, `hevc`, `prores`, `mjpeg`, `gif`. |
| 8-bit 4:2:0 | `video.pix_fmt` | `yuv420p`. 10-bit 4:2:2 is `yuv422p10le`. For H.264, HEVC and MJPEG, ffprobe names full-range 8-bit 4:2:0 `yuvj420p`; VP9 and AV1 keep `yuv420p` (see Tagged colour). Run `qa_probe.py` on a sample and copy the `pix_fmt` it prints. |
| 1920x1080 | `video.width`, `video.height` | Whole numbers. |
| 24, 25, 29.97, 23.976 fps | `video.fps` | Write 23.976 as "24000/1001" and 29.97 as "30000/1001"; a fraction must match exactly or within 0.01 percent (containers may round the rate), which still tells 24 from 23.976 apart. A GIF has no frame rate: use `gif.fps` (below). A raw elementary stream (a bare .h264, .hevc, .m4v, .m2v, .mjpeg or .obu file) has no timestamps of its own, so its rate is not measured; an Apple-style .m4v is an MP4 container and is measured normally, only a raw MPEG-4 elementary stream is not measured. |
| Constant frame rate | `video.cfr` | Checked even when absent, true is the default. Write false to allow variable rate. |
| Even dimensions | `video.even_dimensions` | true. |
| Exact length | `video.frames` and/or `duration_s` | `frames` counts decoded frames. `duration_s` compares the container duration within `duration_tol_s` (default 0.02 s, right for mp4 and mov written with an edit list, which is ffmpeg's default). Use `duration_tol_s` of 0.05 for mkv and mpegts files with AAC audio. An mp4 written without an edit list, and a fragmented mp4, can read more than that past the picture (measured 4.021 to 4.083 s for a 4.000 s picture, and 4.094 s when the audio had been re-encoded from AAC); when the picture alone matches, the detail names the audio overrun. |
| BT.709, limited range | `color` | `{"space": "bt709", "transfer": "bt709", "primaries": "bt709", "range": "tv"}`. Any subset can be given. HDR and full range: see Tagged colour. |
| 48 kHz stereo AAC | `audio` | `{"present": true, "sample_rate": 48000, "channels": 2, "codec": "aac"}`. Read by `qa_probe.py` and `run_qa.py`, not by `qa_audio.py`. |
| Streams start together | `max_av_start_offset_s` | Compares the container's stream start times only. true means 0.05 s; a number sets the limit. It does not prove lip-sync: audio delayed by silence written into the audio track, or any delay inside a stream, reads as 0.0. Check sync with a clap or flash test. On a file that lacks the audio or the video stream nothing is compared, so it is NOT MEASURED (on a file without audio, a spec that also says `audio.present` is false makes the limit vacuous, a plain SKIP). |
| Integrated loudness | `lufs_target`, `lufs_tol` | Default tolerance 1.0 LU. Read at one decimal (see Loudness). |
| True peak limit | `true_peak_max_db` | In dBTP, for example -1.0. Read at one decimal; for a hard ceiling leave margin. |
| True-peak ceiling | `no_clipping` | true fails when the true peak is at or above the -0.1 dBTP ceiling (clip risk; the practical cutoff is about -0.15 dBTP). It is a ceiling check, not a detector of clipped samples. For any other ceiling use `true_peak_max_db`. |
| No dead air | `max_leading_silence_s`, `max_trailing_silence_s`, `max_silence_s` | Silence means below -50 dB for at least 0.5 s. |
| GIF speed and length | `gif` | `{"fps": 15, "expect_duration_s": 3.0, "expect_frames": 45, "tol_s": 0.02}`. Read by `run_qa.py` on `.gif` files. `gif.fps` is what judges the speed: `video.fps` alone is NOT MEASURED for a GIF. |
| Which source frames, in what order | `barcode` | `{"expect": "0-29,36-95"}`, needs a barcode clip. `run_qa.py` reads it only when a barcode expectation is given (`--barcode-expect` or this key). See references/editor-recipes.md. |

A spec key that was asked for and cannot be measured is never a PASS: it is NOT MEASURED, or a FAIL where the requirement cannot be met (an audio limit on a file with no audio stream). That includes `max_av_start_offset_s` on a file that lacks the audio or the video stream: there are not two streams to compare, so it is NOT MEASURED, unless the file has no audio and the spec also says `audio.present` is false, which makes the limit vacuous (a plain SKIP). The plain SKIPs that remain are correct by design: nothing was asked for and nothing applies, a value that makes the check vacuous (`video.cfr` false), or a check judged by another row (`expect`, `rounding_drift`). There are no keys for H.264 profile and level, bit rate, field order, pixel aspect ratio, container or audio bit rate; a key the scripts do not know is a WARN (`unknown_spec_key`) and is ignored.

## Common targets and the reason for each

**Constant frame rate.** Editors, broadcast tools and many players assume every frame lasts the same time. A variable rate file drifts against its audio or plays unevenly. Phone and screen recordings are the usual source of variable rate.

**Even width and height.** 4:2:0 video stores colour at half resolution in both directions, so the picture needs an even number of pixels each way. Odd sizes make encoders refuse the job or silently crop a pixel.

**8-bit yuv420p, High profile.** The most widely decodable combination: phones, browsers, TVs, social platforms. Image sequences encoded without options often come out as yuv444p (High 4:4:4 Predictive), which many devices cannot play. 10-bit and 4:2:2 are for mastering and exchange, not for casual delivery.

**Standard frame rates.** 23.976 (24000/1001), 24, 25, 29.97 (30000/1001), 30, 50, 59.94 (60000/1001), 60. Pick the one the project was cut at. Converting between 23.976 and 24 changes the length by 0.1 percent (3.6 s per hour), see references/pitfalls.md.

**Tagged colour.** Players read four tags, not the picture: colour space (the matrix), transfer, primaries and range. These are common conventions that vary by recipient; check the delivery document before writing the `color` block. The spec is compared with the exact strings ffprobe prints. Without a `color` section an untagged field is a WARN (`color_tags`); with one, a tag that is missing or different is a FAIL.
- HD and UHD SDR (720p and above): usually BT.709 for all three (`bt709`), limited range (`tv`, 16 to 235). This is what HD players assume when tags are missing, but missing tags are still a risk, and the pixel data must have been converted with the same matrix. Some UHD SDR deliveries use BT.2020 primaries and matrix with an SDR transfer, which ffprobe shows as `bt2020-10`; the recipient's document decides.
- SD: BT.601. ffprobe names it smpte170m (525 line) or bt470bg (625 line).
- HDR (HDR10, HLG): BT.2020 primaries (ffprobe `bt2020`), the BT.2020 non-constant-luminance matrix (ffprobe prints `bt2020nc`, which HDR10 and HLG use; `bt2020c` is rare) and an HDR transfer (PQ is `smpte2084`, HLG is `arib-std-b67`). Write those exact strings: `{"space": "bt2020nc", "transfer": "smpte2084", "primaries": "bt2020", "range": "tv"}`. A spec value of `bt2020` for the matrix fails a correct HDR10 file. HDR static metadata (mastering display, MaxCLL) is not checked.
- Full range (`pc`, 0 to 255) is correct only when the recipient asks for it. For H.264, HEVC and MJPEG, ffprobe names full-range 8-bit 4:2:0 `yuvj420p` (also `yuvj422p` and `yuvj444p`), so a spec that asks for `color.range` "pc" in 8-bit 4:2:0 H.264 must write `video.pix_fmt` "yuvj420p"; a `yuv420p` spec fails a full-range H.264 file on `pix_fmt`. VP9 and AV1 files keep `yuv420p` and show the range only as `color_range` pc, so for those write `yuv420p`. When unsure, run `qa_probe.py` on a sample file and copy the `pix_fmt` it prints. (Measured on ffmpeg 8.1.1 with libx264, libx265, mjpeg, libvpx-vp9 and libsvtav1; other builds were not checked.)

**48 kHz audio.** 48 kHz is the sample rate of video and broadcast (44.1 kHz belongs to CD music). A mismatch forces a resample somewhere in the chain. Web delivery usually wants AAC-LC stereo. Masters are normally uncompressed PCM at 24 bit.

**Loudness.** These are public conventions. Check the recipient's current document before using a number.
- EBU R 128 (European broadcast): programme loudness -23.0 LUFS with a tolerance of plus or minus 0.5 LU, maximum true peak -1 dBTP. Spec: `"lufs_target": -23, "lufs_tol": 0.5, "true_peak_max_db": -1`.
- ATSC A/85 (US broadcast): -24 LKFS with plus or minus 2 dB, true peak not above -2 dBTP. LKFS and LUFS are the same unit from ITU-R BS.1770.
- Online platforms often normalise somewhere around -14 to -16 LUFS. They differ and change, so use the platform's own current guidance.
- `qa_audio.py` measures integrated loudness of the whole file as ffmpeg's ebur128 filter computes it (ITU-R BS.1770 gating). It does not isolate dialogue, so a recipient that specifies dialogue-gated loudness needs a different meter.
- Integrated loudness and true peak are read from ffmpeg's summary, which prints one decimal (0.1 LU, 0.1 dB), so a value within 0.05 of a limit can pass. For a hard ceiling ask for margin (a limit of -1.1 for a -1.0 requirement).
- The barcode test clip's tone measures about -20.7 LUFS and -19.7 dBTP (mean volume -23.0 dB; -20.0 dBTP if ffmpeg 4.4.2 made the clip, whose AAC encoder lands 0.3 dB lower), so loudness keys cannot be judged on it: run them on the real deliverable.

**GIF.** Frame delays are whole centiseconds, so only a frame rate of exactly 100 / n for a whole number n (50, 25, 20, 10 and so on) can use one delay for every frame; any other rate needs mixed delays (24 fps needs a pattern of 4 and 5 centiseconds). Delays of 0 or 1 centisecond are played as 10 by common browsers. Loop forever unless told otherwise. Keep width and height modest, because file size grows quickly. Use `gif.fps` so the drift check knows the intended speed.

## Tolerances

Defaults suit mp4 and mov written with an edit list (ffmpeg's default). `duration_tol_s` 0.02 s is below one AAC frame (0.021 s at 48 kHz), so a correct mkv or mpegts export with AAC audio reads about 0.021 s past the picture and needs `duration_tol_s` 0.05 (see the Exact length row and references/pitfalls.md); an mp4 written without an edit list, or a fragmented mp4, can read 0.083 s or more past the picture even then (measured 4.094 s, 0.094 s over, on a 4.000 s clip whose audio had been re-encoded from AAC), so read the detail before calling it a length error. The other defaults: `lufs_tol` 1.0 LU, `max_av_start_offset_s` true = 0.05 s, GIF `tol_s` 0.02 s. Tighten them when the recipient does, and say so in the report.
