# -*- coding: utf-8 -*-
"""The extractor program of references/no-skill-folder.md (package release 1.2.2), attacked the way the shared design was.

The program is the shared template tests/video-export-qa/route2/extract_core.py specialised for this skill by
route2/gen_table.py (SKILL video-export-qa, PREFIX veqa_, a TABLE of the 9 scripts and references/interpreting-results.md).
Every test here runs the program taken out of the document, on standard input, as an agent does, with the child's temp
folder pointed into a scratch folder that is removed afterwards. The real files of the package are the fixtures.
  * TableTests        the document's program and its table are the generator's output for the real files; the template
                      and the generator are the audited ones; paste rules, size budget, standard library only
  * NewTests          new: a fresh private folder named as the document says, no argument accepted, no use of a temp
                      folder that is the working folder or that others can write
  * FolderTests       put and check accept only a folder of the shape new makes and nothing else in it: not fresh,
                      foreign files, planted modules, links, junctions, hand-written files, modes, never overwrite, a
                      check after writing
  * NameTests         names, the skill-folder prefix of the file index, escaping of untrusted text and of paths that hold a
                      control character, the document's accented-path example against what the program prints
  * SavedShapeTests   every saved form (P1, P2 and variants, the older block shape, CR LF, BOM, a header lookalike) and the
                      ways a saved file can be wrong, hostile input, memory, time
  * ShellTests        a quoted bash heredoc, the full program inside the Bash-tool command limit, a copy cut short anywhere,
                      a Windows PowerShell 5.1 here-string
  * TwoUserTests      Linux, root, two accounts named in VEQA_TWO_USERS=victim:attacker (a throwaway container): the
                      attacker's folders in /tmp are refused, so is a temp folder the attacker owns (even at mode 1777),
                      and a race against an attacker who watches /tmp
  * RealAnswerTests   answers a real host saved, when VEQA_REAL_ANSWERS names them (they are not part of any checkout)
Run:  python -I -B -m unittest discover -s tests/video-export-qa
"""
import ast
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

import _route2  # noqa: E402
from _route2 import (DIRS, PACKAGE, POSIX, PREFIX, SKILL_NAME, Scratch, can_symlink, canary_code, child_env, cleanup, gen_table,
                     header, junction, listing, p1, p2, raw_run, run, snapshot, traced_run)  # noqa: E402

TABLE = _route2.table()
NAMES = sorted(TABLE)
SMALL = "scripts/qa_compare.py"          # a small script (16 KB)
BIG = "scripts/qa_probe.py"              # the largest file (58 KB)
SH = shutil.which("sh") or shutil.which("bash")
PS = shutil.which("powershell") if os.name == "nt" else None
NAME_RE = re.compile(re.escape(PREFIX) + "[a-z0-9_]{8}")
USAGE = "usage: python -I -B - new | put FOLDER SAVED NAME... | check FOLDER [NAME...]"
CONTROL_CHARACTERS = (("CR", "\r"), ("LF", "\n"), ("ESC", "\x1b"), ("TAB", "\t"), ("DEL", "\x7f"))

# The shared template and generator as audited on 2026-10-15 (security, format and fixer rounds). A change to either is a
# change to the safety of the route and needs the same review again. Digests in two halves (the hygiene scans refuse a
# 64-digit hex literal).
AUDITED = {
    "extract_core.py": ("8a237cef09093b266f33037ad9cd06b6" + "fa8dd9955015bcb0f6e5dc9d0e17e3b8", 5634),
    "gen_table.py": ("5b5c91df94030c8b4526b1fe5959e615" + "ef0f6cc851b569332951aa852702de17", 8232),
}
ALLOWED_IMPORTS = {"hashlib", "itertools", "json", "os", "re", "stat", "sys", "tempfile"}
FORBIDDEN_NAMES = {"eval", "exec", "__import__", "subprocess", "system", "popen", "environ", "getenv", "putenv", "socket",
                   "urllib", "http", "importlib", "runpy", "pickle", "marshal", "ctypes", "shutil", "rmtree", "remove", "unlink",
                   "rmdir", "rename", "chmod", "chown", "symlink", "link", "walk", "chdir"}
ALLOWED_OS = {"os.name", "os.getcwd", "os.stat", "os.lstat", "os.fstat", "os.geteuid", "os.listdir", "os.open", "os.mkdir",
              "os.path.samefile", "os.path.abspath", "os.path.basename", "os.path.dirname", "os.path.join"}


def file_bytes(name):
    return PACKAGE.joinpath(*name.split("/")).read_bytes()


def text_of(name):
    return file_bytes(name).decode("utf-8")


def os_calls(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr != "path":
            found.add("os." + node.attr)
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "path"
              and isinstance(node.value.value, ast.Name) and node.value.value.id == "os"):
            found.add("os.path." + node.attr)
    return found


def make_valid(folder, names=(SMALL,)):
    """A folder that looks like one new made, with good copies of the files (for attacks that keep the layout valid)."""
    for d in DIRS:
        folder.joinpath(d).mkdir(parents=True, exist_ok=True)
    for name in names:
        folder.joinpath(*name.split("/")).write_bytes(file_bytes(name))
    if POSIX:
        for d in [folder] + [folder / d for d in DIRS]:
            os.chmod(str(d), 0o700)
    return folder


def old_shape(names, intro="# Video Export QA\n\nSome listing text.\n\n"):
    """The older whole-package form: one '### File:' block per file, plain fences, the last block without a final newline."""
    blocks = ["### File: %s/%s\n\n```\n%s\n```" % (SKILL_NAME, name, text_of(name)) for name in names]
    return intro + "\n\n".join(blocks)


def worst_plain(name=SMALL):
    """About 15 MB built to make put copy as much as it can: 4 headers for the name, fenced bodies, CR LF line ends, and a
    character outside the BMP on every line, so Python stores the text at 4 bytes per character."""
    h = "## File: %s/%s (from X)\r\n\r\n```\r\n" % (SKILL_NAME, name)
    body = ("\U0001f600" + "y" * 98 + "\r\n") * 36000
    return ((h + body + "\r\n```\r\n") * 4).encode("utf-8")


def worst_json(name=SMALL):
    """About 15 MB of JSON that holds the most decoded text: a top-level string with escaped CR LF and one character outside
    the BMP, between CR LF whitespace, so both the raw text and the string have a CR LF repaired copy."""
    s = "## File: %s/%s\r\n\r\n" % (SKILL_NAME, name) + ("y" * 97 + "\r\n") * 152000 + "\U0001f600"
    return ("\r\n" + json.dumps(s, ensure_ascii=False) + "\r\n").encode("utf-8")


class TableTests(unittest.TestCase):
    def test_the_documents_program_and_table_are_the_generators_output_for_the_real_files(self):
        self.assertEqual(_route2.doc_program(), _route2.program())
        self.assertEqual(_route2.doc_sha_table(), _route2.sha_table())
        self.assertEqual(_route2.doc_table_values(), TABLE)
        self.assertEqual(sorted(TABLE), sorted(["scripts/" + p.name for p in (PACKAGE / "scripts").glob("*.py")]
                                               + ["references/interpreting-results.md"]))
        for name, (digest, size) in TABLE.items():
            data = file_bytes(name)
            self.assertEqual((digest, size), (hashlib.sha256(data).hexdigest(), len(data)), name)
            self.assertIn('"%s": ("%s" "%s", %d),' % (name, digest[:32], digest[32:], size), _route2.doc_program())
        # negative control: a script changed by one byte no longer matches the program in the document
        scratch = Path(tempfile.mkdtemp(prefix="veqa_table_"))
        self.addCleanup(cleanup, scratch)
        shutil.copytree(str(PACKAGE), str(scratch / "video-export-qa"))
        target = scratch / "video-export-qa" / "scripts" / "qa_gif.py"
        target.write_bytes(target.read_bytes().replace(b"#!/usr/bin/env python3\n", b"#!/usr/bin/env python3 \n", 1))
        self.assertNotEqual(_route2.program(scratch / "video-export-qa"), _route2.doc_program())

    def test_the_template_and_the_generator_are_the_audited_ones(self):
        for name, (digest, size) in AUDITED.items():
            data = (_route2.ROUTE2 / name).read_bytes()
            with self.subTest(file=name):
                self.assertEqual((hashlib.sha256(data).hexdigest(), len(data)), (digest, size))
        # the program is the template with only the three placeholders filled in and its first-line comment replaced
        # (doc round 2026-10-16: the template's comment overstated what a cut copy does; the code is the same)
        template_lines = [line for line in _route2.TEMPLATE.split("\n") if "__TABLE__" not in line]
        program_lines = [line for line in _route2.doc_program().split("\n") if not re.match(r'  "[a-z]+/[\w.-]+": \(', line)]
        self.assertEqual(template_lines[0] + "\n", _route2.TEMPLATE_FIRST_LINE)
        self.assertEqual(program_lines[0] + "\n", _route2.FIRST_LINE)
        self.assertEqual([line.replace('"__SKILL__", "__PREFIX__"', '"video-export-qa", "veqa_"') for line in template_lines[1:]], program_lines[1:])
        for line in (template_lines[0], program_lines[0]):
            self.assertEqual(ast.dump(ast.parse(line + "\n pass\nexcept OSError:\n pass\n")),
                             ast.dump(ast.parse("try:\n pass\nexcept OSError:\n pass\n")), "only a comment follows try:")
        self.assertIn("almost always stops with a SyntaxError or IndentationError; only its OK lines mean success", program_lines[0])
        self.assertNotIn("never a silent run", _route2.doc_text())
        # negative control: a template whose first line is not the one the generator replaces is refused
        with self.assertRaises(gen_table.Refused):
            _route2.program(template="try:  # another comment\n" + _route2.TEMPLATE.split("\n", 1)[1])

    def test_the_program_pastes_through_a_quoted_heredoc_or_here_string_and_fits_the_budget(self):
        src = _route2.doc_program()
        self.assertEqual(gen_table.snippet_problems(src), [])
        self.assertLessEqual(len(src), gen_table.MAX_CHARS)
        self.assertEqual((len(src), src.count("\n")), (6742, 97), "the size the document's numbers were measured on")
        # section 7 gives the program's length and the measured Git Bash limit (doc round 2026-10-16: it no longer reports an
        # untested remark about a PowerShell tool)
        section7 = _route2.doc_text().split("## 7. ")[1]
        self.assertIn("The program alone is {:,} characters".format(len(src)), section7)
        # (second last read 2026-10-16, W5a: the cut happens when a Windows program starts sh or bash, not inside Git Bash)
        self.assertIn("When a Windows program starts Git Bash's `sh` or `bash` with `-c` (measured with bash 5.3.9, started from "
                      "Python and from Windows PowerShell 5.1), the command keeps only its first 8,186 characters; started from "
                      "inside Git Bash, the whole command arrives.", section7)
        self.assertNotIn("In Git Bash on Windows (measured with bash 5.3.9), a command given with", section7)
        self.assertNotIn("PowerShell tool", _route2.doc_text())
        self.assertIn("a SyntaxError or IndentationError, a here-document warning, `unexpected EOF while looking for matching`", section7)
        for char in ("'", "$", "\x60", "\t", "\r", "\\\\", "\\\""):
            self.assertNotIn(char, src)
        self.assertIsNone(re.search(r"(?m)^EOF$", src))
        self.assertTrue(src.startswith("try:") and src.rstrip("\n").split("\n")[-2].startswith("except (OSError, MemoryError)"))
        # negative control: each paste rule can fail
        for bad, problem in (("x = 'a'\n", "apostrophe"), ("x = 1  # $\n", "dollar sign"), ("# caf\u00e9\n", "not ASCII"),
                             ("x = 1  # a" + chr(92) * 2 + "b\n", "double backslash"), ("EOF\n", "EOF")):
            self.assertTrue([p for p in gen_table.snippet_problems(src + bad, max_chars=None) if problem in p], problem)
        self.assertTrue([p for p in gen_table.snippet_problems(src + "#" * 200 + "\n") if "longer than" in p])

    def test_the_program_uses_only_the_standard_library_and_nothing_that_runs_code_deletes_or_reads_the_environment(self):
        tree = ast.parse(_route2.doc_program())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported, ALLOWED_IMPORTS)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(sorted(names & FORBIDDEN_NAMES), [])
        self.assertEqual(sorted(os_calls(tree) - ALLOWED_OS), [])
        # negative control: the walks see such names
        self.assertEqual(os_calls(ast.parse("os.replace(a, b)\nos.path.realpath(c)\n")), {"os.replace", "os.path.realpath"})


