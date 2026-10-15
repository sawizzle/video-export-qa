# Tests for version 1.2.1 (documentation, tests and one version number; no script behaviour changed).
# The release adds references/no-skill-folder.md for an agent that received the skill as TEXT and has no skill folder.
#   * ImportGraphTests    the file sets in the document's table are derived from the real import graph and from what
#                         the scripts open at run time, so the table cannot drift when someone adds an import
#   * MinimalSetTests     each documented set is copied ALONE into an empty folder and run for real (help plus a run on
#                         a clip made here); leaving out any one file of a set makes it fail
#   * SimulatorTests      get_skill_sim.py (the helper that makes a text file shaped like a "skill as text" answer)
#   * SnippetTests        the snippet is taken out of the document verbatim and run against the simulated file; every
#                         file must come out byte for byte, including a last block with no final newline and markdown
#                         files that contain fenced code; broken snippets and bad requests must fail
#   * DocumentPinTests    the new text in SKILL.md, README.md and the reference says what the code does
# Every scanner and every harness has a negative control, so a pass means something.
# Portability: ffmpeg and ffprobe are needed only by the tests that run a script on a clip; without them those tests
# skip with the reason below. The listing source (listing/) is read only when it is part of the checkout.
# Run:  python -I -B -m unittest discover -s tests/video-export-qa
import ast
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

import get_skill_sim  # noqa: E402  (the helper next to this file)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "video-export-qa"
SCRIPTS = PACKAGE / "scripts"
REFERENCES = PACKAGE / "references"
EXAMPLES = PACKAGE / "examples"
DOC = REFERENCES / "no-skill-folder.md"
LISTING = ROOT / "listing" / "video-export-qa"
SIM_SCRIPT = Path(__file__).resolve().parent / "get_skill_sim.py"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NO_FFMPEG = "ffmpeg and ffprobe are required on PATH"
LISTING_ABSENT = "the listing source folder (listing/) is not part of this checkout; the public repository has only packages/ and tests/"

# The description line of SKILL.md was trigger-tested for 1.2.0 and must not change in 1.2.1.
DESCRIPTION_LINE = ("description: Measures video, GIF and audio exports with ffprobe, ffmpeg and a built-in GIF parser. "
                    "Use when asked to check a delivery spec, prove which frames an edit kept, test constant frame rate, "
                    "measure GIF duration or find silent or too-quiet audio. Do not use for editing, trimming or converting.")

# Words the new text must not use to describe what a marketplace's wrapper text says: the wording changed within two
# days once, so the package makes no claim about it and does not name the service.
WRAPPER_CLAIMS = (
    r"never write (any part of )?this skill",
    r"pip(e|ing) the script",
    r"short-lived",
    r"(do not|don't) (save|write) (it |them )?(to|into) (a |the )?(skills? |project )",
    r"load this skill again",
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
                          capture_output=True, cwd=str(cwd))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def ffmpeg(*args, cwd):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args],
                          capture_output=True, cwd=str(cwd))
    if proc.returncode != 0:
        raise AssertionError("fixture ffmpeg failed: %s\n%s" % (args, proc.stderr.decode("utf-8", "replace")))


def listing_of(folder):
    return sorted(str(p.relative_to(folder)).replace(os.sep, "/") for p in Path(folder).rglob("*") if p.is_file())


def snippet_blocks(doc_text=None):
    doc_text = read(DOC) if doc_text is None else doc_text
    return re.findall(r"^```python\n(.*?)^```$", doc_text, re.S | re.M)


def run_snippet(snippet, response, folder, names, cwd=None):
    """What an agent does: the program on standard input, the response file, the folder and the names as arguments."""
    proc = subprocess.run([sys.executable, "-I", "-B", "-", str(response), str(folder)] + list(names),
                          input=snippet.encode("ascii"), capture_output=True, cwd=None if cwd is None else str(cwd))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


class Scratch:
    """A scratch folder for one test class, removed afterwards."""
    def __init__(self, test_case_class, prefix):
        self.path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        test_case_class.addClassCleanup(shutil.rmtree, str(self.path), True)

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


