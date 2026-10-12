#!/usr/bin/env python3
# run_qa.py - run every video-export-qa check on one file and print one merged report.
#
# Purpose: one command for the whole job. It runs qa_probe.py (container, streams, spec),
#          qa_audio.py (loudness, silence), optionally qa_frames.py (which source frame sits
#          on every output frame, when a barcode clip was exported) and, for a file whose name
#          ends in .gif, qa_gif.py (frame delays, duration, loop). The results are merged into
#          one Markdown report: provenance header, PASS/WARN/FAIL/SKIP counts, one row per
#          check, then "Next steps" for every FAIL, WARN and NOT MEASURED check, taken from the
#          table in references/interpreting-results.md.
# Inputs:  FILE                    the exported video, audio or GIF to check
#          --spec SPEC.json        expectations (optional). Keys of qa_probe.py and qa_audio.py
#                                  work as documented there. Two extra sections drive the other
#                                  parts:  "barcode": {"expect": "0-95", "region": "0,0,1,0.0833"}
#                                  and  "gif": {"fps": 24, "expect_duration_s": 2.0,
#                                  "expect_frames": 48, "tol_s": 0.02}
#          --barcode-expect SEGS   run qa_frames.py with this --expect (wins over the spec)
#          --barcode-region R      barcode area as x0,y0,x1,y1 fractions (wins over the spec)
#          --out-dir DIR           also write DIR/report.md and DIR/report.json (DIR is created;
#                                  nothing is overwritten unless --overwrite is given)
#          --format md|json        what is printed on stdout (default md); --json is the same as
#                                  --format json
#          --ffmpeg / --ffprobe    optional tool paths (default: found on PATH)
# Outputs: the report on stdout; with --out-dir also the two files. Nothing else is written and
#          no input file is ever modified or deleted.
# Exit codes: 0 = overall PASS or WARN only, 1 = at least one FAIL, 2 = could not run
#          (missing tool, unreadable input, bad spec or arguments, output file exists).
#
# Rules: standard library only, no environment reads except PATH, no network, argument-list subprocess only
# (inside the parts). A check that was not measured is SKIP and is never counted as a PASS.

import argparse
import json
import os
import re
import sys

# python -I (isolated mode) leaves the script folder off sys.path, so add it before importing
# the modules that sit next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import qa_audio  # noqa: E402
import qa_frames  # noqa: E402
import qa_gif  # noqa: E402
import qa_probe  # noqa: E402
from common import FAIL, PASS, SKIP, WARN  # noqa: E402

SCRIPT_NAME = "run_qa.py"
GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "interpreting-results.md")

BARCODE_SPEC_KEYS = ("expect", "region")
GIF_SPEC_KEYS = ("fps", "expect_duration_s", "expect_frames", "tol_s")

# Limits for table cells; the full text of every failure is repeated under "Next steps".
EXPECTED_ACTUAL_LIMIT = 70
DETAIL_LIMIT = 200

# av_duration_match: picture and sound may differ by this much without a WARN. The last audio packet
# ends up to one codec frame past the picture (AAC 0.021 s, Opus up to 0.06 s), and a Matroska file is
# 0.021 s longer than its picture; a real mismatch (audio not cut with the picture) is whole seconds.
AV_LENGTH_TOLERANCE_S = 0.1


# --------------------------------------------------------------------------- reading the spec

def read_barcode_options(spec, cli_expect, cli_region):
    """Return (expect, region, unknown_keys). The command line wins over the spec."""
    section = spec.get("barcode")
    if section is not None and not isinstance(section, dict):
        raise common.UsageError('spec key "barcode" must be a JSON object such as {"expect": "0-95"}')
    section = section or {}
    unknown = ["barcode." + key for key in section if key not in BARCODE_SPEC_KEYS]
    expect = cli_expect if cli_expect is not None else section.get("expect")
    region = cli_region if cli_region is not None else section.get("region")
    if expect is not None:
        if not isinstance(expect, str):
            raise common.UsageError('spec key "barcode.expect" must be a string such as "0-95", got %r' % (expect,))
        qa_frames.parse_expect(expect)  # a bad grammar is exit code 2 now, before any decoding
    if isinstance(region, (list, tuple)):
        region = ",".join(str(part) for part in region)
    if region is not None:
        if not isinstance(region, str):
            raise common.UsageError('spec key "barcode.region" must be "x0,y0,x1,y1", got %r' % (region,))
        qa_frames.parse_region(region)
    return expect, region, unknown


