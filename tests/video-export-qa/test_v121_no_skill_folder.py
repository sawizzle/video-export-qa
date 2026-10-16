# Tests for the no-skill-folder route: added in 1.2.1, rebuilt in 1.2.2 (documentation, tests and one version number; no
# script behaviour changed). references/no-skill-folder.md is for an agent that received the skill as TEXT and has no
# skill folder. In 1.2.1 it held a snippet that cut files out of one long saved answer of "### File:" blocks, and could
# add files to an existing folder. The tool that hands out skills as text changed shape: its first answer is now an index,
# each file is fetched on its own, and a long result is saved by the host (plain text or JSON) while a short one comes back
# in the conversation. 1.2.2 replaces the snippet with a program that makes a fresh private folder, writes a file only
# when its length and SHA-256 are the published ones, and checks the whole folder before any script runs.
#   * ImportGraphTests    the file sets in the document's table are derived from the real import graph and from what
#                         the scripts open at run time, so the table cannot drift when someone adds an import
#   * MinimalSetTests     each documented set is copied ALONE into an empty folder and run for real (help plus a run on
#                         a clip made here); leaving out any one file of a set makes it fail
#   * SimulatorTests      get_skill_sim.py: the first answer with its index, one result per file, the two saved forms
#                         (P1 plain text, P2 JSON), the older block shape, and the stand-in tool fake_get_skill.py
#   * RouteTests          the program is taken out of the document verbatim and every documented set is made through the
#                         route: put from P1 and from P2 saved results, hand-written inline results then check, one put
#                         from an older-shape answer, the stand-in tool end to end; the scripts then run from that folder.
#                         Negative controls: a wrong hash, a missing file, a planted module (shown to run without check),
#                         a foreign layout, an existing folder handed to the program, hand copies with the usual slips
#   * DocumentPinTests    the text in SKILL.md, README.md and the reference says what the code does
# The attacks on the program itself (folders, links, names, saved shapes, memory, shells) are in test_v122_extractor.py.
# Every scanner and every harness has a negative control, so a pass means something.
# Portability: ffmpeg and ffprobe are needed only by the tests that run a script on a clip; without them those tests
# skip with the reason below. The listing source (listing/) is read only when it is part of the checkout.
# Run:  python -I -B -m unittest discover -s tests/video-export-qa
import ast
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

import _route2  # noqa: E402  (the generator and the runner of the document's program, next to this file)
import get_skill_sim  # noqa: E402  (the helper next to this file)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "video-export-qa"
SCRIPTS = PACKAGE / "scripts"
REFERENCES = PACKAGE / "references"
EXAMPLES = PACKAGE / "examples"
DOC = REFERENCES / "no-skill-folder.md"
LISTING = ROOT / "listing" / "video-export-qa"
PROPOSAL = "update_v1.2.2_PROPOSAL.md"
SIM_SCRIPT = Path(__file__).resolve().parent / "get_skill_sim.py"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
SH = shutil.which("sh") or shutil.which("bash")
PS = shutil.which("powershell") if os.name == "nt" else None
NO_FFMPEG = "ffmpeg and ffprobe are required on PATH"
LISTING_ABSENT = "the listing source folder (listing/) is not part of this checkout; the public repository has only packages/ and tests/"

# The description line of SKILL.md was trigger-tested for 1.2.0 and must not change.
DESCRIPTION_LINE = ("description: Measures video, GIF and audio exports with ffprobe, ffmpeg and a built-in GIF parser. "
                    "Use when asked to check a delivery spec, prove which frames an edit kept, test constant frame rate, "
                    "measure GIF duration or find silent or too-quiet audio. Do not use for editing, trimming or converting.")

# Words the new text must not use to describe what a marketplace's own text around an answer says: that wording changed
# within days, so the package makes no claim about it and does not name the service or its tool.
WRAPPER_CLAIMS = (
    r"never write (any part of )?this skill",
    r"pip(e|ing) the script",
    r"\bpip(e|ed|es|ing)\b",
    r"short-lived",
    r"(do not|don't) (save|write) (it |them )?(to|into) (a |the )?(skills? |project )",
    r"load this skill again",
    r"in-session",
    r"get_skill",
    r"Agensi",
)

# What the pre-submit scan of the release tooling fails a package for: a recursive delete in any file of it.
RECURSIVE_DELETE = re.compile(r"rm\s+-rf|Remove-Item\b[^\n]*-Recurse|rmtree\(|del\s+/[sfq]")


def read(path):
    return Path(path).read_bytes().decode("ascii")


def wrapper_claims(text):
    return [pattern for pattern in WRAPPER_CLAIMS if re.search(pattern, text, re.I)]


# --------------------------------------------------------------------------- the table in the document

TABLE_ROW = re.compile(r"^\| (?P<task>[^|\n]+) \| (?P<run>[^|\n]+) \| (?P<files>[^|\n]+) \| (?P<size>\d+) KB \|$", re.M)


def documented_sets(text=None):
    """[(task, scripts to run, files to write, size in KB)] from the table in section 1 of the document."""
    text = read(DOC) if text is None else text
    section = text.split("## 1. ")[1].split("\n## ")[0]
    rows = []
    for match in TABLE_ROW.finditer(section):
        rows.append((match.group("task").strip(), re.findall(r"`([\w]+\.py)`", match.group("run")),
                     re.findall(r"`([\w]+\.py)`", match.group("files")), int(match.group("size"))))
    return rows


def kb(size):
    """Bytes in KB of 1,000 bytes, rounded to the nearest whole KB (the unit section 1 of the document states)."""
    return (size + 500) // 1000


GUIDE = "references/interpreting-results.md"
UNIT_SENTENCE = ("Size is what you would have to write, in KB of 1,000 bytes (the skill's file list counts KB of 1,024 bytes, "
                 "so its numbers are a little smaller).")


def size_problems(text):
    """Every size in section 1 of the document that is not the real size of the package files, in KB of 1,000 bytes:
    the Size column of each row, the sizes the text after the table repeats, and the guidance file."""
    section = text.split("## 1. ")[1].split("\n## ")[0]
    problems = []
    rows = documented_sets(text)
    for task, _run, files, size_kb in rows:
        real = sum((SCRIPTS / name).stat().st_size for name in files)
        if size_kb != kb(real):
            problems.append("%s: %d KB stated, %d bytes = %d KB" % (task, size_kb, real, kb(real)))
    sizes = {task: size for task, _run, _files, size in rows}
    real_rows = {task: kb(sum((SCRIPTS / name).stat().st_size for name in files)) for task, _run, files, _size in rows}
    audio = [t for t in real_rows if t.startswith("Loudness")]
    for label, pattern, real in (
            ("audio row in the text", r"one set of three files \((\d+) KB\)", real_rows[audio[0]] if audio else None),
            ("whole report in the text", r"The whole report needs seven files \((\d+) KB\)", real_rows.get("The whole report")),
            ("guidance file", r"`references/interpreting-results\.md` \((\d+) KB\)", kb(PACKAGE.joinpath(*GUIDE.split("/")).stat().st_size))):
        found = re.findall(pattern, section)
        if found != [str(real)]:
            problems.append("%s: %s stated, %s KB real" % (label, found, real))
    if UNIT_SENTENCE not in section:
        problems.append("the unit sentence (KB of 1,000 bytes, the file list's KB of 1,024 bytes) is missing")
    if len(sizes) != 7:
        problems.append("%d rows, 7 expected" % len(sizes))
    return problems