# Files that make the simulated answer hard on purpose: fences inside markdown, a markdown file that ends in a fence
# line, one with no final newline, an empty file, Windows line ends, and a LAST block with no final newline.
HARD_FILES = {
    "references/fenced.md": "# Notes\n\n```python\nx = 1\n```\n\nText between.\n\n```\nplain fence\n```\n\n### Not a file header\n",
    "references/ends_in_fence.md": "Last line is a fence:\n\n```\ncode\n```\n",
    "references/no_newline.md": "no final newline and a fence at the end\n```",
    "references/empty.md": "",
    "scripts/crlf.py": b"a = 1\r\nb = 2\r\n",
    "scripts/zlast.py": "print(1)",
}


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch(cls, "veqa_test_sim_")
        cls.hard = make_package(cls.scratch / "hardpkg", HARD_FILES)

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, "-I", str(SIM_SCRIPT)] + [str(a) for a in args], capture_output=True)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")

    def test_the_command_line_writes_the_response_for_any_package_folder(self):
        for package in (PACKAGE, self.hard):
            out = self.scratch / ("sim_%s.txt" % package.name)
            code, stdout, err = self.run_cli(package, out)
            with self.subTest(package=package.name):
                self.assertEqual((code, err), (0, ""))
                self.assertIn("%d file blocks" % len(get_skill_sim.package_files(package)), stdout)
                data = out.read_bytes()
                self.assertEqual(data, get_skill_sim.build(package).encode("utf-8"))
                text = data.decode("utf-8")
                headers = re.findall(r"^### File: (.*)$", text, re.M)
                self.assertEqual(headers, ["%s/%s" % (package.name, rel) for rel in get_skill_sim.package_files(package)])
                self.assertEqual(headers, sorted(headers), "sorted by path")
                self.assertNotIn("### File: %s/SKILL.md" % package.name, text)
                self.assertIn((package / "SKILL.md").read_bytes().decode("ascii").rstrip("\n"), text, "SKILL.md is inline")
                self.assertFalse(text.endswith("\n"), "the last block has no final newline")
                self.assertTrue(text.endswith("\n```"))
                self.assertNotIn("\r\n", text.replace("a = 1\r\nb = 2\r\n", ""), "LF line ends outside the CRLF fixture")

    def test_every_block_is_the_file_between_two_plain_fence_lines(self):
        text = get_skill_sim.build(PACKAGE)
        parts = re.split(r"(?m)^### File: ", text)[1:]
        self.assertEqual(len(parts), len(get_skill_sim.package_files(PACKAGE)))
        for part in parts:
            head, _, body = part.partition("\n")
            rel = head.split("/", 1)[1]
            original = (PACKAGE / Path(*rel.split("/"))).read_bytes().decode("ascii")
            self.assertEqual(body.rstrip("\n"), "\n```\n" + original + "\n```", rel)

    def test_the_real_package_gives_the_snippet_a_hard_job(self):
        # the markdown files of the package contain fences of their own, so a parser that trusts the first closing
        # fence cuts them short; this is why the simulated answer is built from the real package
        text = get_skill_sim.build(PACKAGE)
        inner = [rel for rel in get_skill_sim.package_files(PACKAGE) if rel.endswith(".md")
                 and "\n```" in (PACKAGE / Path(*rel.split("/"))).read_bytes().decode("ascii")]
        self.assertGreaterEqual(len(inner), 4, inner)
        self.assertIn("### File: video-export-qa/references/no-skill-folder.md", text)
        # negative control: the wrapper wording is neutral filler, not a copy of any service's text
        self.assertEqual(wrapper_claims(get_skill_sim.HEADER + get_skill_sim.MIDDLE), [])

    def test_the_command_line_refuses_bad_arguments(self):
        out = self.scratch / "never_written.txt"
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli(PACKAGE)[0], 2)
        self.assertEqual(self.run_cli(self.scratch.path, out)[0], 2, "a folder without SKILL.md")
        code, _stdout, err = self.run_cli(self.hard, self.hard / "inside.txt")
        self.assertEqual(code, 2)
        self.assertIn("must not be inside", err)
        self.assertFalse(out.exists())
        self.assertFalse((self.hard / "inside.txt").exists())
        self.assertNotIn("inside.txt", get_skill_sim.package_files(self.hard))


