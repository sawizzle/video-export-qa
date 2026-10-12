# video-export-qa

A free agent skill that proves what is really inside an exported video, GIF or audio file. Every result is a measurement from ffprobe or ffmpeg, a count of decoded frames, or a read of a frame-number barcode. Nothing is called a PASS unless it was measured.

- **Delivery spec:** codec, pixel format, size, frame rate, constant frame rate, frame count, duration, colour tags, audio format.
- **Audio:** integrated loudness, true peak, clipping, silence gaps, and the "audio stream exists but is silent" export.
- **Frame identity:** a test clip with a barcode on every frame, so after an editor trims, rejoins or retimes it, the skill reports which source frame sits on each output frame.
- **GIFs:** real frame delays and total duration. **Two files:** frame-by-frame comparison.

It only reads your files and writes reports to a folder you name. Standard-library Python plus ffmpeg and ffprobe, no network.

## Get it

- Copy `packages/video-export-qa/` into your agent's skills folder, or
- get it from Agensi: https://agensi.io/skills/video-export-qa

Needs `ffmpeg` and `ffprobe` on PATH and Python 3.9 or newer. The skill's own [README](packages/video-export-qa/README.md) and [SKILL.md](packages/video-export-qa/SKILL.md) have the full instructions, the tested versions and the limits.

## Try it

```
python -I -B packages/video-export-qa/scripts/make_test_clip.py barcode_clip.mp4
python -I -B packages/video-export-qa/scripts/run_qa.py barcode_clip.mp4 --barcode-expect "0-95"
```

## Tests

```
python -I -B -m unittest discover -s tests/video-export-qa
```

The tests generate their own media with ffmpeg (nothing binary is stored here) and include negative controls: each check is shown to be able to fail. [tests/video-export-qa/README.md](tests/video-export-qa/README.md) explains the portable fixtures and the few tests that skip.

## Licence

This repository is licensed under MIT OR Apache-2.0, at your option ([LICENSE-MIT](LICENSE-MIT), [LICENSE-APACHE](LICENSE-APACHE)).

The copy of the skill delivered through Agensi is under Agensi's buyer terms (a personal, non-transferable licence). The licence line inside `packages/video-export-qa/README.md` describes that delivered copy, not this repository.

## Author and issues

By PurpleDaddy. If you try it on a real export and it gets something wrong, please open an issue with the exact command and the `report.json` (check it for local paths first).