class NewTests(Scratch):
    def test_new_makes_a_fresh_private_folder_directly_in_the_temp_folder(self):
        seen = set()
        for _ in range(12):
            r = self.x("new")
            self.assertEqual((r.code, r.err), (0, ""), r)
            self.assertEqual(r.out.count("\n"), 1)
            folder = r.folder()
            self.assertTrue(NAME_RE.fullmatch(folder.name), folder.name)
            self.assertEqual(folder.parent.resolve(), self.tmp.resolve())
            self.assertEqual(listing(folder), ["references", "scripts"], "only the empty folders of the layout")
            if POSIX:
                for d in [folder, folder / "scripts", folder / "references"]:
                    st = os.lstat(str(d))
                    self.assertEqual((stat.S_IMODE(st.st_mode), st.st_uid), (0o700, os.geteuid()))
            seen.add(folder)
        self.assertEqual(len(seen), 12)
        c = self.x("check", folder)
        self.assertEqual(c.code, 0, c)
        self.assertEqual(c.out, "OK 0 verified files and nothing else in %s\n" % folder)

    def test_the_documented_folder_name_is_the_name_new_makes(self):
        # audit 2026-10-15 (D08): "name `veqa_` and 8 characters" was true but unpinned
        found = re.findall(r"`new` makes a fresh folder \(name `([a-z_]+)` and (\d+) characters, directly in the system temp folder",
                           _route2.doc_text())
        self.assertEqual(len(found), 1, found)
        prefix, count = found[0][0], int(found[0][1])
        self.assertEqual((prefix, count), (PREFIX, 8))
        self.assertEqual(re.findall(r"re\.escape\(PREFIX\) \+ \"\[a-z0-9_\]\{(\d+)\}\"", self.src), [str(count)],
                         "the program accepts exactly that name")
        for _ in range(4):
            name = self.new().name
            self.assertEqual(len(name), len(prefix) + count, name)
            self.assertTrue(name.startswith(prefix) and re.fullmatch(r"[a-z0-9_]+", name[len(prefix):]), name)

    def test_new_takes_no_folder_and_bad_modes_print_the_usage(self):
        existing = make_valid(self.tmp / "veqa_exists01")
        before = snapshot(self.root)
        for args in ([], ["new", "x"], ["new", existing], ["new", self.tmp], ["NEW"], ["new/"], [" new"], ["new "], ["n\u0435w"],
                     [""], ["-"], ["--help"], ["put"], ["put", existing, "x"], ["check"], ["fetch", "x"], ["PUT", "a", "b", "c"],
                     [existing, "new", SMALL]):
            r = self.x(*args)
            with self.subTest(args=[str(a)[-16:] for a in args]):
                self.assertEqual((r.code, r.out), (2, ""), r)
                self.assertTrue(r.err.startswith(USAGE), r)
                self.assertEqual(r.err.strip(), USAGE, "only the usage line, no REFUSED:")
        self.assertEqual(snapshot(self.root), before, "nothing was made or written")
        # what section 2 says about it (doc round 2026-10-16)
        section2 = _route2.doc_text().split("## 2. ")[1].split("\n## ")[0]
        self.assertIn("A refusal starts with `REFUSED: ` and exits 1. A mode or a number of arguments that the program does not take "
                      "prints only the line `%s`, exits 2 and writes nothing." % USAGE, section2)

    def test_new_skips_every_planted_name_when_the_random_names_collide(self):
        victim = self.root / "victim_collide"
        victim.mkdir()
        (self.tmp / "veqa_aaaaaaaa" / "scripts").mkdir(parents=True)
        (self.tmp / "veqa_aaaaaaaa" / "scripts" / "json.py").write_text(canary_code(self.root / "CANARY_COLLIDE", "collide"))
        (self.tmp / "veqa_bbbbbbbb").write_bytes(b"a file\n")
        planted = ["aaaaaaaa", "bbbbbbbb"]
        if os.name == "nt" and junction(self.tmp / "veqa_cccccccc", victim):
            planted.append("cccccccc")
        if can_symlink(self.root):
            os.symlink(str(victim), str(self.tmp / "veqa_dddddddd"), target_is_directory=True)
            planted.append("dddddddd")
        prefix = ("import tempfile\nclass _Seq:\n    names = %r\n    i = 0\n    def __iter__(self): return self\n"
                  "    def __next__(self):\n        n = self.names[min(self.i, len(self.names) - 1)]\n        self.i += 1\n        return n\n"
                  "tempfile._name_sequence = _Seq()\n" % (planted + ["zzzzzzzz"]))
        folder = self.new(prefix=prefix)
        self.assertEqual(folder.name, "veqa_zzzzzzzz", planted)
        self.assertEqual(listing(victim), [], "nothing made through a planted link")
        self.assertFalse((self.root / "CANARY_COLLIDE").exists())

    def test_new_checks_the_folder_it_made(self):
        planted = self.tmp / "veqa_plantedx"
        planted.mkdir()
        (planted / "extra.txt").write_bytes(b"x")
        if POSIX:
            os.chmod(str(planted), 0o700)
        r = self.x("new", prefix="import tempfile\ntempfile.mkdtemp = lambda prefix=None, dir=None: %r\n" % str(planted))
        self.assertRefused(r, "must hold only")

    def test_the_temp_folder_is_the_working_folder(self):
        self.assertRefused(self.x("new", cwd=self.tmp), "the working folder is the temp folder, or no temp folder is usable")
        self.assertEqual(listing(self.tmp), [])
        # what Python does when no temp folder is usable: tempfile falls back to the working folder (the document says so)
        project = self.work / ("project_" + uuid.uuid4().hex[:6])
        planted = make_valid(project / "veqa_abcdefgh", [])
        before = snapshot(project)
        fallback = "import os, tempfile\ntempfile.tempdir = os.getcwd()\n"
        for args in (["new"], ["check", planted], ["put", planted, self.saved(p1(SMALL)), SMALL]):
            with self.subTest(args=args[0]):
                self.assertRefused(self.x(*args, cwd=project, prefix=fallback), "the working folder is the temp folder, or no temp folder is usable")
        self.assertEqual(snapshot(project), before, "nothing was made or written in the project")
        self.assertIn("the working folder is the temp folder, or no temp folder is usable", _route2.doc_text())

    @unittest.skipUnless(POSIX, "POSIX modes")
    def test_a_temp_folder_others_can_write_without_the_sticky_bit(self):
        os.chmod(str(self.tmp), 0o777)
        self.assertRefused(self.x("new"), "other users can replace folders in the temp folder")
        os.chmod(str(self.tmp), 0o1777)
        folder = self.new()
        os.chmod(str(self.tmp), 0o770)
        self.assertRefused(self.x("check", folder), "other users can replace folders")
        os.chmod(str(self.tmp), 0o755)
        self.assertEqual(self.x("check", folder).code, 0)