# --------------------------------------------------------------------------- the snippet in the document

# Breakages an agent could write (or a later edit could introduce). Each replaces one line of the snippet.
SNIPPET_MUTATIONS = {
    "stop at the first closing fence": (
        'block = re.fullmatch(r"\\s*(`{3,})[^\\n]*\\n(.*)\\n\\1\\s*", body, re.S)',
        'block = re.match(r"\\s*(`{3,})[^\\n]*\\n(.*?)\\n\\1", body, re.S)'),
    "need a newline after the last fence": (
        'block = re.fullmatch(r"\\s*(`{3,})[^\\n]*\\n(.*)\\n\\1\\s*", body, re.S)',
        'block = re.fullmatch(r"\\s*(`{3,})[^\\n]*\\n(.*)\\n\\1\\n\\s*", body, re.S)'),
    "keep the newline in front of the closing fence": (
        'block = re.fullmatch(r"\\s*(`{3,})[^\\n]*\\n(.*)\\n\\1\\s*", body, re.S)',
        'block = re.fullmatch(r"\\s*(`{3,})[^\\n]*\\n(.*\\n)\\1\\s*", body, re.S)'),
    "read the response with universal newlines (a CRLF file changes)": (
        'text = open(response, encoding="utf-8", newline="").read()',
        'text = open(response, encoding="utf-8").read()'),
}
# The check that nothing is written unless every name was found. It is tested on its own, because a snippet without it
# still extracts the files that exist.
GUARD = ('if missing:\n    sys.exit("no complete block for: " + ", ".join(missing))\n', '')


class SnippetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = Scratch(cls, "veqa_test_snippet_")
        cls.response = cls.scratch / "response.txt"
        cls.response.write_bytes(get_skill_sim.build(PACKAGE).encode("utf-8"))
        cls.hard = make_package(cls.scratch / "hardpkg", HARD_FILES)
        cls.hard_response = cls.scratch / "hard_response.txt"
        cls.hard_response.write_bytes(get_skill_sim.build(cls.hard).encode("utf-8"))
        cls.snippet = snippet_blocks()[0]

    def new_folder(self):
        """A folder path that does not exist yet (the snippet makes it)."""
        return self.scratch / ("out_" + uuid.uuid4().hex[:8])

    def extraction_problems(self, snippet):
        """Run a snippet against both simulated answers; what is wrong with the result, or [] when it is right."""
        problems = []
        wanted = [
            (self.response, PACKAGE, [name for _t, _r, files, _s in documented_sets() for name in ["scripts/" + f for f in files]]
             + ["references/interpreting-results.md", "references/no-skill-folder.md", "references/pitfalls.md",
                "examples/spec_barcode_run.json", "scripts/run_qa.py"]),
            (self.hard_response, self.hard, ["references/fenced.md", "references/ends_in_fence.md", "references/no_newline.md",
                                             "references/empty.md", "scripts/crlf.py", "scripts/zlast.py"]),
        ]
        for response, package, names in wanted:
            names = sorted(set(names))
            folder = self.new_folder()
            code, out, err = run_snippet(snippet, response, folder, names)
            if code != 0:
                problems.append("%s: exit %d %s" % (package.name, code, err.strip().splitlines()[-1:] or ""))
                continue
            for name in names:
                made = folder / Path(*name.split("/"))
                original = package / Path(*name.split("/"))
                if not made.is_file() or made.read_bytes() != original.read_bytes():
                    problems.append("%s: %s differs from the original" % (package.name, name))
        return problems

    def test_the_document_has_one_snippet_and_it_is_plain_stdlib_python(self):
        blocks = snippet_blocks()
        self.assertEqual(len(blocks), 1)
        snippet = blocks[0]
        snippet.encode("ascii")
        self.assertLessEqual(len(snippet.splitlines()), 40, "short enough to paste under token pressure")
        for character, why in (("'", "an apostrophe breaks a shell here-document"),
                               ("$", "a dollar sign is expanded by some shells"), ("\t", "tabs are lost when pasting")):
            self.assertNotIn(character, snippet, why)
        tree = ast.parse(snippet, feature_version=(3, 9))     # the grammar of the oldest Python the skill supports
        for path in (SIM_SCRIPT, Path(__file__).resolve()):
            ast.parse(read(path), feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported, {"re", "sys", "tempfile", "pathlib"})
        self.assertNotIn("subprocess", imported)
        # negative control: the scan can fail
        self.assertEqual(len(snippet_blocks("```python\nx\n```\n\n```python\ny\n```\n")), 2)

    def test_the_snippet_gives_every_documented_set_back_byte_for_byte_and_the_hard_cases_too(self):
        self.assertEqual(self.extraction_problems(self.snippet), [])

    def test_the_snippet_prints_what_it_wrote_and_names_the_folder(self):
        folder = self.new_folder()
        names = ["scripts/qa_probe.py", "scripts/common.py"]
        code, out, err = run_snippet(self.snippet, self.response, folder, names)
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        for line, name in zip(lines, names):
            self.assertEqual(line, "wrote %s %d characters" % ((folder / Path(*name.split("/"))).as_posix(),
                                                              (PACKAGE / Path(*name.split("/"))).stat().st_size))
        self.assertEqual(lines[-1], "skill folder: " + folder.as_posix())
        self.assertEqual(listing_of(folder), sorted(names), "nothing but what was asked for")

    def test_new_makes_a_fresh_temporary_folder_outside_the_project_and_an_existing_folder_is_filled_further(self):
        code, out, err = run_snippet(self.snippet, self.response, "new", ["scripts/common.py"])
        self.assertEqual((code, err), (0, ""))
        folder = Path(out.splitlines()[-1].split("skill folder: ", 1)[1])
        self.addCleanup(shutil.rmtree, str(folder), True)
        self.assertTrue(folder.name.startswith("veqa_"))
        self.assertEqual(folder.resolve().parent, Path(tempfile.gettempdir()).resolve())
        self.assertEqual(listing_of(folder), ["scripts/common.py"])
        code, out, err = run_snippet(self.snippet, self.response, folder, ["scripts/qa_gif.py", "references/interpreting-results.md"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(listing_of(folder), ["references/interpreting-results.md", "scripts/common.py", "scripts/qa_gif.py"])
        # two runs with "new" give two folders
        code, out2, _err = run_snippet(self.snippet, self.response, "new", ["scripts/common.py"])
        other = Path(out2.splitlines()[-1].split("skill folder: ", 1)[1])
        self.addCleanup(shutil.rmtree, str(other), True)
        self.assertNotEqual(other, folder)

    def test_the_last_block_has_no_final_newline_and_is_still_read(self):
        text = self.response.read_bytes()
        self.assertFalse(text.endswith(b"\n"))
        last = re.findall(rb"(?m)^### File: (\S+)$", text)[-1].decode("ascii").split("/", 1)[1]
        self.assertEqual(last, "scripts/run_qa.py")
        folder = self.new_folder()
        code, _out, err = run_snippet(self.snippet, self.response, folder, [last])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual((folder / "scripts" / "run_qa.py").read_bytes(), (SCRIPTS / "run_qa.py").read_bytes())
        # the same for the synthetic package, whose last block is a one-line file with no newline
        self.assertFalse(self.hard_response.read_bytes().endswith(b"\n"))
        # negative control: a pattern that needs a newline after the closing fence misses exactly this block
        needs_newline = SNIPPET_MUTATIONS["need a newline after the last fence"]
        broken = self.snippet.replace(needs_newline[0], needs_newline[1])
        self.assertNotEqual(broken, self.snippet)
        code, _out, err = run_snippet(broken, self.response, self.new_folder(), [last])
        self.assertNotEqual(code, 0)
        self.assertIn("no complete block for: scripts/run_qa.py", err)

    def test_markdown_files_with_fenced_code_come_out_whole(self):
        for rel in ("references/pitfalls.md", "references/editor-recipes.md", "references/no-skill-folder.md"):
            self.assertIn("\n```", read(PACKAGE / Path(*rel.split("/"))), rel + " has a fence of its own")
        for rel in ("references/fenced.md", "references/ends_in_fence.md", "references/no_newline.md", "references/empty.md"):
            folder = self.new_folder()
            code, _out, err = run_snippet(self.snippet, self.hard_response, folder, [rel])
            with self.subTest(file=rel):
                self.assertEqual((code, err), (0, ""))
                self.assertEqual((folder / Path(*rel.split("/"))).read_bytes(), (self.hard / Path(*rel.split("/"))).read_bytes())
        # negative control: a pattern that stops at the first closing fence cuts a file short
        stop_early = SNIPPET_MUTATIONS["stop at the first closing fence"]
        broken = self.snippet.replace(stop_early[0], stop_early[1])
        self.assertNotEqual(broken, self.snippet)
        folder = self.new_folder()
        run_snippet(broken, self.hard_response, folder, ["references/fenced.md"])
        self.assertNotEqual((folder / "references" / "fenced.md").read_bytes(), (self.hard / "references" / "fenced.md").read_bytes())

    def test_windows_line_ends_survive(self):
        folder = self.new_folder()
        code, _out, err = run_snippet(self.snippet, self.hard_response, folder, ["scripts/crlf.py"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual((folder / "scripts" / "crlf.py").read_bytes(), b"a = 1\r\nb = 2\r\n")

    def test_a_missing_name_writes_nothing_and_fails_loudly(self):
        folder = self.new_folder()
        code, out, err = run_snippet(self.snippet, self.response, folder, ["scripts/common.py", "scripts/not_in_the_skill.py"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("no complete block for: scripts/not_in_the_skill.py", err)
        self.assertFalse(folder.exists(), "no folder, so no half-written set")
        # and with the check taken away the first name IS written before the failure
        self.assertEqual(self.snippet.count(GUARD[0]), 1)
        broken = self.snippet.replace(GUARD[0], GUARD[1])
        self.assertNotEqual(broken, self.snippet)
        folder = self.new_folder()
        code, _out, _err = run_snippet(broken, self.response, folder, ["scripts/common.py", "scripts/not_in_the_skill.py"])
        self.assertNotEqual(code, 0)
        self.assertTrue((folder / "scripts" / "common.py").exists())

    def test_a_cut_off_response_is_refused_for_the_cut_block_only(self):
        text = self.response.read_bytes()
        cut = self.scratch / "cut_response.txt"
        cut.write_bytes(text[:text.rindex(b"\n```")])      # the closing fence of the last block is gone
        folder = self.new_folder()
        code, _out, err = run_snippet(self.snippet, cut, folder, ["scripts/run_qa.py"])
        self.assertNotEqual(code, 0)
        self.assertIn("no complete block for: scripts/run_qa.py", err)
        code, _out, err = run_snippet(self.snippet, cut, folder, ["scripts/qa_probe.py"])
        self.assertEqual((code, err), (0, ""), "the blocks before the cut are complete")

    def nested_folder(self):
        """A folder path two levels below the scratch folder, so that a file written one or two levels up stays inside it."""
        return self.nest() / uuid.uuid4().hex[:8]

    def nest(self):
        return self.scratch / ("nest_" + self._testMethodName)

    def test_names_that_are_not_files_of_the_skill_are_refused(self):
        for bad in ("../outside.py", "scripts/../../outside.py", "/absolute.py", "scripts/", "scripts", "README.md",
                    "tests/test_x.py", "scripts/.hidden", "scripts/a b.py", "scripts/x.py;rm"):
            folder = self.nested_folder()
            code, out, err = run_snippet(self.snippet, self.response, folder, ["scripts/common.py", bad])
            with self.subTest(name=bad):
                self.assertNotEqual(code, 0)
                self.assertIn("not a file name of the skill", err)
                self.assertFalse(folder.exists())
        self.assertEqual(list(self.nest().glob("*")), [], "nothing was written, not even one level up")

    def test_the_header_path_in_the_response_cannot_choose_where_a_file_goes(self):
        hostile = self.scratch / "hostile.txt"
        hostile.write_bytes(b"### File: skill/../../escaped.py\n\n```\nprint(1)\n```\n\n### File: skill/scripts/ok.py\n\n```\nprint(2)\n```")
        folder = self.nested_folder()
        code, _out, err = run_snippet(self.snippet, hostile, folder, ["scripts/ok.py"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(listing_of(folder), ["scripts/ok.py"])
        self.assertFalse((self.scratch / "escaped.py").exists(), "the header path would have led two levels up")
        self.assertEqual([p.name for p in self.nest().glob("*")], [folder.name])

    def test_broken_snippets_fail_the_extraction_check(self):
        # negative control for extraction_problems itself: every mutation must be reported as a problem
        self.assertEqual(self.extraction_problems(self.snippet), [])
        for label, (old, new) in SNIPPET_MUTATIONS.items():
            with self.subTest(mutation=label):
                self.assertEqual(self.snippet.count(old), 1, "the mutation targets a line that is in the snippet")
                problems = self.extraction_problems(self.snippet.replace(old, new))
                self.assertTrue(problems, label)

    @unittest.skipUnless(FFMPEG and FFPROBE, NO_FFMPEG)
    def test_the_extracted_probe_runs_on_a_generated_clip(self):
        data = self.scratch / "probe_data"
        data.mkdir()
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
               "silent.mp4", cwd=data)
        _task, _run, files, _size = [r for r in documented_sets() if "qa_probe.py" in r[1]][0]
        code, out, err = run_snippet(self.snippet, self.response, "new", ["scripts/" + name for name in files])
        self.assertEqual((code, err), (0, ""))
        folder = Path(out.splitlines()[-1].split("skill folder: ", 1)[1])
        self.addCleanup(shutil.rmtree, str(folder), True)
        code, out, err = run_script(folder, "qa_probe.py", "silent.mp4", "--json", cwd=data)
        self.assertEqual(code, 0, err)
        video = json.loads(out)["info"]["video"]
        self.assertEqual((video["counted_frames"], video["width"], video["height"], video["fps"]), (24, 320, 180, 24.0))
        # negative control: the same set minus common.py, taken from the same response, does not run
        lonely = self.new_folder()
        code, _out, err = run_snippet(self.snippet, self.response, lonely, ["scripts/qa_probe.py"])
        self.assertEqual((code, err), (0, ""))
        code, out, err = run_script(lonely, "qa_probe.py", "silent.mp4", "--json", cwd=data)
        self.assertNotEqual(code, 0)
        self.assertIn("No module named 'common'", err)


# --------------------------------------------------------------------------- the words in the package

def package_texts():
    return {str(p.relative_to(PACKAGE)).replace(os.sep, "/"): read(p) for p in sorted(PACKAGE.rglob("*")) if p.is_file()}


class DocumentPinTests(unittest.TestCase):
    def skill(self):
        return read(PACKAGE / "SKILL.md")

    def test_the_version_is_1_2_1_everywhere_the_package_states_it(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import common
        finally:
            sys.path.remove(str(SCRIPTS))
        self.assertEqual(common.VERSION, "1.2.1")
        for name in ("sample_report.md", "example_session.md"):
            self.assertIn("run_qa.py 1.2.1 on Python", read(EXAMPLES / name))
        for name, text in package_texts().items():
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"(?<![\d.])1\.2\.0(?!\d)", text), "the previous version is not named in the package")

    def test_the_reference_is_short_and_has_its_sections_in_order(self):
        text = read(DOC)
        self.assertLessEqual(len(text.splitlines()), 80)
        self.assertLessEqual(len(text), 8000)
        headings = re.findall(r"^## (.*)$", text, re.M)
        self.assertEqual(headings, ["1. The files each task needs", "2. Write the files", "3. Run", "4. Delete",
                                    "5. The response came back inline"])
        self.assertTrue(text.startswith("# No skill folder"))
        self.assertIn("If the folder exists, ignore this file", text)
        self.assertIn("outside the user's project", text)

    def test_the_table_covers_every_common_task(self):
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
        # negative control: the parser finds no row in a document without the table
        self.assertEqual(documented_sets("## 1. x\n\nno table here\n"), [])

    def test_every_command_in_the_reference_uses_the_skill_folder_form(self):
        text = read(DOC)
        commands = re.findall(r"python3? -I -B (\S+)", text)
        self.assertGreaterEqual(len(commands), 2)
        for path in commands:
            if "scripts/" in path:
                self.assertTrue(path.startswith("<skill-dir>/scripts/"), path)
        self.assertIn("python -I -B <skill-dir>/scripts/qa_probe.py export.mp4 --spec spec.json", text)
        self.assertIn("python -I -B - RESPONSE new scripts/qa_probe.py scripts/common.py", text)
        self.assertIn("that folder is `<skill-dir>` for the rest of the task", text)

    def test_the_delete_step_matches_the_prefix_the_snippet_uses(self):
        text = read(DOC)
        delete = text.split("## 4. Delete")[1].split("\n## ")[0]
        self.assertIn("starts with `veqa_`", delete)
        self.assertIn('tempfile.mkdtemp(prefix="veqa_")', snippet_blocks()[0])
        self.assertIn("delete nothing else", delete)
        self.assertIn("compare the path with the `skill folder:` line", delete)
        # The pre-submit scan fails a package that carries a recursive-delete command, so the document says in words
        # what to do and names no command. No file of the package may carry one.
        for name, content in package_texts().items():
            with self.subTest(file=name):
                self.assertEqual(RECURSIVE_DELETE.findall(content), [])
        # negative control: the pattern finds each spelling that the scan fails
        for sample in ("rm -rf /tmp/x", "Remove-Item -Path x -Recurse -Force", "shutil.rmtree(folder)", "del /s /q x"):
            self.assertTrue(RECURSIVE_DELETE.findall(sample), sample)
        self.assertEqual(RECURSIVE_DELETE.findall("delete the folder with your shell's own command"), [])

    def test_the_inline_case_is_covered_and_points_at_the_smallest_row(self):
        inline = read(DOC).split("## 5. The response came back inline")[1]
        self.assertIn("nothing was saved to a file", inline)
        self.assertIn("smallest row", inline)
        self.assertIn("write nothing that is not in the row", inline)
        self.assertIn("tempfile.mkdtemp", inline)

    def test_skill_md_has_a_short_section_before_anything_runs(self):
        skill = self.skill()
        self.assertEqual(skill.split("\n")[2], DESCRIPTION_LINE, "the description was trigger-tested and must not change")
        self.assertEqual(skill.split("\n")[:2], ["---", "name: video-export-qa"])
        self.assertEqual(skill.split("\n")[3], "---", "the frontmatter stays name and description only")
        section = skill.split("## If there is no skill folder\n")[1].split("\n## ")[0]
        lines = [line for line in section.strip("\n").split("\n") if line.strip()]
        self.assertGreaterEqual(len(lines), 4)
        self.assertLessEqual(len(lines), 8, "6 to 10 lines with the heading")
        for needle in ("references/no-skill-folder.md", "temporary folder", "outside the user's project", "<skill-dir>",
                       "write only the files it lists", "delete the temporary folder"):
            self.assertIn(needle, section)
        self.assertLess(skill.index("## If there is no skill folder"), skill.index("## When to use"))
        self.assertLess(skill.index("## If there is no skill folder"), skill.index("## Workflow"))
        self.assertGreater(skill.index("## If there is no skill folder"), skill.index("use the video-export-qa skill"))
        self.assertLessEqual(len(skill), 33000, "SKILL.md is not much longer than 1.2.0 (31,660 characters)")
        self.assertIn("(with no skill folder: the temporary folder, see above)", skill)

    def test_readme_gives_the_folder_less_route_in_the_install_section(self):
        readme = read(PACKAGE / "README.md")
        install = readme.split("## Install")[1].split("## Requirements")[0]
        self.assertIn("references/no-skill-folder.md", install)
        self.assertIn("nothing was installed", install)
        self.assertIn("temporary folder outside your project", install)
        self.assertLessEqual(len([line for line in install.strip().split("\n") if line.strip()]), 6)
        table = readme.split("## What is in the folder")[1].split("## Limits")[0]
        self.assertIn("| `references/no-skill-folder.md` |", table)

    def test_no_package_line_starts_like_a_block_separator(self):
        # the snippet splits a response on lines that start with the separator, so a package file must not contain one
        separator = re.compile(r"^### File: ", re.M)
        for name, text in package_texts().items():
            with self.subTest(file=name):
                self.assertIsNone(separator.search(text))
        self.assertIsNotNone(separator.search("text\n### File: video-export-qa/x.py\n"))
        self.assertIsNone(separator.search("the line `### File: x` is quoted in the middle of a line"))

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
        self.assertTrue(wrapper_claims("call get_skill"))
        self.assertEqual(wrapper_claims("write the files into a temporary folder and delete it afterwards"), [])

    def test_the_tests_readme_documents_the_new_module_and_the_simulator(self):
        text = read(Path(__file__).resolve().parent / "README.md")
        self.assertIn("`test_v121_no_skill_folder.py`", text)
        self.assertIn("python -I tests/video-export-qa/get_skill_sim.py packages/video-export-qa out.txt", text)
        self.assertIn("ffmpeg and ffprobe are required on PATH", text)
        self.assertIn("no script behaviour changed", text)

    def test_the_listing_source_and_the_proposal_carry_the_new_text(self):
        # last statement of the test: a skip ends the test under failfast
        if not LISTING.is_dir():
            self.skipTest(LISTING_ABSENT)
        proposal = (LISTING / "update_v1.2.1_PROPOSAL.md").read_bytes().decode("utf-8")
        # the fresh-buyer re-test is done: its result stands in its own section and the placeholder is gone
        self.assertNotIn("[[RETEST RESULT", proposal)
        retest = proposal.split("## Fresh-buyer re-test")[1].split("\n## ")[0]
        for word in ("Claude Sonnet", "Claude Haiku", "simulated answer", "failed commands"):
            self.assertIn(word, retest)
        self.assertIn("no script behaviour changed", proposal)
        for heading in ("## 1. Full Description (the whole field)", "## 3. Compatibility Note", "## 6. FAQ",
                        "## 7. Changelog for the Update dialog", "## Fresh-buyer re-test"):
            self.assertIn("\n" + heading, proposal)
        # the placeholder sits in its own section, never inside a New block that would be pasted into the listing
        new_text = "\n".join(re.findall(r"^New:\n((?:>.*\n?)+)", proposal, re.M))
        self.assertNotIn("RETEST RESULT", new_text)
        fields = (LISTING / "listing_fields.md").read_bytes().decode("utf-8")
        description = (LISTING / "description.md").read_bytes().decode("utf-8")
        for text in (fields, description):
            self.assertIn("references/no-skill-folder.md", text)
        self.assertEqual(wrapper_claims(fields.split("## Compatibility note")[1].split("\n## ")[0]), [])
        # the sizes the listing quotes are the sizes of the document's table
        sizes = {task: size for task, _run, _files, size in documented_sets()}
        limitation = [line for line in fields.splitlines() if line.startswith("- If your agent receives the skill as text")][0]
        self.assertIn("about %d KB for a frame-rate or colour-tag check" % [s for t, s in sizes.items() if t.startswith("Frame rate")][0], limitation)
        self.assertIn("about %d KB for the whole report" % sizes["The whole report"], limitation)


if __name__ == "__main__":
    unittest.main()
