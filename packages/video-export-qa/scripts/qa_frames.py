#!/usr/bin/env python3
# qa_frames.py - prove which source frame sits on every output frame, using the barcode.
#
# Purpose: decode the frame-index barcode (see barcode.py, written by make_test_clip.py or
#          burnt into your own test footage) from EVERY video frame of an exported file,
#          decoding sequentially (never seeking, because seeking is not frame-exact on
#          long-GOP video), and report dropped frames, duplicated frames, reversed or
#          re-ordered passages and wrong frame counts.
# Inputs:  FILE (video), --expect "SEGMENTS" (the edit you intended), --region x0,y0,x1,y1
#          (barcode area as fractions of the frame, default 0,0,1,0.0833), --show-runs,
#          --json, --ffmpeg PATH, --ffprobe PATH.
#          --expect grammar: comma-separated segments START-END[:STEP], inclusive. STEP
#          defaults to +1 when START<=END and -1 otherwise. A lone number means one frame.
#          Examples: "0-95", "95-48:-1", "0-94:2", "0-47,24-71", "0-29,36-95".
# Outputs: a table (or one JSON object with --json) with the checks all_frames_decodable,
#          low_block_margin, no_dropped_frames, no_duplicate_frames, monotonic and, with --expect, expect.
#          info lists frames_total, frames_decoded, frames_undecodable, runs, dropped,
#          duplicated, reversed_runs, first_index, last_index and min_block_margin.
#          With --expect, the three continuity checks are SKIPPED (an edit was declared, so
#          the expect check is the verdict) and their facts stay in info.
# Exit codes: 0 = overall PASS or WARN only, 1 = at least one FAIL (including a video that
#          decodes to zero frames), 2 = could not run (missing ffmpeg, unreadable file,
#          bad arguments, ffmpeg failed).
#
# Rules: standard library only, no environment reads except PATH, no network (ffmpeg is told to allow
# the local file protocol only), argument-list subprocess only, no seeking: the command
# never contains -ss, -t, -to or any seek option, and the frame stream is read start to end.
# Frames are read as they are STORED: -noautorotate stops ffmpeg from turning a frame by the file's
# rotation metadata first, which would move the barcode strip to the side and make every frame unreadable.

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # needed under python -I

import barcode  # noqa: E402
import common  # noqa: E402
from common import FAIL, PASS, SKIP, WARN, QAError, UsageError  # noqa: E402

SCRIPT = "qa_frames.py"
MAX_LISTED = 100  # entries kept in info lists (totals are always exact)
MAX_RUNS_LISTED = 500
# An --expect string is a few bytes but expands into one number per output frame. A barcode clip has at
# most 65536 frames, so four times that is far more than any real edit and keeps memory small.
MAX_EXPECT_FRAMES = 4 * (barcode.MAX_INDEX + 1)
# A decoded block should sit far from the black/white threshold. Measured on clean exports the lowest
# margin was 50 or more (crf 51, 20 kb/s, brightness and contrast shifts, scaling down to 52x30 pixels);
# frame blends measured 1.0 to 22.9, and a frame so small that a block is one pixel wide (34x20) 14.6.
# Below this the barcode is faint and a decoded number may be a wrong one.
LOW_MARGIN = 40.0
# One --expect segment: START, START-END or START-END:STEP (spaces allowed, STEP may be negative).
SEGMENT_PATTERN = r"^\s*(\d+)\s*(?:-\s*(\d+)\s*(?::\s*(-?\d+))?)?\s*$"
# ffmpeg 5.1+ spells it -fps_mode, older builds -vsync. Both mean "never duplicate or drop".
SYNC_OPTIONS = (("-fps_mode", "passthrough"), ("-vsync", "passthrough"))


# ---------------------------------------------------------------- arguments

def parse_region(text):
    """'x0,y0,x1,y1' as fractions of the frame -> tuple of four floats."""
    if text is None:
        return barcode.DEFAULT_REGION
    problem = (
        "--region must be four fractions of the frame, x0,y0,x1,y1, with 0 <= x0 < x1 <= 1 and "
        "0 <= y0 < y1 <= 1 (default 0,0,1,0.0833); got %r" % text
    )
    parts = text.split(",")
    if len(parts) != 4:
        raise UsageError(problem)
    try:
        x0, y0, x1, y1 = [float(p) for p in parts]
    except ValueError:
        raise UsageError(problem)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise UsageError(problem)
    return (x0, y0, x1, y1)


