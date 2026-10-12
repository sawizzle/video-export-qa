# video-export-qa

A free skill that proves what is really inside an exported video, GIF or audio file. It uses ffprobe and ffmpeg for measurements and a frame-number barcode for frame identity, so the answer to "did my export keep the right frames, at the right speed, with sound, in the right format?" is a measured PASS, WARN or FAIL instead of a guess.

It works with any editor or encoder that writes a video file, because it only reads the exported file. The frame-proof recipe was run through Blender 5.1, DaVinci Resolve Studio 21.1 and FilmCraft (driven by script or API, not by hand in the program, and not in Premiere Pro, After Effects or Final Cut). It is a checker: it does not edit, convert or repair files.

## What it can tell you

- Whether an export matches a delivery spec: codec, pixel format, size, frame rate, constant frame rate, frame count, duration, colour tags, audio format and start offset.
- Whether the audio is silent, how loud it is, whether it clips, and where it goes quiet. An audio stream that exists but contains only silence is reported as a FAIL.
- Which source frame sits on every output frame, so dropped, repeated, reversed, sped-up and trimmed frames are visible. This needs the test clip from `scripts/make_test_clip.py` to go through your edit and export.
- Whether a GIF plays at the speed and length you meant (frame delays are whole hundredths of a second, so rounding is a real risk).
- Whether two files decode to exactly the same frames.

## Install

Copy this whole folder (`video-export-qa`) into your agent's skills folder. For Claude Code that is `~/.claude/skills/` for every project, or `.claude/skills/` inside one project. Then ask the agent in plain words, for example "check this export against my delivery spec". `SKILL.md` is the file the agent reads.

If your agent does not pick the skill up on its own, say: use the video-export-qa skill.

## Requirements

- FFmpeg: `ffmpeg` and `ffprobe` on the PATH (or pass `--ffmpeg` and `--ffprobe`). Not included, not downloaded.
- Python 3.9 or newer, standard library only.

Tested: the scripts were run from Claude Code on Windows 11. The 673-test suite, which builds its own clips, passes with ffmpeg and ffprobe 5.1, 8.0.1, 8.1.1 and 9.0.1 on Windows, and 4.4.2, 5.1.9, 6.1.1 and 8.0.1 in stock Debian 12 and Ubuntu 22.04, 24.04 and 26.04 containers, on Python 3.9 to 3.14. On 4.4 and 5.1 the five tests that need a rotated test clip are skipped because those builds lack `-display_rotation`, so rotation handling was not exercised there. Not tested: macOS, ffmpeg older than 4.4, and agents other than Claude Code. The code uses only the Python standard library and is written to the Python 3.9 grammar.

## Quick start

Run these in a folder you can write to. `<skill-dir>` is this folder.

```
python -I -B <skill-dir>/scripts/make_test_clip.py barcode_clip.mp4
python -I -B <skill-dir>/scripts/run_qa.py barcode_clip.mp4 --barcode-expect "0-95"
```

The first line writes a 4 second test clip (96 frames, 24 fps, 640x360, with a tone). The second checks it and prints a Markdown report; the exit code is 0 when nothing failed. Import the clip into your editor, make an edit, export, and check the export with the segments you expect, for example `--barcode-expect "0-29,36-95"` for "frames 30 to 35 removed". With a delivery spec: add `--spec spec.json` (see `examples/`) and `--out-dir qa_out` to save `report.md` and `report.json`.

Nothing is written to disk except the files you name, and nothing is overwritten without `--overwrite`. `report.json` records the exact commands with full local paths (`report.md` shortens them), so look through it before you share it.

## What is in the folder

| Path | Contents |
| --- | --- |
| `SKILL.md` | The instructions the agent follows: workflow, spec keys, output format, limits. |
| `scripts/` | `run_qa.py` (everything in one report), `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `qa_compare.py`, `make_test_clip.py`, and the shared modules `common.py` and `barcode.py`. |
| `references/delivery-specs.md` | Common delivery targets and the reason for each. |
| `references/interpreting-results.md` | Every check id: what a FAIL or WARN means, likely causes, what to do. |
| `references/pitfalls.md` | Measured traps: variable frame rate, GIF delay rounding, AAC start offsets, untagged colour, silent exports. |
| `references/editor-recipes.md` | How to prove an edit with the barcode clip, with expected segments for common edits. |
| `examples/` | Two spec files, a real sample report, and a complete example session. |

## Limits

It proves frame identity and measurable properties. It does not judge how a picture looks or sounds. It checks colour tags, not colour correctness. The barcode proves that your editor and export settings keep the frames you asked for, using the test clip; it proves picture frames only, so `run_qa.py` also warns when the sound and the picture differ in length. The full list is in `SKILL.md`.

## Licence

Provided through Agensi under the licence of this listing (personal use). No warranty. Check results against your own delivery requirements before relying on them.
