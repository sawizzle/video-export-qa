# No skill folder: run the scripts from text

Read this only when the skill reached you as text and nothing was installed: the files came as `### File: video-export-qa/<path>` blocks in a saved response file or in the message, and there is no `video-export-qa` folder on disk, so `<skill-dir>` in `SKILL.md` points at nothing. If the folder exists, ignore this file.

Write only the files your task needs into a temporary folder outside the user's project, run the scripts from there, and delete the folder when you are done. Do not write every script, and do not retype a script into a command.

## 1. The files each task needs

The scripts import each other, so one script alone does not start. Each set below was run in an otherwise empty folder, with the layout `scripts/<file>`. Size is what you would have to copy.

| Task | Script to run | Files to write (in `scripts/`) | Size |
| --- | --- | --- | --- |
| Frame rate, constant frame rate, colour tags, frame count, codec, size | `qa_probe.py` | `qa_probe.py`, `common.py` | 79 KB |
| Loudness, true peak, silence, silent audio | `qa_audio.py` | `qa_audio.py`, `qa_probe.py`, `common.py` | 106 KB |
| The whole report | `run_qa.py` | `run_qa.py`, `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `barcode.py`, `common.py` | 199 KB |
| The whole report with a barcode expectation (`--barcode-expect`) | `run_qa.py` | `run_qa.py`, `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `barcode.py`, `common.py` | 199 KB |
| Make the barcode clip, read the frame numbers back | `make_test_clip.py`, `qa_frames.py` | `make_test_clip.py`, `qa_frames.py`, `barcode.py`, `common.py` | 73 KB |
| GIF timing | `qa_gif.py` | `qa_gif.py`, `common.py` | 46 KB |
| Two files, frame for frame | `qa_compare.py` | `qa_compare.py`, `common.py` | 37 KB |

Only `run_qa.py` opens a file from `references/`: `references/interpreting-results.md` (30 KB), looked up next to `scripts/`. Without it the run still works and gives the same verdict, counts, checks and exit code. The only difference: under Next steps, each FAIL or WARN row says "No guidance is listed for this check id" instead of giving the likely causes and what to do. Write that file too (same layout), or read the row for the check id in the response yourself; never invent the guidance. A spec to start from is under 1 KB: `examples/spec_h264_1080p24.json`, `examples/spec_barcode_run.json` or `examples/spec_web_gif.json`; write it with the snippet below, or copy it from the response.

## 2. Write the files

You need the path of the saved response file and the names from section 1, written as `scripts/qa_probe.py`. The snippet splits the response on its `### File: ` lines and takes each file from between the first and the last fence line of its block, so fenced code inside a markdown file and a last block with no final newline are handled. It writes nothing unless every name you asked for was found as a complete block. Pass `new` as the folder to make a fresh folder with `tempfile`, or the path it printed to add files to that folder.

```python
import re, sys, tempfile
from pathlib import Path
response, folder, *names = sys.argv[1:]
for name in names:
    if not re.fullmatch(r"(scripts|references|examples)/[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
        sys.exit("not a file name of the skill: " + name)
text = open(response, encoding="utf-8", newline="").read()
files = {}
for part in re.split(r"(?m)^### File: ", text)[1:]:
    head, _, body = part.partition("\n")
    block = re.fullmatch(r"\s*(`{3,})[^\n]*\n(.*)\n\1\s*", body, re.S)
    if block:
        files[head.strip().split("/", 1)[-1]] = block.group(2)
missing = [name for name in names if name not in files]
if missing:
    sys.exit("no complete block for: " + ", ".join(missing))
if folder == "new":
    folder = tempfile.mkdtemp(prefix="veqa_")
for name in names:
    path = Path(folder, *name.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(files[name].encode("utf-8"))
    print("wrote", path.as_posix(), len(files[name]), "characters")
print("skill folder:", Path(folder).as_posix())
```

Run it with the interpreter reading the program from standard input, for example `python -I -B - RESPONSE new scripts/qa_probe.py scripts/common.py` with the snippet as the input, or save it as a `.py` file outside the project and delete that file afterwards. The last line it prints, `skill folder: <path>`, names the folder: that folder is `<skill-dir>` for the rest of the task.

## 3. Run

From the folder that holds the export, so reports land next to it, exactly as in `SKILL.md`:

```
python -I -B <skill-dir>/scripts/qa_probe.py export.mp4 --spec spec.json
```

Running a script with `--help` prints its usage when the set is complete. A `ModuleNotFoundError` names a file missing from the set: write it into the same folder.

## 4. Delete

When the last run is done, delete the folder the snippet printed (its name starts with `veqa_`) with your shell's own command for removing a folder and its contents, and delete nothing else; compare the path with the `skill folder:` line before you run it. Reports you wrote with `--out-dir` are in the user's folder and stay there. If the task goes on later (for example after the user edits and exports the barcode clip), keep the folder until the last run, or write the set again with `new`.

## 5. The response came back inline

If nothing was saved to a file, there is nothing for the snippet to read. Then write the files of one row by hand, each exactly as it stands in its block, into a folder made with `tempfile.mkdtemp(prefix="veqa_")`, in the same layout. Take the smallest row that answers the question (`qa_probe.py` for rates, tags and counts), write nothing that is not in the row, and finish with `--help` as in section 3.
