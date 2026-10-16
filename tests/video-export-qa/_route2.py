# _route2.py - the generator for the extractor program in references/no-skill-folder.md (a test helper, not part of
# the package). The program is the shared template route2/extract_core.py specialised by route2/gen_table.py for this
# skill: SKILL video-export-qa, PREFIX veqa_, and a TABLE of every file in scripts/ plus
# references/interpreting-results.md (the one file a script opens at run time). Its first line is FIRST_LINE in place
# of the template's first line: the same code, another comment (see FIRST_LINE). The document carries the program and a
# table of the same lengths and SHA-256 values; the tests regenerate both from the package files and compare them with
# the document, so a script that changes without the document being regenerated fails the tests.
#
# Purpose: one place that says how the document's program and table are made.
# Inputs:  the package folder (default: packages/video-export-qa of this checkout).
# Outputs: python -I -B tests/video-export-qa/_route2.py program        prints the program
#          python -I -B tests/video-export-qa/_route2.py table          prints the table of lengths and SHA-256 values
#          python -I -B tests/video-export-qa/_route2.py refresh        rewrites the program block and the table in
#                       references/no-skill-folder.md from the package files (run it after any file of the TABLE changed)
#          python -I -B tests/video-export-qa/_route2.py fill TEMPLATE OUT
#                       writes OUT from a document template that holds the lines <<PROGRAM>> and <<SHATABLE>>
# Exit codes: 0 done, 2 bad arguments or a refusal of the generator.
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTE2 = HERE / "route2"
ROOT = HERE.parents[1]
PACKAGE = ROOT / "packages" / "video-export-qa"
DOC = PACKAGE / "references" / "no-skill-folder.md"
TEMPLATE_PATH = ROUTE2 / "extract_core.py"
SKILL_NAME = "video-export-qa"
PREFIX = "veqa_"
EXTRA = ("references/interpreting-results.md",)

if str(ROUTE2) not in sys.path:
    sys.path.insert(0, str(ROUTE2))
import gen_table  # noqa: E402  (route2/gen_table.py, the shared generator)

TEMPLATE = TEMPLATE_PATH.read_bytes().decode("ascii")

# The template's first line says that a copy cut short is a SyntaxError, never a silent run. Measured on 2026-10-16 by
# compiling every prefix of the program on Python 3.9 to 3.14: almost every cut stops with a SyntaxError or an
# IndentationError (a copy without its last line), but a cut after the first one or two characters and a few cuts inside
# the last line compile, and a copy that ends in "sys" or "sys.exit" there prints nothing and exits 0 when the system
# refuses something. So this skill's program says what is true; the shared, audited template stays byte for byte.
TEMPLATE_FIRST_LINE = "try:  # Skill file extractor. One try block: a copy cut short is a SyntaxError, never a silent run.\n"
FIRST_LINE = ("try:  # Skill file extractor, one try block: a copy cut short almost always stops with a SyntaxError or "
              "IndentationError; only its OK lines mean success.\n")


def table_names(package=PACKAGE):
    """Every file of scripts/ (the scripts import each other) and the guidance file run_qa.py opens."""
    scripts = sorted("scripts/" + p.name for p in Path(package, "scripts").iterdir() if p.is_file() and p.suffix == ".py")
    return scripts + list(EXTRA)


def table(package=PACKAGE):
    return gen_table.table_entries(package, table_names(package))


def program(package=PACKAGE, template=None):
    template = template or TEMPLATE
    if not template.startswith(TEMPLATE_FIRST_LINE):
        raise gen_table.Refused("the template does not start with the line this generator replaces")
    return gen_table.specialise(FIRST_LINE + template[len(TEMPLATE_FIRST_LINE):], SKILL_NAME, PREFIX, table(package))


def sha_table(package=PACKAGE):
    rows = ["| File | Bytes | SHA-256 |", "| --- | --- | --- |"]
    for name, (digest, size) in sorted(table(package).items()):
        rows.append("| `%s` | %d | `%s %s` |" % (name, size, digest[:32], digest[32:]))
    return "\n".join(rows)


def doc_text(path=DOC):
    return Path(path).read_bytes().decode("ascii")


def doc_program(text=None):
    """The one python block of the document, exactly as an agent copies it (with its final line end)."""
    blocks = re.findall(r"^```python\n(.*?)^```$", doc_text() if text is None else text, re.S | re.M)
    if len(blocks) != 1:
        raise ValueError("the document must hold exactly one python block, found %d" % len(blocks))
    return blocks[0]


def doc_sha_table(text=None):
    text = doc_text() if text is None else text
    match = re.search(r"^\| File \| Bytes \| SHA-256 \|\n(?:\|.*\|\n?)+", text, re.M)
    return match.group(0).rstrip("\n") if match else None