def parse_expect(text):
    """The --expect grammar -> the list of source indices expected on each output frame."""
    if text is None or not text.strip():
        raise UsageError('--expect is empty; give segments such as "0-95" or "0-29,36-95"')
    indices = []
    total = 0
    for raw in text.split(","):
        match = re.match(SEGMENT_PATTERN, raw)
        if not match:
            raise UsageError(
                'cannot read segment %r in --expect; use START-END or START-END:STEP, for example '
                '"0-95", "95-48:-1", "0-94:2" or "0-47,24-71"' % raw.strip()
            )
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else start
        step = int(match.group(3)) if match.group(3) is not None else (1 if start <= end else -1)
        if step == 0:
            raise UsageError("segment %r in --expect has step 0" % raw.strip())
        if (end - start) * step < 0:
            raise UsageError("segment %r in --expect: step %d points away from the end value" % (raw.strip(), step))
        if max(start, end) > barcode.MAX_INDEX:
            raise UsageError("segment %r in --expect: indices go up to %d" % (raw.strip(), barcode.MAX_INDEX))
        total += abs(end - start) // abs(step) + 1  # counted before the list is built
        if total > MAX_EXPECT_FRAMES:
            raise UsageError("--expect describes more than %d output frames; a barcode clip has at most %d "
                             "frames, so this is not a real edit" % (MAX_EXPECT_FRAMES, barcode.MAX_INDEX + 1))
        indices.extend(range(start, end + (1 if step > 0 else -1), step))
    return indices


# ---------------------------------------------------------------- decoding through ffmpeg

def decode_filter(region):
    """crop the barcode region, area-average it to 208x4, keep the grey (luma) channel."""
    x0, y0, x1, y1 = region
    return "crop=w=iw*%.6f:h=ih*%.6f:x=iw*%.6f:y=ih*%.6f,scale=%d:%d:flags=area,format=gray" % (
        x1 - x0, y1 - y0, x0, y0, barcode.DECODE_WIDTH, barcode.DECODE_HEIGHT,
    )


def build_decode_command(ffmpeg, path, region, sync_option):
    """Whole file, first video stream, one tiny grey image per frame on stdout. No seeking."""
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-noautorotate", "-protocol_whitelist", "file",
        "-i", path, "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", decode_filter(region),
        sync_option[0], sync_option[1],
        "-f", "rawvideo", "-",
    ]


class Decoded:
    """What came out of one decode pass."""

    def __init__(self):
        self.sequence = []  # per output frame: source index, or None if undecodable
        self.reasons = Counter()  # why frames were undecodable
        self.undecodable_frames = []  # first output frame numbers that failed (capped)
        self.min_margin = None
        self.returncode = 0
        self.stderr = ""
        self.trailing_bytes = 0

    def add(self, index, reason, margin):
        if index is None:
            self.reasons[reason] += 1
            if len(self.undecodable_frames) < MAX_LISTED:
                self.undecodable_frames.append(len(self.sequence))
        self.sequence.append(index)
        if self.min_margin is None or margin < self.min_margin:
            self.min_margin = margin


def decode_once(cmd, commands):
    decoded = Decoded()
    process = barcode.StreamingProcess(cmd, stdout=True, record=commands)
    try:
        while True:
            chunk = process.stdout.read(barcode.DECODE_FRAME_BYTES)
            if len(chunk) < barcode.DECODE_FRAME_BYTES:
                decoded.trailing_bytes = len(chunk)
                break
            decoded.add(*barcode.decode_gray(chunk))
    except BaseException:
        process.abort()
        raise
    decoded.returncode, decoded.stderr = process.finish()
    decoded.stderr = common.strip_ansi(decoded.stderr)  # colour codes from AV_LOG_FORCE_COLOR
    return decoded


def decode_video(ffmpeg, path, region, commands):
    """Decode every frame. Falls back to the older -vsync spelling if -fps_mode is unknown."""
    decoded = None
    for option in SYNC_OPTIONS:
        decoded = decode_once(build_decode_command(ffmpeg, path, region, option), commands)
        if decoded.returncode != 0 and "Unrecognized option" in decoded.stderr and not decoded.sequence:
            continue
        break
    if decoded.returncode != 0:
        tail = " | ".join(decoded.stderr.strip().splitlines()[-3:]) or "no message"
        hint = ""
        if "non positive size" in decoded.stderr:
            hint = (" Hint: the barcode strip is thinner than one pixel row at this frame size (the default strip is "
                    "8.33 percent of the height, so a 4:2:0 frame under about 20 rows high cannot be cropped). "
                    "Use a larger export, or pass --region with a taller area")
        raise QAError("ffmpeg could not decode %s (exit code %d): %s%s" % (path, decoded.returncode, tail, hint))
    return decoded


