# video-export-qa

A free skill that proves what is really inside an exported video or GIF, and measures the loudness and silence of an audio track or an audio-only file. It uses ffprobe and ffmpeg for measurements, a built-in parser for GIF delays and a frame-number barcode for frame identity, so the answer to "did my export keep the right frames, at the right speed, with sound, in the right format?" is a measured PASS, WARN or FAIL instead of a guess.

It reads only the exported file, so the check does not depend on the editor or encoder that made it. Real-editor coverage is limited, though: the frame-proof recipe was run through Blender 5.1, DaVinci Resolve Studio 21.1 and a third editor (version 0.5.0), all driven by script or API and not by hand in the program, and not in Premiere Pro, After Effects or Final Cut. It is a checker: it does not edit, convert or repair files.

## What it can tell you

- Whether an export matches a delivery spec: codec, pixel format, size, frame rate, constant frame rate, frame count, duration, colour tags, audio format and audio start offset (the stream start times the container records; lip-sync is not measured).
- Whether the audio is silent, how loud it is, whether its true peak reaches the -0.1 dBTP ceiling (clip risk; audio that was clipped earlier and then lowered is not detected), and where it goes quiet. An audio stream that exists but contains only silence is reported as a FAIL. For an audio-only file use `qa_audio.py`; `run_qa.py` reports such a file as a FAIL on `video_stream` on purpose.
- Which source frame sits on every output frame, so dropped, repeated, reversed, sped-up and trimmed frames are visible. This needs the test clip from `scripts/make_test_clip.py` to go through your edit and export.
- Whether a GIF plays at the speed and length you meant (frame delays are whole hundredths of a second, so rounding is a real risk).
- Whether two files decode to exactly the same picture frames (audio and timing are not compared).

## Install

Copy this whole folder (`video-export-qa`) into your agent's skills folder. For Claude Code that is `~/.claude/skills/` for every project, or `.claude/skills/` inside one project. Then ask the agent in plain words, for example "check this export against my delivery spec". `SKILL.md` holds the instructions for the agent.

If your agent got the skill as text from a marketplace tool and nothing was installed, there is no folder to copy. `SKILL.md` then sends the agent to `references/no-skill-folder.md`: it lists the few files each task needs and gives a short program that makes a fresh folder in the system temp folder, outside your project, and checks every file in it against its published SHA-256 before any script runs. The note tells the agent to delete that folder afterwards.

If your agent does not pick the skill up on its own, say: use the video-export-qa skill.

## Requirements

- FFmpeg: `ffmpeg` and `ffprobe` on the PATH (or pass `--ffmpeg` and `--ffprobe`). Not included, not downloaded.
- Python 3.9 or newer, standard library only.

Tested: the scripts were run from Claude Code on Windows 11. The 985-test suite for this version, which builds its own clips, passes on Windows 11 with ffmpeg 5.1, 7.1, 8.0.1, 8.1.1 and 9.0.1 on Python 3.14.6 (each with the ffprobe of the same build, except two runs: the 5.1 run used an ffprobe built from a development snapshot of 31 August 2022, a little newer than 5.1, and the 7.1 run used the ffprobe of 8.1.1), and with ffmpeg and ffprobe 8.1.1 also on Python 3.9.25 and 3.13.13. It passes in stock Ubuntu 22.04, Debian 12, Ubuntu 24.04 and Ubuntu 26.04 containers too, with ffmpeg and ffprobe 4.4.2 (Python 3.10.12), 5.1.9 (3.11.2), 6.1.1 (3.12.3) and 8.0.1 (3.14.4). Together these runs cover Python 3.9 to 3.14 (3.9.25, 3.10.12, 3.11.2, 3.12.3, 3.13.13, 3.14.4 and 3.14.6). On 4.4 and 5.1 the tests that need a rotated test clip are skipped because those builds lack `-display_rotation`, so rotation handling was not exercised there. Not tested: macOS, ffmpeg older than 4.4, and agents other than Claude Code. The code uses only the Python standard library and is written to the Python 3.9 grammar.