def read_gif_options(spec):
    """Return (options for qa_gif.build_report, unknown_keys). Empty options if no gif section."""
    section = spec.get("gif")
    if section is not None and not isinstance(section, dict):
        raise common.UsageError('spec key "gif" must be a JSON object such as {"fps": 24}')
    section = section or {}
    unknown = ["gif." + key for key in section if key not in GIF_SPEC_KEYS]
    options = {}
    if "fps" in section:
        qa_probe.spec_fps(section["fps"], "gif.fps")  # names the key itself when the value is bad
        options["fps"] = section["fps"]
    if "expect_duration_s" in section:
        options["expect_duration"] = qa_probe.spec_number(section["expect_duration_s"], "gif.expect_duration_s")
    if "expect_frames" in section:
        options["expect_frames"] = qa_probe.spec_int(section["expect_frames"], "gif.expect_frames")
    if "tol_s" in section:
        options["tol"] = qa_probe.spec_number(section["tol_s"], "gif.tol_s")
    return options, unknown


# --------------------------------------------------------------------------- running the parts

class Part:
    """One finished sub-report and the name it is shown under."""

    def __init__(self, script, report, label):
        self.script = script
        self.report = report
        self.label = label  # short text for the "parts run" line


def video_stream_found(probe_report):
    """True when qa_probe found a real video stream (cover art does not count)."""
    return bool(probe_report.info.get("video"))


def frames_part_without_video(expect):
    """qa_frames needs a video stream. Say so with a FAIL row instead of stopping the run."""
    report = common.Report(qa_frames.SCRIPT)
    report.add("expect", FAIL, expect, None,
               "the file has no video stream, so the barcode could not be read (nothing was measured)")
    return report


def picture_seconds(video):
    """Length of the picture in seconds from what decoded: frames counted divided by the frame rate."""
    if not video or not video.get("counted_frames"):
        return None
    fps = qa_probe.as_ratio(video.get("avg_frame_rate")) or qa_probe.as_ratio(video.get("r_frame_rate"))
    if not fps or not qa_probe.has_real_frame_rate(video):
        return None
    return video["counted_frames"] / float(fps)


def av_length_check(probe_report, audio_report):
    """av_duration_match: does the sound last as long as the picture? A barcode run proves which PICTURE
    frames survived an edit; it says nothing about the sound, so an edit whose picture was cut but whose
    audio was not would otherwise pass. Compares decoded audio (first audio stream) with decoded picture."""
    expected = "sound and picture within %.1f s of each other" % AV_LENGTH_TOLERANCE_S
    picture = picture_seconds(probe_report.info.get("video"))
    sound = audio_report.info.get("audio_duration_s")
    row = {"id": "av_duration_match", "expected": expected, "actual": None}
    if picture is None or sound is None:
        row.update(status=SKIP, detail="needs a decoded picture with a frame rate and a decoded audio stream "
                                       "(no audio, no video or an unreadable stream), so the two were not compared")
        return row
    gap = sound - picture
    row["actual"] = "picture %.3f s, sound %.3f s" % (picture, sound)
    if abs(gap) <= AV_LENGTH_TOLERANCE_S:
        row.update(status=PASS, detail=("the sound and the picture are the same length" if gap == 0 else
                                        "the sound is %.3f s %s than the picture" % (abs(gap), "longer" if gap > 0 else "shorter")))
    else:
        row.update(status=WARN, detail=(
            "the sound is %.3f s %s than the picture. If the picture was cut or extended, the audio was not cut "
            "with it. (A music tail or a deliberate pause is fine; the barcode proves picture frames only.)"
            % (abs(gap), "longer" if gap > 0 else "shorter")))
    return row