# ---------------------------------------------------------------- analysing the index sequence

def decodable_pairs(sequence):
    """(output_a, index_a, output_b, index_b) for each pair of neighbouring decodable frames."""
    last = None
    for position, index in enumerate(sequence):
        if index is None:
            continue
        if last is not None:
            yield last[0], last[1], position, index
        last = (position, index)


def find_runs(sequence):
    """Maximal stretches of adjacent decodable frames whose index moves by a constant step.

    step is +1 for normal playback, -1 for reversed, 2 for double speed, 0 for a held frame.
    A run of one frame has step None.
    """
    runs = []
    current = None
    for position, index in enumerate(sequence):
        if index is None:
            current = None
            continue
        if current is None:
            current = {"from": index, "to": index, "step": None, "count": 1, "at_output_frame": position}
            runs.append(current)
        elif current["step"] is None:
            current["step"] = index - current["to"]
            current["to"] = index
            current["count"] = 2
        elif index == current["to"] + current["step"]:
            current["to"] = index
            current["count"] += 1
        else:
            current = {"from": index, "to": index, "step": None, "count": 1, "at_output_frame": position}
            runs.append(current)
    return runs


def find_dropped(sequence):
    """Forward jumps bigger than the number of output frames between: indices missing."""
    events = []
    for pos_a, idx_a, pos_b, idx_b in decodable_pairs(sequence):
        gap = pos_b - pos_a
        missing = idx_b - idx_a - gap
        if missing > 0:
            events.append({
                "output_frame": pos_b, "after_index": idx_a, "next_index": idx_b,
                "missing_count": missing, "missing_from": idx_a + gap, "missing_to": idx_b - 1,
            })
    return events


def find_backwards(sequence):
    events = []
    for pos_a, idx_a, pos_b, idx_b in decodable_pairs(sequence):
        if idx_b < idx_a:
            events.append({"output_frame": pos_b, "from_index": idx_a, "to_index": idx_b})
    return events


def find_duplicates(sequence):
    """Every index that appears on more than one output frame, with the frames it appears on."""
    seen = {}
    for position, index in enumerate(sequence):
        if index is not None:
            seen.setdefault(index, []).append(position)
    return [
        {"index": index, "output_frames": frames, "count": len(frames)}
        for index, frames in seen.items() if len(frames) > 1
    ]


def span_text(first, last):
    return str(first) if first == last else "%d-%d" % (first, last)


def run_token(run):
    if run["count"] == 1:
        return str(run["from"])
    if run["step"] == 0:
        return "%dx%d" % (run["from"], run["count"])  # one frame held for count output frames
    token = "%d-%d" % (run["from"], run["to"])
    if run["step"] not in (1, -1):
        token += ":%d" % run["step"]
    return token


def describe_sequence(runs, total):
    """The decoded sequence in --expect notation; '?' marks undecodable frames, 'NxC' a held frame."""
    parts = []
    position = 0
    for run in runs:
        if run["at_output_frame"] > position:
            parts.append(_unknown_token(run["at_output_frame"] - position))
        parts.append(run_token(run))
        position = run["at_output_frame"] + run["count"]
    if position < total:
        parts.append(_unknown_token(total - position))
    text = ",".join(parts)
    return text if len(text) <= 300 else text[:297] + "..."


def _unknown_token(count):
    return "?" if count == 1 else "?x%d" % count


def compare_with_expected(sequence, expected):
    """(mismatch_count, first_mismatch_output_frame_or_None) over the longer of the two lists."""
    overlap = min(len(sequence), len(expected))
    first = None
    count = 0
    for position in range(overlap):
        if sequence[position] != expected[position]:
            count += 1
            if first is None:
                first = position
    longer = max(len(sequence), len(expected))
    if longer > overlap:
        count += longer - overlap
        if first is None:
            first = overlap
    return count, first


