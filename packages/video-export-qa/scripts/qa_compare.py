#!/usr/bin/env python3
# qa_compare.py - prove whether two video files decode to exactly the same frames.
#
# Purpose: after a re-export, a transcode, or a "lossless" step, show whether the decoded video
#          of file B is bit-identical to file A, and if not, which frame is the first to differ.
#          Both files are decoded from the start, front to back, with ffmpeg's framemd5 muxer
#          (one MD5 per decoded frame). Nothing is seeked, so the comparison is frame-exact
#          even for long-GOP H.264. Optionally measures PSNR per frame with ffmpeg's psnr filter.
# Inputs:  A B            the two video files (only the first video stream of each is compared)
#          --min-psnr DB  also require every frame pair to reach at least this PSNR in dB
#          --json         print one JSON object instead of the table
#          --ffmpeg PATH  ffmpeg to use (default: found on PATH)
#          --ffprobe PATH accepted for uniformity with the other scripts; ffprobe is not used here
# Outputs: a table (or JSON with --json) on stdout. Nothing is written to disk.
# Exit codes: 0 = PASS or WARN only, 1 = at least one FAIL,
#             2 = could not run (ffmpeg missing, unreadable input, decode failure, bad arguments).
#
# Notes on what "identical" means: the MD5 is taken over the decoded pixel data in the decoder's
# own pixel format, so two files that decode to the same picture in different pixel formats
# (for example yuv420p vs yuv444p) are reported as different. Audio and metadata are not compared.
# PSNR values are read at the 0.01 dB precision the psnr filter prints; an infinite PSNR is
# reported as the string "inf" so the JSON stays valid.

import argparse
import datetime
import math
import os
import re
import sys
from fractions import Fraction

# "python -I" leaves the script folder off sys.path, so add it before importing the shared module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SCRIPT_NAME = "qa_compare.py"

# Timestamps are rewritten to one second per frame (index * 1000 ticks of 1/1000 s) before the psnr
# filter so frames always pair by index, whatever the two files' frame rates or start times are.
PSNR_GRAPH = (
    "[0:v:0]settb=1/1000,setpts=N*1000[a];"
    "[1:v:0]settb=1/1000,setpts=N*1000[b];"
    "[a][b]psnr=stats_file=-"
)

HASH_PATTERN = r"[0-9a-f]{32}"
DIMENSIONS_PATTERN = r"#dimensions\s+0:\s*(\d+)x(\d+)"
TIMEBASE_PATTERN = r"#tb\s+0:\s*(\d+)/(\d+)"
PSNR_LINE_PATTERN = r"n:(\d+)\s.*?\bpsnr_avg:(\S+)"


# ---------------------------------------------------------------------------
# Running ffmpeg
# ---------------------------------------------------------------------------

def _run_passthrough(report, build):
    """Run build(flags) with -fps_mode passthrough so ffmpeg never duplicates or drops frames.

    ffmpeg older than 5.1 does not know -fps_mode; for those, retry once with the older -vsync 0.
    Both attempts are recorded in the report's command list.
    """
    code, out, err = common.run(build(["-fps_mode", "passthrough"]), report)
    if code != 0 and "fps_mode" in err and "nrecognized option" in err:
        code, out, err = common.run(build(["-vsync", "0"]), report)
    return code, out, err


def _decode_command(ffmpeg, path):
    def build(flags):
        return [ffmpeg, "-hide_banner", "-nostdin", "-v", "error"] + common.LOCAL_FILES_ONLY + [
            "-i", os.path.abspath(path), "-map", "0:v:0"] + flags + ["-f", "framemd5", "-"]
    return build


def _last_lines(text, count=3):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-count:]) if lines else "no message from ffmpeg"


def parse_framemd5(text, label):
    """Parse framemd5 output into {'width','height','timebase','hashes','pts','sizes'}."""
    result = {"width": None, "height": None, "timebase": None, "hashes": [], "pts": [], "sizes": []}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.match(DIMENSIONS_PATTERN, line)
            if match:
                result["width"], result["height"] = int(match.group(1)), int(match.group(2))
            match = re.match(TIMEBASE_PATTERN, line)
            if match and int(match.group(2)) != 0:
                result["timebase"] = Fraction(int(match.group(1)), int(match.group(2)))
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) != 6 or not re.fullmatch(HASH_PATTERN, fields[5]):
            raise common.QAError("unexpected framemd5 line for %s: %r" % (label, line[:80]))
        try:
            result["pts"].append(int(fields[2]))
            result["sizes"].append(int(fields[4]))
        except ValueError:
            raise common.QAError("unexpected framemd5 line for %s: %r" % (label, line[:80]))
        result["hashes"].append(fields[5])
    return result