def run_parts(path, spec, spec_sha, tools, barcode_expect, barcode_region):
    """Run the parts that apply and return (parts, extra_checks). Order = order in the report."""
    gif_options, gif_unknown = read_gif_options(spec)
    is_gif = path.lower().endswith(".gif")
    extra_checks = []
    for name in gif_unknown:
        extra_checks.append(unknown_key_check(name, "keys inside gif: " + ", ".join(GIF_SPEC_KEYS)))
    if "gif" in spec and not is_gif:
        extra_checks.append({
            "id": "unknown_spec_key", "status": WARN, "expected": "a file whose name ends in .gif",
            "actual": "gif", "detail": "the gif section applies only to .gif files; this file was not checked as a GIF",
        })

    parts = []
    probe = qa_probe.analyze(path, spec, tools, spec_sha)
    parts.append(Part(qa_probe.SCRIPT_NAME, probe, "qa_probe.py"))
    audio = qa_audio.analyze(path, spec, tools, spec_sha)
    parts.append(Part(qa_audio.SCRIPT_NAME, audio, "qa_audio.py"))
    if not is_gif:
        extra_checks.append(av_length_check(probe, audio))

    if barcode_expect is not None:
        if video_stream_found(probe):
            region = qa_frames.parse_region(barcode_region)
            frames = qa_frames.check_file(path, tools["ffmpeg"], tools["ffprobe"], barcode_expect, region)
        else:
            frames = frames_part_without_video(barcode_expect)
        parts.append(Part(qa_frames.SCRIPT, frames, "qa_frames.py (expect %s)" % barcode_expect))

    if is_gif:
        gif = qa_gif.build_report(path, **gif_options)
        parts.append(Part(qa_gif.SCRIPT_NAME, gif, "qa_gif.py"))
    return parts, extra_checks


def unknown_key_check(name, hint):
    return {
        "id": "unknown_spec_key", "status": WARN, "expected": "a documented key", "actual": name,
        "detail": "not a documented key, so it was ignored (%s)" % hint,
    }


# --------------------------------------------------------------------------- merging

def merge(path, tools, spec_sha, parts, extra_checks, notes):
    """Build one common.Report holding every check (each tagged with its source script).

    The same unknown spec key is reported by several parts; it is listed once.
    """
    merged = common.Report(SCRIPT_NAME, file=path, tools=tools)
    merged.spec_sha256 = spec_sha
    seen_unknown = set()
    for part in parts:
        for check in part.report.checks:
            if check["id"] == "unknown_spec_key":
                key = json.dumps(check["actual"], sort_keys=True)
                if key in seen_unknown:
                    continue
                seen_unknown.add(key)
            row = dict(check)
            row["source"] = part.script
            merged.checks.append(row)
        merged.commands.extend(part.report.commands)
    for check in extra_checks:
        key = json.dumps(check["actual"], sort_keys=True)
        if check["id"] == "unknown_spec_key" and key in seen_unknown:
            continue
        seen_unknown.add(key)
        row = dict(check)
        row["source"] = SCRIPT_NAME
        merged.checks.append(row)
    merged.info = {"parts_run": [part.label for part in parts], "notes": notes}
    for part in parts:
        merged.info[part.script.replace(".py", "")] = part.report.info
    return merged


def count_statuses(checks):
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for check in checks:
        counts[check["status"]] += 1
    return counts


# --------------------------------------------------------------------------- guidance table

def load_guidance(path=GUIDE_PATH):
    """Read references/interpreting-results.md into {check id: {meaning, causes, action}}.

    A table row looks like:  | `check_id` | what it means | likely causes | what to do |
    A missing file gives an empty dict (the report then says no guidance was found).
    """
    guidance = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return guidance
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.match(r"^`[A-Za-z0-9_.]+`$", cells[0]):
            guidance[cells[0].strip("`")] = {"meaning": cells[1], "causes": cells[2], "action": cells[3]}
    return guidance


