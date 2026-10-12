#!/usr/bin/env python3
# common.py - shared helpers for the video-export-qa scripts.
#
# Purpose: check/result bookkeeping, ffmpeg/ffprobe discovery, command recording,
#          provenance (tool versions, input hash, spec hash) and exit codes.
# Inputs:  none. This module is imported by the other scripts; it is not run directly.
# Outputs: none. Helpers return dicts/strings; scripts decide what to print.
# Exit codes used by the scripts: 0 = PASS or WARN only, 1 = at least one FAIL,
#          2 = could not run (missing tool, unreadable input, bad arguments).
#
# Rules this module follows: standard library only, no network, every subprocess call takes an
# argument list (never a shell string), and no environment variables are read except the program
# search path (PATH), which find_on_path() reads through os.get_exec_path() to locate ffmpeg and ffprobe.
# ffmpeg and ffprobe themselves inherit your environment (see references/pitfalls.md).

import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from fractions import Fraction

VERSION = "1.2.0"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# Longest piece of text kept from a string that a media file supplies (tags, comments, decoder
# messages). Whoever made the file wrote that text, so it is data to quote, never an instruction,
# and one hostile tag must not be able to flood a report.
MAX_FILE_TEXT = 200

# Input option for every ffmpeg/ffprobe command that opens a user's file. A media file can be a
# playlist whose lines are web addresses; with only the local file protocol allowed, such a file is
# refused ("Protocol 'http' not on whitelist") before any connection is attempted. It goes right
# before each input (before "-i" for ffmpeg, before the file name for ffprobe).
LOCAL_FILES_ONLY = ["-protocol_whitelist", "file"]


class QAError(Exception):
    """Base class for errors that mean 'could not run' (exit code 2)."""


class ToolMissing(QAError):
    pass


class UsageError(QAError):
    pass


def is_current_folder(entry, here):
    """True when `entry` is the current folder `here`, however it is spelled (a link or a junction too)."""
    if here is None:
        return False
    try:
        return os.path.samefile(entry, here)
    except OSError:  # an entry that does not exist cannot hold a tool
        return False


def find_on_path(name):
    """First executable called `name` in the program search path, or None.

    Only absolute search-path entries are used, and the current folder is never searched: a delivery
    folder that holds its own ffmpeg.exe must not be able to replace the real tool. (shutil.which
    would search the current folder first on Windows.) On Windows only "<name>.exe" counts.
    """
    try:
        here = os.getcwd()
    except OSError:  # the current folder was deleted: there is nothing to keep out of the search
        here = None
    names = [name + ".exe"] if os.name == "nt" else [name]
    for entry in os.get_exec_path():
        if not entry or not os.path.isabs(entry) or is_current_folder(entry, here):
            continue
        for candidate_name in names:
            candidate = os.path.join(entry, candidate_name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return os.path.abspath(candidate)
    return None


def skipped_current_folder_copy(name):
    """The path of `name` when the only copy sits in the current folder AND that folder is listed on PATH
    (it looks installed, but the current folder is never searched), else None. Used only for the error message."""
    try:
        here = os.getcwd()
    except OSError:
        return None
    names = [name + ".exe"] if os.name == "nt" else [name]
    for entry in os.get_exec_path():
        if entry and os.path.isabs(entry) and is_current_folder(entry, here):
            for candidate_name in names:
                candidate = os.path.join(entry, candidate_name)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)
    return None


def find_tool(name, override=None):
    """Return the absolute path of ffmpeg/ffprobe: explicit path first, else the program search path
    (never the current folder)."""
    if override:
        if os.path.isfile(override):
            return os.path.abspath(override)
        raise ToolMissing("%s not found at the path given: %s" % (name, override))
    found = find_on_path(name)
    if not found:
        in_here = skipped_current_folder_copy(name)
        if in_here:
            raise ToolMissing(
                "%s was found only in the current folder (%s), which is listed on PATH, but the current folder is "
                "never searched (a folder holding an export must not be able to replace the real tool). Run the "
                "command from the folder that holds the export, not from FFmpeg's own folder, or pass --%s PATH."
                % (name, in_here, name)
            )
        raise ToolMissing(
            "%s was not found on PATH (the current folder is never searched). Install FFmpeg and put its "
            "folder on PATH, or pass --%s PATH." % (name, name)
        )
    return found