def _label(sequence, position):
    if position >= len(sequence):
        return "end of file"
    return "undecodable" if sequence[position] is None else str(sequence[position])


# ---------------------------------------------------------------- building the report

def add_decodable_check(report, decoded):
    total = len(decoded.sequence)
    bad = total - sum(1 for index in decoded.sequence if index is not None)
    if total == 0:
        report.add(
            "all_frames_decodable", FAIL, "every frame carries a readable barcode", "0 frames decoded",
            "ffmpeg returned no video frames, so nothing was measured (an empty measurement is never a pass)",
        )
    elif bad:
        reasons = ", ".join("%s: %d" % (name, n) for name, n in sorted(decoded.reasons.items()))
        report.add(
            "all_frames_decodable", FAIL, "0 undecodable frames", "%d of %d undecodable" % (bad, total),
            "no valid barcode on output frame(s) %s (%s). Marker mismatch means no barcode, a mirrored, "
            "cropped or rescaled-away strip; checksum mismatch means damaged bits."
            % (", ".join(str(n) for n in decoded.undecodable_frames[:8]), reasons),
        )
    else:
        report.add(
            "all_frames_decodable", PASS, "0 undecodable frames", "0 of %d undecodable" % total,
            "every frame decoded (lowest block margin %.1f of a possible 127)" % decoded.min_margin,
        )


def unreliable_note(decoded):
    """Text added to the dropped / duplicate / expect details when the barcode was damaged or faint:
    a damaged barcode can still decode to a wrong but valid number, so those findings may describe
    frames that did not exist. Empty when every frame was clean."""
    undecodable = sum(1 for index in decoded.sequence if index is None)
    faint = decoded.min_margin is not None and decoded.min_margin < LOW_MARGIN
    if not (undecodable or faint):
        return ""
    return (" Caution: %s, so this finding may describe frames that did not exist."
            % ("some frames were unreadable" if undecodable else "the barcode is faint (see low_block_margin)"))


def add_margin_check(report, decoded):
    """low_block_margin: WARN when the barcode is faint. A frame blend, a heavy filter or a very low
    bitrate can turn one frame number into another valid-looking number (the checksum is only 4 bits),
    and then the decoded index is wrong even though every frame 'decoded'."""
    expected = "lowest block margin at least %.0f of a possible 127" % LOW_MARGIN
    decodable = sum(1 for index in decoded.sequence if index is not None)
    if decoded.min_margin is None or decodable == 0:
        report.add("low_block_margin", SKIP, expected, None,
                   "no valid barcode was read, so there is no margin to judge (see all_frames_decodable)")
    elif decoded.min_margin < LOW_MARGIN:
        report.add(
            "low_block_margin", WARN, expected, round(decoded.min_margin, 1),
            "the barcode is faint (a clean export keeps 50 or more). Frame blending, optical flow, motion "
            "smoothing, a heavy filter or a very low bitrate can turn one frame number into another valid-"
            "looking one, so decoded numbers may be wrong. Turn blending and smoothing off for the test.")
    else:
        report.add("low_block_margin", PASS, expected, round(decoded.min_margin, 1),
                   "every barcode block is clearly black or white")


def leading_missing(sequence, backwards):
    """(count, first_index, first_output_frame) when the first readable frame is not source frame 0 and
    the passage plays forwards, else None. A reversed passage has its own check (monotonic)."""
    if backwards:
        return None
    for position, index in enumerate(sequence):
        if index is not None:
            missing = index - position
            return (missing, index, position) if missing > 0 else None
    return None


