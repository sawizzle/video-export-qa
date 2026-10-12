# Running the video-export-qa tests

Run from the repository root:

```powershell
python -I -B -m unittest discover -s 'tests/video-export-qa'
```

The suite needs ffmpeg and ffprobe on PATH (ffmpeg 4.4 or newer). To compare builds, put the other ffmpeg directory first on PATH and keep the rest of PATH. The suite has 673 tests.

## Portable fixtures

All fixture compatibility lives in [ffmpeg_fixtures.py](ffmpeg_fixtures.py). The [product scripts](../../packages/video-export-qa/scripts/) are always run unchanged: the tests adapt only the commands that build test clips and reference output.

- Real one-frame probes cache whether a build accepts `-fps_mode` or `-display_rotation`, once per executable. Fixture and reference commands use `-fps_mode` when it exists and otherwise the exact older spelling: passthrough = `-vsync 0`, vfr = `-vsync 2`, cfr = `-vsync 1`.
- A few tests skip and say why: the rotation tests when the build lacks `-display_rotation` (reason `ffmpeg build lacks -display_rotation`), the deleted-current-folder test on Windows, the two private-term scans where their local deny-list file is absent, and the forced-colour test on a build that ignores `AV_LOG_FORCE_COLOR`. Any other failure to build a fixture, or an unexpected probe error, is an assertion failure in the tests that use it, never a skip.
- The probe fixtures use a test picture (not a flat colour) for the odd-size clips and check the stored width and height with ffprobe. The clip that starts 2 s in is made by remuxing an encoded clip and is checked with ffprobe.
- The frame and integration fixtures that are meant to be constant frame rate say so (`-fps_mode cfr -r 24`). The variable frame rate fixtures keep their timestamps. The GIF fixtures give the last 30 fps frame an explicit delay instead of inheriting one that depends on the ffmpeg version.
- The Windows-only `?` folder-name case runs only on Windows. The "path below a regular file" case runs everywhere.
- The audio tests check that a readable generated clip actually reached measurement before they use any number from it, so a product filter failure fails there instead of surfacing later as arithmetic on `None`.

## Rules for editing the tests

Keep every negative control. Use subtests or an outside harness to exercise helper branches without changing the test count. An unexpected encoding error is a failure, not evidence that a capability is missing.