# --------------------------------------------------------------------------- Markdown

def clean(text):
    """One line, no table-breaking characters."""
    return str(text).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def shorten(text, limit):
    text = clean(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def show_scalar(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def show_value(value):
    """Short text for a table cell: key=value pairs for a flat dictionary, JSON for the rest."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return show_scalar(value)
    if isinstance(value, dict) and all(v is None or isinstance(v, (str, bool, int, float)) for v in value.values()):
        return ", ".join("%s=%s" % (key, show_scalar(item)) for key, item in value.items())
    return json.dumps(value, allow_nan=False)


def quote_arg(arg):
    arg = str(arg)
    return '"%s"' % arg if re.search(r"[\s*;|&<>()$`!]", arg) else arg


def display_commands(commands, input_path, tools):
    """Commands with tool and input paths shortened, so a report does not carry folder names."""
    swaps = {os.path.abspath(input_path): input_path} if input_path else {}
    for name, tool in tools.items():
        swaps[tool] = name
    lines = []
    for command in commands:
        lines.append(" ".join(quote_arg(swaps.get(arg, arg)) for arg in command))
    return lines


def table_row(cells):
    return "| " + " | ".join(cells) + " |"


def markdown_report(data, guidance, tools, spec_name):
    """Render the merged report dictionary (Report.to_dict() output) as Markdown."""
    prov = data["provenance"]
    checks = data["checks"]
    counts = count_statuses(checks)
    source = prov.get("input") or {}
    out = ["# Video export QA report", ""]
    out.append("**Overall: %s** - %d PASS, %d WARN, %d FAIL, %d SKIP (SKIP means not measured; it is never a pass)."
               % (data["overall"], counts[PASS], counts[WARN], counts[FAIL], counts[SKIP]))
    unmeasured = [c["id"] for c in checks if c.get("unmeasured")]
    if unmeasured:
        out += ["", "**NOT MEASURED: some checks that apply could not be measured: %s.** The result is at best a WARN, "
                    "never a PASS, because part of the file was not checked. The reason is in the Detail column "
                    "(for example an ffmpeg that is too old or built without a filter, or a file too short or "
                    "damaged for that measurement)."
                % ", ".join("`%s`" % name for name in unmeasured)]
    if data["overall"] == SKIP:
        out += ["", "**Nothing was measured: every check was skipped, so this report proves nothing.** "
                    "The reasons are in the Detail column."]
    out += ["", "## What was checked", "", table_row(["Item", "Value"]), table_row(["---", "---"])]
    rows = [
        ("File", data["file"]),
        ("Size", "%s bytes" % format(source.get("size_bytes", 0), ",")),
        ("Modified (UTC)", source.get("mtime_utc")),
        ("SHA-256 of the file", source.get("sha256")),
        ("Spec", "%s (sha256 %s)" % (spec_name, prov["spec_sha256"]) if prov["spec_sha256"] else "none given"),
        ("Parts run", ", ".join(data["info"]["parts_run"])),
        ("Report made (UTC)", prov["created_utc"]),
        ("Tool", "%s %s on Python %s" % (prov["script"], prov["version"], prov["python"])),
        ("ffmpeg", prov["ffmpeg_version"]),
        ("ffprobe", prov["ffprobe_version"]),
    ]
    out += [table_row([clean(name), clean(show_value(value))]) for name, value in rows]

    out += ["", "## Summary", "", table_row(["PASS", "WARN", "FAIL", "SKIP"]), table_row(["---", "---", "---", "---"]),
            table_row([str(counts[PASS]), str(counts[WARN]), str(counts[FAIL]), str(counts[SKIP])])]

    out += ["", "## Checks", "", table_row(["Status", "Check", "From", "Expected", "Measured", "Detail"]),
            table_row(["---", "---", "---", "---", "---", "---"])]
    for check in checks:
        out.append(table_row([
            check["status"], "`%s`" % check["id"], check["source"],
            shorten(show_value(check["expected"]), EXPECTED_ACTUAL_LIMIT),
            shorten(show_value(check["actual"]), EXPECTED_ACTUAL_LIMIT),
            shorten(("NOT MEASURED: " if check.get("unmeasured") else "") + (check["detail"] or "-"), DETAIL_LIMIT),
        ]))
    out += ["", "Long values are shortened in this table. The complete text of every FAIL and WARN is repeated under "
                "Next steps, and report.json (with --out-dir) keeps everything whole."]
    if "runs" in (data["info"].get("qa_frames") or {}):  # absent when the file had no video to read
        out += ["", "## Barcode runs", "",
                "Which source frame sits on which output frame, read from the barcode on every frame "
                "(output frame numbers and source frame numbers both start at 0):", "", "```"]
        out += qa_frames.runs_lines(data["info"]["qa_frames"]) + ["```"]

    out += ["", "## Next steps", ""] + next_steps(checks, guidance)
    out += ["", "## How this report was made", "",
            "Commands that were run (tool and file paths shortened; the exact argument lists are in report.json):", ""]
    commands = display_commands(prov["commands"], data["file"], tools)
    out += ["- `%s`" % line for line in commands] or ["- none (qa_gif.py reads the file itself)"]
    return "\n".join(out) + "\n"


def next_steps(checks, guidance):
    """One entry per FAIL, WARN or unmeasured check id, FAILs first. PASS and a SKIP that is correct by
    design need no action; a SKIP that applies but could not be measured is listed last."""
    ordered = ([c for c in checks if c["status"] == FAIL] + [c for c in checks if c["status"] == WARN]
               + [c for c in checks if c.get("unmeasured")])
    if not ordered and not any(c["status"] == PASS for c in checks):
        return ["Nothing was measured: every check was skipped. Read the reasons in the Detail column."]
    if not ordered:
        return ["Nothing to fix: no check failed or warned. Checks marked SKIP were not measured and prove nothing."]
    lines, done = [], set()
    for check in ordered:
        if check["id"] in done:
            continue
        done.add(check["id"])
        entry = guidance.get(check["id"])
        label = "NOT MEASURED" if check.get("unmeasured") else check["status"]
        lines.append("%d. **%s** (%s, from %s)" % (len(done), check["id"], label, check["source"]))
        lines.append("   - Measured: %s" % clean(show_value(check["actual"])))
        if check["expected"] is not None:
            lines.append("   - Expected: %s" % clean(show_value(check["expected"])))
        if check["detail"]:
            lines.append("   - Detail: %s" % clean(check["detail"]))
        if check.get("unmeasured"):
            lines.append("   - What to do: this check could not be measured, so nothing is known about it. Read the "
                         "Detail for the cause. If a tool failed, run `ffmpeg -version` and run again with a newer "
                         "ffmpeg (`--ffmpeg PATH --ffprobe PATH`). If the file does not allow the measurement "
                         "(for example audio shorter than 0.4 s), use a file that does or drop that check from the spec.")
        elif entry:
            lines.append("   - Likely causes: %s" % entry["causes"])
            lines.append("   - What to do: %s" % entry["action"])
        else:
            lines.append("   - No guidance is listed for this check id in references/interpreting-results.md.")
    return lines


# --------------------------------------------------------------------------- writing files

REPORT_FILES = ("report.md", "report.json")


def check_out_dir(out_dir, overwrite, input_path=None):
    """Refuse a folder that cannot take the reports. Called before the long decoding passes (the folder
    is created here, so an impossible name fails at once, not after minutes of decoding) and again
    before writing, so a report is never half written and nothing is replaced silently.

    A report may never replace the file being checked, not even with --overwrite: an export that
    sits at OUT_DIR/report.md or OUT_DIR/report.json would otherwise be overwritten by its own report.
    """
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise common.UsageError("--out-dir %s exists and is not a folder" % out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise common.UsageError("cannot create --out-dir %s: %s" % (out_dir, exc))
    for name in REPORT_FILES:
        target = os.path.join(out_dir, name)
        if input_path and os.path.exists(target) and os.path.samefile(target, input_path):
            raise common.UsageError(
                "%s is the file being checked, and a report may not replace it (even with --overwrite). "
                "Choose another --out-dir." % target)
        common.refuse_overwrite(target, overwrite)  # all checked before any is written


def write_outputs(out_dir, markdown, json_text, overwrite, input_path=None):
    """Write report.md and report.json into out_dir (created if needed). Refuses to overwrite."""
    check_out_dir(out_dir, overwrite, input_path)
    targets = [(os.path.join(out_dir, "report.md"), markdown), (os.path.join(out_dir, "report.json"), json_text)]
    for target, text in targets:
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return [target for target, _text in targets]


# --------------------------------------------------------------------------- command line

def build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Run the probe, audio, barcode-frame and GIF checks on one file and print one merged report.",
    )
    parser.add_argument("file", help="exported video, audio or GIF to check")
    parser.add_argument("--spec", default=None, help="JSON file with expectations (see SKILL.md)")
    parser.add_argument("--barcode-expect", default=None, metavar="SEGMENTS",
                        help='intended source frame per output frame, e.g. "0-95" or "0-29,36-95"')
    parser.add_argument("--barcode-region", default=None, metavar="x0,y0,x1,y1",
                        help="barcode area as fractions of the frame (default 0,0,1,0.0833)")
    parser.add_argument("--out-dir", default=None, help="also write report.md and report.json into this folder")
    parser.add_argument("--format", choices=("md", "json"), default=None, help="what to print (default md)")
    parser.add_argument("--overwrite", action="store_true", help="replace report files that already exist")
    common.add_common_args(parser)
    return parser


def choose_format(args):
    if args.json and args.format == "md":
        raise common.UsageError("--json and --format md contradict each other; pick one")
    return args.format or ("json" if args.json else "md")


def main(argv=None):
    args = build_parser().parse_args(argv)
    output_format = choose_format(args)
    if not os.path.isfile(args.file):
        raise common.UsageError("input file not found: %s" % args.file)
    spec, spec_sha = common.load_spec(args.spec)
    expect, region, barcode_unknown = read_barcode_options(spec, args.barcode_expect, args.barcode_region)
    tools = {"ffmpeg": common.find_tool("ffmpeg", args.ffmpeg), "ffprobe": common.find_tool("ffprobe", args.ffprobe)}
    if args.out_dir:  # fail before the long decoding passes if the report cannot be written
        check_out_dir(args.out_dir, args.overwrite, args.file)

    notes = []
    if args.barcode_expect is not None and isinstance(spec.get("barcode"), dict) and "expect" in spec["barcode"]:
        notes.append("--barcode-expect was given, so barcode.expect in the spec was not used")
    parts, extra_checks = run_parts(args.file, spec, spec_sha, tools, expect, region)
    for name in barcode_unknown:
        extra_checks.append(unknown_key_check(name, "keys inside barcode: " + ", ".join(BARCODE_SPEC_KEYS)))
    if region is not None and expect is None:
        extra_checks.append(unknown_key_check(
            "barcode.region", "no barcode expectation was given (barcode.expect or --barcode-expect), "
                              "so the barcode was not read"))
    merged = merge(args.file, tools, spec_sha, parts, extra_checks, notes)

    data = merged.to_dict()
    json_text = json.dumps(data, indent=2, allow_nan=False) + "\n"
    markdown = markdown_report(data, load_guidance(), tools, args.spec)
    if args.out_dir:
        write_outputs(args.out_dir, markdown, json_text, args.overwrite, args.file)

    qa_probe.make_stdout_safe()
    sys.stdout.write(json_text if output_format == "json" else markdown)
    return merged.exit_code()


if __name__ == "__main__":
    sys.exit(common.run_main(main))