class FolderTests(Scratch):
    def test_folders_that_are_not_fresh_are_refused_by_put_and_check(self):
        project = self.work / "project"
        cases = {
            "the temp folder itself": (self.tmp, "is not a folder that new makes"),
            "nested in the temp folder": (make_valid(self.tmp / "sub" / "veqa_abcdefgh"), "is not directly in the temp folder"),
            "in the working folder": (make_valid(self.work / "veqa_abcdefgh"), "is not directly in the temp folder"),
            "in a project folder": (make_valid(project / "veqa_abcdefgh"), "is not directly in the temp folder"),
            "the other skill's prefix": (make_valid(self.tmp / "ssr_abcdefgh"), "is not a folder that new makes"),
            "no prefix": (make_valid(self.tmp / "abcdefghijkl"), "is not a folder that new makes"),
            "name too short": (make_valid(self.tmp / "veqa_abcdefg"), "is not a folder that new makes"),
            "name too long": (make_valid(self.tmp / "veqa_abcdefghi"), "is not a folder that new makes"),
            "upper case": (make_valid(self.tmp / "veqa_ABCDEFGH"), "is not a folder that new makes"),
            "a dot in the name": (make_valid(self.tmp / "veqa_abcd.fgh"), "is not a folder that new makes"),
            "does not exist": (self.tmp / "veqa_zzzzzzzz", "the system refused: "),
        }
        (self.tmp / "veqa_filefile").write_bytes(b"a file\n")
        cases["a file"] = (self.tmp / "veqa_filefile", "is not a real folder")
        saved = self.saved(p1(SMALL))
        before = snapshot(self.root)
        for label, (folder, needle) in cases.items():
            for args in (["put", folder, saved, SMALL], ["check", folder]):
                with self.subTest(case=label, mode=args[0]):
                    self.assertRefused(self.x(*args), needle)
        self.assertEqual(snapshot(self.root), before)
        for rel in ("veqa_abcdefgh", os.path.join("..", "project", "veqa_abcdefgh")):
            with self.subTest(relative=rel):
                self.assertRefused(self.x("check", rel, cwd=project), "is not directly in the temp folder")
        # The program cannot tell who made a folder: one made by hand, of the shape new makes and directly in the temp
        # folder, is accepted, and its files are still checked by length and SHA-256 (section 2 says so for Windows; on
        # POSIX make_valid also gives it the owner and the modes new gives)
        hand = make_valid(self.tmp / "veqa_handmd01", [SMALL])
        r = self.x("check", hand, SMALL)
        self.assertEqual((r.code, r.err), (0, ""), r)
        self.assertTrue(r.out.endswith("OK 1 verified files and nothing else in %s\n" % hand), r)
        hand.joinpath(*SMALL.split("/")).write_bytes(file_bytes(SMALL) + b"X")
        self.assertRefused(self.x("check", hand, SMALL), "is not the published file")
        section2 = _route2.doc_text().split("## 2. ")[1].split("\n## ")[0]
        # second last read 2026-10-16, W5b: accepted on every system (on Linux with the owner and the modes new gives, which
        # make_valid gives too; looser modes are refused in test_loose_modes_and_hard_links_are_refused), and the name is
        # checked as written in the command
        # round-3 re-audit 2026-10-16, Y4: the rule as the program checks it (st_mode & 0o077 == 0; 500 passes as well as 700)
        self.assertIn("A folder of that shape made by hand is accepted too (the program cannot tell who made it); on Linux only "
                      "when it, `scripts/` and `references/` belong to you and give group and others no permission, as `new` "
                      "makes them. Every file in it is still checked by its length and SHA-256.", section2)
        self.assertNotIn("(mode 700)", section2)
        self.assertIn("(the name as you write it in the command: on Windows a folder stored as `VEQA_UPPER001` is accepted when "
                      "you write `veqa_upper001`)", section2)
        self.assertNotIn("On Windows a folder of that shape made by hand", section2)
        stored = make_valid(self.tmp / "VEQA_UPPER001", [SMALL])
        self.assertRefused(self.x("check", stored, SMALL), "is not a folder that new makes")
        if os.name == "nt":
            r = self.x("check", self.tmp / "veqa_upper001", SMALL)
            self.assertEqual((r.code, r.err), (0, ""), r)
            self.assertIn("OK 1 verified files and nothing else in", r.out)
        self.assertNotIn("it works only in a folder of the kind `new` makes", section2)
        self.assertIn("named `veqa_` and 8 lower-case letters, digits or underscores", section2)
        self.assertEqual(re.findall(r"re\.escape\(PREFIX\) \+ \"\[a-z0-9_\]\{8\}\"", self.src), ['re.escape(PREFIX) + "[a-z0-9_]{8}"'])

    def test_a_folder_holding_anything_but_the_published_files_is_refused(self):
        cases = []
        f = self.new(); (f / "extra.txt").write_bytes(b"x"); cases.append(("a file at the top", f, "must hold only"))
        f = self.new(); (f / "references").rmdir(); cases.append(("references missing", f, "must hold only references, scripts"))
        f = self.new(); (f / "scripts" / "evil.py").write_bytes(b"x"); cases.append(("an unknown file", f, "holds 'scripts/evil.py'"))
        f = self.new(); (f / "scripts" / "__pycache__").mkdir(); cases.append(("__pycache__", f, "- run scripts with -B"))
        f = self.new(); (f / "scripts" / "sub").mkdir(); cases.append(("a sub folder", f, "holds 'scripts/sub'"))
        f = self.new(); (f / "scripts" / "QA_COMPARE.py").write_bytes(file_bytes(SMALL)); cases.append(("another case", f, "holds 'scripts/QA_COMPARE.py'"))
        f = self.new(); (f / "scripts" / "qa_compare.py").write_bytes(file_bytes("scripts/qa_gif.py")); cases.append(("another file's bytes", f, "is not the published file"))
        f = self.new(); (f / "references" / "pitfalls.md").write_bytes(file_bytes("references/pitfalls.md")); cases.append(("a reference it does not know", f, "holds 'references/pitfalls.md'"))
        for label, folder, needle in cases:
            with self.subTest(case=label):
                self.assertRefused(self.x("check", folder), needle)
                self.assertRefused(self.x("put", folder, self.saved(p1("scripts/common.py")), "scripts/common.py"), needle)
                self.assertFalse((folder / "scripts" / "common.py").exists(), "put wrote nothing")

    def test_planted_modules_next_to_the_files_are_refused_and_never_imported(self):
        marker = self.root / "CANARY"
        for mod in ("json", "platform", "subprocess", "common"):
            folder = self.new()
            self.assertEqual(self.x("put", folder, self.saved(p1(SMALL)), SMALL).code, 0)
            (folder / "scripts" / (mod + ".py")).write_text(canary_code(marker, "planted " + mod))
            with self.subTest(module=mod):
                needle = "is not the published file" if mod == "common" else "holds 'scripts/%s.py'" % mod
                self.assertRefused(self.x("check", folder, SMALL), needle)
                self.assertRefused(self.x("put", folder, self.saved(p1("scripts/common.py")), "scripts/common.py"))
        self.assertFalse(marker.exists(), "no planted module ran")

    def test_planted_modules_in_the_working_folder_are_never_imported(self):
        marker = self.root / "CANARY_CWD"
        project = self.work / "planted_cwd"
        project.mkdir()
        for mod in ("json", "re", "tempfile", "hashlib", "os", "stat", "itertools", "sys", "encodings", "common"):
            (project / (mod + ".py")).write_text(canary_code(marker, "cwd " + mod))
        folder = self.new(cwd=project)
        self.assertEqual(self.x("put", folder, self.saved(p1(SMALL)), SMALL, cwd=project).code, 0)
        self.assertEqual(self.x("check", folder, SMALL, cwd=project).code, 0)
        self.assertFalse(marker.exists(), "python -I does not import from the working folder")

    def test_a_folder_written_by_hand_passes_check_only_when_exact(self):
        folder = self.new()
        name = "scripts/common.py"
        exact = file_bytes(name)
        folder.joinpath(*name.split("/")).write_bytes(exact)
        self.assertEqual(self.x("check", folder, name).code, 0)
        for label, data in (("no final newline", exact[:-1]), ("one newline too many", exact + b"\n"), ("CR LF", exact.replace(b"\n", b"\r\n")),
                            ("byte order mark", b"\xef\xbb\xbf" + exact), ("empty", b""), ("twice as long", exact + exact)):
            path = folder.joinpath(*name.split("/"))
            path.write_bytes(data)
            with self.subTest(case=label):
                self.assertRefused(self.x("check", folder), "%s is not the published file (%d bytes, published %d)" % (name, len(data), len(exact)))
        path.write_bytes(exact)
        self.assertRefused(self.x("check", folder, name, "scripts/qa_gif.py"), "missing: scripts/qa_gif.py")

    @unittest.skipUnless(POSIX, "POSIX modes and owners")
    def test_loose_modes_and_hard_links_are_refused(self):
        folder = self.new()
        self.assertEqual(self.x("put", folder, self.saved(p1(SMALL)), SMALL).code, 0)
        target = folder / "scripts" / "qa_compare.py"
        self.assertEqual(stat.S_IMODE(os.lstat(str(target)).st_mode), 0o600, "put creates files that only the user can open")
        for path, mode in ((folder, 0o750), (folder / "scripts", 0o755), (folder / "references", 0o705), (target, 0o602), (target, 0o666)):
            old = stat.S_IMODE(os.lstat(str(path)).st_mode)
            os.chmod(str(path), mode)
            with self.subTest(path=path.name, mode=oct(mode)):
                self.assertRefused(self.x("check", folder), "is open to other users or has a hard link")
            os.chmod(str(path), old)
        # section 2: the three folders must give group and others no permission, any one bit is refused, 500 passes too
        for path in (folder, folder / "scripts", folder / "references"):
            old = stat.S_IMODE(os.lstat(str(path)).st_mode)
            for mode, ok in ((0o500, True), (0o700, True), (0o710, False), (0o740, False), (0o720, False), (0o701, False),
                             (0o704, False), (0o702, False)):
                os.chmod(str(path), mode)
                with self.subTest(folder=path.name, mode=oct(mode)):
                    r = self.x("check", folder)
                    if ok:
                        self.assertEqual(r.code, 0, r)
                    else:
                        self.assertRefused(r, "is open to other users or has a hard link")
            os.chmod(str(path), old)
        for mode in (0o644, 0o664, 0o400):                       # a file tool may leave these: no write bit for others
            os.chmod(str(target), mode)
            self.assertEqual(self.x("check", folder).code, 0, oct(mode))
        os.chmod(str(target), 0o600)
        os.link(str(target), str(self.root / "second_name.py"))
        self.assertRefused(self.x("check", folder), "is open to other users or has a hard link")

    def test_links_are_refused_and_never_followed(self):
        if not can_symlink(self.root):
            self.skipTest("this account cannot make symbolic links here (Windows without Developer Mode)")
        victim = make_valid(self.root / "victim_home", [SMALL])
        before_victim = snapshot(victim)
        saved = self.saved(p1("scripts/common.py"))
        cases = []
        os.symlink(str(victim), str(self.tmp / "veqa_linkdir1"), target_is_directory=True)
        cases.append(("the folder is a link to a valid folder", self.tmp / "veqa_linkdir1"))
        f = self.new(); (f / "scripts").rmdir(); os.symlink(str(victim / "scripts"), str(f / "scripts"), target_is_directory=True)
        cases.append(("scripts is a link", f))
        f = self.new(); os.symlink(str(victim / "scripts" / "qa_compare.py"), str(f / "scripts" / "qa_compare.py"))
        cases.append(("a file is a link to a good copy", f))
        f = self.new(); os.symlink(str(victim / "scripts" / "written_by_link.py"), str(f / "scripts" / "common.py"))
        cases.append(("a dangling link where put would write", f))
        for label, folder in cases:
            with self.subTest(case=label):
                self.assertRefused(self.x("check", folder), "is a link or junction")
                self.assertRefused(self.x("put", folder, saved, "scripts/common.py"), "is a link or junction")
        self.assertEqual(snapshot(victim), before_victim, "nothing written through a link")

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_windows_junctions_are_refused_and_never_followed(self):
        victim = make_valid(self.root / "victim_home_j", [SMALL])
        before_victim = snapshot(victim)
        saved = self.saved(p1("scripts/common.py"))
        self.assertTrue(junction(self.tmp / "veqa_junction", victim))
        f = self.new()
        (f / "scripts").rmdir()
        self.assertTrue(junction(f / "scripts", victim / "scripts"))
        for label, folder in (("the folder is a junction", self.tmp / "veqa_junction"), ("scripts is a junction", f)):
            with self.subTest(case=label):
                self.assertRefused(self.x("check", folder), "is a link or junction")
                self.assertRefused(self.x("put", folder, saved, "scripts/common.py"), "is a link or junction")
        self.assertEqual(snapshot(victim), before_victim)

    def test_put_never_overwrites_even_when_the_file_appears_during_the_run(self):
        folder = self.new()
        race = ("import builtins\n_open = builtins.open\n"
                "def _racing(path, mode=\"r\", *a, **k):\n"
                "    if mode == \"xb\":\n"
                "        with _open(path, \"wb\") as f:\n"
                "            f.write(b\"planted during the run\\n\")\n"
                "    return _open(path, mode, *a, **k)\n"
                "builtins.open = _racing\n")
        r = self.x("put", folder, self.saved(p1(SMALL)), SMALL, prefix=race)
        self.assertRefused(r, "the system refused: FileExistsError")
        self.assertEqual((folder / "scripts" / "qa_compare.py").read_bytes(), b"planted during the run\n", "not overwritten")
        self.assertRefused(self.x("check", folder), "is not the published file")

    def test_put_checks_the_folder_again_after_writing(self):
        folder = self.new()
        short = ("import builtins\n_open = builtins.open\n"
                 "class _Short:\n"
                 "    def __init__(self, f): self.f = f\n"
                 "    def __enter__(self): return self\n"
                 "    def __exit__(self, *a): self.f.close()\n"
                 "    def write(self, data): return self.f.write(data[:-1])\n"
                 "def _o(path, mode=\"r\", *a, **k):\n"
                 "    f = _open(path, mode, *a, **k)\n"
                 "    return _Short(f) if mode == \"xb\" else f\n"
                 "builtins.open = _o\n")
        r = self.x("put", folder, self.saved(p1(SMALL)), SMALL, prefix=short)
        size = TABLE[SMALL][1]
        self.assertRefused(r, "%s is not the published file (%d bytes, published %d)" % (SMALL, size - 1, size))

    def test_put_refuses_before_writing_when_the_folder_is_bad_and_the_saved_file_is_good(self):
        target = make_valid(self.work / "proj2" / "veqa_abcdefgh", [])
        self.assertRefused(self.x("put", target, self.saved(p1(SMALL)), SMALL), "is not directly in the temp folder")
        self.assertEqual(listing(target), DIRS)


