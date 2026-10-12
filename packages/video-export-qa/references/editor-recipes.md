# Proving an edit with the barcode clip

Idea: every frame of the test clip carries a barcode of its own frame number. Whatever your editor or exporter does to the clip, the barcode in the exported file says exactly which source frame sits on each output frame. The checker reads only the exported file, so the check does not depend on the editor; real-editor coverage is limited, though (three programs, driven by script, see the table below).

What it proves: that this editor, with these settings and this export preset, keeps the frames you told it to keep, in the order you told it to. It does not prove anything about other footage. To check a real delivery, repeat the export with the same settings and run the other checks (`run_qa.py` with the full delivery spec) on the real file. The test clip's tone measures about -20.7 LUFS and -19.7 dBTP (-20.0 dBTP if ffmpeg 4.4.2 made the clip), so loudness cannot be judged on the clip (step 5).

`<skill-dir>` is the folder that holds `SKILL.md`; replace it with that folder's path (for example `~/.claude/skills/video-export-qa`). Run every command from the folder that holds your clip and your export, not from inside the skill folder, so `barcode_clip.mp4` and `qa_out/` are written there.

## The recipe (editor-neutral, tested in three programs)

1. Make the clip with the same frame size and frame rate as your project. The script is in this skill's `scripts` folder.

   ```
   python -I -B <skill-dir>/scripts/make_test_clip.py barcode_clip.mp4 --frames 96 --fps 24 --size 1920x1080
   ```

   For 23.976 fps pass `--fps 24000/1001` (`--fps 23.976` is accepted and means the same; likewise 29.97 and 59.94 mean 30000/1001 and 60000/1001). If you do not know the project size, ask; if you cannot, use 1920x1080 at the project's frame rate. Add `--tone 0` for a clip without audio (then also delete the `audio` block and `max_av_start_offset_s` from the spec in step 5, or `audio.present`, `audio.sample_rate`, `audio.channels` and `audio.codec` FAIL on a perfect export and the start-offset limit is NOT MEASURED, which keeps the Overall result from being PASS). The default is 96 frames, 640x360, 24 fps, with a 440 Hz tone. The clip is deterministic: the same arguments and the same ffmpeg build give the same bytes.
2. Import the clip and create a sequence or timeline from it, with the sequence set to the same size and frame rate. Leave the picture at 100 percent, with no crop, no mirror, no letterbox bars, no overlay, no title over the top edge, and with frame blending, optical flow and motion smoothing turned off.
3. Make the edit you want to prove.
4. Export with the settings you will use for the real delivery.
5. Check the export. Say which source frames you expect on the output frames. Use a spec WITHOUT the loudness keys for this run (`lufs_target`, `lufs_tol`, `true_peak_max_db`, `no_clipping`), because the clip's tone cannot meet them; keep the `audio` keys. `<skill-dir>/examples/spec_barcode_run.json` is the example delivery spec with those four keys removed: copy it next to the export (it is named without a folder in the command below) and edit it for the real job.

   ```
   python -I -B <skill-dir>/scripts/run_qa.py export.mp4 --spec spec_barcode_run.json --barcode-expect "0-29,36-95" --out-dir qa_out
   ```

   `run_qa.py` spells the flags `--barcode-expect` and `--barcode-region`; `qa_frames.py` takes the same values as `--expect` and `--region`. `run_qa.py` reads the barcode only when it is given `--barcode-expect` (or `barcode.expect` in the spec); without one, a PASS says nothing about frames.

   Or only the frame check, with the runs listed:

   ```
   python -I -B <skill-dir>/scripts/qa_frames.py export.mp4 --expect "0-29,36-95" --show-runs
   ```

   Then check the real deliverable (the export made with the same settings from the real footage) in a second `run_qa.py` call with the full delivery spec, for example `<skill-dir>/examples/spec_h264_1080p24.json`, and no `--barcode-expect`.
6. If you do not know what the export contains, run `python -I -B <skill-dir>/scripts/qa_frames.py export.mp4 --show-runs` without `--expect`. It prints the runs of consecutive frames, for example `output 0-29 -> source 0-29` and `output 30-89 -> source 36-95`, and flags dropped frames inside the clip, repeated frames, reversed passages and a first frame that is not source 0 (a trim at the start). A trim at the END of the clip is only caught when you state the edit with `--expect`, because without it the length of the clip is not known.

## Writing the expected segments

Frame numbers start at 0. Segments are `START-END` or `START-END:STEP`, both ends included, joined with commas. If START is bigger than END the step is -1. A lone number is one frame. Frame 0 of the barcode clip is the first frame of the clip, whatever your editor labels it (some start at 1, timecode starts at 00:00:00:00).