Source and tests: the suite and the source are in the public repository https://github.com/sawizzle/video-export-qa, not in this folder. From a checkout of that repository, run `python -I -B -m unittest discover -s tests/video-export-qa` in its root.

## Quick start

Run these in a folder you can write to. `<skill-dir>` is this folder.

```
python -I -B <skill-dir>/scripts/make_test_clip.py barcode_clip.mp4
python -I -B <skill-dir>/scripts/run_qa.py barcode_clip.mp4 --barcode-expect "0-95"
```

The first line writes a 4 second test clip (96 frames, 24 fps, 640x360, with a tone). The second checks it and prints a Markdown report; the exit code is 0 when nothing failed. Import the clip into your editor, make an edit, export, and check the export with the segments you expect, for example `--barcode-expect "0-29,36-95"` for "frames 30 to 35 removed" (`qa_frames.py` spells the same flag `--expect`).

With a delivery spec: add `--spec spec.json` (see `examples/`) and `--out-dir qa_out` to save `report.md` and `report.json`. The test clip's tone measures about -20.7 LUFS and -19.7 dBTP (-20.0 dBTP if ffmpeg 4.4.2 made the clip), so it cannot meet a loudness target: for the barcode run use `examples/spec_barcode_run.json` (the full delivery spec without the four loudness keys), and check loudness with the full spec in a second run on the real deliverable. That spec asks for 1920x1080, so make the clip with `--size 1920x1080` (the quick start above makes a 640x360 clip), or change `width` and `height` in your copy of the spec.

Nothing is written to disk except the files you name, and nothing is overwritten without `--overwrite`. When there is a spec to check, `SKILL.md` also tells the agent to write it as a JSON file next to the export. With no skill folder, `references/no-skill-folder.md` tells your agent (not the scripts) to write the few skill files a task needs into a fresh folder in the system temp folder, and to delete that folder afterwards. `report.json` records the exact commands with full local paths (`report.md` shortens them), so look through it before you share it.

## What is in the folder

| Path | Contents |
| --- | --- |
| `SKILL.md` | The instructions for the agent: workflow, spec keys, output format, limits. |
| `scripts/` | `run_qa.py` (everything in one report), `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `qa_compare.py`, `make_test_clip.py`, and the shared modules `common.py` and `barcode.py`. |
| `references/delivery-specs.md` | Common delivery targets and the reason for each. |
| `references/interpreting-results.md` | Every check id: what a FAIL or WARN means, likely causes, what to do. |
| `references/pitfalls.md` | Measured traps: variable frame rate, GIF delay rounding, AAC start offsets, untagged colour, full-range pixel formats, silent exports, raw streams. |
| `references/editor-recipes.md` | How to prove an edit with the barcode clip, with expected segments for common edits. |
| `references/no-skill-folder.md` | For an agent that received the skill as text and has no skill folder: the smallest set of files for each task, a tested program that makes a fresh temporary folder and checks every file in it by SHA-256, and how to delete the folder. |
| `examples/` | Three spec files (a full delivery spec, the same without the loudness keys for a barcode run, and a GIF spec), a real sample report, and a complete example session. |

## Limits

It proves frame identity and measurable properties. It does not judge how a picture looks or sounds. It checks colour tags, not colour correctness. The barcode proves that your editor and export settings keep the frames you asked for, using the test clip; it proves picture frames only, so `run_qa.py` also warns when the sound and the picture differ in length. Audio sync is not measured beyond the container's start times: check lip-sync with a clap or flash test. The true-peak ceiling (`no_clipping`) does not detect clipped samples. The full list is in `SKILL.md`.

## Licence

This copy, delivered through Agensi, is under the licence of this listing (personal use). The public repository named above is licensed MIT OR Apache-2.0 (its README explains the split). No warranty. Check results against your own delivery requirements before relying on them.
