"""Make the TABLE literal (or the whole specialised extractor) for one skill package.

usage:
  python -I -B gen_table.py PACKAGE NAME...                                     print the TABLE entries
  python -I -B gen_table.py PACKAGE NAME... --skill S --prefix P --out FILE     write the specialised extractor
  (NAME is a path inside the package such as scripts/scan.py; --scripts adds every file of scripts/)

Every name must be a plain file of the package, one folder deep, with a plain name. Each digest is written as two 32-digit
halves that Python joins (no 64-digit hex run in the text). The specialised extractor is checked before it is written: ASCII,
no apostrophe, dollar sign, backtick, tab or CR (so it survives a quoted bash heredoc and a PowerShell here-string), Python 3.9
grammar, and the placeholders replaced. A double backslash or a backslash before a double quote is refused too: a heredoc that
reaches Git Bash through a Windows command line loses one backslash of every pair (measured 2026-10-15). Exit 0 done, 2 refused.
"""
import argparse
import ast
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "extract_core.py"
NAME = re.compile(r"[a-z]+/[A-Za-z0-9_][A-Za-z0-9_.-]*")
SKILL = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
PREFIX = re.compile(r"[a-z][a-z0-9]*_")
FORBIDDEN = {"'": "apostrophe", "$": "dollar sign", "\x60": "backtick", "\t": "tab", "\r": "carriage return",
             # measured 2026-10-15: a command that reaches Git Bash through a Windows command line (an agent's Bash tool on
             # Windows) has every double backslash turned into one, even inside a quoted heredoc
             "\\\\": "double backslash", "\\\"": "backslash before a double quote"}
PLACEHOLDERS = ('"__SKILL__"', '"__PREFIX__"', "  # __TABLE__\n")
# Measured 2026-10-15: Git Bash on Windows cuts the text after `sh -c` at 8191 characters, and an agent's Bash tool wraps
# each command in about 440 more (more with a longer home path). A put command adds the heredoc frame, the folder, a saved
# file path of up to about 250 characters and the name. 6800 leaves room for all of that.
MAX_CHARS = 6800
# Measured 2026-10-15: results under about 50 KB came back inline in the conversation (not saved to a file), so an agent
# copies such a file by hand. A file tool can turn an escape such as \u00e9 in the text into the character itself, and a
# heredoc through the Windows Bash tool halves every double backslash (format review R8). Such files are flagged, not
# refused: put and check refuse a changed copy, so the risk is a refused run, never a wrong file.
INLINE_BYTES = 50000
RISKY_ESCAPE = re.compile(rb"\\(?:[uUx]|N\{)|\\\\")


class Refused(Exception):
    pass


def table_entries(package, names):
    """{name: (sha256 hex, length in bytes)} for the named files of the package."""
    package = Path(package)
    table = {}
    for name in names:
        if not NAME.fullmatch(name):
            raise Refused("not a plain one-folder-deep name: %s" % ascii(name))
        path = package.joinpath(*name.split("/"))
        st = os.lstat(str(path)) if os.path.lexists(str(path)) else None
        if st is None or not stat.S_ISREG(st.st_mode):
            raise Refused("not a plain file of the package: %s" % ascii(name))
        data = path.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused("not UTF-8 text, so it cannot travel as text: %s" % ascii(name))
        table[name] = (hashlib.sha256(data).hexdigest(), len(data))
    return table


def inline_risks(package, table):
    """Warnings for TABLE files small enough to come back inline whose text a hand copy could change."""
    out = []
    for name, (_digest, size) in sorted(table.items()):
        if size < INLINE_BYTES:
            hits = len(RISKY_ESCAPE.findall(Path(package).joinpath(*name.split("/")).read_bytes()))
            if hits:
                out.append("%s (%d bytes) may come back inline and holds %d backslash escapes (\\u, \\U, \\x, \\N{ or a double "
                           "backslash) that a hand copy can change: agents must write it with a file tool, never a shell "
                           "heredoc, and put or check will refuse a changed copy" % (name, size, hits))
    return out


def table_literal(table):
    lines = []
    for name in sorted(table):
        digest, size = table[name]
        lines.append('  "%s": ("%s" "%s", %d),\n' % (name, digest[:32], digest[32:], size))
    return "".join(lines)


def snippet_problems(text, max_chars=MAX_CHARS):
    """What makes a text unfit to be fed on standard input through a quoted heredoc or here-string."""
    problems = []
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        problems.append("not ASCII")
    for char, label in FORBIDDEN.items():
        if char in text:
            problems.append("holds a " + label)
    if re.search(r"(?m)^EOF$", text):
        problems.append("holds a line EOF")
    if max_chars and len(text) > max_chars:
        problems.append("longer than %d characters (%d): a Bash tool on Windows would cut the command" % (max_chars, len(text)))
    if re.search(r"\b[0-9a-fA-F]{64}\b", text):
        problems.append("holds a 64-digit hex run")
    for placeholder in ("__SKILL__", "__PREFIX__", "__TABLE__"):
        if placeholder in text:
            problems.append("placeholder left: " + placeholder)
    try:
        ast.parse(text, feature_version=(3, 9))
    except SyntaxError as e:
        problems.append("not Python 3.9 grammar: %s" % e)
    return problems


def specialise(template_text, skill, prefix, table, max_chars=MAX_CHARS):
    if not SKILL.fullmatch(skill):
        raise Refused("skill name must be lower-case words joined by hyphens: %s" % ascii(skill))
    if not PREFIX.fullmatch(prefix):
        raise Refused("prefix must be lower-case letters or digits ending in an underscore: %s" % ascii(prefix))
    if not table:
        raise Refused("no files: the TABLE would be empty")
    for placeholder in PLACEHOLDERS:
        if template_text.count(placeholder) != 1:
            raise Refused("the template does not hold the placeholder once: %s" % ascii(placeholder))
    text = (template_text.replace(PLACEHOLDERS[0], '"%s"' % skill)
            .replace(PLACEHOLDERS[1], '"%s"' % prefix)
            .replace(PLACEHOLDERS[2], table_literal(table)))
    problems = snippet_problems(text, max_chars)
    if problems:
        raise Refused("the specialised extractor is not fit to paste: " + "; ".join(problems))
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gen_table.py", description="Make the TABLE (or the extractor) for one skill package.")
    parser.add_argument("package")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--scripts", action="store_true", help="add every file of scripts/")
    parser.add_argument("--skill")
    parser.add_argument("--prefix")
    parser.add_argument("--template", default=str(TEMPLATE))
    parser.add_argument("--out")
    args = parser.parse_intermixed_args(argv)          # names may follow the options (parse_args refused that on 3.10)
    names = list(args.names)
    if args.scripts:
        names += sorted("scripts/" + p.name for p in Path(args.package, "scripts").iterdir() if p.is_file())
    try:
        table = table_entries(args.package, sorted(set(names)))
        for warning in inline_risks(args.package, table):
            sys.stderr.write("WARNING: %s\n" % warning)
        if args.out is None:
            sys.stdout.write(table_literal(table))
            return 0
        if not (args.skill and args.prefix):
            raise Refused("--out needs --skill and --prefix")
        text = specialise(Path(args.template).read_bytes().decode("ascii"), args.skill, args.prefix, table)
        Path(args.out).write_bytes(text.encode("ascii"))
        print("wrote %s: %d lines, %d files in TABLE" % (args.out, text.count("\n"), len(table)))
        return 0
    except Refused as e:
        sys.stderr.write("REFUSED: %s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
