# No skill folder: run the scripts from text

Read this only when the skill reached you as text and nothing was installed: there is no `video-export-qa` folder on disk, so `<skill-dir>` in `SKILL.md` points at nothing. If the folder exists, ignore this file.

The route: a short program (section 2) makes a fresh private folder in the system temp folder, writes or checks the few files your task needs, and proves every file in that folder by its length and SHA-256; run no script before it has done so. You fetch each file the same way you fetched this one, by its path in the skill's file list (for example `video-export-qa/scripts/qa_probe.py`). Put nothing of the skill into the user's project, and delete the folder at the end (section 6).

## 1. The files each task needs

The scripts import each other, so one script alone does not start. Each set below was run in an otherwise empty folder, with the layout `scripts/<file>`. Size is what you would have to write, in KB of 1,000 bytes (the skill's file list counts KB of 1,024 bytes, so its numbers are a little smaller).

| Task | Script to run | Files to write (in `scripts/`) | Size |
| --- | --- | --- | --- |
| Frame rate, constant frame rate, colour tags, frame count, codec, size | `qa_probe.py` | `qa_probe.py`, `common.py` | 79 KB |
| Loudness, true peak, silence, silent audio | `qa_audio.py` | `qa_audio.py`, `qa_probe.py`, `common.py` | 106 KB |
| The whole report | `run_qa.py` | `run_qa.py`, `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `barcode.py`, `common.py` | 199 KB |
| The whole report with a barcode expectation (`--barcode-expect`) | `run_qa.py` | `run_qa.py`, `qa_probe.py`, `qa_audio.py`, `qa_frames.py`, `qa_gif.py`, `barcode.py`, `common.py` | 199 KB |
| Make the barcode clip, read the frame numbers back | `make_test_clip.py`, `qa_frames.py` | `make_test_clip.py`, `qa_frames.py`, `barcode.py`, `common.py` | 73 KB |
| GIF timing | `qa_gif.py` | `qa_gif.py`, `common.py` | 46 KB |
| Two files, frame for frame | `qa_compare.py` | `qa_compare.py`, `common.py` | 37 KB |

Take the smallest row that answers the question, and write nothing that is not in it: a file that comes back as text in the conversation has to be written out by you, so single scripts first. The audio row holds the probe row, so one set of three files (106 KB) answers "is it constant frame rate, what are its frame rate and size, is the audio silent or too quiet": run `qa_probe.py` and `qa_audio.py`. The whole report needs seven files (199 KB); use it only when the user wants one saved report or a whole spec checked.

Only `run_qa.py` opens a file from `references/`: `references/interpreting-results.md` (30 KB), looked up next to `scripts/`. Without it the run still works and gives the same verdict, counts, checks and exit code. The only difference: under Next steps, each FAIL or WARN row says "No guidance is listed for this check id" instead of giving the likely causes and what to do. Write that file too (it goes in the `references/` folder the program makes), or read the row for the check id in the text yourself; never invent the guidance. The examples and the other references are never needed on disk: when `SKILL.md` points at one (a spec example, `references/delivery-specs.md`), fetch it and read it, and write the spec the user needs next to the export, as step 3 of the Workflow says.

## 2. The program

It takes one mode and its arguments:

- `new` makes a fresh folder (name `veqa_` and 8 characters, directly in the system temp folder; on Linux and other POSIX systems only you can open it) with an empty `scripts/` and `references/` in it, checks it and prints `OK folder: <FOLDER>`. That folder is `<skill-dir>` for the rest of the task. Run `new` once.
- `put <FOLDER> <SAVED FILE> <NAME>...` reads an answer that was saved to a file, finds each NAME in it, checks its length and SHA-256, and only then writes it into the folder. It never overwrites, and it writes nothing when anything is wrong.
- `check <FOLDER> <NAME>...` checks the folder and every file in it, and that each NAME is there. It prints `OK <NAME> verified (<N> bytes)` per file and then `OK <N> verified files and nothing else in <FOLDER>`.

NAME is the path inside the skill as the table below writes it (`scripts/qa_probe.py`); the path as the file list shows it (`video-export-qa/scripts/qa_probe.py`) works too. `put` and `check` accept only a folder of the shape `new` makes: directly in the system temp folder, named `veqa_` and 8 lower-case letters, digits or underscores (the name as you write it in the command: on Windows a folder stored as `VEQA_UPPER001` is accepted when you write `veqa_upper001`), holding only `scripts/` and `references/` and in them only files of the table below. A folder of that shape made by hand is accepted too (the program cannot tell who made it); on Linux only when it, `scripts/` and `references/` belong to you and give group and others no permission, as `new` makes them. Every file in it is still checked by its length and SHA-256. Success is the `OK` line, not the exit code. A refusal starts with `REFUSED: ` and exits 1. A mode or a number of arguments that the program does not take prints only the line `usage: python -I -B - new | put FOLDER SAVED NAME... | check FOLDER [NAME...]`, exits 2 and writes nothing.

Feed the program to Python on standard input, unchanged, every time you run it: in bash or sh with a quoted here-document, in Windows PowerShell with a single-quoted here-string. Do not save it to a file, do not shorten it and do not retype it from memory: copy it from this block.

```python
try:  # Skill file extractor, one try block: a copy cut short almost always stops with a SyntaxError or IndentationError; only its OK lines mean success.
 import hashlib, itertools, json, os, re, stat, sys, tempfile
 SKILL, PREFIX, TABLE = "video-export-qa", "veqa_", {
  "references/interpreting-results.md": ("54bafee91e80da7396b366c2fbb68e7b" "e89846805c077f6a3d81a9d953e97b2c", 29548),
  "scripts/barcode.py": ("3f5091182f800035a3b03b241590ad14" "2b0d7d9029630234e5d64263f0afd5ec", 11538),
  "scripts/common.py": ("eca1476518b173b8cf8dc4c9367d9465" "641a57de385e23fc68fba81540db7300", 20306),
  "scripts/make_test_clip.py": ("22cb71c748f0ecbb002fe9c06c89eaae" "5e397f572c4d1fff0e01916488513f84", 12481),
  "scripts/qa_audio.py": ("9be5d9762cfcd5c0546be22dff3daed4" "3344057cfb61e16cc128a6b5f3f090d5", 27000),
  "scripts/qa_compare.py": ("c02e91764a68d6b68b16ed6fb246aa32" "688709df79a3cfa6267c09e61e0cb349", 16263),
  "scripts/qa_frames.py": ("9565f2b14b54018ce7ed77ca624f8847" "5f09ff33e757215b3b58985aae7bf7c1", 28178),
  "scripts/qa_gif.py": ("c8e8514ceb966aa7b36593b768b9bb75" "e1700af0c1e968d6cc6dce40a32a7d99", 25407),
  "scripts/qa_probe.py": ("74c50b222294e420565ea333f4c87605" "3b3825d9cf9e7f092e569a866d8e968b", 58268),
  "scripts/run_qa.py": ("cfaeaff2f44bdb511104bf8e8fe5c7e6" "d8d6e0b24b01099b1b8f8ce130c852f8", 28574),
 }
 DIRS, POSIX, J = sorted({n.split("/")[0] for n in TABLE}), os.name != "nt", os.path.join
 HEAD = re.compile(r"^#{2,3} File: [^\r\n]*", re.M)
 def stop(why): sys.exit("REFUSED: " + why)
 def show(p): return str(p) if re.fullmatch(r"[\w .:/\x5c~+-]+", str(p), re.A) else ascii(str(p))
 def same(n, data): return (hashlib.sha256(data).hexdigest(), len(data)) == TABLE[n]
 def tempdir():
   t = tempfile.gettempdir()
   s = os.stat(t)
   if os.path.samefile(t, os.getcwd()): stop("the working folder is the temp folder, or no temp folder is usable: change to another folder (such as your home folder) and run again")
   if POSIX and (s.st_uid not in (0, os.geteuid()) or s.st_mode & 0o022 and not s.st_mode & stat.S_ISVTX): stop("other users can replace folders in the temp folder " + show(t))
   return t
 def entry(p, d):
   s = os.lstat(p)
   if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 1024: stop(show(p) + " is a link or junction")
   if not (stat.S_ISDIR if d else stat.S_ISREG)(s.st_mode): stop(show(p) + " is not a real " + ("folder" if d else "file"))
   if POSIX and s.st_uid != os.geteuid(): stop(show(p) + " belongs to another user")
   if POSIX and (s.st_mode & (0o077 if d else 0o002) or not d and s.st_nlink != 1): stop(show(p) + " is open to other users or has a hard link")
 def verify(arg, need=()):
   f = os.path.abspath(arg)
   if not re.fullmatch(re.escape(PREFIX) + "[a-z0-9_]{8}", os.path.basename(f)): stop(show(f) + " is not a folder that new makes")
   entry(f, True)
   if not os.path.samefile(os.path.dirname(f), tempdir()): stop(show(f) + " is not directly in the temp folder")
   if sorted(os.listdir(f)) != DIRS: stop(show(f) + " must hold only " + ", ".join(DIRS))
   found = []
   for d in DIRS:
     entry(J(f, d), True)
     for n in sorted(d + "/" + x for x in os.listdir(J(f, d))):
       if n not in TABLE: stop(show(f) + " holds " + ascii(n) + " - run scripts with -B" * n.endswith("__pycache__"))
       entry(J(f, n), False)
       with open(J(f, n), "rb") as h:
         if not same(n, h.read(TABLE[n][1] + 1)): stop("%s is not the published file (%d bytes, published %d)" % (n, os.fstat(h.fileno()).st_size, TABLE[n][1]))
       found.append(n)
   if set(need) - set(found): stop("missing: " + ", ".join(sorted(set(need) - set(found))))
   return f, found
 def find(t, n):
   k = TABLE[n][1] + 99
   bodies = [t[:k]]
   for _, m in zip(range(4), re.finditer(r"^#{2,3} File: " + re.escape(SKILL + "/" + n) + r"(?: \(from [^\r\n]*\))?\r?(?![^\n])", t, re.M)):
     for b in {t[m.end() + 1:min(e, m.end() + k)] for e in [h.start() for _, h in zip(range(4), HEAD.finditer(t, m.end()))] + [len(t)]}:
       fence = re.fullmatch(r"\s*(\x60{3,})(?!\x60)[^\n]*\n(.*)\n\1\s*", b, re.S)
       bodies += [b[1:] if b[:1] == "\n" else b] + ([fence.group(2)] if fence else [])
   for c in (v for b in bodies for v in (b, b.rstrip("\n"), b.rstrip("\n") + "\n")):
     data = c.encode("utf-8", "surrogatepass")
     if same(n, data): return data
 def put(arg, saved, names):
   f, found = verify(arg)
   if not stat.S_ISREG(os.stat(saved).st_mode) or os.stat(saved).st_size > 16000000: stop(show(saved) + " is not a file of at most 16 MB")
   with open(saved, "rb") as h:
     texts = [h.read(16000001).decode("utf-8-sig", "replace")]
   try:
     doc = json.loads(texts[0], object_pairs_hook=lambda kv: texts.extend(v for _, v in kv if isinstance(v, str)))
   except (ValueError, RecursionError):
     doc = None
   texts += [doc] * isinstance(doc, str)
   todo = {n: next((d for x in texts for y in dict.fromkeys((x.replace("\r\n", "\n"), x)) for d in [find(y, n)] if d is not None), None) for n in names if n not in found}
   for n in [n for n in todo if todo[n] is None][:1]:
     heads = list(itertools.islice((m.group(0)[:99] for x in texts for m in HEAD.finditer(x)), 5))
     stop("%s holds no exact copy of %s; file headers in it: %s. Nothing was written." % (show(saved), n, ascii(heads)))
   for n, data in todo.items():
     with open(J(f, n), "xb", opener=lambda p, fl: os.open(p, fl, 0o600)) as h:
       h.write(data)
   verify(f, names)
   for n in names: print("OK %s verified (%d bytes)%s" % (n, TABLE[n][1], " - was already there" * (n in found)))
 def run(mode, args):
   if not TABLE or not (mode == "new" and not args or mode == "put" and len(args) > 2 or mode == "check" and args):
     sys.exit(print("usage: python -I -B - new | put FOLDER SAVED NAME... | check FOLDER [NAME...]", file=sys.stderr) or 2)
   i = 1 + (mode == "put")
   args[i:] = [a[len(SKILL) + 1:] if a.startswith(SKILL + "/") else a for a in args[i:]]
   bad = [n for n in args[i:] if n not in TABLE]
   if bad: stop("not a file of " + SKILL + ": " + ", ".join(map(ascii, bad)) + "; its files: " + ", ".join(sorted(TABLE)))
   if mode == "new":
     f = tempfile.mkdtemp(prefix=PREFIX, dir=tempdir())
     for d in DIRS: os.mkdir(J(f, d), 0o700)
     print("OK folder:", show(verify(f)[0]))
   elif mode == "put":
     put(args[0], args[1], args[2:])
   else:
     f, found = verify(args[0], args[1:])
     for n in found: print("OK %s verified (%d bytes)" % (n, TABLE[n][1]))
     print("OK %d verified files and nothing else in %s" % (len(found), show(f)))
 run((sys.argv + [""])[1], sys.argv[2:])
except (OSError, MemoryError) as e:
  sys.exit("REFUSED: the system refused: %s %s" % (type(e).__name__, str(e).encode("ascii", "backslashreplace").decode()))
```

The files it knows (the same values as in the program; the SHA-256 is written in two halves):

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `references/interpreting-results.md` | 29548 | `54bafee91e80da7396b366c2fbb68e7b e89846805c077f6a3d81a9d953e97b2c` |
| `scripts/barcode.py` | 11538 | `3f5091182f800035a3b03b241590ad14 2b0d7d9029630234e5d64263f0afd5ec` |
| `scripts/common.py` | 20306 | `eca1476518b173b8cf8dc4c9367d9465 641a57de385e23fc68fba81540db7300` |
| `scripts/make_test_clip.py` | 12481 | `22cb71c748f0ecbb002fe9c06c89eaae 5e397f572c4d1fff0e01916488513f84` |
| `scripts/qa_audio.py` | 27000 | `9be5d9762cfcd5c0546be22dff3daed4 3344057cfb61e16cc128a6b5f3f090d5` |
| `scripts/qa_compare.py` | 16263 | `c02e91764a68d6b68b16ed6fb246aa32 688709df79a3cfa6267c09e61e0cb349` |
| `scripts/qa_frames.py` | 28178 | `9565f2b14b54018ce7ed77ca624f8847 5f09ff33e757215b3b58985aae7bf7c1` |
| `scripts/qa_gif.py` | 25407 | `c8e8514ceb966aa7b36593b768b9bb75 e1700af0c1e968d6cc6dce40a32a7d99` |
| `scripts/qa_probe.py` | 58268 | `74c50b222294e420565ea333f4c87605 3b3825d9cf9e7f092e569a866d8e968b` |
| `scripts/run_qa.py` | 28574 | `cfaeaff2f44bdb511104bf8e8fe5c7e6 d8d6e0b24b01099b1b8f8ce130c852f8` |

## 3. Commands

Write every path in single quotes so that the shell changes nothing in it (an apostrophe inside is written `'\''` in bash and `''` in PowerShell). Use `python3` in place of `python` where that is the name, as `SKILL.md` says. In bash or sh:

```text
python -I -B - new <<'EOF'
<the program, every line exactly as in section 2>
EOF
```

```text
python -I -B - put '<FOLDER>' '<SAVED FILE>' scripts/qa_probe.py <<'EOF'
<the program>
EOF
```

```text
python -I -B - check '<FOLDER>' scripts/qa_probe.py scripts/common.py scripts/qa_audio.py <<'EOF'
<the program>
EOF
```

In Windows PowerShell 5.1 (the `@'` ends its line and the `'@` starts its line):

```text
@'
<the program, every line exactly as in section 2>
'@ | python -I -B - check '<FOLDER>' scripts/qa_probe.py scripts/common.py scripts/qa_audio.py
```

`new` and `put` take the same form. Do not run `new` from the temp folder itself.

## 4. Fetch, write and check the files

1. Run `new` and note the folder it prints.
2. Fetch each file of your row by its path in the file list. The answer starts with the line `## File: video-export-qa/<path> (from ...)` and a blank line; the file follows.
   - If the answer was saved to a file and you were given its path, run `put` with that path and the NAME. The saved file may hold plain text or JSON; `put` finds the file in it.
   - If the answer came back in the conversation, write the text after the header line and the blank line, exactly as shown, with your file tool (never a shell here-document, `echo` or `printf`) to `<FOLDER>/<NAME>`, for example `<FOLDER>/scripts/common.py`. Keep plain line ends (LF) and end the file with exactly one line end, as every file of the table does. Eight of the nine scripts contain backslashes, in escapes such as `\x1b`, `\n` or `\d` (`qa_gif.py` has none): write each backslash exactly as shown, never the character an escape stands for.
3. Run `check '<FOLDER>'` with every NAME of your row. Never run a script before `check` prints `OK <N> verified files and nothing else in <FOLDER>`, and run `check` again before each later run. Run `check` as a command of its own, read its `OK` lines, and only then run the script, as a separate command. Do not join the two with `;` or `&&`: a copy of the program that was cut short, or that arrived empty, can exit 0 without printing any `OK` line.

If `check` refuses a file you wrote with `<NAME> is not the published file (<N> bytes, published <M>)`, the difference points to the usual slips: N - M = -1, the final line end is missing; +1, one line end too many at the end; equal to the number of lines, Windows line ends (CR LF); 3, a byte order mark in front. Write that one file again with the fix and run `check` again, once. Any other difference, 0 included, -1 while the file does end with one line end, or +1 while it does not end with two line ends (one extra character anywhere also gives +1), means the text differs in another way (an escape such as `\n` or `\\` written as what it stands for is 1 shorter, `\x1b` 3 shorter): write the file once more, exactly as shown, and run `check` again; if it refuses again, stop. Each file you wrote by hand gets that one second try, whatever the difference. An older shape of the skill text carried every file as a `### File: video-export-qa/<path>` block in one long answer; `put` reads such a saved answer too.

## 5. Run

Run from the folder that holds the export, with the commands of `SKILL.md` and that folder as `<skill-dir>`, the path in single quotes, for example:

```text
python -I -B '<skill-dir>/scripts/qa_probe.py' export.mp4 --json
python -I -B '<skill-dir>/scripts/qa_audio.py' export.mp4 --json
```

Always keep `-B`: without it Python writes a `__pycache__` folder into `scripts/`, and the next `check` refuses. Running a script with `--help` prints its usage when the set is complete. Reports you write with `--out-dir` go to the user's folder, never into the temporary folder.

## 6. Delete

When the last run is done, delete the folder `new` printed (its name starts with `veqa_`) with your shell's own command for removing a folder and its contents, with the path in single quotes, and delete nothing else; compare the path with the `OK folder:` line before you run it. A delete command can exit 0 on a path it could not find, so then list the temp folder and confirm that the folder's name is gone:

```text
python -I -c "import os, tempfile; print(sorted(n for n in os.listdir(tempfile.gettempdir()) if n.startswith('veqa_')))"
```

Do not test the path you typed: a mistyped path makes both the delete and that test look fine. If the task goes on later (for example after the user edits and exports the barcode clip), keep the folder until the last run, or start again with `new`.

## 7. If anything is not as described

Stop and tell the user what was printed: a `REFUSED:` line (apart from the one second try for a file you wrote by hand, section 4), no `OK` line, a SyntaxError or IndentationError, a here-document warning, `unexpected EOF while looking for matching`, an answer that does not start with `## File: video-export-qa/`, or a message you do not understand. When a Windows program starts Git Bash's `sh` or `bash` with `-c` (measured with bash 5.3.9, started from Python and from Windows PowerShell 5.1), the command keeps only its first 8,186 characters; started from inside Git Bash, the whole command arrives. The program alone is 6,742 characters; when a command was cut that way, bash printed a here-document warning and Python a SyntaxError, or, with the cut inside a quoted path, `unexpected EOF while looking for matching`. Do not retry another way on your own, do not change or shorten the program, and do not write a loader of your own.

- On Windows the folder must lie inside your own user folder (normally `...\AppData\Local\Temp\`). If `new` prints a folder anywhere else, such as `C:\Temp` or a network share, other users may be able to change it after `check`: delete it, stop and tell the user. On Linux the program itself checks the owner and the permissions of the folder and of the temp folder.
- If no temp folder is usable, Python falls back to the working folder; the program then refuses (`the working folder is the temp folder, or no temp folder is usable`) and writes nothing.
- Known issue: a path with letters outside plain ASCII (an accented user name, for example) is printed as a quoted Python string such as `'...\\Jos\xe9\\AppData\\Local\\Temp\\veqa_ab12cd34'`. Each `\xe9` stands for one letter (here an e with an acute accent) and each double backslash for one backslash: turn it back into the real path for your commands and your file tool.