def doc_table_values(text=None):
    """{name: (sha256, length)} read back from the document's table of files."""
    found = {}
    for name, size, first, second in re.findall(r"^\| `([^`]+)` \| (\d+) \| `([0-9a-f]{32}) ([0-9a-f]{32})` \|$",
                                                 doc_sha_table(text) or "", re.M):
        found[name] = (first + second, int(size))
    return found


def fill(template_text, package=PACKAGE):
    for marker in ("<<PROGRAM>>\n", "<<SHATABLE>>\n"):
        if template_text.count(marker) != 1:
            raise ValueError("the template must hold the line %s once" % marker.strip())
    return template_text.replace("<<PROGRAM>>\n", program(package)).replace("<<SHATABLE>>\n", sha_table(package) + "\n")


def refresh(text, package=PACKAGE):
    """The document with its program block and its table of files regenerated from the package (after a script changed)."""
    doc_program(text)                                           # exactly one python block, or ValueError
    old_table = doc_sha_table(text)
    if old_table is None or text.count(old_table) != 1:
        raise ValueError("the document must hold the table of files once")
    text = re.sub(r"(?ms)^```python\n.*?^```$", lambda m: "```python\n" + program(package) + "```", text)
    return text.replace(old_table, sha_table(package))


# --------------------------------------------------------------------------- running the program as an agent does

POSIX = os.name != "nt"


def child_env(tmp, extra=None):
    """The environment of every child: no colour, and `tmp` as the system temp folder (-I does not hide TMP, TEMP or
    TMPDIR, and tempfile.gettempdir() reads them), so a test never touches the machine's own temp folder."""
    env = dict(os.environ)
    for key in ("FORCE_COLOR", "PYTHON_COLORS"):
        env.pop(key, None)
    env["NO_COLOR"] = "1"
    for key in ("TMPDIR", "TEMP", "TMP"):
        env[key] = str(tmp)
    env.update(extra or {})
    return env


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err

    def folder(self):
        for line in self.out.splitlines():
            if line.startswith("OK folder: "):
                return Path(line[len("OK folder: "):])
        return None

    def __repr__(self):
        return "Result(code=%r, out=%r, err=%r)" % (self.code, self.out[-600:], self.err[-600:])


def run(source, *args, tmp, cwd, prefix="", exe=None, timeout=300, user=None, extra_env=None):
    """The program on standard input, the mode and its arguments after the -, as section 3 of the document shows."""
    kwargs = {"user": user, "group": user, "extra_groups": []} if user is not None else {}
    proc = subprocess.run([exe or sys.executable, "-I", "-B", "-"] + [str(a) for a in args],
                          input=(prefix + source).encode("ascii"), capture_output=True, cwd=str(cwd), timeout=timeout,
                          env=child_env(tmp, extra_env), **kwargs)
    norm = lambda b: b.decode("utf-8", "replace").replace("\r\n", "\n")
    return Result(proc.returncode, norm(proc.stdout), norm(proc.stderr))


def raw_run(source, *args, tmp, cwd, prefix=""):
    """Like run, with the raw bytes of standard output and standard error."""
    proc = subprocess.run([sys.executable, "-I", "-B", "-"] + [str(a) for a in args], input=(prefix + source).encode("ascii"),
                          capture_output=True, cwd=str(cwd), timeout=300, env=child_env(tmp))
    return proc.returncode, proc.stdout, proc.stderr


def traced_run(source, *args, tmp, cwd, marker):
    """Like run, with tracemalloc on in the child: the Result and the peak of traced memory in bytes."""
    trace = ("import atexit, tracemalloc\ntracemalloc.start()\n"
             "atexit.register(lambda: open(%r, \"w\").write(str(tracemalloc.get_traced_memory()[1])))\n" % str(marker))
    r = run(source, *args, tmp=tmp, cwd=cwd, prefix=trace, timeout=900)
    return r, int(Path(marker).read_text())


def header(name, display="Video Export QA", skill=SKILL_NAME, hashes="##"):
    return "%s File: %s/%s (from %s)" % (hashes, skill, name, display)


def file_text(name, package=PACKAGE):
    return Path(package).joinpath(*name.split("/")).read_bytes().decode("utf-8")


def p1(name, content=None, before=""):
    """The plain-text form a host saves: the header line, a blank line, the exact file."""
    return before + header(name) + "\n\n" + (file_text(name) if content is None else content)


def p2(name, content=None, ensure_ascii=True, indent=2, wrap=None):
    """The JSON form a host saves: a list with one {"type": "text", "text": ...} object."""
    doc = [{"type": "text", "text": p1(name, content)}]
    if wrap == "content":
        doc = {"content": doc, "isError": False}
    return json.dumps(doc, ensure_ascii=ensure_ascii, indent=indent)