def local_imports(source, modules):
    """The local modules a Python source imports, by a walk of its syntax tree (a lazy import inside a function too)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found & modules


def import_graph():
    modules = {path.stem for path in SCRIPTS.glob("*.py")}
    return {path.stem: local_imports(read(path), modules) - {path.stem} for path in sorted(SCRIPTS.glob("*.py"))}


def closure(script, graph):
    """The script plus everything it imports, directly or not (module names without .py)."""
    needed, todo = set(), [script]
    while todo:
        name = todo.pop()
        if name not in needed:
            needed.add(name)
            todo.extend(graph[name])
    return needed


def set_problems(rows, graph):
    """Rows of the table whose file list is not exactly the closure of the scripts to run."""
    problems = []
    for task, run, files, _size in rows:
        needed = set()
        for script in run:
            needed |= closure(script.replace(".py", ""), graph)
        listed = {name.replace(".py", "") for name in files}
        if needed - listed:
            problems.append("%s: missing %s" % (task, sorted(needed - listed)))
        if listed - needed:
            problems.append("%s: not needed %s" % (task, sorted(listed - needed)))
    return problems


def constants_equal_to(source, text):
    return any(isinstance(node, ast.Constant) and node.value == text for node in ast.walk(ast.parse(source)))


# --------------------------------------------------------------------------- helpers that run things

def materialise(files, without=None):
    """Copy ONLY the listed scripts into a new empty folder (layout scripts/<file>); returns the folder."""
    folder = Path(tempfile.mkdtemp(prefix="veqa_test_set_"))
    (folder / "scripts").mkdir()
    for name in files:
        if name != without:
            shutil.copyfile(str(SCRIPTS / name), str(folder / "scripts" / name))
    return folder


def run_script(folder, script, *args, cwd):
    proc = subprocess.run([sys.executable, "-I", "-B", str(Path(folder) / "scripts" / script)] + [str(a) for a in args],
                          capture_output=True, cwd=str(cwd), env=_route2.child_env(tempfile.gettempdir()))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def ffmpeg(*args, cwd):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args],
                          capture_output=True, cwd=str(cwd))
    if proc.returncode != 0:
        raise AssertionError("fixture ffmpeg failed: %s\n%s" % (args, proc.stderr.decode("utf-8", "replace")))


def listing_of(folder):
    return sorted(str(p.relative_to(folder)).replace(os.sep, "/") for p in Path(folder).rglob("*") if p.is_file())


class Scratch:
    """A scratch folder for one test class, removed afterwards."""
    def __init__(self, test_case_class, prefix):
        self.path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        test_case_class.addClassCleanup(_route2.cleanup, self.path)

    def __truediv__(self, name):
        return self.path / name


# --------------------------------------------------------------------------- the import graph and the table

class ImportGraphTests(unittest.TestCase):
    def test_the_graph_walk_sees_imports_and_ignores_the_rest(self):
        modules = {"common", "barcode"}
        self.assertEqual(local_imports("import common\nimport os\nfrom barcode import x\n", modules), {"common", "barcode"})
        self.assertEqual(local_imports("def f():\n    import common\n", modules), {"common"}, "a lazy import counts")
        self.assertEqual(local_imports("import os, sys\nx = 'common'\n", modules), set())

    def test_the_real_graph_is_the_one_the_document_was_written_from(self):
        graph = import_graph()
        self.assertEqual(sorted(graph), ["barcode", "common", "make_test_clip", "qa_audio", "qa_compare", "qa_frames",
                                         "qa_gif", "qa_probe", "run_qa"])
        self.assertEqual(graph["qa_audio"], {"common", "qa_probe"})
        self.assertEqual(graph["run_qa"], {"common", "qa_audio", "qa_frames", "qa_gif", "qa_probe"})
        self.assertEqual(graph["common"], set())

    def test_no_script_loads_a_module_in_a_way_the_graph_walk_cannot_see(self):
        for path in sorted(SCRIPTS.glob("*.py")):
            with self.subTest(script=path.name):
                tree = ast.parse(read(path))
                names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
                imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                self.assertNotIn("__import__", names)
                self.assertNotIn("importlib", imported)
                self.assertNotIn("runpy", imported)

    def test_every_documented_set_is_exactly_the_closure_of_its_scripts(self):
        rows = documented_sets()
        self.assertEqual(len(rows), 7)
        self.assertEqual(set_problems(rows, import_graph()), [])

    def test_the_drift_check_fails_when_an_import_is_added_or_a_file_is_listed_without_need(self):
        graph = import_graph()
        rows = documented_sets()
        grown = dict(graph, qa_gif=graph["qa_gif"] | {"barcode"})   # somebody adds "import barcode" to qa_gif.py
        problems = set_problems(rows, grown)
        self.assertTrue([p for p in problems if p.startswith("GIF timing") and "barcode" in p], problems)
        shrunk = dict(graph, qa_compare=set())                       # or removes the import of common
        self.assertTrue([p for p in set_problems(rows, shrunk) if "not needed" in p])
        padded = [(t, r, f + ["qa_gif.py"] if t.startswith("Two files") else f, s) for t, r, f, s in rows]
        self.assertTrue([p for p in set_problems(padded, graph) if "not needed" in p])

    def test_every_script_the_table_names_exists_and_the_stated_sizes_are_close(self):
        for task, run, files, size_kb in documented_sets():
            with self.subTest(task=task):
                for name in run + files:
                    self.assertTrue((SCRIPTS / name).is_file(), name)
                self.assertLessEqual(set(run), set(files))
                real = sum((SCRIPTS / name).stat().st_size for name in files) / 1000.0
                self.assertLessEqual(abs(real - size_kb), max(1.5, real * 0.05), "%s: %.1f KB real, %d KB stated" % (task, real, size_kb))

    def test_every_size_in_section_1_is_the_real_size_in_kb_of_1000_bytes(self):
        # audit 2026-10-15 (D01, D07, D10): the sizes were true but only pinned within 5 per cent
        text = read(DOC)
        self.assertEqual(size_problems(text), [])
        # the unit matters: in KB of 1,024 bytes (the file list's unit) the frame-rate row would be 77, not 79
        rows = {task: sum((SCRIPTS / name).stat().st_size for name in files) for task, _run, files, _size in documented_sets(text)}
        probe = [size for task, size in rows.items() if task.startswith("Frame rate")][0]
        self.assertEqual((kb(probe), (probe + 512) // 1024), (79, 77))
        self.assertTrue(all((size + 512) // 1024 < kb(size) for size in rows.values()), "every row is smaller in KB of 1,024 bytes")
        # negative controls: each kind of wrong number is caught
        for old, new in (("| 79 KB |", "| 75 KB |"), ("(30 KB)", "(20 KB)"), ("| 199 KB |\n| The whole report with", "| 190 KB |\n| The whole report with"),
                         ("three files (106 KB)", "three files (105 KB)"), ("seven files (199 KB)", "seven files (200 KB)"),
                         ("KB of 1,000 bytes (", "KB of 1,024 bytes (")):
            with self.subTest(change=new):
                self.assertEqual(text.count(old), 1, old)
                self.assertTrue(size_problems(text.replace(old, new)), new)

    def test_only_run_qa_opens_a_file_from_references_and_the_document_says_what_is_lost_without_it(self):
        users = {path.stem for path in SCRIPTS.glob("*.py") if constants_equal_to(read(path), "references")}
        self.assertEqual(users, {"run_qa"})
        # negative control: the scan finds such a script
        self.assertTrue(constants_equal_to("p = os.path.join(d, 'references', 'x.md')", "references"))
        self.assertFalse(constants_equal_to("references = []", "references"))
        doc = read(DOC)
        self.assertIn("Only `run_qa.py` opens a file from `references/`", doc)
        self.assertIn("references/interpreting-results.md", read(SCRIPTS / "run_qa.py"))
        message = "No guidance is listed for this check id"
        self.assertIn(message, doc)
        self.assertIn(message, read(SCRIPTS / "run_qa.py"), "the document quotes the message the script prints")
        # the one file of references/ the program knows is exactly that file
        self.assertEqual([n for n in _route2.table() if n.startswith("references/")], ["references/interpreting-results.md"])


# --------------------------------------------------------------------------- each set, alone, for real

class MinimalSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise unittest.SkipTest(NO_FFMPEG)
        cls.data = Scratch(cls, "veqa_test_data_")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
               "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "clip_av.mp4", cwd=cls.data.path)
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-frames:v", "20", "anim.gif", cwd=cls.data.path)
        shutil.copyfile(str(cls.data / "clip_av.mp4"), str(cls.data / "clip_copy.mp4"))
        # the barcode clip comes from the full package: it is test data here, not the script under test
        code, _out, err = cls.full("make_test_clip.py", "barcode.mp4", "--frames", "24", "--fps", "24", "--size", "640x360")
        if code != 0:
            raise AssertionError("make_test_clip.py failed: " + err)

    @classmethod
    def full(cls, script, *args):
        proc = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args],
                              capture_output=True, cwd=str(cls.data.path))
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")

    def row(self, run_script_name, with_barcode=None):
        rows = [r for r in documented_sets() if run_script_name in r[1]
                and (with_barcode is None or ("--barcode-expect" in r[0]) == with_barcode)]
        self.assertEqual(len(rows), 1, (run_script_name, with_barcode))
        return rows[0]

    def isolated(self, files):
        folder = materialise(files)
        self.addCleanup(shutil.rmtree, str(folder), True)
        return folder

    def run_in(self, files, script, *args):
        return run_script(self.isolated(files), script, *args, cwd=self.data.path)

    def test_each_documented_set_starts_in_an_otherwise_empty_folder(self):
        for task, run, files, _size in documented_sets():
            folder = self.isolated(files)
            self.assertEqual(listing_of(folder), sorted("scripts/" + name for name in files), "nothing but the documented files")
            for script in run:
                with self.subTest(task=task, script=script):
                    code, out, err = run_script(folder, script, "--help", cwd=self.data.path)
                    self.assertEqual(code, 0, err)
                    self.assertIn("usage: %s" % script, out)

    def test_leaving_out_any_one_file_of_a_set_makes_the_set_fail(self):
        # negative control for every set: each file is needed by at least one script that the row runs
        for task, run, files, _size in documented_sets():
            for left_out in files:
                with self.subTest(task=task, left_out=left_out):
                    folder = materialise(files, without=left_out)
                    self.addCleanup(shutil.rmtree, str(folder), True)
                    graph = import_graph()
                    failed = 0
                    for script in run:
                        code, _out, err = run_script(folder, script, "--help", cwd=self.data.path)
                        if left_out == script:
                            self.assertNotEqual(code, 0)
                            self.assertIn("can't open file", err)
                        elif left_out.replace(".py", "") in closure(script.replace(".py", ""), graph):
                            self.assertNotEqual(code, 0)
                            self.assertIn("No module named", err)
                        else:
                            self.assertEqual(code, 0, "a script that does not need %s must still start" % left_out)
                        failed += code != 0
                    self.assertGreaterEqual(failed, 1, "the set still starts without " + left_out)

    def test_the_probe_set_measures_frame_rate_and_frame_count(self):
        _task, run, files, _size = self.row("qa_probe.py")
        code, out, err = self.run_in(files, "qa_probe.py", "clip_av.mp4", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        video = data["info"]["video"]
        self.assertEqual((video["counted_frames"], video["width"], video["height"]), (24, 320, 180))
        self.assertEqual(video["fps"], 24.0)
        self.assertEqual({c["id"]: c["status"] for c in data["checks"]}["cfr"], "PASS")
        # negative control: the same run with common.py left out fails before it reads the clip
        folder = materialise(files, without="common.py")
        self.addCleanup(shutil.rmtree, str(folder), True)
        code, out, err = run_script(folder, "qa_probe.py", "clip_av.mp4", "--json", cwd=self.data.path)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("No module named", err)

    def test_the_audio_set_measures_loudness_and_silence(self):
        _task, _run, files, _size = self.row("qa_audio.py")
        code, out, err = self.run_in(files, "qa_audio.py", "clip_av.mp4", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual({c["id"]: c["status"] for c in data["checks"]}["not_silent"], "PASS")
        proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", "clip_av.mp4", "-vn", "-af", "ebur128",
                               "-f", "null", "-"], capture_output=True, cwd=str(self.data.path))
        reading = re.findall(r"^\s+I:\s+(-?\d+\.\d) LUFS", proc.stderr.decode("utf-8", "replace"), re.M)
        self.assertTrue(reading, "ffmpeg itself gave no integrated loudness")
        self.assertAlmostEqual(data["info"]["integrated_lufs"], float(reading[-1]), delta=0.2)
        self.assertLess(data["info"]["integrated_lufs"], -10.0)
        self.assertEqual(data["info"]["audio_streams"][0]["leading_silence_s"], 0.0)
        # negative control: without qa_probe.py (qa_audio.py imports it) the set does not start
        folder = materialise(files, without="qa_probe.py")
        self.addCleanup(shutil.rmtree, str(folder), True)
        self.assertNotEqual(run_script(folder, "qa_audio.py", "clip_av.mp4", cwd=self.data.path)[0], 0)

    def test_the_whole_report_set_with_and_without_a_barcode_expectation(self):
        _task, _run, files, _size = self.row("run_qa.py", True)
        code, out, err = self.run_in(files, "run_qa.py", "barcode.mp4", "--barcode-expect", "0-23", "--format", "json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        rows = {c["id"]: c["status"] for c in data["checks"]}
        self.assertEqual((data["overall"], rows["expect"], rows["all_frames_decodable"]), ("PASS", "PASS", "PASS"))
        self.assertEqual(data["info"]["qa_frames"]["frames_decoded"], 24)
        # the same set without an expectation: no barcode rows at all
        _task, _run, plain_files, _size = self.row("run_qa.py", False)
        code, out, err = self.run_in(plain_files, "run_qa.py", "barcode.mp4", "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertNotIn("expect", {c["id"] for c in json.loads(out)["checks"]})
        # negative control: a wrong expectation is a FAIL through the same set, so the PASS above means something
        code, out, _err = self.run_in(files, "run_qa.py", "barcode.mp4", "--barcode-expect", "0-11", "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual({c["id"]: c["status"] for c in json.loads(out)["checks"]}["expect"], "FAIL")
        # negative control: leaving out barcode.py (qa_frames.py imports it) stops the run before it reads the file
        folder = materialise(files, without="barcode.py")
        self.addCleanup(shutil.rmtree, str(folder), True)
        code, out, err = run_script(folder, "run_qa.py", "barcode.mp4", cwd=self.data.path)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_the_references_file_is_optional_for_run_qa_and_only_the_guidance_lines_differ(self):
        _task, _run, files, _size = self.row("run_qa.py", True)
        spec = self.data / "spec_wrong_width.json"
        spec.write_text(json.dumps({"video": {"width": 1920}}), encoding="utf-8")
        folder = self.isolated(files)
        without = run_script(folder, "run_qa.py", "clip_av.mp4", "--spec", spec.name, cwd=self.data.path)
        (folder / "references").mkdir()
        shutil.copyfile(str(REFERENCES / "interpreting-results.md"), str(folder / "references" / "interpreting-results.md"))
        with_guide = run_script(folder, "run_qa.py", "clip_av.mp4", "--spec", spec.name, cwd=self.data.path)
        self.assertEqual((without[0], with_guide[0]), (1, 1), "the exit code is the same")
        self.assertIn("No guidance is listed for this check id in references/interpreting-results.md.", without[1])
        self.assertNotIn("No guidance is listed", with_guide[1])
        self.assertIn("Likely causes:", with_guide[1])
        self.assertNotIn("Likely causes:", without[1])

        def stable(text):
            """The verdict line and the table of checks: what must not depend on the guidance file."""
            overall = [line for line in text.splitlines() if line.startswith("**Overall")]
            table = [line for line in text.splitlines() if line.startswith("| PASS |") or line.startswith("| FAIL |")
                     or line.startswith("| WARN |") or line.startswith("| SKIP |")]
            return overall, table

        self.assertEqual(stable(without[1]), stable(with_guide[1]))
        self.assertGreater(len(stable(with_guide[1])[1]), 5)

    def test_the_barcode_clip_set_makes_a_clip_and_reads_the_frame_numbers_back(self):
        _task, run, files, _size = self.row("make_test_clip.py")
        self.assertEqual(run, ["make_test_clip.py", "qa_frames.py"])
        folder = self.isolated(files)
        code, _out, err = run_script(folder, "make_test_clip.py", "made.mp4", "--frames", "24", "--fps", "24", "--size", "640x360",
                                     cwd=self.data.path)
        self.assertEqual(code, 0, err)
        code, out, err = run_script(folder, "qa_frames.py", "made.mp4", "--expect", "0-23", "--json", cwd=self.data.path)
        self.assertEqual(code, 0, err)
        info = json.loads(out)["info"]
        self.assertEqual((info["frames_total"], info["frames_decoded"], info["first_index"], info["last_index"]), (24, 24, 0, 23))
        # negative control: a wrong expectation fails through the same set
        code, _out, _err = run_script(folder, "qa_frames.py", "made.mp4", "--expect", "0-11", "--json", cwd=self.data.path)
        self.assertEqual(code, 1)
        # negative control: the make_test_clip.py half needs barcode.py and common.py
        broken = materialise(files, without="barcode.py")
        self.addCleanup(shutil.rmtree, str(broken), True)
        code, _out, _err = run_script(broken, "make_test_clip.py", "never.mp4", cwd=self.data.path)
        self.assertNotEqual(code, 0)
        self.assertFalse((self.data / "never.mp4").exists())

    def test_the_gif_set_reads_the_frame_delays(self):
        _task, _run, files, _size = self.row("qa_gif.py")
        code, out, err = self.run_in(files, "qa_gif.py", "anim.gif", "--fps", "10", "--expect-frames", "20", "--json")
        self.assertEqual(code, 0, err)
        info = json.loads(out)["info"]
        self.assertEqual(info["frame_count"], 20)
        self.assertEqual(info["delays_cs"][:19], [10] * 19)
        # negative control: an expectation that the file does not meet fails
        code, _out, _err = self.run_in(files, "qa_gif.py", "anim.gif", "--fps", "10", "--expect-frames", "30", "--json")
        self.assertEqual(code, 1)

    def test_the_compare_set_tells_identical_files_from_different_ones(self):
        _task, _run, files, _size = self.row("qa_compare.py")
        code, out, err = self.run_in(files, "qa_compare.py", "clip_av.mp4", "clip_copy.mp4", "--json")
        self.assertEqual(code, 0, err)
        rows = {c["id"]: c["status"] for c in json.loads(out)["checks"]}
        self.assertEqual((rows["frame_count_equal"], rows["identical"]), ("PASS", "PASS"))
        # negative control: a different picture is not identical
        code, out, _err = self.run_in(files, "qa_compare.py", "clip_av.mp4", "barcode.mp4", "--json")
        self.assertEqual(code, 1)
        self.assertEqual({c["id"]: c["status"] for c in json.loads(out)["checks"]}["identical"], "FAIL")


# --------------------------------------------------------------------------- the simulator

def make_package(folder, files, skill="---\nname: demo\ndescription: x\n---\n\n# Demo\n"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_bytes(skill.encode("ascii"))
    for rel, data in files.items():
        path = folder / Path(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("ascii"))
    return folder


# Files that make the simulated answers hard on purpose: fences inside markdown, a markdown file that ends in a fence
# line, one with no final newline, an empty file, Windows line ends, and a LAST block with no final newline.
HARD_FILES = {
    "references/fenced.md": "# Notes\n\n```python\nx = 1\n```\n\nText between.\n\n```\nplain fence\n```\n\n### Not a file header\n",
    "references/ends_in_fence.md": "Last line is a fence:\n\n```\ncode\n```\n",
    "references/no_newline.md": "no final newline and a fence at the end\n```",
    "references/empty.md": "",
    "scripts/crlf.py": b"a = 1\r\nb = 2\r\n",
    "scripts/zlast.py": "print(1)",
}


def result_paths(sim, rel):
    base = Path(sim) / "results" / get_skill_sim.result_file_name(rel)
    return {"inline": Path(str(base) + ".txt"), "p1": Path(str(base) + ".p1.txt"), "p2": Path(str(base) + ".p2.json")}


def run_fake(sim, *args, extra_env=None):
    proc = subprocess.run([sys.executable, "-I", "-B", str(Path(sim) / "fake_get_skill.py")] + list(args), capture_output=True,
                          cwd=str(sim), env=_route2.child_env(tempfile.gettempdir(), extra_env))
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", "replace")


SAVED_MESSAGE = re.compile(r"(?:output saved to|full output saved to) (.+?)(?:\n|$)")


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch(cls, "veqa_test_sim_")
        cls.hard = make_package(cls.scratch / "hardpkg", HARD_FILES)

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, "-I", str(SIM_SCRIPT)] + [str(a) for a in args], capture_output=True)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")

    def test_the_command_line_writes_every_shape_for_any_package_folder(self):
        for package in (PACKAGE, self.hard):
            out = self.scratch / ("sim_%s" % package.name)
            code, stdout, err = self.run_cli(package, out)
            rels = get_skill_sim.package_files(package)
            with self.subTest(package=package.name):
                self.assertEqual((code, err), (0, ""))
                self.assertIn("%d file results" % len(rels), stdout)
                first = (out / "first_answer.txt").read_bytes().decode("utf-8")
                self.assertEqual(first, get_skill_sim.first_answer(package))
                self.assertIn((package / "SKILL.md").read_bytes().decode("ascii").rstrip("\n"), first, "SKILL.md is inline")
                self.assertNotRegex(first, r"(?m)^#{2,3} File: ", "the first answer of the current shape holds no file")
                index = re.findall(r"^- `([^`]+)` \((\d+\.\d) KB\)$", first, re.M)
                self.assertEqual([p for p, _kb in index], ["%s/%s" % (package.name, rel) for rel in rels])
                self.assertTrue(first.endswith("when SKILL.md tells you to."))
                for rel in rels:
                    paths = result_paths(out, rel)
                    raw = package.joinpath(*rel.split("/")).read_bytes()
                    result = paths["inline"].read_bytes()
                    head = ("## File: %s/%s (from %s)\n\n" % (package.name, rel, get_skill_sim.display_name(package.name))).encode("ascii")
                    self.assertEqual(result, head + raw, rel)
                    self.assertEqual(paths["p1"].read_bytes(), result)
                    doc = json.loads(paths["p2"].read_bytes().decode("ascii"))
                    self.assertEqual(doc, [{"type": "text", "text": result.decode("utf-8")}])
                    # the layout a real host wrote on 2026-10-15: indented by 2, no line end after the closing bracket
                    self.assertTrue(paths["p2"].read_bytes().startswith(b'[\n  {\n    "type": "text",\n    "text": "## File: '), rel)
                    self.assertTrue(paths["p2"].read_bytes().endswith(b'"\n  }\n]'), rel)
                old = (out / "old_shape_answer.txt").read_bytes().decode("utf-8")
                self.assertEqual(old, get_skill_sim.old_answer(package))
                self.assertTrue((out / "fake_get_skill.py").is_file())

    def test_the_index_gives_sizes_in_kb_of_1024_bytes_with_one_decimal(self):
        first = get_skill_sim.first_answer(PACKAGE)
        sizes = dict(re.findall(r"^- `video-export-qa/([^`]+)` \((\d+\.\d) KB\)$", first, re.M))
        for rel, kb in sizes.items():
            self.assertEqual(kb, "%.1f" % (PACKAGE.joinpath(*rel.split("/")).stat().st_size / 1024.0), rel)
        # the values the index of the real first answer gave for files that did not change (2026-10-15)
        self.assertEqual((sizes["scripts/qa_probe.py"], sizes["scripts/common.py"], sizes["scripts/run_qa.py"]), ("56.9", "19.8", "27.9"))

    def test_the_older_shape_is_the_file_between_two_plain_fence_lines(self):
        text = get_skill_sim.old_answer(PACKAGE)
        self.assertEqual(text, get_skill_sim.build(PACKAGE))
        parts = re.split(r"(?m)^### File: ", text)[1:]
        self.assertEqual(len(parts), len(get_skill_sim.package_files(PACKAGE)))
        for part in parts:
            head, _, body = part.partition("\n")
            rel = head.split("/", 1)[1]
            original = (PACKAGE / Path(*rel.split("/"))).read_bytes().decode("ascii")
            self.assertEqual(body.rstrip("\n"), "\n```\n" + original + "\n```", rel)
        self.assertFalse(text.endswith("\n"), "the last block has no final newline")
        self.assertTrue(get_skill_sim.old_answer(self.hard).endswith("\n```"))

    def test_the_stand_in_tool_prints_short_results_and_saves_long_ones_as_a_host_does(self):
        sim = self.scratch / "sim_fake"
        get_skill_sim.write_simulation(PACKAGE, str(sim))
        # a short result comes back as text: the header line, a blank line, the exact file
        code, out, err = run_fake(sim, "--file", "video-export-qa/scripts/common.py")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.encode("utf-8"), result_paths(sim, "scripts/common.py")["inline"].read_bytes())
        self.assertEqual(run_fake(sim, "--file", "scripts/common.py")[1], out, "the path without the skill folder works too")
        # a result over 50,000 characters is saved as JSON (P2), one over 30,000 as plain text (P1)
        code, out, err = run_fake(sim, "--file", "video-export-qa/scripts/qa_probe.py")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("Output too large ("), out)
        saved = Path(SAVED_MESSAGE.search(out).group(1))
        self.assertEqual(saved.suffix, ".json")
        self.assertEqual(saved.read_bytes(), result_paths(sim, "scripts/qa_probe.py")["p2"].read_bytes())
        self.assertEqual(saved.parent, sim / "tool-results")
        code, out, err = run_fake(sim)
        self.assertEqual(code, 0, err)
        self.assertIn("exceeds maximum allowed tokens", out)
        self.assertIn("Format: Plain text", out)
        saved = Path(SAVED_MESSAGE.search(out).group(1))
        self.assertEqual(saved.read_bytes().decode("utf-8"), get_skill_sim.first_answer(PACKAGE))
        # an unknown path gets two short sentences and exit 1
        code, out, _err = run_fake(sim, "--file", "video-export-qa/scripts/nope.py")
        self.assertEqual(code, 1)
        self.assertIn("is not in this skill", out)
        # negative control: with a higher limit the same long result comes back as text
        roomy = self.scratch / "sim_roomy"
        get_skill_sim.write_simulation(PACKAGE, str(roomy), limit=10 ** 7)
        code, out, _err = run_fake(roomy, "--file", "video-export-qa/scripts/qa_probe.py")
        self.assertEqual(out.encode("utf-8"), result_paths(roomy, "scripts/qa_probe.py")["inline"].read_bytes())
        self.assertFalse((roomy / "tool-results").exists())

    def test_the_wording_around_the_files_is_neutral_filler(self):
        texts = (get_skill_sim.HEADER + get_skill_sim.MIDDLE + get_skill_sim.FIRST_HEADER + get_skill_sim.INDEX_HEADER
                 + get_skill_sim.INDEX_END.replace("fake_get_skill.py", "the stand-in tool"))
        self.assertEqual(wrapper_claims(texts), [])
        self.assertIn("python fake_get_skill.py --file <path>", get_skill_sim.INDEX_END, "the end sentence names the stand-in")
        # negative control: the scan finds the tool name
        self.assertTrue(wrapper_claims(get_skill_sim.INDEX_END))

    def test_the_command_line_refuses_bad_arguments(self):
        out = self.scratch / "never_written"
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli(PACKAGE)[0], 2)
        self.assertEqual(self.run_cli(self.scratch.path, out)[0], 2, "a folder without SKILL.md")
        self.assertEqual(self.run_cli(PACKAGE, out, "--inline-limit", "x")[0], 2)
        code, _stdout, err = self.run_cli(self.hard, self.hard / "inside")
        self.assertEqual(code, 2)
        self.assertIn("must not be inside", err)
        busy = self.scratch / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_bytes(b"x")
        code, _stdout, err = self.run_cli(PACKAGE, busy)
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)
        self.assertEqual(listing_of(busy), ["keep.txt"])
        self.assertFalse(out.exists())
        self.assertFalse((self.hard / "inside").exists())


# --------------------------------------------------------------------------- the route, with the document's program

def doc_section(number):
    return read(DOC).split("## %d. " % number)[1].split("\n## ")[0]


def inline_text(result_bytes):
    """What the document says to write for a result that came back as text: everything after the header line and the
    blank line."""
    head, sep, rest = result_bytes.partition(b"\n\n")
    assert sep and head.startswith(b"## File: video-export-qa/"), head[:80]
    return rest


class RouteTests(_route2.Scratch):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sim = cls.root / "sim"
        get_skill_sim.write_simulation(PACKAGE, str(cls.sim))
        cls.data = cls.root / "data"
        cls.data.mkdir()
        if FFMPEG and FFPROBE:
            ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i",
                   "sine=frequency=440:sample_rate=48000", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "export.mp4", cwd=cls.data)

    def sets(self):
        return [(task, run, ["scripts/" + f for f in files]) for task, run, files, _size in documented_sets()]

    def check_ok(self, folder, names):
        r = self.x("check", folder, *names)
        self.assertEqual(r.code, 0, r)
        self.assertTrue(r.out.endswith("OK %d verified files and nothing else in %s\n" % (len(names), folder)), r)
        for name in names:
            self.assertIn("OK %s verified (%d bytes)\n" % (name, _route2.table()[name][1]), r.out)
        self.assertExact(folder, names)

    def help_runs(self, folder, scripts):
        for script in scripts:
            code, out, err = run_script(folder, script, "--help", cwd=self.data)
            self.assertEqual(code, 0, err)
            self.assertIn("usage: %s" % script, out)

    def test_every_documented_set_comes_out_through_put_from_saved_results_of_both_forms(self):
        for form in ("p1", "p2"):
            for task, run, names in self.sets():
                with self.subTest(form=form, task=task):
                    folder = self.new()
                    for name in names:
                        r = self.x("put", folder, result_paths(self.sim, name)[form], name)
                        self.assertEqual((r.code, r.out), (0, "OK %s verified (%d bytes)\n" % (name, _route2.table()[name][1])), r)
                    self.check_ok(folder, names)
                    self.help_runs(folder, run)

    def test_every_documented_set_comes_out_from_results_written_by_hand_then_check(self):
        # section 4: a result that came back as text is written, after its header line and the blank line, exactly as shown
        for task, run, names in self.sets():
            with self.subTest(task=task):
                folder = self.new()
                for name in names:
                    text = inline_text(result_paths(self.sim, name)["inline"].read_bytes())
                    folder.joinpath(*name.split("/")).write_bytes(text)
                self.check_ok(folder, names)
                self.help_runs(folder, run)

    def test_the_guidance_file_goes_through_the_route_too(self):
        name = "references/interpreting-results.md"
        for form in ("p1", "p2", "inline"):
            with self.subTest(form=form):
                folder = self.new()
                if form == "inline":
                    folder.joinpath(*name.split("/")).write_bytes(inline_text(result_paths(self.sim, name)["inline"].read_bytes()))
                else:
                    self.assertEqual(self.x("put", folder, result_paths(self.sim, name)[form], "video-export-qa/" + name).code, 0)
                self.check_ok(folder, [name])

    def test_one_put_with_an_older_shape_answer_writes_a_whole_set(self):
        saved = self.sim / "old_shape_answer.txt"
        names = sorted(_route2.table())
        folder = self.new()
        r = self.x("put", folder, saved, *names)
        self.assertEqual(r.code, 0, r)
        self.check_ok(folder, names)
        self.help_runs(folder, ["run_qa.py", "make_test_clip.py", "qa_compare.py"])

    @unittest.skipUnless(FFMPEG and FFPROBE, NO_FFMPEG)
    def test_an_agent_with_the_stand_in_tool_answers_the_frame_rate_and_silence_question(self):
        # the task of the fresh-buyer test: is the frame rate constant, what are the frame rate and the size, is the audio
        # silent or too quiet. The smallest row that answers it is the audio row (it holds the probe row).
        sim = self.root / "sim_agent"
        get_skill_sim.write_simulation(PACKAGE, str(sim))
        names = [["scripts/" + x for x in f] for t, _r, f, _s in documented_sets() if t.startswith("Loudness")][0]
        folder = self.new()
        forms = []
        for name in names:
            code, out, err = run_fake(sim, "--file", "video-export-qa/" + name)
            self.assertEqual(code, 0, err)
            if out.startswith("## File: "):                           # inline: write it with a file tool, as shown
                folder.joinpath(*name.split("/")).write_bytes(inline_text(out.encode("utf-8")))
                forms.append("inline")
            else:                                                     # saved: put reads the saved file
                saved = SAVED_MESSAGE.search(out).group(1)
                r = self.x("put", folder, saved, name)
                self.assertEqual(r.code, 0, r)
                forms.append("saved")
        self.assertEqual(sorted(forms), ["inline", "inline", "saved"], "qa_probe.py is the one result over the limit")
        self.check_ok(folder, names)
        got = {}
        for script in ("qa_probe.py", "qa_audio.py"):
            code, out, err = run_script(folder, script, "export.mp4", "--json", cwd=self.data)
            self.assertEqual(code, 0, err)
            got[script] = json.loads(out)
        video = got["qa_probe.py"]["info"]["video"]
        self.assertEqual((video["counted_frames"], video["width"], video["height"], video["fps"]), (24, 320, 180, 24.0))
        self.assertEqual({c["id"]: c["status"] for c in got["qa_probe.py"]["checks"]}["cfr"], "PASS")
        self.assertEqual({c["id"]: c["status"] for c in got["qa_audio.py"]["checks"]}["not_silent"], "PASS")
        # the same numbers as the installed package gives
        code, out, err = run_script(PACKAGE, "qa_audio.py", "export.mp4", "--json", cwd=self.data)
        self.assertEqual(json.loads(out)["info"]["integrated_lufs"], got["qa_audio.py"]["info"]["integrated_lufs"])

    @unittest.skipUnless(FFMPEG and FFPROBE, NO_FFMPEG)
    def test_run_qa_through_the_route_finds_the_guidance_only_when_it_was_written(self):
        names = [["scripts/" + x for x in f] for t, _r, f, _s in documented_sets() if t == "The whole report"][0]
        spec = self.data / "spec_width.json"
        spec.write_text(json.dumps({"video": {"width": 1920}}), encoding="utf-8")
        reports = {}
        for with_guide in (False, True):
            folder = self.new()
            wanted = names + (["references/interpreting-results.md"] if with_guide else [])
            self.assertEqual(self.x("put", folder, self.sim / "old_shape_answer.txt", *wanted).code, 0)
            self.check_ok(folder, wanted)
            code, out, err = run_script(folder, "run_qa.py", "export.mp4", "--spec", spec.name, cwd=self.data)
            self.assertEqual(code, 1, err)
            reports[with_guide] = out
        self.assertIn("No guidance is listed for this check id", reports[False])
        self.assertIn("Likely causes:", reports[True])
        self.assertNotIn("No guidance is listed", reports[True])

    def test_hand_copies_with_the_usual_slips_are_refused_by_check_with_the_size_the_document_explains(self):
        name = "scripts/common.py"
        exact = PACKAGE.joinpath(*name.split("/")).read_bytes()
        lines = exact.count(b"\n")
        cases = {"the final line end is missing": (exact[:-1], -1), "one line end too many": (exact + b"\n", 1),
                 "Windows line ends": (exact.replace(b"\n", b"\r\n"), lines), "a byte order mark": (b"\xef\xbb\xbf" + exact, 3)}
        doc = doc_section(4)
        for label, (data, difference) in cases.items():
            with self.subTest(slip=label):
                folder = self.new()
                folder.joinpath(*name.split("/")).write_bytes(data)
                r = self.x("check", folder, name)
                self.assertRefused(r, "%s is not the published file (%d bytes, published %d)" % (name, len(exact) + difference, len(exact)))
                # what put does with a saved copy that has the slip (program behaviour only: the note no longer offers put
                # as a repair, round-3 re-audit 2026-10-16): it refuses a folder that still holds the refused file, and
                # writes the published file once that file is gone; it leaves the saved copy as it is
                saved = self.saved(None, raw=data)
                self.assertRefused(self.x("put", folder, saved, name),
                                   "%s is not the published file (%d bytes, published %d)" % (name, len(exact) + difference, len(exact)))
                folder.joinpath(*name.split("/")).unlink()                # delete only the refused file
                r = self.x("put", folder, saved, name)
                self.assertEqual(r.code, 0, r)
                self.assertExact(folder, [name])
                self.assertEqual(self.x("check", folder, name).code, 0)
                self.assertEqual(saved.read_bytes(), data, "put never deletes or changes the copy it read")
        for words in ("N - M = -1, the final line end is missing", "+1, one line end too many at the end",
                      "equal to the number of lines, Windows line ends (CR LF)", "3, a byte order mark in front",
                      "Write that one file again with the fix and run `check` again, once. Any other difference, 0 included,",
                      "Any other difference, 0 included, -1 while the file does end with one line end, or +1 while it does not end "
                      "with two line ends (one extra character anywhere also gives +1), means the text differs "
                      "in another way (an escape such as `\\n` or `\\\\` written as what it stands for is 1 shorter, `\\x1b` 3 "
                      "shorter): write the file once more, exactly as shown, and run `check` again; if it refuses again, stop.",
                      "Each file you wrote by hand gets that one second try, whatever the difference."):
            self.assertIn(words, doc)
        self.assertNotIn("the difference says why", doc, "+1 has more than one cause")
        self.assertNotIn("repairs exactly these slips", doc, "put repairs more than these slips")
        # the put repair route is gone (round-3 re-audit 2026-10-16): with two hand-written files that carry the same slip,
        # check names only the first, and put after deleting that one is refused because of the other (measured below)
        for gone in ("can repair these slips", "private working folder", "delete only the refused file", "Delete the copy"):
            self.assertNotIn(gone, read(DOC))
        both = self.new()
        audio = "scripts/qa_audio.py"
        audio_exact = PACKAGE.joinpath(*audio.split("/")).read_bytes()
        both.joinpath(*name.split("/")).write_bytes(exact.replace(b"\n", b"\r\n"))
        both.joinpath(*audio.split("/")).write_bytes(audio_exact.replace(b"\n", b"\r\n"))
        self.assertRefused(self.x("check", both, name, audio), "%s is not the published file" % name)
        both.joinpath(*name.split("/")).unlink()
        self.assertRefused(self.x("put", both, self.saved(None, raw=exact.replace(b"\n", b"\r\n")), name),
                           "%s is not the published file (%d bytes, published %d)" % (audio, len(audio_exact) + audio_exact.count(b"\n"), len(audio_exact)))
        self.assertFalse(both.joinpath(*name.split("/")).exists(), "put wrote nothing")
        # one extra character anywhere gives the same refusal as a doubled final line end (review 2026-10-16)
        doubled = self.new()
        doubled.joinpath(*name.split("/")).write_bytes(exact + b"\n")
        expected = self.x("check", doubled, name)
        for label, data in (("an X at the end", exact + b"X"), ("an X in the middle", exact[:5000] + b"X" + exact[5000:]),
                            ("a line end in the middle", exact[:5000] + b"\n" + exact[5000:])):
            with self.subTest(slip=label):
                folder = self.new()
                folder.joinpath(*name.split("/")).write_bytes(data)
                r = self.x("check", folder, name)
                self.assertEqual((r.code, r.out, r.err), (expected.code, expected.out, expected.err))
                self.assertRefused(r, "%s is not the published file (%d bytes, published %d)" % (name, len(exact) + 1, len(exact)))
        # the other differences (audit 2026-10-15: N - M = 0 was not explained; review: -1 is also what an escape slip
        # gives). Each is measured here; check names the two sizes, put repairs none of them (they are not line-end or
        # byte-order-mark slips), so the one second try and then stop is the rule
        clip = "scripts/make_test_clip.py"
        clip_exact = PACKAGE.joinpath(*clip.split("/")).read_bytes()
        other = {"one letter changed": (name, exact[:1000] + (b"y" if exact[1000:1001] != b"y" else b"z") + exact[1001:], 0),
                 "one character too many": (name, exact[:1000] + b"Q" + exact[1000:], 1),
                 "\\n written as a line end": (name, exact.replace(b"\\n", b"\n", 1), -1),
                 "\\x1b written as ESC": (name, exact.replace(b"\\x1b", b"\x1b", 1), -3),
                 "\\\\ written as one backslash": (clip, clip_exact.replace(b"\\\\", b"\\", 1), -1)}
        for label, (target, data, difference) in other.items():
            published = PACKAGE.joinpath(*target.split("/")).read_bytes()
            with self.subTest(slip=label):
                self.assertNotEqual(data, published)
                self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), "the file still ends with one line end")
                folder = self.new()
                folder.joinpath(*target.split("/")).write_bytes(data)
                self.assertRefused(self.x("check", folder, target), "%s is not the published file (%d bytes, published %d)"
                                   % (target, len(published) + difference, len(published)))
                fresh = self.new()
                self.assertRefused(self.x("put", fresh, self.saved(None, raw=data), target), "holds no exact copy of " + target)
                self.assertExact(fresh, [])
        # every file the program knows ends with exactly one plain line end, as the document says
        for table_name in _route2.table():
            data = PACKAGE.joinpath(*table_name.split("/")).read_bytes()
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n") and b"\r" not in data, table_name)

    def test_an_escape_turned_into_its_character_is_refused(self):
        # a file tool can turn the four characters \x1b into the one character they name: never accepted
        name = "scripts/common.py"
        exact = PACKAGE.joinpath(*name.split("/")).read_bytes()
        self.assertIn(b"\\x1b", exact)
        changed = exact.replace(b"\\x1b", b"\x1b", 1)
        folder = self.new()
        folder.joinpath(*name.split("/")).write_bytes(changed)
        self.assertRefused(self.x("check", folder), "is not the published file")
        self.assertRefused(self.x("put", self.new(), self.saved(None, raw=b"## File: video-export-qa/scripts/common.py\n\n" + changed), name),
                           "holds no exact copy of scripts/common.py")
        # the generator flags the small files with \x, \u, \N{ escapes or a double backslash (its own warning list)
        risky = sorted(w.split(" ")[0].split("/")[1] for w in _route2.gen_table.inline_risks(PACKAGE, _route2.table()))
        self.assertEqual(risky, ["barcode.py", "common.py", "make_test_clip.py"])
        # what the document says is broader and true of the real files (audit 2026-10-15: the old sentence named only three
        # files, while run_qa.py, qa_audio.py, qa_frames.py and others hold backslash sequences too)
        sentence = ("Eight of the nine scripts contain backslashes, in escapes such as `\\x1b`, `\\n` or `\\d` (`qa_gif.py` has none): "
                    "write each backslash exactly as shown, never the character an escape stands for.")
        self.assertIn(sentence, doc_section(4))
        scripts = {path.name: path.read_bytes() for path in sorted(SCRIPTS.glob("*.py"))}
        with_backslash = sorted(n for n, data in scripts.items() if b"\\" in data)
        self.assertEqual((len(scripts), len(with_backslash)), (9, 8))
        self.assertEqual(sorted(set(scripts) - set(with_backslash)), ["qa_gif.py"])
        for escape in (b"\\x1b", b"\\n", b"\\d"):
            self.assertTrue([n for n, data in scripts.items() if escape in data], escape)
        self.assertNotIn(b"\\", PACKAGE.joinpath(*GUIDE.split("/")).read_bytes(), "the guidance file has none either")
        self.assertNotIn("`common.py`, `barcode.py` and `make_test_clip.py` contain", read(DOC))

    # ----------------------------------------------------------------------- negative controls

    def test_a_wrong_hash_in_the_table_refuses_the_real_file(self):
        name = "scripts/qa_gif.py"
        digest, size = _route2.table()[name]
        bad_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
        old_line = '"%s": ("%s" "%s", %d),' % (name, digest[:32], digest[32:], size)
        self.assertEqual(self.src.count(old_line), 1)
        bad = self.src.replace(old_line, '"%s": ("%s" "%s", %d),' % (name, bad_digest[:32], bad_digest[32:], size))
        folder = self.new(src=bad)
        self.assertRefused(self.x("put", folder, result_paths(self.sim, name)["p1"], name, src=bad), "holds no exact copy of " + name)
        folder.joinpath(*name.split("/")).write_bytes(PACKAGE.joinpath(*name.split("/")).read_bytes())
        self.assertRefused(self.x("check", folder, name, src=bad), "is not the published file")
        # and the document's own program accepts that same folder
        self.assertEqual(self.x("check", folder, name).code, 0)

    def test_a_missing_file_is_refused_by_check(self):
        folder = self.new()
        self.assertEqual(self.x("put", folder, result_paths(self.sim, "scripts/qa_probe.py")["p2"], "scripts/qa_probe.py").code, 0)
        self.assertRefused(self.x("check", folder, "scripts/qa_probe.py", "scripts/common.py"), "missing: scripts/common.py")

    def test_a_planted_module_is_refused_by_check_and_would_have_run_without_it(self):
        names = ["scripts/qa_probe.py", "scripts/common.py"]
        folder = self.new()
        self.assertEqual(self.x("put", folder, self.sim / "old_shape_answer.txt", *names).code, 0)
        marker = self.root / ("CANARY_" + uuid.uuid4().hex[:6])
        # common.py imports platform after the scripts folder is on sys.path, so a planted platform.py would run
        (folder / "scripts" / "platform.py").write_text(_route2.canary_code(marker, "planted"))
        self.assertRefused(self.x("check", folder, *names), "holds 'scripts/platform.py'")
        self.assertRefused(self.x("put", folder, self.sim / "old_shape_answer.txt", "scripts/qa_gif.py"), "holds 'scripts/platform.py'")
        self.assertFalse(marker.exists(), "check never imports anything from the folder")
        # negative control: why check comes first. Run without it, the planted module runs as the user
        run_script(folder, "qa_probe.py", "--help", cwd=self.data)
        self.assertTrue(marker.exists(), "a planted module next to the scripts is imported by a plain run")

    def test_a_foreign_layout_is_refused(self):
        import tempfile as _tempfile
        name = "scripts/qa_gif.py"
        cases = []
        # what the 1.2.1 snippet made: tempfile.mkdtemp(prefix="veqa_") with scripts/ only
        old = Path(_tempfile.mkdtemp(prefix="veqa_", dir=str(self.tmp)))
        (old / "scripts").mkdir()
        cases.append(("the 1.2.1 layout (no references folder)", old, "must hold only references, scripts"))
        f = self.new(); (f / "examples").mkdir(); cases.append(("an examples folder", f, "must hold only references, scripts"))
        f = self.new(); (f / "SKILL.md").write_bytes(b"x"); cases.append(("a file at the top", f, "must hold only"))
        f = self.new(); (f / "references" / "pitfalls.md").write_bytes(PACKAGE.joinpath("references", "pitfalls.md").read_bytes())
        cases.append(("a reference the program does not know", f, "holds 'references/pitfalls.md'"))
        f = self.new(); (f / "scripts" / "__pycache__").mkdir(); cases.append(("a bytecode folder", f, "- run scripts with -B"))
        for label, folder, needle in cases:
            with self.subTest(case=label):
                self.assertRefused(self.x("check", folder), needle)
                self.assertRefused(self.x("put", folder, result_paths(self.sim, name)["p1"], name), needle)
                self.assertFalse(folder.joinpath(*name.split("/")).exists(), "put wrote nothing")

    def test_an_existing_folder_is_never_handed_to_the_program(self):
        # the security lesson of 2026-10-15: a snippet that accepted the path of an EXISTING folder in the shared temp
        # folder let another user plant a module that the next script run imported. new takes no folder at all, and put
        # and check accept only a folder of the shape new makes, owned by the user, with nothing else in it.
        planted = self.tmp / "veqa_plantd01"
        (planted / "scripts").mkdir(parents=True)
        (planted / "references").mkdir()
        marker = self.root / "CANARY_EXISTING"
        (planted / "scripts" / "json.py").write_text(_route2.canary_code(marker, "existing"))
        if _route2.POSIX:
            for p in (planted, planted / "scripts", planted / "references"):
                os.chmod(str(p), 0o700)
        before = _route2.snapshot(self.root)
        for args in (["new", planted], ["new", "new"], [result_paths(self.sim, "scripts/qa_gif.py")["p1"], planted, "scripts/qa_gif.py"],
                     [result_paths(self.sim, "scripts/qa_gif.py")["p1"], "new", "scripts/qa_gif.py"]):
            r = self.x(*args)
            with self.subTest(args=[str(a)[-20:] for a in args]):
                self.assertEqual((r.code, r.out), (2, ""), r)
                self.assertTrue(r.err.startswith("usage: python -I -B - new | put FOLDER SAVED NAME... | check FOLDER [NAME...]"), r)
        self.assertRefused(self.x("check", planted), "holds 'scripts/json.py'")
        self.assertRefused(self.x("put", planted, result_paths(self.sim, "scripts/qa_gif.py")["p1"], "scripts/qa_gif.py"), "holds 'scripts/json.py'")
        self.assertEqual(_route2.snapshot(self.root), before, "nothing was written anywhere")
        self.assertFalse(marker.exists())
        # the old snippet is gone from the document
        doc = read(DOC)
        self.assertNotIn("tempfile.mkdtemp(prefix=\"veqa_\")", doc)
        self.assertNotIn("re.split(r\"(?m)^### File: \", text)", doc)
        self.assertNotIn("the path it printed to add files to that folder", doc)

    # ----------------------------------------------------------------------- the commands of section 3, as written

    def command_blocks(self):
        return re.findall(r"^```text\n(.*?)^```$", doc_section(3), re.S | re.M)

    def fill(self, block, program, folder=None, saved=None, exe=None, name=None):
        """A command block of section 3 with its placeholders filled in: only the first line (the command) changes,
        and the placeholder line becomes the program, character for character."""
        first, rest = block.split("\n", 1)
        if folder is not None:
            first = first.replace("'<FOLDER>'", "'%s'" % str(folder).replace("'", "'\\''"))
        if saved is not None:
            first = first.replace("'<SAVED FILE>'", "'%s'" % str(saved).replace("'", "'\\''"))
        if name is not None:
            first = first.replace("scripts/qa_probe.py", name)
        first = first.replace("python -I -B -", exe + " -I -B -")
        rest = re.sub(r"^<the program[^>\n]*>$", lambda m: program.rstrip("\n"), rest, flags=re.M)
        return first + "\n" + rest

    @unittest.skipUnless(SH, "no sh on this machine")
    def test_the_bash_commands_of_section_3_work_as_written(self):
        blocks = self.command_blocks()
        bash = [b for b in blocks if "<<'EOF'" in b]
        self.assertEqual([b.split(" ")[4] for b in bash], ["new", "put", "check"])
        exe = '"%s"' % Path(sys.executable).as_posix()

        def sh(text):
            proc = subprocess.run([SH, "-c", text], capture_output=True, cwd=str(self.work), env=_route2.child_env(self.tmp), timeout=300)
            return proc.returncode, proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")

        code, out, err = sh(self.fill(bash[0], self.src, exe=exe))
        self.assertEqual((code, err), (0, ""), out)
        folder = _route2.Result(code, out, err).folder()
        self.assertIsNotNone(folder, out)
        names = ["scripts/qa_probe.py", "scripts/common.py", "scripts/qa_audio.py"]
        for name in names:
            # the folder exactly as new printed it (backslashes on Windows), in single quotes
            code, out, err = sh(self.fill(bash[1], self.src, folder, result_paths(self.sim, name)["p2"], exe=exe, name=name))
            self.assertEqual((code, err), (0, ""), out)
        code, out, err = sh(self.fill(bash[2], self.src, folder, exe=exe))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("OK 3 verified files and nothing else in", out)
        self.assertExact(folder, names)

    @unittest.skipUnless(PS, "Windows PowerShell is not on this machine")
    def test_the_powershell_command_of_section_3_works_as_written(self):
        blocks = [b for b in self.command_blocks() if b.startswith("@'")]
        self.assertEqual(len(blocks), 1)
        folder = self.new()
        names = ["scripts/qa_probe.py", "scripts/common.py", "scripts/qa_audio.py"]
        self.assertEqual(self.x("put", folder, self.sim / "old_shape_answer.txt", *names).code, 0)
        script = blocks[0].replace("<the program, every line exactly as in section 2>", self.src.rstrip("\n"))
        script = script.replace("'<FOLDER>'", "'%s'" % str(folder).replace("'", "''")).replace("| python ", '| & "%s" ' % sys.executable)
        check = script

        def powershell(text):
            argv = [PS, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(text.encode("utf-16-le")).decode("ascii")]
            proc = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=300, env=_route2.child_env(self.tmp))
            return proc.returncode, proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")

        code, out, err = powershell(check + "\nexit $LASTEXITCODE\n")
        self.assertEqual(code, 0, err)
        self.assertIn("OK 3 verified files and nothing else in", out)

    def test_the_listing_command_of_section_6_shows_the_folder_until_it_is_deleted(self):
        command = re.search(r'^python -I -c "(.*)"$', doc_section(6), re.M).group(1)
        folder = self.new()

        def listed():
            proc = subprocess.run([sys.executable, "-I", "-c", command], capture_output=True, env=_route2.child_env(self.tmp))
            return ast.literal_eval(proc.stdout.decode("ascii").strip())

        self.assertEqual(listed(), [folder.name])
        _route2.cleanup(folder)
        self.assertEqual(listed(), [])
        self.assertIn("starts with `veqa_`", doc_section(6))
        self.assertIn('PREFIX, TABLE = "video-export-qa", "veqa_"', self.src)


# --------------------------------------------------------------------------- the words in the package

def package_texts():
    return {str(p.relative_to(PACKAGE)).replace(os.sep, "/"): read(p) for p in sorted(PACKAGE.rglob("*")) if p.is_file()}


class DocumentPinTests(unittest.TestCase):
    def skill(self):
        return read(PACKAGE / "SKILL.md")

    def test_the_version_is_1_2_2_everywhere_the_package_states_it(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import common
        finally:
            sys.path.remove(str(SCRIPTS))
        self.assertEqual(common.VERSION, "1.2.2")
        for name in ("sample_report.md", "example_session.md"):
            self.assertIn("run_qa.py 1.2.2 on Python", read(EXAMPLES / name))
        for name, text in package_texts().items():
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"(?<![\d.])1\.2\.[01](?!\d)", text), "no earlier version is named in the package")

    def test_the_reference_has_its_sections_in_order_and_one_program(self):
        text = read(DOC)
        self.assertLessEqual(len(text.splitlines()), 240)
        self.assertLessEqual(len(text), 21500, "19,000 until the doc round of 2026-10-16 (20,613 after it), 21,000 until the "
                                               "round after the second last read (21,087 after it)")
        headings = re.findall(r"^## (.*)$", text, re.M)
        self.assertEqual(headings, ["1. The files each task needs", "2. The program", "3. Commands",
                                    "4. Fetch, write and check the files", "5. Run", "6. Delete", "7. If anything is not as described"])
        self.assertTrue(text.startswith("# No skill folder"))
        self.assertIn("If the folder exists, ignore this file", text)
        self.assertIn("Put nothing of the skill into the user's project, and delete the folder at the end (section 6).", text)
        self.assertNotIn("the folder is deleted at the end", text, "the note says what to do, not what an agent does")
        self.assertEqual(_route2.doc_program(text), _route2.program())
        # negative control: the block finder sees two blocks where there are two
        with self.assertRaises(ValueError):
            _route2.doc_program("```python\nx\n```\n\n```python\ny\n```\n")

    def test_the_table_covers_every_common_task_and_names_the_cheapest_route(self):
        tasks = {task: (run, files) for task, run, files, _size in documented_sets()}
        wanted = {
            "qa_probe.py": ("frame rate", "constant frame rate", "colour tags", "frame count"),
            "qa_audio.py": ("loudness", "silence"),
            "qa_gif.py": ("GIF timing",),
            "qa_compare.py": ("frame for frame",),
        }
        for script, words in wanted.items():
            rows = [(task, run) for task, (run, _files) in tasks.items() if script in run]
            self.assertEqual(len(rows), 1, script)
            for word in words:
                self.assertIn(word.lower(), rows[0][0].lower())
        whole = [task for task, (run, _files) in tasks.items() if run == ["run_qa.py"]]
        self.assertEqual(len(whole), 2)
        self.assertTrue([task for task in whole if "--barcode-expect" in task])
        both = [run for run, _files in tasks.values() if "make_test_clip.py" in run]
        self.assertEqual(both, [["make_test_clip.py", "qa_frames.py"]])
        # the cheapest route: the audio row holds the probe row, and the whole report is seven files
        probe = set([files for task, (run, files) in tasks.items() if run == ["qa_probe.py"]][0])
        audio = set([files for task, (run, files) in tasks.items() if run == ["qa_audio.py"]][0])
        self.assertLess(probe, audio)
        self.assertEqual(len(tasks["The whole report"][1]), 7)
        section = doc_section(1)
        self.assertIn("The audio row holds the probe row, so one set of three files (106 KB)", section)
        self.assertIn("The whole report needs seven files (199 KB)", section)
        self.assertIn("single scripts first", section)
        self.assertNotRegex(read(DOC), r"(?i)\b(are|is) (always )?(saved|inline)\b", "no claim about which result a host saves")
        # negative control: the parser finds no row in a document without the table
        self.assertEqual(documented_sets("## 1. x\n\nno table here\n"), [])

    def test_every_command_in_the_reference_uses_the_skill_folder_form_and_quoted_paths(self):
        text = read(DOC)
        runs = re.findall(r"^python -I -B '(<skill-dir>/scripts/\w+\.py)' ", text, re.M)
        self.assertEqual(runs, ["<skill-dir>/scripts/qa_probe.py", "<skill-dir>/scripts/qa_audio.py"])
        commands = re.findall(r"^python -I -B - (new|put|check)(.*)$", text, re.M)
        self.assertEqual([c[0] for c in commands], ["new", "put", "check"])
        for _mode, rest in commands:
            for path in re.findall(r"<[A-Z ]+>", rest):
                self.assertIn("'%s'" % path, rest, "every path is in single quotes")
            self.assertTrue(rest.endswith("<<'EOF'"), rest)
        self.assertIn("'@ | python -I -B - check '<FOLDER>'", text)
        self.assertIn("Write every path in single quotes", text)

    def test_the_delete_step_matches_the_prefix_the_program_uses(self):
        delete = read(DOC).split("## 6. Delete")[1].split("\n## ")[0]
        self.assertIn("starts with `veqa_`", delete)
        self.assertIn("delete nothing else", delete)
        self.assertIn("compare the path with the `OK folder:` line", delete)
        self.assertIn("Do not test the path you typed", delete)
        self.assertIn("with the path in single quotes", delete)
        # only the folder new made is deleted: the repair copies went with the repair route (round-3 re-audit 2026-10-16)
        self.assertIn("with the path in single quotes, and delete nothing else; compare the path with the `OK folder:` line before "
                      "you run it. A delete command can exit 0 on a path it could not find, so then list the temp folder and "
                      "confirm that the folder's name is gone:", delete)
        for gone in ("copies for `put`", "working folder", "that one file"):
            self.assertNotIn(gone, delete)
        self.assertNotIn("make no folder for this", doc_section(4))
        # The pre-submit scan fails a package that carries a recursive-delete command, so the document says in words
        # what to do and names no command. No file of the package may carry one.
        for name, content in package_texts().items():
            with self.subTest(file=name):
                self.assertEqual(RECURSIVE_DELETE.findall(content), [])
        # negative control: the pattern finds each spelling that the scan fails
        for sample in ("rm -rf /tmp/x", "Remove-Item -Path x -Recurse -Force", "shutil.rmtree(folder)", "del /s /q x"):
            self.assertTrue(RECURSIVE_DELETE.findall(sample), sample)
        self.assertEqual(RECURSIVE_DELETE.findall("delete the folder with your shell's own command"), [])

    def test_the_windows_rule_for_a_temp_folder_outside_the_user_folder_is_stated(self):
        # audit 2026-10-15 (D17): on Windows the program checks no owner and no permission (those checks are POSIX only);
        # it relies on a per-user TEMP and on the owner-only permissions Python gives the folders it makes. Against a temp
        # folder outside the user folder (a shared folder, a network share) this sentence is the only rule the agent has
        section = doc_section(7)
        for needle in ("On Windows the folder must lie inside your own user folder (normally `...\\AppData\\Local\\Temp\\`).",
                       "If `new` prints a folder anywhere else, such as `C:\\Temp` or a network share, other users may be able to "
                       "change it after `check`: delete it, stop and tell the user.",
                       "On Linux the program itself checks the owner and the permissions of the folder and of the temp folder."):
            self.assertIn(needle, section)
        self.assertLess(section.index("On Windows the folder must lie"), section.index("Known issue:"))
        program = _route2.program()
        owner_lines = [line for line in program.split("\n") if re.search(r"st_uid|0o022|0o077|st_nlink", line)]
        self.assertEqual(len(owner_lines), 3, owner_lines)
        self.assertTrue(all("if POSIX and" in line for line in owner_lines), "every owner and mode check is POSIX only")
        for word in ("expanduser", "USERPROFILE", "GetSecurityInfo", "win32"):
            self.assertNotIn(word, program, "the program has no Windows owner check, so the sentence must stay")

    def test_the_inline_and_the_saved_case_are_both_covered(self):
        fetch = doc_section(4)
        self.assertIn("If the answer was saved to a file and you were given its path, run `put`", fetch)
        self.assertIn("write the text after the header line and the blank line, exactly as shown, with your file tool", fetch)
        self.assertIn("never a shell here-document", fetch)
        self.assertIn("Never run a script before `check` prints `OK <N> verified files and nothing else in <FOLDER>`", fetch)
        # check and a script run as separate commands, never joined (second last read 2026-10-16, W3: the join advice of the
        # round before was unsafe and is gone)
        self.assertIn("Run `check` as a command of its own, read its `OK` lines, and only then run the script, as a separate "
                      "command. Do not join the two with `;` or `&&`: a copy of the program that was cut short, or that arrived "
                      "empty, can exit 0 without printing any `OK` line.", fetch)
        for gone in ("if ($?)", "with `&&` and the script command", "If you join them in one command anyway"):
            self.assertNotIn(gone, read(DOC))
        # an empty program exits 0 and prints no OK line (measured on Python 3.9.25, 3.10.21 and 3.14.6 with an empty
        # pipe); a copy cut to end in "sys" does the same on a system refusal (test_v122_extractor.py)
        with tempfile.TemporaryDirectory(prefix="veqa_route2_test_") as tmp:
            r = _route2.run("", "check", os.path.join(tmp, "veqa_abcdefgh"), "scripts/common.py", tmp=tmp, cwd=tmp)
        self.assertEqual((r.code, r.out), (0, ""), r)
        self.assertNotIn("OK", r.err)
        self.assertIn("## File: video-export-qa/<path> (from ...)", fetch)
        # the header the document describes is the one the program looks for
        self.assertRegex("## File: video-export-qa/scripts/qa_probe.py (from Video Export QA)", r"^#{2,3} File: ")
        self.assertIn(r'r"^#{2,3} File: " + re.escape(SKILL + "/" + n)', _route2.program())

    def test_the_older_block_shape_is_mentioned_once_and_nothing_depends_on_it(self):
        doc, skill = read(DOC), self.skill()
        self.assertEqual(doc.count("### File: "), 1)
        self.assertIn("An older shape of the skill text carried every file as a `### File: video-export-qa/<path>` block", doc)
        section = skill.split("## If there is no skill folder\n")[1].split("\n## ")[0]
        for text in (section, read(PACKAGE / "README.md")):
            self.assertNotIn("### File:", text)
            self.assertNotIn("search for", text)
        for name in ("SKILL.md", "README.md", "references/no-skill-folder.md"):
            self.assertNotRegex(package_texts()[name], r"(?i)\bsnippet\b|saved response", name)

    def test_skill_md_has_a_short_section_before_anything_runs(self):
        skill = self.skill()
        self.assertEqual(skill.split("\n")[2], DESCRIPTION_LINE, "the description was trigger-tested and must not change")
        self.assertEqual(skill.split("\n")[:2], ["---", "name: video-export-qa"])
        self.assertEqual(skill.split("\n")[3], "---", "the frontmatter stays name and description only")
        section = skill.split("## If there is no skill folder\n")[1].split("\n## ")[0]
        lines = [line for line in section.strip("\n").split("\n") if line.strip()]
        self.assertGreaterEqual(len(lines), 4)
        self.assertLessEqual(len(lines), 9, "6 to 10 lines with the heading")
        for needle in ("video-export-qa/references/no-skill-folder.md", "the same way you received this text", "system temp folder",
                       "<skill-dir>", "only the files the user's task needs", "verify every file", "delete the temporary folder"):
            self.assertIn(needle, section)
        self.assertLess(skill.index("## If there is no skill folder"), skill.index("## When to use"))
        self.assertLess(skill.index("## If there is no skill folder"), skill.index("## Workflow"))
        self.assertGreater(skill.index("## If there is no skill folder"), skill.index("use the video-export-qa skill"))
        self.assertLessEqual(len(skill), 33000, "SKILL.md is not much longer than 1.2.0 (31,660 characters)")
        self.assertIn("(with no skill folder: the verified temporary folder of `references/no-skill-folder.md`, which holds the scripts "
                      "but not this file; write it in single quotes)", skill)

    def test_readme_gives_the_folder_less_route_in_the_install_section(self):
        readme = read(PACKAGE / "README.md")
        install = readme.split("## Install")[1].split("## Requirements")[0]
        self.assertIn("references/no-skill-folder.md", install)
        self.assertIn("nothing was installed", install)
        self.assertIn("a fresh folder in the system temp folder, outside your project", install)
        self.assertIn("published SHA-256 before any script runs", install)
        self.assertLessEqual(len([line for line in install.strip().split("\n") if line.strip()]), 6)
        table = readme.split("## What is in the folder")[1].split("## Limits")[0]
        self.assertIn("| `references/no-skill-folder.md` |", table)
        self.assertIn("checks every file in it by SHA-256", table)
        # what goes to disk, said as what the note tells the agent (review 2026-10-16, V1 and V2), and what it tells the agent
        # to delete: the folder new made, named so that it cannot be read as the system temp folder (round-3 re-audit, Y1, Y2)
        self.assertIn("`references/no-skill-folder.md` tells your agent (not the scripts) to write the few skill files a task needs "
                      "into a fresh folder in the system temp folder, and to delete that folder afterwards.", readme)
        for gone in ("delete that folder and that copy", "delete the temp folder", "working folder", "repairs a hand copy"):
            self.assertNotIn(gone, readme)
        self.assertIn("The note tells the agent to delete that folder afterwards.", install)
        for promise in ("The agent deletes", "and deletes that folder afterwards", "is the file the agent reads", "the agent follows"):
            self.assertNotIn(promise, readme)

    def test_no_package_line_starts_like_a_file_header(self):
        # a line that starts like a header of a saved answer is a lookalike the program has to step over; keep none
        separator = re.compile(r"^#{2,3} File: ", re.M)
        for name, text in package_texts().items():
            with self.subTest(file=name):
                self.assertIsNone(separator.search(text))
        self.assertIsNotNone(separator.search("text\n## File: video-export-qa/x.py\n"))
        self.assertIsNone(separator.search("the line `## File: x` is quoted in the middle of a line"))

    def test_the_new_text_makes_no_claim_about_a_marketplace_wrapper_text(self):
        section = self.skill().split("## If there is no skill folder\n")[1].split("\n## ")[0]
        readme = read(PACKAGE / "README.md")
        install = readme.split("## Install")[1].split("## Requirements")[0]
        for label, text in (("reference", read(DOC)), ("SKILL.md section", section), ("README install", install)):
            with self.subTest(text=label):
                self.assertEqual(wrapper_claims(text), [])
        # negative control: both known wordings of the wrapper are caught, and so is the tool name
        self.assertTrue(wrapper_claims("Never write any part of this skill to disk"))
        self.assertTrue(wrapper_claims("run it by piping the script straight into the interpreter"))
        self.assertTrue(wrapper_claims("or a short-lived temp file removed afterwards"))
        self.assertTrue(wrapper_claims("don't save them into project or skill folders"))
        self.assertTrue(wrapper_claims("Run scripts in-session"))
        self.assertTrue(wrapper_claims("call get_skill"))
        self.assertEqual(wrapper_claims("write the files into a temporary folder and delete it afterwards"), [])

    def test_the_tests_readme_documents_the_modules_and_the_simulator(self):
        text = read(Path(__file__).resolve().parent / "README.md")
        self.assertIn("`test_v121_no_skill_folder.py`", text)
        self.assertIn("`test_v122_extractor.py`", text)
        self.assertIn("python -I tests/video-export-qa/get_skill_sim.py packages/video-export-qa sim_out", text)
        self.assertIn("python fake_get_skill.py --file video-export-qa/scripts/qa_probe.py", text)
        self.assertIn("ffmpeg and ffprobe are required on PATH", text)
        self.assertIn("no script behaviour changed", text)

    def test_the_listing_source_and_the_proposal_carry_the_new_text(self):
        # last statement of the test: a skip ends the test under failfast
        if not LISTING.is_dir():
            self.skipTest(LISTING_ABSENT)
        proposal = (LISTING / PROPOSAL).read_bytes().decode("utf-8")
        self.assertIn("no script behaviour changed", proposal)
        for heading in ("## 1. Full Description (the whole field)", "## 3. Compatibility Note", "## 4. Extra Details > Known limitations",
                        "## 5. Permissions (tools, file scopes and notes)", "## 6. FAQ", "## 7. Changelog for the Update dialog",
                        "## Fresh-buyer re-test", "## Not claimed"):
            self.assertIn("\n" + heading, proposal)
        # the doc round after the re-test is recorded with its commit and the fact that the buyers were not re-run, and the
        # precheck WARNs carry their written reason (review 2026-10-16, V9)
        retest_section = proposal.split("## Fresh-buyer re-test")[1].split("\n## ")[0]
        for needle in ("After the re-test, an independent last read of the zip", "the commit that follows c722a04 on branch veqa-1.2.2",
                       "The two buyers were NOT re-run on it."):
            self.assertIn(needle, retest_section)
        tested = proposal.split("## What was tested for this text")[1].split("\n## ")[0]
        for needle in ("0 FAIL and 2 WARN", "process spawn x6 in `scripts/barcode.py` and x3 in `scripts/common.py`",
                       "Every such call takes an argument list and never goes through a shell",
                       "the exception class `subprocess.SubprocessError` named in an except clause (line 246)"):
            self.assertIn(needle, tested)
        # the round after the second last read is recorded too, and the join advice is marked as removed (W3)
        for needle in ("the commit that follows 5125adb on branch veqa-1.2.2", "The two buyers were NOT re-run on that round either.",
                       "the next round removed that joining again"):
            self.assertIn(needle, retest_section)
        self.assertNotIn("assertions only", proposal)
        self.assertNotIn("or joined so that", proposal)
        # the file scopes name the spec JSON that SKILL.md step 3 has the agent write next to the export (W5c), and say that
        # only the veqa_* folder is to be deleted; no repair copy is left anywhere in the listing (round-3 re-audit, Y1 to Y3)
        fields = (LISTING / "listing_fields.md").read_bytes().decode("utf-8")
        self.assertIn("the spec *.json that SKILL.md tells your agent to write next to the export", fields)
        self.assertIn("a veqa_* folder in the system temp folder; references/no-skill-folder.md tells it to delete only that veqa_* "
                      "folder afterwards.", fields)
        new_blocks = "\n".join(re.findall(r"^New:\n((?:>.*\n?)+)", proposal, re.M))
        changelog_text = proposal.split("## 7. Changelog for the Update dialog")[1].split("```")[1]
        for text in (fields, new_blocks, changelog_text):
            for gone in ("repair", "working folder", "all of which"):
                self.assertNotIn(gone, text)
        for needle in ("the commit that follows b6ee4a3 on branch veqa-1.2.2", "the next round removed that route again"):
            self.assertIn(needle, retest_section)
        self.assertIn("Write the spec as a JSON file next to the export", read(PACKAGE / "SKILL.md"))
        self.assertIn("ffmpeg and ffprobe are started with argument lists, never through a shell", read(PACKAGE / "SKILL.md"))
        self.assertNotRegex("\n".join(re.findall(r"^New:\n((?:>.*\n?)+)", proposal, re.M)), r"\[\[|<[A-Za-z ]+>|\bTODO\b",
                            "no placeholder in a New block")
        changelog = proposal.split("## 7. Changelog for the Update dialog")[1].split("```")[1]
        self.assertTrue(changelog.strip("\n").startswith("Package docs release 1.2.2 (Agensi numbers this update v1.4)"), changelog[:120])
        # the re-test result is either still a placeholder in its own section, or filled in there: never inside a New block
        new_text = "\n".join(re.findall(r"^New:\n((?:>.*\n?)+)", proposal, re.M))
        self.assertNotIn("RETEST", new_text)
        retest = proposal.split("## Fresh-buyer re-test")[1].split("\n## ")[0]
        if "[[RETEST RESULT" not in retest:
            self.assertGreater(len(retest.strip()), 200, "the re-test section holds the result once the placeholder is gone")
        fields = (LISTING / "listing_fields.md").read_bytes().decode("utf-8")
        description = (LISTING / "description.md").read_bytes().decode("utf-8")
        for text in (fields, description):
            self.assertIn("references/no-skill-folder.md", text)
            self.assertNotRegex(text, r"(?i)\bsnippet\b", "the old route is not described any more")
        self.assertEqual(wrapper_claims(fields.split("## Compatibility note")[1].split("\n## ")[0]), [])
        # the sizes the listing quotes are the sizes of the document's table
        sizes = {task: size for task, _run, _files, size in documented_sets()}
        limitation = [line for line in fields.splitlines() if line.startswith("- If your agent receives the skill as text")][0]
        self.assertIn("about %d KB for a frame-rate or colour-tag check" % [s for t, s in sizes.items() if t.startswith("Frame rate")][0], limitation)
        self.assertIn("%d KB with the audio checks" % [s for t, s in sizes.items() if t.startswith("Loudness")][0], limitation)
        self.assertIn("%d KB for the whole report" % sizes["The whole report"], limitation)


if __name__ == "__main__":
    unittest.main()