def add_continuity_checks(report, sequence, declared_edit, facts, caution=""):
    """no_dropped_frames, no_duplicate_frames, monotonic: identity expectations, judged only
    when no --expect was given (an --expect declares the intended edit)."""
    ids = ("no_dropped_frames", "no_duplicate_frames", "monotonic")
    decodable = sum(1 for index in sequence if index is not None)
    if declared_edit:
        for check_id in ids:
            report.add(check_id, SKIP, None, None,
                       "--expect given: the declared edit is judged by the expect check; facts are in info")
        return
    if decodable < 2:
        for check_id in ids:
            report.add(check_id, SKIP, None, None,
                       "needs at least 2 decodable frames, got %d" % decodable)
        return

    dropped, duplicated, backwards = facts["dropped"], facts["duplicated"], facts["backwards"]
    missing_total = sum(e["missing_count"] for e in dropped)
    lead = leading_missing(sequence, backwards)
    start_note = ""
    if lead:
        start_note = "; also the first readable frame is source %d, so source frame(s) %s are missing before it" % (
            lead[1], span_text(0, lead[0] - 1))
    if dropped:
        first = dropped[0]
        report.add(
            "no_dropped_frames", FAIL, "0 missing frames", "%d missing frames in %d places" % (missing_total, len(dropped)),
            "first: output frame %d jumps from source %d to %d, source %s missing%s%s"
            % (first["output_frame"], first["after_index"], first["next_index"],
               span_text(first["missing_from"], first["missing_to"]), start_note, caution),
        )
    elif lead:
        report.add(
            "no_dropped_frames", WARN, "starts at source frame 0, no gaps",
            "first readable frame is source %d (%d missing at the start)" % (lead[1], lead[0]),
            "output frame %d shows source %d, so source frame(s) %s are missing from the start. A cut at the start or end "
            "of the clip is only judged properly when you state the edit with --expect (the end of the clip "
            "cannot be known without it).%s" % (lead[2], lead[1], span_text(0, lead[0] - 1), caution),
        )
    else:
        report.add("no_dropped_frames", PASS, "0 missing frames", "0 missing frames",
                   "indices never skip forward between neighbouring frames and start at source 0 (%d neighbour "
                   "pairs compared). Frames cut from the END of the clip show only with --expect." % (decodable - 1))

    extra = sum(d["count"] - 1 for d in duplicated)
    if duplicated:
        first = duplicated[0]
        report.add(
            "no_duplicate_frames", FAIL, "0 repeated frames", "%d repeated frames across %d indices" % (extra, len(duplicated)),
            "first: source %d appears on output frames %s%s"
            % (first["index"], ", ".join(str(n) for n in first["output_frames"][:6]), caution),
        )
    else:
        report.add("no_duplicate_frames", PASS, "0 repeated frames", "0 repeated frames",
                   "every source index appears once (%d frames compared)" % decodable)

    if backwards:
        first = backwards[0]
        report.add(
            "monotonic", WARN, "indices never go backwards", "%d backward steps" % len(backwards),
            "first at output frame %d (source %d -> %d). Reversed or re-ordered passages may be "
            "intentional; use --expect to state the intended order." % (first["output_frame"], first["from_index"], first["to_index"]),
        )
    else:
        report.add("monotonic", PASS, "indices never go backwards", "0 backward steps",
                   "indices never decrease (%d neighbour pairs compared)" % (decodable - 1))


def add_expect_check(report, sequence, runs, expect_text, expected, caution=""):
    total = len(sequence)
    expected_text = "%s (%d frames)" % (expect_text, len(expected))
    actual_text = describe_sequence(runs, total) if total else "no frames decoded"
    if total == 0:
        report.add("expect", FAIL, expected_text, actual_text,
                   "no frames were decoded, so the expectation cannot be met (an empty measurement is never a pass)")
        return
    count, first = compare_with_expected(sequence, expected)
    if count == 0:
        report.add("expect", PASS, expected_text, "%s (%d frames)" % (actual_text, total),
                   "all %d output frames carry the expected source index" % total)
        return
    parts = []
    if total != len(expected):
        parts.append("decoded %d frames, expected %d" % (total, len(expected)))
    exp_label = "end of file" if first >= len(expected) else str(expected[first])
    parts.append("first mismatch at output frame %d: expected %s, got %s" % (first, exp_label, _label(sequence, first)))
    parts.append("%d mismatching output frame(s)" % count)
    report.add("expect", FAIL, expected_text, "%s (%d frames)" % (actual_text, total), "; ".join(parts) + caution)