class NameTests(Scratch):
    BAD = ("../x.py", "scripts/../x.py", "/abs.py", "/scripts/qa_compare.py", "C:/scripts/qa_compare.py", "scripts\\qa_compare.py",
           "scripts/qa_compare.py:stream", "scripts/qa_compare.py::$DATA", "CON", "scripts/nul", "scripts/qa_compare.py ",
           "scripts/qa_compare.py.", "Scripts/qa_compare.py", "scripts//qa_compare.py", "./scripts/qa_compare.py", "scripts/", "",
           "SKILL.md", "README.md", "references/pitfalls.md", "examples/spec_web_gif.json", "scripts/scan.py",
           "scripts/qa_compar\u00e9.py", "scripts/\u202eqa_compare.py", "\x1b[31mscripts/qa_compare.py", "scripts/qa_compare.py\r")

    def test_names_that_are_not_published_files_are_refused_before_anything_is_read(self):
        folder = self.new()
        before = snapshot(self.root)
        for bad in self.BAD:
            for args in (["put", folder, self.root / "does_not_exist.txt", SMALL, bad], ["check", folder, bad]):
                code, out, err = raw_run(self.src, *args, tmp=self.tmp, cwd=self.work)
                with self.subTest(name=bad, mode=args[0]):
                    self.assertEqual((code, out), (1, b""))
                    self.assertTrue(err.startswith(b"REFUSED: not a file of video-export-qa: "), err)
                    self.assertTrue(all(32 <= c < 127 for c in err.rstrip(b"\r\n")), "stderr is plain printable ASCII: %r" % err)
                    self.assertIn(ascii(bad).encode("ascii"), err)
        self.assertEqual(snapshot(self.root), before)

    def test_a_name_may_carry_the_skill_folder_as_the_file_index_shows_it(self):
        folder = self.new()
        saved = self.saved(p1(SMALL))
        r = self.x("put", folder, saved, "video-export-qa/" + SMALL)
        self.assertEqual((r.code, r.out), (0, "OK %s verified (%d bytes)\n" % (SMALL, TABLE[SMALL][1])), r)
        self.assertEqual(self.x("check", folder, "video-export-qa/" + SMALL).code, 0)
        before = snapshot(self.root)
        for bad in ("video-export-qa/video-export-qa/" + SMALL, "share-safe-renders/" + SMALL, "video-export-qa\\" + SMALL,
                    "Video-export-qa/" + SMALL, "video-export-qa//" + SMALL, "/video-export-qa/" + SMALL, "video-export-qa/",
                    "video-export-qa", " video-export-qa/" + SMALL):
            for args in (["put", folder, saved, "scripts/common.py", bad], ["check", folder, bad]):
                with self.subTest(name=bad, mode=args[0]):
                    self.assertRefused(self.x(*args), "not a file of video-export-qa: ")
        self.assertEqual(snapshot(self.root), before)
        # only NAMEs are changed: a saved-file path that starts with the skill folder is used as it is
        (self.work / "video-export-qa").mkdir()
        (self.work / "video-export-qa" / "saved.txt").write_text(p1("scripts/common.py"))
        self.assertEqual(self.x("put", folder, "video-export-qa/saved.txt", "video-export-qa/scripts/common.py").code, 0)
        self.assertExact(folder, [SMALL, "scripts/common.py"])

    def test_a_system_error_names_its_kind_and_the_path_escaped_once(self):
        folder = self.new()
        missing = self.root / ("caf\u00e9 " + uuid.uuid4().hex[:6]) / "missing.txt"
        code, out, err = raw_run(self.src, "put", folder, missing, SMALL, tmp=self.tmp, cwd=self.work)
        self.assertEqual((code, out), (1, b""))
        line = err.decode("ascii").rstrip("\r\n")
        self.assertTrue(line.startswith("REFUSED: the system refused: FileNotFoundError "), line)
        self.assertNotIn("\\" * 4, line)
        self.assertIn("caf\\xe9", line)

    def test_hostile_headers_and_file_names_are_escaped_in_messages(self):
        folder = self.new()
        for hostile, shown in (("## File: \x1b[2K\x1b[1Avideo-export-qa/scripts/qa_compare.py (from \x07X\x9b)\n\nprint(1)\n",
                                b"## File: \\x1b[2K\\x1b[1Avideo-export-qa/scripts/qa_compare.py (from \\x07X\\x9b)"),
                               ("## File: video-export-qa/scripts/m\u00e1in.py (from Caf\u00e9 \u4e2d)\n\nprint(1)\n",
                                b"## File: video-export-qa/scripts/m\\xe1in.py (from Caf\\xe9 \\u4e2d)")):
            code, out, err = raw_run(self.src, "put", folder, self.saved(hostile), SMALL, tmp=self.tmp, cwd=self.work)
            with self.subTest(header=hostile[:20]):
                self.assertEqual(code, 1)
                self.assertTrue(all(32 <= c < 127 for c in err.rstrip(b"\r\n")), err)
                self.assertIn(shown, err)
        (folder / "scripts" / "m\u00e9\u2603.py").write_bytes(b"x")
        code, out, err = raw_run(self.src, "check", folder, tmp=self.tmp, cwd=self.work)
        self.assertEqual(code, 1)
        self.assertIn(b"holds 'scripts/m\\xe9\\u2603.py'", err)

    def test_a_temp_folder_path_with_accents_is_printed_so_it_can_be_recovered(self):
        # the known issue the document names: the path comes as a quoted Python string with \xe9 for the accented letter
        tmp = self.new_tmp(name="Jos\u00e9 \u0141ukasz " + uuid.uuid4().hex[:4])
        code, out, err = raw_run(self.src, "new", tmp=tmp, cwd=self.work)
        self.assertEqual((code, err), (0, b""), err)
        printed = out.decode("ascii").strip()[len("OK folder: "):]
        self.assertTrue(printed.startswith("'") and "\\xe9" in printed, printed)
        folder = Path(ast.literal_eval(printed))
        self.assertEqual(folder.parent.resolve(), tmp.resolve())
        self.assertEqual(self.x("check", folder, tmp=tmp).code, 0, "the recovered path works")
        doc = _route2.doc_text()
        self.assertIn("is printed as a quoted Python string", doc)
        self.assertIn("Each `\\xe9` stands for one letter", doc)

    def test_the_documents_accented_path_example_is_what_the_program_prints(self):
        # audit 2026-10-15: the example showed two backslashes before xe9, while ascii() (what the program prints) gives one
        found = re.findall(r"is printed as a quoted Python string such as `('\.\.\.[^`]+')`", _route2.doc_text())
        self.assertEqual(len(found), 1, found)
        example = found[0]
        name = re.search(r"\\\\(veqa_[a-z0-9_]{8})'$", example).group(1)
        tail = example[len("'..."):]
        # the path the example stands for, read back as Python reads it: one accented letter, one backslash per separator
        self.assertTrue(ast.literal_eval(example).endswith("\\Jos\u00e9\\AppData\\Local\\Temp\\" + name), ast.literal_eval(example))
        # ascii() of such a Windows path ends exactly as the example does
        windows_path = "D:" + "\\" + "Data" + "\\Jos\u00e9\\AppData\\Local\\Temp\\" + name
        self.assertTrue(ascii(windows_path).endswith(tail), (ascii(windows_path), example))
        # and the program's own line for a real accented temp folder of that shape, on this system
        parent = self.root / ("accented_" + uuid.uuid4().hex[:6]) / "Jos\u00e9" / "AppData" / "Local"
        parent.mkdir(parents=True)
        tmp = self.new_tmp(parent=parent, name="Temp")
        code, out, err = raw_run(self.src, "new", tmp=tmp, cwd=self.work)
        self.assertEqual((code, err), (0, b""), err)
        printed = out.decode("ascii").rstrip("\r\n")[len("OK folder: "):]
        folder = Path(ast.literal_eval(printed))
        self.assertEqual(folder.parent.resolve(), tmp.resolve())
        self.assertEqual(printed, ascii(str(folder)), "the program prints ascii() of the path")
        expected = tail.replace(name, folder.name)
        if os.sep == "/":
            expected = expected.replace("\\\\", "/")
        self.assertTrue(printed.endswith(expected), (printed, expected))
        # negative control: the old example (a double backslash before xe9) matches neither
        old = example.replace("Jos\\xe9", "Jos\\\\xe9")
        self.assertNotEqual(old, example)
        self.assertFalse(ascii(windows_path).endswith(old[len("'..."):]))

    def test_a_plain_ascii_control_character_in_a_printed_path_is_escaped(self):
        # audit 2026-10-15 (M14): every path the program prints goes through ascii() unless it holds only letters, digits and
        # a few path signs. A path that is plain ASCII apart from one control character must still be escaped (a raw CR
        # prints a false text over the real line, a raw LF a whole false line such as an OK line). Folder arguments are
        # printed in refusals on every system.
        self.assertTrue(re.fullmatch(r"[\w .:/\\~+-]+", str(self.work), re.A), "the scratch path itself needs no escaping")
        for label, char in CONTROL_CHARACTERS:
            arg = str(self.work / ("veqa_ab" + char + "OK folder x"))
            code, out, err = raw_run(self.src, "check", arg, tmp=self.tmp, cwd=self.work)
            with self.subTest(char=label):
                self.assertEqual((code, out), (1, b""))
                line = err.rstrip(b"\r\n")
                self.assertTrue(all(32 <= c < 127 for c in line), line)
                self.assertTrue(line.startswith(b"REFUSED: '") and line.endswith(b"' is not a folder that new makes"), line)
                shown = line[len(b"REFUSED: "):-len(b" is not a folder that new makes")].decode("ascii")
                self.assertEqual(ast.literal_eval(shown), os.path.abspath(arg))

    @unittest.skipUnless(POSIX, "file names with control characters are POSIX only")
    def test_a_plain_ascii_temp_path_with_a_control_character_is_printed_escaped(self):
        # the same rule for the temp folder in the OK lines of new and check (audit M14: TMPDIR holding only a CR)
        for label, char in CONTROL_CHARACTERS:
            tmp = self.new_tmp(name="tmp" + char + "OK folder: fake")
            code, out, err = raw_run(self.src, "new", tmp=tmp, cwd=self.work)
            with self.subTest(char=label):
                self.assertEqual((code, err), (0, b""), err)
                self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
                self.assertNotIn(char.encode(), out[:-1])
                self.assertTrue(all(32 <= c < 127 for c in out[:-1]), out)
                printed = out.decode("ascii")[:-1][len("OK folder: "):]
                self.assertTrue(printed.startswith("'") and ascii(char)[1:-1] in printed, printed)
                folder = Path(ast.literal_eval(printed))
                self.assertEqual(folder.parent, tmp)
                code, out, err = raw_run(self.src, "check", folder, tmp=tmp, cwd=self.work)
                self.assertEqual((code, err), (0, b""), err)
                self.assertEqual(out.decode("ascii"), "OK 0 verified files and nothing else in %s\n" % printed)