def decode_hashes(report, ffmpeg, path, label):
    """Decode the first video stream of path front to back and return its parsed framemd5."""
    code, out, err = _run_passthrough(report, _decode_command(ffmpeg, path))
    if code != 0:
        raise common.QAError(
            "ffmpeg could not decode the first video stream of %s %s (exit code %d): %s"
            % (label, path, code, _last_lines(err))
        )
    parsed = parse_framemd5(out, label)
    parsed["messages"] = [common.clip_text(line.strip()) for line in err.splitlines() if line.strip()][:5]
    return parsed


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare_hashes(hashes_a, hashes_b):
    """Return (indexes where both have a frame and the hashes differ, first differing index or None).

    A length difference counts as a difference starting at the first index only one file has.
    """
    common_len = min(len(hashes_a), len(hashes_b))
    diffs = [i for i in range(common_len) if hashes_a[i] != hashes_b[i]]
    if diffs:
        first = diffs[0]
    elif len(hashes_a) != len(hashes_b):
        first = common_len
    else:
        first = None
    return diffs, first


def _times(parsed):
    """Presentation times as exact fractions, or None when the timebase is unknown."""
    if parsed["timebase"] is None:
        return None
    return [pts * parsed["timebase"] for pts in parsed["pts"]]


def _size_text(parsed):
    if parsed["width"] is None:
        return "unknown size"
    return "%dx%d" % (parsed["width"], parsed["height"])


def _file_facts(path):
    stat = os.stat(path)
    return {
        "path": path,
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "sha256": common.sha256_file(path),
    }


def _stream_info(path, parsed):
    facts = _file_facts(path)
    facts.update(
        {
            "frames_decoded": len(parsed["hashes"]),
            "width": parsed["width"],
            "height": parsed["height"],
            "timebase": None if parsed["timebase"] is None else str(parsed["timebase"]),
            "decoded_frame_bytes": sorted(set(parsed["sizes"])),
            "decoder_messages": parsed["messages"],
        }
    )
    return facts


def _check_frame_count(report, a, b):
    na, nb = len(a["hashes"]), len(b["hashes"])
    if na == 0 or nb == 0:
        report.add("frame_count_equal", common.FAIL, na, nb,
                   "no frames were decoded from %s; there is nothing to compare"
                   % ("either file" if na == 0 and nb == 0 else ("A" if na == 0 else "B")))
    elif na == nb:
        report.add("frame_count_equal", common.PASS, na, nb, "both files decode to %d frames" % na)
    else:
        report.add("frame_count_equal", common.FAIL, na, nb,
                   "A decodes to %d frames, B to %d (difference %+d)" % (na, nb, nb - na))


def _check_identical(report, a, b):
    na, nb = len(a["hashes"]), len(b["hashes"])
    if na == 0 or nb == 0:
        report.add("identical", common.SKIP, "all frames equal", None,
                   "no frames were decoded from at least one file, so nothing was compared", unmeasured=True)
        return
    if a["width"] is not None and b["width"] is not None and (a["width"], a["height"]) != (b["width"], b["height"]):
        report.add("identical", common.FAIL, "same resolution", "%s vs %s" % (_size_text(a), _size_text(b)),
                   "resolution differs: A is %s, B is %s, so the frames cannot be identical"
                   % (_size_text(a), _size_text(b)))
        return
    diffs, first = compare_hashes(a["hashes"], b["hashes"])
    both = min(na, nb)
    report.info["first_differing_frame"] = first
    report.info["differing_frames"] = len(diffs) + abs(na - nb)
    if first is None:  # nothing differs in content; also say whether the timing matches
        detail = "all %d frames have the same MD5 in A and B" % na
        times_a, times_b = _times(a), _times(b)
        if times_a is not None and times_b is not None:
            equal = times_a == times_b
            report.info["timestamps_equal"] = equal
            if not equal:
                detail += "; note: the presentation timestamps differ, so the frames are the same but timed differently"
        report.add("identical", common.PASS, "all frames equal", {"frames_compared": na}, detail)
        return
    if diffs:
        detail = "first differing frame index %d (0-based); %d of the %d frames both files have differ" % (
            diffs[0], len(diffs), both)
        if na != nb:
            detail += "; frame counts also differ (A %d, B %d)" % (na, nb)
    else:
        longer, extra = ("A", na - nb) if na > nb else ("B", nb - na)
        detail = ("frames 0-%d are identical but the frame counts differ (A %d, B %d): %s has %d extra "
                  "frame(s) from index %d" % (both - 1, na, nb, longer, extra, both))
    report.add("identical", common.FAIL, "all frames equal",
               {"first_differing_frame": first, "differing_frames": len(diffs) + abs(na - nb)}, detail)


def _psnr_text(value):
    return "inf" if math.isinf(value) else round(value, 2)