def add_common_args(parser):
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--ffmpeg", default=None, help="path to ffmpeg (default: found on PATH)")
    parser.add_argument("--ffprobe", default=None, help="path to ffprobe (default: found on PATH)")


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")


def decode_spec_text(raw, path):
    """The text of a spec file. UTF-8 (with or without a byte order mark) and UTF-16 with a byte order mark
    (what Windows PowerShell writes with > or Out-File) are read; anything else is a clear error."""
    if raw.startswith(UTF32_BOMS):
        raise UsageError("spec file %s is saved as UTF-32, which is not read; save it as UTF-8 (a BOM is accepted)" % path)
    if raw.startswith(UTF16_BOMS):
        try:
            return raw.decode("utf-16")  # this codec reads the byte order mark and drops it
        except UnicodeDecodeError:
            raise UsageError("spec file %s starts like UTF-16 text but cannot be read as UTF-16; save it as UTF-8" % path)
    if b"\x00" in raw[:4]:
        raise UsageError("spec file %s looks like UTF-16 or UTF-32 text without a byte order mark; save it as UTF-8 "
                         "(or as UTF-16 with a byte order mark)" % path)
    try:
        return raw.decode("utf-8-sig")  # drops a UTF-8 byte order mark when there is one
    except UnicodeDecodeError as exc:
        raise UsageError("spec file %s is not UTF-8 text (an unreadable byte at position %d); save it as UTF-8"
                         % (path, exc.start))