def cleanup(path):
    """Remove a scratch tree, links included (never followed), even with private modes left by a test."""
    def onerror(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if os.name == "nt":
        for dirpath, dirnames, _files in os.walk(str(path)):
            for d in list(dirnames):
                full = os.path.join(dirpath, d)
                if getattr(os.lstat(full), "st_file_attributes", 0) & 1024:      # a junction: remove the link only
                    os.rmdir(full)
                    dirnames.remove(d)
    shutil.rmtree(str(path), onerror=onerror)


def listing(folder):
    """Every entry under a folder (links not followed), as relative names with forward slashes."""
    out = []
    for dirpath, dirnames, files in os.walk(str(folder)):
        for n in dirnames + files:
            out.append(os.path.relpath(os.path.join(dirpath, n), str(folder)).replace(os.sep, "/"))
    return sorted(out)


def snapshot(root):
    """Type, size and content hash of every entry under root, links not followed."""
    out = {}
    for dirpath, dirnames, files in os.walk(str(root)):
        for n in dirnames + files:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 1024:
                out[p] = "link"
            elif stat.S_ISDIR(st.st_mode):
                out[p] = "dir"
            else:
                with open(p, "rb") as f:
                    out[p] = "file %d %s" % (st.st_size, hashlib.sha256(f.read()).hexdigest()[:16])
    return out


def can_symlink(where):
    probe = Path(where) / ("probe_" + uuid.uuid4().hex[:8])
    try:
        os.symlink(str(where), str(probe), target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    os.unlink(str(probe)) if POSIX else os.rmdir(str(probe))
    return True


def junction(link, target):
    """A Windows junction (no privilege needed). True when it was made."""
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return proc.returncode == 0


def canary_code(marker, label):
    return "open(%r, \"a\").write(%r + \"\\n\")\n" % (str(marker), label)


class Scratch(unittest.TestCase):
    """A scratch root, a neutral working folder and a fresh child temp folder per test; the program is the document's."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="veqa_route2_test_")).resolve()
        cls.addClassCleanup(cleanup, cls.root)
        cls.work = cls.root / "work"
        cls.work.mkdir()
        cls.src = doc_program()

    def setUp(self):
        self.tmp = self.new_tmp()

    def new_tmp(self, parent=None, name=None):
        tmp = (parent or self.root) / (name or ("tmp_" + uuid.uuid4().hex[:8]))
        tmp.mkdir(parents=True)
        if POSIX:
            os.chmod(str(tmp), 0o700)
        return tmp

    def x(self, *args, src=None, tmp=None, cwd=None, **kw):
        return run(src or self.src, *args, tmp=tmp or self.tmp, cwd=cwd or self.work, **kw)

    def new(self, **kw):
        r = self.x("new", **kw)
        self.assertEqual(r.code, 0, r)
        folder = r.folder()
        self.assertIsNotNone(folder, r)
        return folder

    def saved(self, text, name="saved.txt", raw=None):
        path = self.root / ("saved_" + uuid.uuid4().hex[:8]) / name
        path.parent.mkdir()
        path.write_bytes(raw if raw is not None else text.encode("utf-8"))
        return path

    def assertRefused(self, r, *needles):
        self.assertEqual(r.code, 1, r)
        self.assertEqual(r.out, "", r)
        self.assertTrue(r.err.startswith("REFUSED: "), r)
        self.assertNotIn("Traceback", r.err)
        for needle in needles:
            self.assertIn(needle, r.err)

    def assertExact(self, folder, names):
        """The folder holds exactly the package files `names` (byte for byte) and the empty folders new made."""
        self.assertEqual(listing(folder), sorted(set(DIRS) | set(names)))
        for name in names:
            self.assertEqual(Path(folder).joinpath(*name.split("/")).read_bytes(),
                             PACKAGE.joinpath(*name.split("/")).read_bytes(), name)


DIRS = sorted({n.split("/")[0] for n in table_names()})


def main(argv):
    try:
        if argv == ["program"]:
            sys.stdout.write(program())
        elif argv == ["table"]:
            sys.stdout.write(sha_table() + "\n")
        elif len(argv) == 3 and argv[0] == "fill":
            text = fill(Path(argv[1]).read_bytes().decode("ascii"))
            Path(argv[2]).write_bytes(text.encode("ascii"))
            print("wrote %s: %d characters, program %d characters" % (argv[2], len(text), len(program())))
        elif argv == ["refresh"]:
            old = doc_text()
            text = refresh(old)
            if text != old:
                DOC.write_bytes(text.encode("ascii"))
            print("%s: %s" % (DOC.name, "regenerated" if text != old else "already current"))
        else:
            sys.stderr.write("usage: python -I -B _route2.py program | table | refresh | fill TEMPLATE OUT\n")
            return 2
    except (gen_table.Refused, ValueError) as e:
        sys.stderr.write("REFUSED: %s\n" % e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