def measure_psnr(report, ffmpeg, path_a, path_b, frame_count):
    """Return (list of per-frame PSNR values, None) or (None, reason why it could not be measured)."""
    def build(flags):
        return ([ffmpeg, "-hide_banner", "-nostdin", "-v", "error"]
                + common.LOCAL_FILES_ONLY + ["-i", os.path.abspath(path_a)]
                + common.LOCAL_FILES_ONLY + ["-i", os.path.abspath(path_b)]
                + ["-lavfi", PSNR_GRAPH] + flags + ["-f", "null", "-"])

    code, out, err = _run_passthrough(report, build)
    if code != 0:
        return None, "ffmpeg's psnr filter failed (exit code %d): %s" % (code, _last_lines(err))
    values, numbers = [], []
    for line in out.splitlines():
        match = re.match(PSNR_LINE_PATTERN, line.strip())
        if match:
            try:
                values.append(float(match.group(2)))
            except ValueError:
                return None, "unreadable PSNR value %r in the psnr filter output" % match.group(2)
            numbers.append(int(match.group(1)))
    if len(values) != frame_count or numbers != list(range(1, frame_count + 1)):
        return None, ("the psnr filter reported %d frame(s) for %d decoded frame(s), so frame pairing cannot be "
                      "trusted" % (len(values), frame_count))
    if any(math.isnan(v) for v in values):
        return None, "the psnr filter reported NaN for at least one frame"
    return values, None


def _check_psnr(report, ffmpeg, path_a, path_b, a, b, min_psnr):
    na, nb = len(a["hashes"]), len(b["hashes"])
    expected = ">= %s dB" % min_psnr
    if na == 0 or nb == 0:
        report.add("psnr", common.SKIP, expected, None, "no frames were decoded from at least one file",
                   unmeasured=True)
        return
    if na != nb:
        report.add("psnr", common.SKIP, expected, None,
                   "frame counts differ (A %d, B %d); PSNR pairs frames by index, so it was not measured" % (na, nb))
        return
    if a["width"] is not None and b["width"] is not None and (a["width"], a["height"]) != (b["width"], b["height"]):
        report.add("psnr", common.SKIP, expected, None,
                   "resolutions differ (A %s, B %s); PSNR needs equal sizes, so it was not measured"
                   % (_size_text(a), _size_text(b)))
        return
    values, reason = measure_psnr(report, ffmpeg, path_a, path_b, na)
    if values is None:
        report.add("psnr", common.SKIP, expected, None, reason, unmeasured=True)
        return
    lowest = min(values)
    worst = values.index(lowest)
    below = sum(1 for v in values if v < min_psnr)
    report.info["psnr"] = {
        "requested_min_db": min_psnr,
        "min_db": _psnr_text(lowest),
        "worst_frame_index": worst,
        "frames_measured": len(values),
        "frames_below_threshold": below,
        "precision": "values are read at the 0.01 dB the psnr filter prints",
    }
    status = common.PASS if lowest >= min_psnr else common.FAIL
    if math.isinf(lowest):
        detail = "all %d frames have infinite PSNR (the decoded pictures are identical)" % len(values)
    else:
        detail = "lowest PSNR over %d frames is %.2f dB at frame index %d (0-based); %d frame(s) are below %s dB" % (
            len(values), lowest, worst, below, min_psnr)
    report.add("psnr", status, expected, _psnr_text(lowest), detail)


def build_report(path_a, path_b, min_psnr=None, ffmpeg=None):
    """Decode both files, run the checks, and return a common.Report.

    Raises common.QAError subclasses (ToolMissing, UsageError, QAError) when nothing can be measured.
    """
    if min_psnr is not None and (not math.isfinite(min_psnr) or min_psnr <= 0):
        raise common.UsageError("--min-psnr must be a number greater than 0")
    for label, path in (("A", path_a), ("B", path_b)):
        if not os.path.isfile(path):
            raise common.UsageError("input %s is not a file: %s" % (label, path))
    if os.path.samefile(path_a, path_b):
        raise common.UsageError("A and B are the same file; compare two different files")
    ffmpeg_path = common.find_tool("ffmpeg", ffmpeg)

    report = common.Report(SCRIPT_NAME, file=path_a, tools={"ffmpeg": ffmpeg_path})
    a = decode_hashes(report, ffmpeg_path, path_a, "A")
    b = decode_hashes(report, ffmpeg_path, path_b, "B")
    report.info["a"] = _stream_info(path_a, a)
    report.info["b"] = _stream_info(path_b, b)
    report.info["method"] = ("each file is decoded once, front to back, with ffmpeg -f framemd5 and "
                             "-fps_mode passthrough; no seeking, no frame duplication or dropping")
    # Keys exist on every path; they stay null when the comparison could not get that far.
    report.info.update({"first_differing_frame": None, "differing_frames": None,
                        "timestamps_equal": None, "psnr": None})
    _check_frame_count(report, a, b)
    _check_identical(report, a, b)
    if min_psnr is not None:
        _check_psnr(report, ffmpeg_path, path_a, path_b, a, b, min_psnr)
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Compare two video files frame by frame: exact MD5 per decoded frame, optional PSNR.",
    )
    parser.add_argument("a", metavar="A", help="first video file (the reference)")
    parser.add_argument("b", metavar="B", help="second video file")
    parser.add_argument("--min-psnr", type=float, default=None, metavar="DB",
                        help="also require every frame pair to reach this PSNR in dB")
    common.add_common_args(parser)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = build_report(args.a, args.b, min_psnr=args.min_psnr, ffmpeg=args.ffmpeg)
    if not args.json:
        print("A: %s" % args.a)
        print("B: %s" % args.b)
    return common.emit(report, args.json)


if __name__ == "__main__":
    sys.exit(common.run_main(main))