For the default 96 frame clip (4 s at 24 fps):

| Edit | Segments (`--barcode-expect` in `run_qa.py`, `--expect` in `qa_frames.py`) | Output frames |
| --- | --- | --- |
| Straight export, no edit | `0-95` | 96 |
| Trim the first second off the start | `24-95` | 72 |
| Keep only the first 3 seconds | `0-71` | 72 |
| Keep frames 24 to 71 | `24-71` | 48 |
| Remove frames 30 to 35 and close the gap | `0-29,36-95` | 90 |
| Play the first 2 seconds twice | `0-47,0-47` | 96 |
| Swap the two halves | `48-95,0-47` | 96 |
| Reverse the whole clip | `95-0` | 96 |
| Reverse only frames 30 to 59 | `0-29,59-30,60-95` | 96 |
| Double speed, every second frame | `0-94:2` | 48 |
| Triple speed, every third frame | `0-93:3` | 32 |
| Hold frame 40 for three extra frames | `0-40,40,40,40,41-95` | 99 |
| Half speed by repeating each frame, first four frames | `0,0,1,1,2,2,3,3` | 8 |

Notes:
- Double speed can pick frames 0, 2, 4 or frames 1, 3, 5 depending on the tool. Run once without `--expect` and read the runs, then write down what you intended.
- If the tool blends neighbouring frames for slow motion or speed changes (frame blending, optical flow, motion smoothing), the barcodes of the two frames are mixed. Some blended frames become undecodable, but others decode to a valid-looking WRONG frame number, so a blend can look like clean duplicates. `qa_frames.py` warns with `low_block_margin` when the barcode is faint. Choose frame sampling or nearest frame for the test.
- Cuts where an out point is inclusive in one tool and exclusive in another differ by one frame. The report shows the first output frame that disagrees and the numbers expected and found, which tells you which way round it went.

## Reading the answer

- `expect` PASS: all output frames carry the stated source frames, and the number of frames is right.
- `expect` FAIL: the detail gives the first output frame that disagrees, what was expected, what was found (`undecodable` if the barcode could not be read), and how many output frames differ. The `actual` value is written in the same notation as `--expect` (a frame held for several output frames shows as `NxC`, for example `40x4`, and an unreadable frame as `?`; those two forms are for reading only and `--expect` does not accept them).
- `all_frames_decodable` FAIL: the barcode strip was damaged or moved. See the notes on what breaks it in references/pitfalls.md.
- `qa_frames.py` without `--expect`: `no_dropped_frames`, `no_duplicate_frames` and `monotonic` are judged. With `--expect` (in `run_qa.py`: `--barcode-expect`) they are SKIP, because you declared the edit and `expect` is the verdict. `run_qa.py` without `--barcode-expect` does not read the barcode at all.

## Settings that were run in real editors (2026-10-10)

The recipe was run end to end in three programs with the 96 frame, 1280x720, 24 fps barcode clip: a straight export (`0-95`), a cut that removes frames 30 to 35 (`0-29,36-95`) and a 2x speed-up with blending off (`0-94:2`). All three passed in all three programs. The programs were driven by script or API, not by hand in their windows. Premiere Pro, After Effects and Final Cut were not tested. Names change between versions, so treat these as a starting point and always check the export.