def check_file(path, ffmpeg, ffprobe=None, expect=None, region=None):
    """Run every frame check on one file. Returns a common.Report (the script's whole result)."""
    if not os.path.isfile(path):
        raise UsageError("cannot read video file %s (not found or not a file)" % path)
    expected = parse_expect(expect) if expect is not None else None
    region = region or barcode.DEFAULT_REGION
    tools = {"ffmpeg": ffmpeg}
    if ffprobe:
        tools["ffprobe"] = ffprobe
    report = common.Report(SCRIPT, file=path, tools=tools)

    decoded = decode_video(ffmpeg, os.path.abspath(path), region, report.commands)
    sequence = decoded.sequence
    runs = find_runs(sequence)
    facts = {
        "dropped": find_dropped(sequence),
        "duplicated": find_duplicates(sequence),
        "backwards": find_backwards(sequence),
    }
    index_values = [i for i in sequence if i is not None]
    reversed_runs = [r for r in runs if r["count"] >= 2 and r["step"] is not None and r["step"] < 0]

    report.info.update({
        "barcode": {
            "version": 1, "blocks": barcode.NUM_BLOCKS, "threshold": barcode.THRESHOLD,
            "region": list(region), "filter": decode_filter(region),
        },
        "frames_total": len(sequence),
        "frames_decoded": len(index_values),
        "frames_undecodable": len(sequence) - len(index_values),
        "undecodable_reasons": dict(decoded.reasons),
        "undecodable_first_output_frames": decoded.undecodable_frames,
        "first_index": index_values[0] if index_values else None,
        "last_index": index_values[-1] if index_values else None,
        "runs_total": len(runs),
        "runs": runs[:MAX_RUNS_LISTED],
        "dropped_frames_total": sum(e["missing_count"] for e in facts["dropped"]),
        "dropped": facts["dropped"][:MAX_LISTED],
        "duplicated_indices_total": len(facts["duplicated"]),
        "duplicated": facts["duplicated"][:MAX_LISTED],
        "backward_steps_total": len(facts["backwards"]),
        "reversed_runs": reversed_runs[:MAX_LISTED],
        "min_block_margin": None if decoded.min_margin is None else round(decoded.min_margin, 1),
        "partial_trailing_bytes": decoded.trailing_bytes,
        "decoder_messages": [common.clip_text(line) for line in decoded.stderr.strip().splitlines()[:10]],
    })

    caution = unreliable_note(decoded)
    add_decodable_check(report, decoded)
    add_margin_check(report, decoded)
    add_continuity_checks(report, sequence, expected is not None, facts, caution)
    if expected is not None:
        add_expect_check(report, sequence, runs, expect, expected, caution)
    return report


def runs_lines(info):
    """Plain-text lines for the runs listed in a qa_frames info dict (used for --show-runs and by run_qa)."""
    lines = []
    runs = info["runs"]
    if not runs:
        lines.append("  none (no frame decoded)")
    for run in runs:
        last_output = run["at_output_frame"] + run["count"] - 1
        step = "" if run["step"] in (None, 1) else "  step %d" % run["step"]
        lines.append("  output %s -> source %s  (%d frames)%s" % (
            span_text(run["at_output_frame"], last_output), span_text(run["from"], run["to"]), run["count"], step,
        ))
    if info["runs_total"] > len(runs):
        lines.append("  ... %d more runs not listed" % (info["runs_total"] - len(runs)))
    if info["frames_undecodable"]:
        lines.append("  %d output frame(s) had no readable barcode and are not in any run" % info["frames_undecodable"])
    return lines


def runs_table(report):
    """Plain-text lines for --show-runs."""
    return ["", "runs (output frames -> source frames):"] + runs_lines(report.info)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Decode the frame-index barcode of every video frame and report dropped, "
                    "duplicated or re-ordered frames.",
    )
    parser.add_argument("file", metavar="FILE", help="video to check")
    parser.add_argument("--expect", default=None, metavar="SEGMENTS",
                        help='intended source frames per output frame, e.g. "0-95", "95-48:-1", "0-94:2", "0-47,24-71"')
    parser.add_argument("--region", default=None, metavar="x0,y0,x1,y1",
                        help="barcode area as fractions of the frame (default 0,0,1,0.0833)")
    parser.add_argument("--show-runs", action="store_true", help="also print the runs of consecutive frames")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    region = parse_region(args.region)
    if args.expect is not None:
        parse_expect(args.expect)  # fail on a bad grammar before spending time decoding
    ffmpeg = common.find_tool("ffmpeg", args.ffmpeg)
    try:
        ffprobe = common.find_tool("ffprobe", args.ffprobe)
    except common.ToolMissing:
        if args.ffprobe:
            raise
        ffprobe = None  # only needed to record its version in the provenance

    report = check_file(args.file, ffmpeg, ffprobe, args.expect, region)
    if args.json:
        return common.emit(report, True)
    print(report.table())
    if args.show_runs:
        print("\n".join(runs_table(report)))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(common.run_main(main))