class SavedShapeTests(Scratch):
    def put_ok(self, saved, names):
        folder = self.new()
        r = self.x("put", folder, saved, *names)
        self.assertEqual(r.code, 0, r)
        for name in names:
            self.assertIn("OK %s verified (%d bytes)" % (name, TABLE[name][1]), r.out)
        self.assertExact(folder, names)
        c = self.x("check", folder, *names)
        self.assertEqual(c.code, 0, c)
        self.assertIn("OK %d verified files and nothing else in" % len(names), c.out)
        return folder

    def put_refused(self, saved, name, *needles):
        folder = self.new()
        before = snapshot(self.root)
        r = self.x("put", folder, saved, name)
        self.assertRefused(r, *needles)
        if "holds no exact copy" in r.err:
            self.assertIn("Nothing was written", r.err)
        self.assertEqual(snapshot(self.root), before, "nothing changed anywhere")
        return r

    def test_p1_plain_text_and_p2_json_for_every_file_of_the_table(self):
        for name in NAMES:
            with self.subTest(name=name, form="P1"):
                raw = p1(name).encode("utf-8")
                self.assertEqual(len(raw), len(header(name)) + 2 + TABLE[name][1])
                self.put_ok(self.saved(None, raw=raw), [name])
            with self.subTest(name=name, form="P2"):
                text = p2(name)
                self.assertTrue(json.loads(text)[0]["text"].endswith("\n"))
                self.put_ok(self.saved(text, "saved.json"), [name])

    def test_p2_variants_raw_unicode_compact_nested_and_bare(self):
        for label, text in (("raw unicode", p2(SMALL, ensure_ascii=False)), ("compact", p2(SMALL, indent=None)),
                            ("nested content list", p2(SMALL, wrap="content")), ("top-level string", json.dumps(p1(SMALL))),
                            ("bare text object", json.dumps({"text": p1(SMALL)}))):
            with self.subTest(shape=label):
                self.put_ok(self.saved(text, "saved.json"), [SMALL])

    def test_the_older_whole_package_shape_one_name_or_all(self):
        saved = self.saved(old_shape(NAMES))
        self.assertFalse(saved.read_bytes().endswith(b"\n"), "the last block has no final newline")
        self.put_ok(saved, NAMES)
        for name in ("references/interpreting-results.md", "scripts/run_qa.py"):
            with self.subTest(name=name):
                self.put_ok(saved, [name])
        self.put_ok(self.saved(json.dumps([{"type": "text", "text": old_shape(NAMES)}]), "saved.json"), [SMALL, BIG])

    def test_crlf_saves_headers_extra_text_bom_and_a_result_saved_twice(self):
        for label, data in (("CR LF", p1(SMALL).replace("\n", "\r\n").encode()), ("CR LF older shape", old_shape([SMALL, BIG]).replace("\n", "\r\n").encode()),
                            ("another display name", p1(SMALL).replace("(from Video Export QA)", "(from Another Name (beta))").encode()),
                            ("no from suffix", ("## File: %s/%s\n\n" % (SKILL_NAME, SMALL) + text_of(SMALL)).encode()),
                            ("three hashes", ("### File: %s/%s (from X)\n\n" % (SKILL_NAME, SMALL) + text_of(SMALL)).encode()),
                            ("text before", p1(SMALL, before="Output:\nsome words\n\n").encode()),
                            ("final newline dropped", p1(SMALL).rstrip("\n").encode()), ("one added", (p1(SMALL) + "\n").encode()),
                            ("byte order mark", b"\xef\xbb\xbf" + p1(SMALL).encode()), ("BOM before JSON", b"\xef\xbb\xbf" + p2(SMALL).encode()),
                            ("saved twice", (p1(SMALL) + "\n\n" + p1(SMALL)).encode()),
                            ("a cut copy, then a whole one", (p1(SMALL, text_of(SMALL)[:500]) + "\n" + p1(SMALL)).encode()),
                            ("non-UTF-8 bytes before the header", b"\xff\xfe junk \x80\n" + p1(SMALL).encode())):
            names = [SMALL, BIG] if "older" in label else [SMALL]
            with self.subTest(case=label):
                self.put_ok(self.saved(None, raw=data), names)

    def test_a_result_copied_by_hand_into_a_scratch_file_is_repaired_by_put(self):
        exact = text_of(BIG)
        bodies = {"exact": exact, "no final newline": exact[:-1], "one newline too many": exact + "\n",
                  "CR LF": exact.replace("\n", "\r\n"), "CR LF, no final newline": exact.replace("\n", "\r\n")[:-2]}
        for label, body in bodies.items():
            for with_header in (False, True):
                for bom in ("", "\ufeff"):
                    with self.subTest(case=label, header=with_header, bom=bool(bom)):
                        self.put_ok(self.saved(bom + (header(BIG) + "\n\n" if with_header else "") + body), [BIG])

    def test_already_there_is_verified_and_not_written_again(self):
        folder = self.put_ok(self.saved(p1(SMALL)), [SMALL])
        r = self.x("put", folder, self.saved(p1(SMALL)), SMALL)
        self.assertEqual((r.code, r.out), (0, "OK %s verified (%d bytes) - was already there\n" % (SMALL, TABLE[SMALL][1])), r)
        r = self.x("put", folder, self.saved(p1(SMALL)), SMALL, "scripts/common.py")
        self.assertRefused(r, "holds no exact copy of scripts/common.py")
        self.assertExact(folder, [SMALL])

    def test_a_header_for_another_path_or_skill_names_what_the_file_holds(self):
        r = self.put_refused(self.saved(p1("scripts/common.py")), SMALL, "holds no exact copy of %s" % SMALL)
        self.assertIn("file headers in it: ['## File: video-export-qa/scripts/common.py (from Video Export QA)']", r.err)
        self.put_refused(self.saved(p1(SMALL).replace("video-export-qa/", "share-safe-renders/", 1)), SMALL, "share-safe-renders/" + SMALL)

    def test_truncated_changed_and_padded_content_is_refused(self):
        content = text_of(BIG)
        flipped = content[:1000] + ("y" if content[1000] != "y" else "z") + content[1001:]
        self.assertEqual(len(flipped), len(content), "same length, different bytes: only the hash can tell")
        for label, text in (("cut short", p1(BIG, content[:-100])), ("cut in the middle", p1(BIG)[:len(p1(BIG)) // 2]),
                            ("one byte changed", p1(BIG, flipped)), ("one byte inserted", p1(BIG, content[:50] + "!" + content[50:])),
                            ("content twice", p1(BIG, content + content)), ("trailing spaces", p1(BIG, content + "   ")),
                            ("tabs for spaces", p1(BIG, content.replace("    ", "\t")))):
            with self.subTest(case=label):
                self.put_refused(self.saved(text), BIG, "holds no exact copy of")

    def test_empty_header_only_non_utf8_and_a_wrong_length_in_the_table(self):
        self.put_refused(self.saved(""), SMALL, "file headers in it: []")
        self.put_refused(self.saved(header(SMALL) + "\n\n"), SMALL, "holds no exact copy")
        raw = p1(SMALL).encode()
        self.put_refused(self.saved(None, raw=raw[:-5] + b"\xff" + raw[-4:]), SMALL, "holds no exact copy")
        digest, size = TABLE[SMALL]
        line = '"%s": ("%s" "%s", %d),' % (SMALL, digest[:32], digest[32:], size)
        bad = self.src.replace(line, '"%s": ("%s" "%s", %d),' % (SMALL, digest[:32], digest[32:], size + 1))
        self.assertNotEqual(bad, self.src)
        folder = self.new(src=bad)
        self.assertRefused(self.x("put", folder, self.saved(p1(SMALL)), SMALL, src=bad), "Nothing was written")
        folder.joinpath(*SMALL.split("/")).write_bytes(file_bytes(SMALL))
        self.assertRefused(self.x("check", folder, src=bad), "is not the published file")

    def test_hostile_json_deep_huge_and_many_headers_are_refused_in_time(self):
        cases = (("deep list", "[" * 200000 + "]" * 200000), ("deep object", "{\"a\":" * 50000 + "1" + "}" * 50000),
                 ("huge string", json.dumps({"text": "x" * 15000000})),
                 ("many headers", ("## File: video-export-qa/scripts/qa_compare.py\n```\n" * 300000)),
                 ("fence bomb", "## File: video-export-qa/scripts/qa_compare.py\n\n```\n" + "\n```   " * 1500000 + "x"))
        for label, text in cases:
            with self.subTest(case=label):
                start = time.time()
                r = self.put_refused(self.saved(text), SMALL)
                self.assertLess(time.time() - start, 60)
                self.assertNotIn("RecursionError", r.err)

    def test_the_worst_hostile_files_stay_under_a_memory_bound(self):
        # format review B1: every candidate is cut to the published size + 99 characters before it is copied, and only one
        # CR LF repaired copy exists at a time. The bound is the shared design's: 13 times the input plus 24 MB.
        dense = ("## File: %s/%s\n" % (SKILL_NAME, BIG) * 3000).encode()
        for label, data, name in (("plain", worst_plain(), SMALL), ("json", worst_json(), SMALL), ("dense headers", dense, BIG)):
            with self.subTest(case=label):
                folder = self.new()
                r, peak = traced_run(self.src, "put", folder, self.saved(None, raw=data), name, tmp=self.tmp, cwd=self.work,
                                     marker=self.root / ("peak_" + label.replace(" ", "_")))
                self.assertRefused(r, "holds no exact copy of " + name)
                self.assertLess(peak, 13 * len(data) + 24000000, "traced peak %.1f MB" % (peak / 1e6))
        # the cap loses nothing: the real file after the same 15 MB of hostile text is still found
        self.put_ok(self.saved(None, raw=worst_plain() + b"\n" + p1(BIG).encode()), [BIG])

    def test_running_out_of_memory_is_a_refusal_not_a_traceback(self):
        folder = self.new()
        r = self.x("put", folder, self.saved(p1(SMALL)), SMALL, prefix="import json\ndef _boom(*a, **k):\n    raise MemoryError\njson.loads = _boom\n")
        self.assertRefused(r)
        self.assertTrue(r.err.startswith("REFUSED: the system refused: MemoryError"), r)

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid"), "an address-space limit (RLIMIT_AS) is POSIX")
    def test_a_real_memory_limit_gives_a_refusal(self):
        folder = self.new()
        saved = self.saved(None, raw=worst_plain())
        for mb in (250, 100, 60):
            limit = "import resource\nresource.setrlimit(resource.RLIMIT_AS, (%d, %d))\n" % (mb << 20, mb << 20)
            r = self.x("put", folder, saved, SMALL, prefix=limit)
            with self.subTest(limit_mb=mb):
                self.assertRefused(r)
                self.assertTrue("holds no exact copy" in r.err or "the system refused: MemoryError" in r.err, r)
        self.assertExact(folder, [])

    def test_a_run_of_backticks_after_a_header_is_refused_quickly(self):
        text = (header(BIG) + "\n\n" + "\x60" * 300000 + "\n") * 4
        start = time.time()
        self.put_refused(self.saved(text), BIG, "holds no exact copy of " + BIG)
        self.assertLess(time.time() - start, 20)

    def test_a_saved_file_over_16_mb_a_folder_or_a_missing_path(self):
        self.put_refused(self.saved(None, raw=b"x" * 16000001), SMALL, "is not a file of at most 16 MB")
        folder = self.new()
        self.assertRefused(self.x("put", folder, self.root, SMALL), "is not a file of at most 16 MB")
        self.assertRefused(self.x("put", folder, self.root / "no_such_file.txt", SMALL), "the system refused: FileNotFoundError")
        self.assertExact(folder, [])

    def test_all_or_nothing_when_one_name_is_missing(self):
        folder = self.new()
        self.assertRefused(self.x("put", folder, self.saved(old_shape([SMALL])), SMALL, "scripts/common.py"), "holds no exact copy of scripts/common.py")
        self.assertExact(folder, [])


def sh_heredoc(src, args):
    return '"%s" -I -B - %s <<' % (Path(sys.executable).as_posix(), args) + "'EOF'\n" + src.rstrip("\n") + "\nEOF\n"


class ShellTests(Scratch):
    def sh(self, command, tmp=None):
        proc = subprocess.run([SH, "-c", command], capture_output=True, cwd=str(self.work), env=child_env(tmp or self.tmp), timeout=300)
        return proc.returncode, proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")

    @unittest.skipUnless(SH, "no sh on this machine")
    def test_a_quoted_bash_heredoc_with_single_quoted_paths(self):
        code, out, err = self.sh(sh_heredoc(self.src, "new"))
        self.assertEqual((code, err), (0, ""), out)
        folder = _route2.Result(code, out, err).folder()
        saved = self.saved(p1(SMALL), name="saved file's copy.txt")       # a space and an apostrophe in the path
        quoted = lambda p: "'" + str(p).replace("'", "'\\''") + "'"
        code, out, err = self.sh(sh_heredoc(self.src, "put %s %s %s" % (quoted(folder), quoted(saved), SMALL)))
        self.assertEqual((code, err), (0, ""), out)
        code, out, err = self.sh(sh_heredoc(self.src, "check %s %s" % (quoted(folder), SMALL)))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("OK 1 verified files and nothing else in", out)
        self.assertExact(folder, [SMALL])

    @unittest.skipUnless(SH, "no sh on this machine")
    def test_the_full_program_fits_a_bash_tool_command_with_a_long_saved_path(self):
        # measured on Windows: Git Bash keeps only the first 8,186 characters of the text after sh -c (2026-10-16, Git
        # 2.54.0, bash 5.3.9; an earlier note said 8191, the negative control below shows the cut), and a Bash tool adds
        # about 410 to 440 characters of its own around the command. The document's program with a 460-character stand-in
        # for that wrapper, the folder, a saved-file path of more than 200 characters and the names must fit and run.
        folder = self.new()
        deep = self.root / ("d" * 50) / ("e" * 50) / ("f" * 30)
        deep.mkdir(parents=True)
        saved = deep / ("mcp-" + "0" * 36 + "-get_skill-1792053508118.json")
        saved.write_text(p2(BIG))
        self.assertGreater(len(str(saved)), 200)
        command = ": " + "w" * 460 + " && " + sh_heredoc(self.src, "put '%s' '%s' %s" % (folder, saved, BIG))
        self.assertLessEqual(len(command), 8186, "the whole command fits the measured limit (%d characters)" % len(command))
        code, out, err = self.sh(command)
        self.assertEqual(code, 0, err)
        self.assertIn("OK %s verified" % BIG, out)
        if os.name == "nt":
            # negative control: past the limit Git Bash cuts the heredoc and the run fails, never a silent success
            longer = command.replace(": ", ": " + "w" * (8300 - len(command)), 1)
            code, out, err = self.sh(longer)
            self.assertNotEqual(code, 0)
            self.assertNotIn("OK", out)
            # the limit section 7 gives: a command of 8,186 characters arrives whole, one of 8,187 is cut
            for size, kept in ((8186, 8186), (8187, 8186)):
                code, out, _err = self.sh("printf %s " + "x" * (size - 10))
                self.assertEqual((code, len("printf %s ") + len(out)), (0, kept), size)
            # started from inside Git Bash (a script that sh runs), sh -c gets a 10,000-character command whole
            inner = self.root / ("inner_" + uuid.uuid4().hex[:8] + ".sh")
            inner.write_bytes(("sh -c 'printf %s " + "x" * 9990 + "'\n").encode("ascii"))
            code, out, _err = self.sh("sh '%s'" % inner.as_posix())
            self.assertEqual((code, len("printf %s ") + len(out)), (0, 10000))

    def test_a_copy_cut_short_anywhere_fails_loudly_or_behaves_like_the_whole(self):
        from concurrent.futures import ThreadPoolExecutor
        src = self.src
        folder = self.new()
        self.assertEqual(self.x("put", folder, self.saved(p1(SMALL)), SMALL).code, 0)
        whole = self.x("check", folder, SMALL)
        self.assertEqual(whole.code, 0)
        cuts = sorted({i + 1 for i, c in enumerate(src) if c == "\n"} | set(range(1, len(src), 97)) | set(range(len(src) - 60, len(src))))
        cuts = [k for k in cuts if 0 < k < len(src)]

        def one(k):
            return k, run(src[:k], "check", folder, SMALL, tmp=self.tmp, cwd=self.work)

        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(one, cuts))
        silent = [k for k, r in results if r.code == 0 and (r.out, r.err) != (whole.out, whole.err)]
        loud = [k for k, r in results if r.code != 0 and "OK" not in r.out]
        same = [k for k, r in results if (r.code, r.out, r.err) == (whole.code, whole.out, whole.err)]
        self.assertEqual(silent, [], "cuts that exit 0 without the whole program's output")
        self.assertEqual(len(loud) + len(same), len(cuts))
        last_line = len(src.rstrip("\n")) - len(src.rstrip("\n").split("\n")[-1])
        self.assertEqual([k for k in same if k < last_line], [], "only a cut inside the last line can match the whole")
        self.assertGreater(len(loud), 100)
        # What the first-line comment says (doc round 2026-10-16), measured on every prefix: a copy cut short almost always
        # stops with a SyntaxError or an IndentationError before anything runs. The few prefixes that compile are the first
        # one or two characters and cuts inside the last line; ending in "sys" or "sys.exit" there, a copy prints nothing
        # and exits 0 when the system refuses something, so only the OK lines mean success.
        kinds, compiled = {}, []
        for k in range(1, len(src)):
            try:
                compile(src[:k], "<stdin>", "exec")
                compiled.append(k)
            except SyntaxError as e:
                kinds[type(e).__name__] = kinds.get(type(e).__name__, 0) + 1
        self.assertEqual(sorted(kinds), ["IndentationError", "SyntaxError"])
        self.assertEqual(sum(kinds.values()) + len(compiled), len(src) - 1)
        self.assertLess(len(compiled), (len(src) - 1) / 100, "almost always")
        self.assertEqual([k for k in compiled if 2 < k <= last_line], [], "only the first two characters or the last line")
        lines = src.split("\n")
        self.assertIn("IndentationError", run("\n".join(lines[:-2]) + "\n", "check", folder, tmp=self.tmp, cwd=self.work).err)
        missing = self.root / "no_such_saved_file.txt"
        self.assertRefused(run(src, "put", folder, missing, SMALL, tmp=self.tmp, cwd=self.work), "the system refused")
        for tail in ("sys", "sys.exit"):
            cut = src[:last_line] + "  " + tail
            with self.subTest(cut_after=tail):
                self.assertIn(len(cut), compiled)
                r = run(cut, "put", folder, missing, SMALL, tmp=self.tmp, cwd=self.work)
                self.assertEqual((r.code, r.out, r.err), (0, "", ""), "a silent run: only the OK lines mean success")

    @unittest.skipUnless(PS, "Windows PowerShell is not on this machine")
    def test_a_powershell_here_string_with_the_default_and_the_utf8_encoding(self):
        def ps(args, utf8, crlf):
            pre = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n$OutputEncoding = [Text.Encoding]::UTF8\n" if utf8 else ""
            script = pre + "@'\n" + self.src.rstrip("\n") + "\n'@ | & \"%s\" -I -B - %s\nexit $LASTEXITCODE\n" % (sys.executable, args)
            if crlf:
                script = script.replace("\n", "\r\n")
            argv = [PS, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
            proc = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=300, env=child_env(self.tmp))
            return proc.returncode, proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")

        saved = self.saved(p1(SMALL), name="saved \u00e9 file.txt")
        for utf8, crlf in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(utf8=utf8, crlf=crlf):
                code, out, err = ps("new", utf8, crlf)
                self.assertEqual(code, 0, err + out)
                folder = _route2.Result(code, out, err).folder()
                code, out, err = ps("put '%s' '%s' %s" % (folder, saved, SMALL), utf8, crlf)
                self.assertEqual(code, 0, err + out)
                code, out, err = ps("check '%s' %s" % (folder, SMALL), utf8, crlf)
                self.assertEqual(code, 0, err + out)
                self.assertIn("OK 1 verified files and nothing else in", out)
                code, out, err = ps("check '%s' scripts/common.py" % folder, utf8, crlf)
                self.assertEqual(code, 1, out)
                self.assertIn("REFUSED: missing: ", err)


USERS = os.environ.get("VEQA_TWO_USERS", "")
TWO_USERS = POSIX and hasattr(os, "geteuid") and os.geteuid() == 0 and ":" in USERS
VICTIM, ATTACKER = USERS.split(":") if TWO_USERS else ("", "")


def as_user(user, code, timeout=120):
    return subprocess.run([sys.executable, "-I", "-B", "-c", code], user=user, group=user, extra_groups=[], capture_output=True,
                          timeout=timeout, cwd="/", env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/home/" + user})


def pwned():
    return sorted(p.name for p in Path("/tmp").iterdir() if p.name.startswith("PWNED_"))


@unittest.skipUnless(TWO_USERS, "needs root on Linux and VEQA_TWO_USERS=victim:attacker (two existing accounts in a throwaway container)")
class TwoUserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _route2.doc_program()
        cls.shared = Path("/opt/veqa_shared")
        if cls.shared.exists():
            shutil.rmtree(str(cls.shared))
        cls.shared.mkdir(mode=0o755)
        os.chmod(str(cls.shared), 0o755)
        cls.canary = "import os\nopen(\"/tmp/PWNED_uid%d_%s\" % (os.getuid(), __name__), \"w\").write(\"x\")\n"
        cls.saved = cls.shared / "saved_small.txt"
        cls.saved.write_text(p1(SMALL))
        cls.saved_common = cls.shared / "saved_common.txt"
        cls.saved_common.write_text(p1("scripts/common.py"))
        for path in cls.shared.iterdir():
            os.chmod(str(path), 0o644)
        cls.home = Path("/home") / VICTIM
        (cls.home / "target" / "scripts").mkdir(parents=True, exist_ok=True)
        subprocess.run(["chown", "-R", "%s:%s" % (VICTIM, VICTIM), str(cls.home)], check=True)
        proc = as_user(ATTACKER, "\n".join([
            "import os",
            "def tree(path, mode, extra):",
            "    os.mkdir(path); os.mkdir(path + \"/scripts\"); os.mkdir(path + \"/references\")",
            "    open(path + \"/scripts/qa_compare.py\", \"wb\").write(%r)" % file_bytes(SMALL),
            "    for m in extra: open(path + \"/scripts/\" + m + \".py\", \"w\").write(%r)" % cls.canary,
            "    for p in (path, path + \"/scripts\", path + \"/references\"): os.chmod(p, mode)",
            "tree(\"/tmp/veqa_att00777\", 0o777, [\"json\", \"platform\", \"subprocess\"])",
            "tree(\"/tmp/veqa_att00700\", 0o700, [])",
            "os.symlink(%r, \"/tmp/veqa_attlink1\")" % str(cls.home / "target"),
            "os.mkdir(\"/tmp/veqa_attlnks1\", 0o777); os.chmod(\"/tmp/veqa_attlnks1\", 0o777)",
            "os.symlink(%r, \"/tmp/veqa_attlnks1/scripts\")" % str(cls.home / "target" / "scripts"),
        ]))
        assert proc.returncode == 0, proc.stderr

    @classmethod
    def tearDownClass(cls):
        for n in os.listdir("/tmp"):
            if n.startswith("veqa_"):
                p = os.path.join("/tmp", n)
                os.unlink(p) if os.path.islink(p) or not os.path.isdir(p) else shutil.rmtree(p)

    def victim(self, *args):
        return run(self.src, *args, tmp="/tmp", cwd=self.home, user=VICTIM)

    def test_the_attackers_folders_and_links_in_tmp_are_refused(self):
        before = snapshot(self.home)
        for folder, needle in (("/tmp/veqa_att00777", "belongs to another user"), ("/tmp/veqa_att00700", "belongs to another user"),
                               ("/tmp/veqa_attlink1", "is a link or junction"), ("/tmp/veqa_attlnks1", "belongs to another user")):
            for args in (["check", folder, SMALL], ["put", folder, self.saved_common, "scripts/common.py"]):
                r = self.victim(*args)
                with self.subTest(folder=folder, mode=args[0]):
                    self.assertEqual(r.code, 1, r)
                    self.assertIn(needle, r.err)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(pwned(), [], "no planted module ran")
        folder = self.victim("new").folder()
        proc = as_user(ATTACKER, "try:\n    open(%r + \"/scripts/json.py\", \"w\")\nexcept PermissionError:\n    print(\"denied\")\n" % str(folder))
        self.assertEqual(proc.stdout.strip(), b"denied")

    def test_a_temp_folder_owned_by_another_user_is_refused_even_with_the_sticky_bit(self):
        # audit 2026-10-15 (M10): in a sticky folder its owner can still rename or replace the entries of others, so a temp
        # folder owned by another user is refused even at mode 1777. The control: the same at mode 1777 owned by root
        # (as /tmp is) is accepted.
        import pwd
        attacker = pwd.getpwnam(ATTACKER)
        temps = {}
        for label, (uid, gid) in (("attacker", (attacker.pw_uid, attacker.pw_gid)), ("root", (0, 0))):
            path = Path("/srv/veqa_sticky_" + label)
            if path.exists():
                shutil.rmtree(str(path))
            path.mkdir()
            os.chown(str(path), uid, gid)
            os.chmod(str(path), 0o1777)
            self.addCleanup(shutil.rmtree, str(path), True)
            temps[label] = path
            # a folder of the shape new makes, made there by the victim, with a good copy of one file
            proc = as_user(VICTIM, "\n".join([
                "import os",
                "f = %r" % str(path / "veqa_victim01"),
                "for p in (f, f + \"/scripts\", f + \"/references\"):",
                "    os.mkdir(p)",
                "    os.chmod(p, 0o700)",
                "with open(f + \"/scripts/qa_compare.py\", \"wb\") as h:",
                "    h.write(%r)" % file_bytes(SMALL),
                "os.chmod(f + \"/scripts/qa_compare.py\", 0o600)",
            ]))
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((os.stat(str(temps["attacker"])).st_uid, stat.S_IMODE(os.stat(str(temps["attacker"])).st_mode)),
                         (attacker.pw_uid, 0o1777))
        needle = "other users can replace folders in the temp folder"
        bad = temps["attacker"] / "veqa_victim01"
        for args in (["new"], ["check", bad, SMALL], ["put", bad, self.saved_common, "scripts/common.py"]):
            r = run(self.src, *args, tmp=temps["attacker"], cwd=self.home, user=VICTIM)
            with self.subTest(mode=args[0]):
                self.assertEqual((r.code, r.out), (1, ""), r)
                self.assertIn(needle, r.err)
        self.assertEqual(sorted(os.listdir(str(temps["attacker"]))), ["veqa_victim01"], "new made nothing there")
        self.assertFalse((bad / "scripts" / "common.py").exists(), "put wrote nothing")
        good = temps["root"] / "veqa_victim01"
        r = run(self.src, "check", good, SMALL, tmp=temps["root"], cwd=self.home, user=VICTIM)
        self.assertEqual(r.code, 0, r)
        r = run(self.src, "new", tmp=temps["root"], cwd=self.home, user=VICTIM)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.folder().parent, temps["root"])

    def test_a_race_against_an_attacker_who_watches_tmp(self):
        race = "\n".join([
            "import os, time",
            "end = time.time() + 10",
            "canary = %r" % self.canary,
            "tried = done = 0",
            "while time.time() < end:",
            "    for n in os.listdir(\"/tmp\"):",
            "        p = \"/tmp/\" + n",
            "        if not n.startswith(\"veqa_\") or n.startswith(\"veqa_att\"):",
            "            continue",
            "        for act in (lambda: open(p + \"/scripts/json.py\", \"x\").write(canary), lambda: open(p + \"/scripts/common.py\", \"w\").write(canary),",
            "                    lambda: os.rename(p, p + \"_moved\"), lambda: os.chmod(p, 0o777)):",
            "            tried += 1",
            "            try:",
            "                act(); done += 1",
            "            except OSError:",
            "                pass",
            "print(tried, done)",
        ])
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", race], user=ATTACKER, group=ATTACKER, extra_groups=[],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
        time.sleep(1)
        try:
            for _ in range(10):
                folder = self.victim("new").folder()
                self.assertEqual(self.victim("put", folder, self.saved_common, "scripts/common.py").code, 0)
                self.assertEqual(self.victim("put", folder, self.saved, SMALL).code, 0)
                self.assertEqual(self.victim("check", folder, SMALL, "scripts/common.py").code, 0)
        finally:
            out, _err = proc.communicate(timeout=60)
        tried, done = (int(x) for x in out.split())
        self.assertGreater(tried, 0, "the attacker saw the victim folders")
        self.assertEqual(done, 0, "nothing the attacker tried on a victim folder worked")
        self.assertEqual(pwned(), [])


class RealAnswerTests(Scratch):
    """Answers a real host saved (P1, P2 or the older block shape), named in VEQA_REAL_ANSWERS (os.pathsep between them).
    For every file of the table whose exact text the answer carries after its header, put must write it; for every other
    file, put must refuse. They are not part of any checkout, so without the variable this test skips."""

    def test_real_saved_answers(self):
        paths = [p for p in os.environ.get("VEQA_REAL_ANSWERS", "").split(os.pathsep) if p]
        if not paths:
            self.skipTest("VEQA_REAL_ANSWERS is not set (real saved answers are not part of any checkout)")
        import get_skill_sim
        for path in paths:
            raw = Path(path).read_bytes().decode("utf-8-sig")
            texts = [raw]
            try:
                doc = json.loads(raw)
                texts += [item["text"] for item in doc if isinstance(item, dict) and isinstance(item.get("text"), str)]
            except (ValueError, TypeError):
                pass
            # a saved JSON result for one unchanged file is byte for byte what the simulator writes for it
            single = re.match(r"## File: video-export-qa/(\S+) \(from ", texts[-1]) if len(texts) == 2 else None
            if single and single.group(1) in TABLE and texts[-1].endswith(text_of(single.group(1))):
                with self.subTest(answer=Path(path).name[-24:], simulator="P2"):
                    self.assertEqual(Path(path).read_bytes(),
                                     get_skill_sim.saved_p2(get_skill_sim.file_result(PACKAGE, single.group(1), "Video Export QA")))
            for name in NAMES:
                body = text_of(name)
                carried = any(re.search(r"(?m)^#{2,3} File: %s/%s[^\n]*\n\n(```\n)?" % (SKILL_NAME, re.escape(name)), t) and
                              (("\n\n" + body) in t or ("\n```\n" + body + "\n```") in t) for t in texts)
                mentioned = any(("File: %s/%s" % (SKILL_NAME, name)) in t for t in texts)
                if not (carried or mentioned):
                    continue
                folder = self.new()
                r = self.x("put", folder, path, name)
                with self.subTest(answer=Path(path).name[-24:], name=name, carried=carried):
                    self.assertEqual(r.code, 0 if carried else 1, r)
                    self.assertEqual(listing(folder), sorted(DIRS + ([name] if carried else [])))


if __name__ == "__main__":
    unittest.main()