| Setting | Blender 5.1 (video sequence editor) | DaVinci Resolve Studio 21.1 (scripting API) | Third editor 0.5.0 (engine commands) |
| --- | --- | --- | --- |
| Match size and rate | Not inherited from the clip: set `render.resolution_x/_y`, `resolution_percentage = 100`, `fps = 24`, `fps_base = 1`. Add the clip and its sound as two strips. | Before the first timeline: `timelineResolutionWidth/Height`, `timelineFrameRate`. A new timeline starts at 01:00:00:00; the barcode frame numbers are not affected. | `file.newSequence` with `width`, `height`, `fps` (the defaults are 1920x1080 and 23.976). |
| Blending off | Speed Control strip `use_frame_interpolate = False`; `transform.filter = NEAREST`. | Per clip `RetimeProcess = RETIME_NEAREST` (the project default was already Nearest). | The default is frame sampling; to state it: `clip.setTimeInterpolation` with `frameSampling`. |
| Cut out frames 30-35 | Split both strips at timeline frames 31 and 37, remove the middle, move the right piece's `content_start` back by 6. | No blade in the API: append two sub-clips, source 0-29 then 36-95 (`endFrame` is exclusive). | `sequence.addEditAllTracks` at both frames, then `edit.rippleDelete` on the middle video and audio clips. |
| 2x speed | Speed Control strip, `speed_control = MULTIPLY`, `speed_factor = 2`. Bound the length with the scene end frame. | `item.SetSpeed` with `Percentage = 200`; for slow motion use `RippleTimeline = True` or the source tail is dropped. | `clip.speedDuration` with `speed = 200`, `interpolation = frameSampling`. |
| Export | FFMPEG / MPEG-4 / H.264 / AAC, `constant_rate_factor = HIGH`. | `H.264 Master` preset, mp4/H264, `SelectAllFrames = True`; the quality value could not be set through the API on this build. | `file.exportMedia` with the `Match Source - Adaptive High Bitrate` preset (in that program's own list the hyphen is an en dash, so copy the name from there), `wait = true`. |
| Colour | Set the view transform to `Standard`. The default (AgX) tone-maps the picture: frame numbers still read and the check passes, but the barcode margin fell from 124 to 66 and the picture no longer matches the source. | Item scaling 0, no zoom or crop. | No scaling or colour change seen. |

What the three programs showed that the recipe above does not say:

- **Out points differ.** A cut's last frame is exclusive in Resolve's `AppendToTimeline` and in the third editor's and Blender's strip ends, but inclusive in Resolve's render mark-out and Blender's scene end frame. If a cut is one frame off, this is the first thing to check. The report shows the first output frame that disagrees.
- **Half speed with blending breaks the barcode, in every program.** In Blender and DaVinci Resolve with frame blending, the 96 even output frames were clean and the 96 in-between frames were blends: 58 unreadable, 25 read as a neighbour (they look like clean duplicates) and 13 read as a wrong number. `low_block_margin` warned (margin 2.2 and 2.1). In the third editor with frame blending the margin stayed at 58, so that warning did not fire, but 58 frames were unreadable, the order check warned and the declared `--expect` failed. Judge a blending test by `all_frames_decodable` and `expect`, not by `low_block_margin` alone. Frame sampling (Nearest) gives clean pairs (`0,0,1,1,...`).
- **The barcode test does not retime or cut the sound; the editor decides.** In Blender the speed control did not retime the sound strip: at 2x the picture ran 2 seconds over the first 2 seconds of sound at normal speed, and at half speed the picture ran 8 seconds over 4 seconds of sound followed by 4 seconds of silence. The lengths matched only because the scene end cut them. Check the sound with `av_duration_match` or listen to it.
- `qa_frames.py` without `--expect`: an export of an intended cut or speed-up reports `no_dropped_frames` as a FAIL and exits with 1. That is correct: `--expect` is the verdict for a declared edit.
- `qa_frames.py` without `--expect`: a trim at the start (for example `24-47`) exits 0 with a WARN. A script that only reads the exit code will not see it, so always pass `--expect` (in `run_qa.py`: `--barcode-expect`).

## Example that was run: a trim made with ffmpeg

This was run on Windows 11 with ffmpeg 8.1.1, standing in for an editor so that anyone can repeat it. The barcode clip is made, its first 24 frames (one second) are trimmed off with ffmpeg, picture and sound together, and the result is checked. Any editor's export is checked the same way.

```
python -I -B <skill-dir>/scripts/make_test_clip.py barcode_clip.mp4
ffmpeg -i barcode_clip.mp4 -vf "trim=start_frame=24,setpts=PTS-STARTPTS" -af "atrim=start=1,asetpts=PTS-STARTPTS" -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac trimmed.mp4
python -I -B <skill-dir>/scripts/qa_frames.py trimmed.mp4 --expect 24-95 --show-runs
```

Result of the last command (exit code 0):

```
qa_frames.py: PASS  (trimmed.mp4)
PASS 3  WARN 0  FAIL 0  SKIP 3
Skipped, no claim made (SKIP proves nothing): no_dropped_frames, no_duplicate_frames, monotonic

[PASS] all_frames_decodable         expected=0 undecodable frames actual=0 of 72 undecodable every frame decoded (lowest block margin 123.2 of a possible 127)
[PASS] low_block_margin             expected=lowest block margin at least 40 of a ... actual=123.2 every barcode block is clearly black or white
[SKIP] no_dropped_frames            expected=null actual=null --expect given: the declared edit is judged by the expect check; facts are in info
[SKIP] no_duplicate_frames          expected=null actual=null --expect given: the declared edit is judged by the expect check; facts are in info
[SKIP] monotonic                    expected=null actual=null --expect given: the declared edit is judged by the expect check; facts are in info
[PASS] expect                       expected=24-95 (72 frames) actual=24-95 (72 frames) all 72 output frames carry the expected source index

runs (output frames -> source frames):
  output 0-71 -> source 24-95  (72 frames)
```

As a negative control, the same file checked with `--expect 0-71` fails: `first mismatch at output frame 0: expected 0, got 24; 72 mismatching output frame(s)`, exit code 1.