def load_spec(path):
    """Read a JSON spec file. Returns (dict, sha256 of the file bytes as they are on disk)."""
    if path is None:
        return {}, None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise UsageError("cannot read spec file %s: %s" % (path, exc))
    text = decode_spec_text(raw, path)
    try:
        spec = json.loads(text)
    except RecursionError:
        raise UsageError("spec file %s is nested too deeply to read" % path)
    except ValueError as exc:
        raise UsageError("spec file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(spec, dict):
        raise UsageError("spec file %s must contain a JSON object" % path)
    return spec, hashlib.sha256(raw).hexdigest()


# A frame rate is written as 24, 23.976 or 24000/1001: plain digits only, a bounded length. Python's
# Fraction would also take exponent notation such as 1e100000000 and try to expand it into an
# enormous integer, which hangs the run or ends in an OverflowError. (Patterns are given to
# re.fullmatch as text; the static tests forbid the name "compile" anywhere in the scripts.)
DECIMAL_TEXT = r"[+-]?(?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})"
RATIO_TEXT = r"[+-]?\d{1,12}/\d{1,12}"
MAX_FRACTION_CHARS = 64  # the longest legitimate text is about 28 characters ("-123456789012 / 123456789012")


def parse_fraction(value):
    """'24', 24, '23.976', '24000/1001' -> Fraction (exact for decimals and fractions)."""
    problem = "cannot read %s as a number or fraction (digits only, for example 24, 23.976 or 24000/1001)"
    try:
        text = str(value).strip()
        if len(text) <= MAX_FRACTION_CHARS:  # checked first, so a megabyte of spaces is never scanned
            text = re.sub(r"\s*/\s*", "/", text)  # spaces around the slash do not matter
            if re.fullmatch(DECIMAL_TEXT, text) or re.fullmatch(RATIO_TEXT, text):
                return Fraction(text)
    except (ValueError, ZeroDivisionError, OverflowError):
        pass  # falls through to the message below (for example a 5000 digit integer)
    raise UsageError(problem % short_repr(value))


def clip_text(value, limit=MAX_FILE_TEXT):
    """Text cut to `limit` characters (a cut is marked with '...'). Other types pass through."""
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[: max(limit - 3, 0)] + "..."


def short_repr(value, limit=60):
    """repr() for an error message that cannot itself fail or fill the screen: a spec value can be a
    5000 digit integer (repr refuses those) or a deeply nested list (repr raises RecursionError)."""
    try:
        text = repr(value)
    except (ValueError, RecursionError, MemoryError):
        return "<a value too large to show>"
    return clip_text(text, limit)


def make_stdout_safe():
    """A console or pipe that cannot print some character of a file name must not crash a report:
    unprintable characters are written as backslash escapes instead of raising an error."""
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def tool_version(path):
    """First line of '<tool> -version' (empty string if it cannot be read)."""
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    return text.splitlines()[0] if text else ""


# Terminal colour and cursor codes. ffmpeg writes them into its messages when your own environment
# asks for coloured logs (AV_LOG_FORCE_COLOR); left in, they stop "[Parsed_ebur128_1 @ ..." lines from
# matching the patterns that read each measurement. They carry no information, so they are removed.
ANSI_ESCAPE = r"\x1b\[[0-9;?]*[ -/]*[@-~]"


def strip_ansi(text):
    return re.sub(ANSI_ESCAPE, "", text)


def run(cmd, report=None, timeout=None):
    """Run an argument list. Returns (returncode, stdout_text, stderr_text).

    The command is recorded on the report so the result documents how it was made. Terminal colour
    codes are removed from the text (see ANSI_ESCAPE).
    """
    if not isinstance(cmd, (list, tuple)):
        raise TypeError("commands must be argument lists, never shell strings")
    if report is not None:
        report.commands.append([str(c) for c in cmd])
    proc = subprocess.run(list(cmd), capture_output=True, timeout=timeout)
    return (
        proc.returncode,
        strip_ansi(proc.stdout.decode("utf-8", errors="replace")),
        strip_ansi(proc.stderr.decode("utf-8", errors="replace")),
    )


def now_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Report:
    """Collects checks, facts and provenance for one script run."""

    def __init__(self, script, file=None, tools=None):
        self.script = script
        self.file = file
        self.tools = tools or {}  # {"ffmpeg": path, "ffprobe": path}
        self.checks = []
        self.info = {}
        self.commands = []
        self.spec_sha256 = None

    def add(self, check_id, status, expected=None, actual=None, detail="", unmeasured=False, remedy=None):
        """Record one check. `unmeasured=True` (only meaningful with SKIP) marks a check that was asked
        for, or always runs, and could not be measured because a tool failed, the file records nothing to
        measure it with, or the file does not allow it. It is kept in the JSON as "unmeasured": true and
        stops the overall verdict from being PASS. `remedy` (only with unmeasured) is the specific advice
        run_qa.py prints under "What to do" for that row; without it the general advice is printed.
        A spec key that was asked for and cannot be measured is never a PASS: it is unmeasured, or a FAIL where
        the requirement cannot be met at all (an audio limit on a file without audio). That includes
        max_av_start_offset_s on a file that lacks the audio or the video stream, which is unmeasured. A SKIP that
        is correct by design is not unmeasured: no video stream, so no video checks; no audio stream when no audio
        key was asked for; a declared edit judged by `expect`; the same rate in video.fps and gif.fps (judged by
        rounding_drift); a requested value that makes the check vacuous (video.cfr false; max_av_start_offset_s
        on a file without audio when the spec itself says audio.present is false)."""
        if status not in (PASS, WARN, FAIL, SKIP):
            raise ValueError("bad status %r" % (status,))
        entry = {"id": check_id, "status": status, "expected": expected, "actual": actual, "detail": detail}
        if unmeasured and status == SKIP:
            entry["unmeasured"] = True
            if remedy:
                entry["remedy"] = remedy
        self.checks.append(entry)

    def unmeasured_ids(self):
        return [c["id"] for c in self.checks if c.get("unmeasured")]

    def overall(self):
        """FAIL if any check failed, else WARN if any warned, else PASS if at least one check was
        measured and matched and nothing that was asked for went unmeasured. When some measurement
        passed but a requested one could not be made, the answer is WARN: part of the file was not
        checked. When every check is SKIP (or there are none) nothing was measured, and the answer is
        SKIP, never PASS (hard rule: nothing is a PASS that was not measured)."""
        statuses = [c["status"] for c in self.checks]
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        if PASS in statuses:
            return WARN if self.unmeasured_ids() else PASS
        return SKIP

    def exit_code(self):
        return 1 if self.overall() == FAIL else 0

    def provenance(self):
        prov = {
            "script": self.script,
            "version": VERSION,
            "python": platform.python_version(),
            "created_utc": now_utc_iso(),
            "ffmpeg_version": tool_version(self.tools["ffmpeg"]) if "ffmpeg" in self.tools else None,
            "ffprobe_version": tool_version(self.tools["ffprobe"]) if "ffprobe" in self.tools else None,
            "input": None,
            "spec_sha256": self.spec_sha256,
            "commands": self.commands,
        }
        if self.file and os.path.isfile(self.file):
            st = os.stat(self.file)
            prov["input"] = {
                "path": self.file,
                "size_bytes": st.st_size,
                "mtime_utc": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "sha256": sha256_file(self.file),
            }
        return prov

    def to_dict(self):
        return {
            "tool": self.script,
            "file": self.file,
            "overall": self.overall(),
            "checks": self.checks,
            "info": self.info,
            "provenance": self.provenance(),
        }

    def table(self):
        lines = ["%s: %s  (%s)" % (self.script, self.overall(), self.file or "-")]
        counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for c in self.checks:
            counts[c["status"]] += 1
        lines.append(
            "PASS %d  WARN %d  FAIL %d  SKIP %d" % (counts[PASS], counts[WARN], counts[FAIL], counts[SKIP])
        )
        skipped = [c["id"] for c in self.checks if c["status"] == SKIP and not c.get("unmeasured")]
        if skipped:
            lines.append("Skipped, no claim made (SKIP proves nothing): %s" % ", ".join(skipped))
        if self.unmeasured_ids():
            lines.append("NOT MEASURED (a check that applies could not be measured, so the result is not a PASS): %s"
                         % ", ".join(self.unmeasured_ids()))
        if self.overall() == SKIP:
            lines.append("Nothing was measured: every check was skipped. Read the SKIP reasons below.")
        lines.append("")
        for c in self.checks:
            detail = ("NOT MEASURED: " if c.get("unmeasured") else "") + (c["detail"] or "")
            if len(detail) > DETAIL_LIMIT:
                detail = detail[: DETAIL_LIMIT - 3] + "..."
            lines.append(
                "[%-4s] %-28s expected=%s actual=%s %s"
                % (c["status"], c["id"], _short(c["expected"]), _short(c["actual"]), detail)
            )
            lines.extend(_full_text_lines(c))
        return "\n".join(lines)


VALUE_LIMIT = 40  # table cells for expected / actual
DETAIL_LIMIT = 110  # table cell for the detail


def _text(value):
    return json.dumps(value) if not isinstance(value, str) else value


def _short(value, limit=VALUE_LIMIT):
    text = _text(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _full_text_lines(check):
    """A row (any status but PASS) whose values were cut for the table gets the whole text on extra
    lines, so numbers and SKIP reasons can be copied exactly. (--json always holds everything.)"""
    if check["status"] == PASS:
        return []
    lines = []
    for label, text, limit in (
        ("expected", _text(check["expected"]), VALUE_LIMIT),
        ("actual", _text(check["actual"]), VALUE_LIMIT),
        ("detail", check["detail"] or "", DETAIL_LIMIT),
    ):
        if len(text) > limit:
            lines.append("       full %s: %s" % (label, text))
    return lines


def emit(report, as_json):
    """Print the report (table or JSON) and return the exit code."""
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=False))
    else:
        print(report.table())
    return report.exit_code()


def refuse_overwrite(path, overwrite):
    if os.path.exists(path) and not overwrite:
        raise UsageError("%s already exists; pass --overwrite to replace it" % path)


def run_main(main):
    """Call main() and map 'could not run' errors to exit code 2 with a message on stderr.

    Exit code 1 is reserved for 'a check FAILED', so nothing that merely stops the run may leave
    through an uncaught exception (a traceback exits with 1): a bad output folder, a file the
    operating system refuses, a spec nested too deeply and so on are all 'could not run'.
    """
    make_stdout_safe()
    try:
        return main()
    except QAError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print("error: file not found: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 2
    except RecursionError:
        print("error: an input is nested too deeply to read", file=sys.stderr)
        return 2
    except MemoryError:
        print("error: ran out of memory while reading an input", file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
