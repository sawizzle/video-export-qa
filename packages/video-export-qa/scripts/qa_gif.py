#!/usr/bin/env python3
# qa_gif.py - check the timing and structure of an animated GIF with a pure-Python parser.
#
# Purpose: GIF stores each frame delay as a whole number of centiseconds (1/100 s). Exporters
#          round those delays, and browsers override very small ones, so the length a GIF
#          really plays can differ from the length the editor intended. This script walks
#          the GIF blocks, reads every Graphic Control Extension delay, and checks frame
#          count, duration, browser-effective duration, rounding drift against an intended
#          frame rate, and the loop setting. No imaging library and no ffmpeg are needed.
# Inputs:  FILE.gif   the GIF to check
#          --fps F               intended frame rate (24, 23.976, "24000/1001"); enables rounding_drift
#          --expect-duration S   intended length in seconds; enables duration
#          --expect-frames N     intended number of image frames
#          --tol S               tolerance in seconds for duration and drift (default 0.02)
#          --json                print one JSON object instead of the table
#          --ffmpeg/--ffprobe    accepted for uniformity with the other scripts; not used here
# Outputs: a table (or JSON with --json) on stdout. Nothing is written to disk.
# Exit codes: 0 = PASS or WARN only, 1 = at least one FAIL,
#             2 = could not run (unreadable, not a GIF, truncated or corrupt file, bad arguments).
#
# Convention used by effective_duration: common browsers play a frame whose delay is 0 or 1
# centisecond as if it were 10 centiseconds. That is browser behaviour, not part of the GIF
# specification, so that check can only WARN, never FAIL.

import argparse
import math
import os
import sys
from collections import Counter
from fractions import Fraction

# "python -I" leaves the script folder off sys.path, so add it before importing the shared module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SCRIPT_NAME = "qa_gif.py"
DEFAULT_TOL_S = 0.02
EPS = 1e-9

# Browser convention: delays below MIN are played as DEFAULT (both in centiseconds).
BROWSER_MIN_DELAY_CS = 2
BROWSER_DEFAULT_DELAY_CS = 10

# GIF block introducers and extension labels.
BLOCK_EXTENSION = 0x21
BLOCK_IMAGE = 0x2C
BLOCK_TRAILER = 0x3B
EXT_PLAIN_TEXT = 0x01
EXT_GRAPHIC_CONTROL = 0xF9
EXT_COMMENT = 0xFE
EXT_APPLICATION = 0xFF

# Application identifiers that carry the animation loop count.
LOOP_APPLICATION_IDS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


class GifFormatError(common.QAError):
    """The file is not a complete, well-formed GIF (exit code 2: nothing could be measured)."""


# ---------------------------------------------------------------------------
# Low-level reading
# ---------------------------------------------------------------------------

class _Reader:
    """Sequential reader that turns a short read into a clear 'truncated GIF' error."""

    def __init__(self, fh):
        self.fh = fh
        self.offset = 0  # bytes consumed so far

    def read(self, count, what):
        data = self.fh.read(count)
        if len(data) != count:
            raise GifFormatError(
                "truncated GIF: the file ends at byte %d while reading %s (needed %d bytes, found %d)"
                % (self.offset + len(data), what, count, len(data))
            )
        self.offset += count
        return data

    def read_byte(self, what):
        return self.read(1, what)[0]

    def read_u16(self, what):
        data = self.read(2, what)
        return data[0] | (data[1] << 8)

    def next_block_type(self, frames_done):
        """Return the next block introducer byte; a clean end of file is an error (no trailer)."""
        byte = self.fh.read(1)
        if not byte:
            raise GifFormatError(
                "truncated GIF: the file ends at byte %d after %d complete image frame(s) without "
                "the GIF trailer byte (0x3B); the file is incomplete" % (self.offset, frames_done)
            )
        self.offset += 1
        return byte[0]

    def count_remaining(self):
        total = 0
        while True:
            chunk = self.fh.read(1 << 16)
            if not chunk:
                return total
            total += len(chunk)


def _read_sub_blocks(reader, what, keep):
    """Read data sub-blocks up to the zero-length terminator.

    Returns the list of payloads when keep is True, otherwise only the total payload size.
    """
    payloads = []
    total = 0
    while True:
        size = reader.read_byte("the length of a data sub-block in %s" % what)
        if size == 0:
            break
        data = reader.read(size, "a data sub-block in %s" % what)
        total += size
        if keep:
            payloads.append(data)
    return payloads if keep else total


def _ascii(data, limit=200):
    """Printable ASCII rendering of bytes (anything else becomes '?'), cut to limit characters."""
    text = "".join(chr(b) if 32 <= b < 127 else "?" for b in data)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Block parsers
# ---------------------------------------------------------------------------

def _parse_graphic_control(reader, frame_number):
    """Graphic Control Extension: returns the fields a following image will use."""
    what = "the graphic control extension before frame %d" % frame_number
    size = reader.read_byte("the block size of " + what)
    data = reader.read(size, what)
    if size < 4:
        raise GifFormatError(
            "corrupt GIF: %s has block size %d (it must be 4) at byte %d" % (what, size, reader.offset)
        )
    _read_sub_blocks(reader, what, keep=False)  # normally just the terminator
    packed = data[0]
    return {
        "delay_cs": data[1] | (data[2] << 8),
        "disposal": (packed >> 2) & 0x07,
        "transparent": bool(packed & 0x01),
    }


def _parse_application(reader, gif):
    """Application extension: remembers the id and, for NETSCAPE/ANIMEXTS, the loop count."""
    what = "an application extension"
    size = reader.read_byte("the block size of " + what)
    head = reader.read(size, what)
    blocks = _read_sub_blocks(reader, what, keep=True)
    gif["application_ids"].append(_ascii(head[:11], 40))
    if size == 11 and head in LOOP_APPLICATION_IDS and gif["loop_count"] is None:
        for block in blocks:
            if len(block) >= 3 and block[0] == 1:  # sub-block id 1 = loop count, little endian
                gif["loop_count"] = block[1] | (block[2] << 8)
                break


def _parse_image(reader, gif, pending):
    """Image descriptor, optional local colour table, and image data (skipped, not decoded)."""
    number = len(gif["frames"]) + 1
    desc = reader.read(9, "the image descriptor of frame %d" % number)
    packed = desc[8]
    local_entries = 0
    if packed & 0x80:
        local_entries = 2 << (packed & 0x07)
        reader.read(3 * local_entries, "the local colour table of frame %d" % number)
    reader.read_byte("the LZW minimum code size of frame %d" % number)
    _read_sub_blocks(reader, "the image data of frame %d" % number, keep=False)
    gif["frames"].append(
        {
            "delay_cs": pending["delay_cs"] if pending else 0,
            "has_gce": pending is not None,
            "disposal": pending["disposal"] if pending else 0,
            "transparent": pending["transparent"] if pending else False,
            "left": desc[0] | (desc[1] << 8),
            "top": desc[2] | (desc[3] << 8),
            "width": desc[4] | (desc[5] << 8),
            "height": desc[6] | (desc[7] << 8),
            "interlaced": bool(packed & 0x40),
            "local_colour_table_entries": local_entries,
        }
    )


def parse_gif(path):
    """Parse a GIF file and return a dict of facts. Raises GifFormatError on a bad file."""
    try:
        fh = open(path, "rb")
    except OSError as exc:
        raise common.UsageError("cannot open %s: %s" % (path, exc))
    with fh:
        return _parse_stream(fh)


def _parse_stream(fh):
    reader = _Reader(fh)
    signature = reader.read(6, "the GIF signature")
    if signature not in (b"GIF87a", b"GIF89a"):
        raise GifFormatError(
            "not a GIF file: the first 6 bytes are %r, expected GIF87a or GIF89a" % (signature,)
        )
    screen = reader.read(7, "the logical screen descriptor")
    packed = screen[4]
    global_entries = 0
    if packed & 0x80:
        global_entries = 2 << (packed & 0x07)
        reader.read(3 * global_entries, "the global colour table")

    gif = {
        "version": signature.decode("ascii"),
        "screen_width": screen[0] | (screen[1] << 8),
        "screen_height": screen[2] | (screen[3] << 8),
        "global_colour_table_entries": global_entries,
        "frames": [],
        "loop_count": None,  # None = no loop extension; 0 = forever
        "application_ids": [],
        "comments": [],
        "comment_blocks": 0,
        "plain_text_extensions": 0,
        "unknown_extensions": [],
        "unused_graphic_control_extensions": 0,
        "stray_zero_bytes": 0,
        "trailing_bytes": 0,
    }

    pending = None  # Graphic Control Extension waiting for the next graphic block
    while True:  # every pass consumes at least one byte, so a bad file cannot loop forever
        kind = reader.next_block_type(len(gif["frames"]))
        if kind == BLOCK_TRAILER:
            break
        if kind == BLOCK_IMAGE:
            _parse_image(reader, gif, pending)
            pending = None
        elif kind == BLOCK_EXTENSION:
            label = reader.read_byte("an extension label")
            if label == EXT_GRAPHIC_CONTROL:
                if pending is not None:
                    gif["unused_graphic_control_extensions"] += 1
                pending = _parse_graphic_control(reader, len(gif["frames"]) + 1)
            elif label == EXT_APPLICATION:
                _parse_application(reader, gif)
            elif label == EXT_COMMENT:
                blocks = _read_sub_blocks(reader, "a comment extension", keep=True)
                gif["comment_blocks"] += 1
                gif["comments"].append(_ascii(b"".join(blocks)))
            elif label == EXT_PLAIN_TEXT:
                # A plain-text block is a graphic block: it uses the pending delay but is not an image frame.
                size = reader.read_byte("the block size of a plain text extension")
                reader.read(size, "a plain text extension")
                _read_sub_blocks(reader, "a plain text extension", keep=False)
                gif["plain_text_extensions"] += 1
                pending = None
            else:
                _read_sub_blocks(reader, "extension 0x%02X" % label, keep=False)
                gif["unknown_extensions"].append("0x%02X" % label)
        elif kind == 0x00:
            gif["stray_zero_bytes"] += 1  # padding some encoders leave between blocks
        else:
            raise GifFormatError(
                "corrupt GIF: unexpected byte 0x%02X at offset %d where a block was expected"
                % (kind, reader.offset - 1)
            )
    if pending is not None:
        gif["unused_graphic_control_extensions"] += 1
    gif["trailing_bytes"] = reader.count_remaining()
    return gif


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------

def effective_delay_cs(delay_cs):
    """Delay a common browser actually uses: 0 or 1 centisecond is played as 10."""
    return delay_cs if delay_cs >= BROWSER_MIN_DELAY_CS else BROWSER_DEFAULT_DELAY_CS


def summarize(gif, path):
    """Turn the parsed GIF into the report's info dict."""
    frames = gif["frames"]
    delays = [f["delay_cs"] for f in frames]
    nominal_cs = sum(delays)
    effective_cs = sum(effective_delay_cs(d) for d in delays)
    width, height = gif["screen_width"], gif["screen_height"]
    return {
        "format": gif["version"],
        "file_size_bytes": os.path.getsize(path),
        "screen_width": width,
        "screen_height": height,
        "global_colour_table_entries": gif["global_colour_table_entries"],
        "frame_count": len(frames),
        "delays_cs": delays,
        "delay_histogram": dict(sorted(Counter(delays).items())),
        "nominal_duration_s": nominal_cs / 100.0,
        "effective_duration_s": effective_cs / 100.0,
        # The speed the file asks for as written: image frames divided by the sum of the delays. A GIF
        # has no frame rate of its own, so this is the number to compare with the intended one.
        "implied_fps": round(len(frames) * 100.0 / nominal_cs, 3) if nominal_cs > 0 else None,
        "implied_fps_in_browsers": round(len(frames) * 100.0 / effective_cs, 3) if effective_cs > 0 else None,
        "effective_duration_convention": "delays of 0 or 1 cs are counted as 10 cs, as common browsers do "
        "(a convention, not part of the GIF specification)",
        "frames_with_delay_0_or_1": sum(1 for d in delays if d < BROWSER_MIN_DELAY_CS),
        "frames_without_graphic_control": sum(1 for f in frames if not f["has_gce"]),
        "loop": {
            "extension_present": gif["loop_count"] is not None,
            "loop_count": gif["loop_count"],
            "meaning": "0 = repeat forever; no extension = play once",
        },
        "interlaced_frames": sum(1 for f in frames if f["interlaced"]),
        "local_colour_table_frames": sum(1 for f in frames if f["local_colour_table_entries"] > 0),
        "transparent_frames": sum(1 for f in frames if f["transparent"]),
        "partial_frames": sum(
            1 for f in frames if (f["left"], f["top"], f["width"], f["height"]) != (0, 0, width, height)
        ),
        "disposal_methods": dict(sorted(Counter(f["disposal"] for f in frames).items())),
        "comment_blocks": gif["comment_blocks"],
        "comments": gif["comments"],
        "application_extensions": gif["application_ids"],
        "plain_text_extensions": gif["plain_text_extensions"],
        "unknown_extensions": gif["unknown_extensions"],
        "unused_graphic_control_extensions": gif["unused_graphic_control_extensions"],
        "stray_zero_bytes": gif["stray_zero_bytes"],
        "trailing_bytes_after_trailer": gif["trailing_bytes"],
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_frame_count(report, info, expect_frames):
    count = info["frame_count"]
    if expect_frames is not None:
        status = common.PASS if count == expect_frames else common.FAIL
        detail = "counted %d image frame(s), expected %d" % (count, expect_frames)
        report.add("frame_count", status, expect_frames, count, detail)
    elif count == 0:
        report.add("frame_count", common.FAIL, ">= 1", 0, "no image frames found in the file")
    elif count == 1:
        report.add(
            "frame_count", common.WARN, ">= 2 (animated)", 1,
            "only 1 frame, so this GIF is a still image; pass --expect-frames 1 if that is intended",
        )
    else:
        report.add("frame_count", common.PASS, ">= 2 (no --expect-frames given)", count,
                   "counted %d image frames" % count)


def _check_duration(report, info, expect_duration, tol):
    nominal = info["nominal_duration_s"]
    if info["frame_count"] == 0:
        report.add("duration", common.SKIP, expect_duration, None, "no image frames, so no duration was measured")
    elif expect_duration is None:
        report.add("duration", common.SKIP, None, nominal,
                   "as written %d frames last %.2f s = %s fps%s. No --expect-duration was given, so nothing was "
                   "compared" % (info["frame_count"], nominal, _implied_text(info),
                                 "" if nominal > 0 else " (all delays are 0)"))
    else:
        diff = nominal - expect_duration
        status = common.PASS if abs(diff) <= tol + EPS else common.FAIL
        report.add(
            "duration", status, expect_duration, nominal,
            "nominal duration (sum of %d delays) %.2f s vs expected %.2f s: difference %+.3f s, tolerance %.3f s"
            % (info["frame_count"], nominal, expect_duration, diff, tol),
        )


def _check_effective_duration(report, info, expect_duration, tol):
    nominal = info["nominal_duration_s"]
    effective = info["effective_duration_s"]
    if info["frame_count"] == 0:
        report.add("effective_duration", common.SKIP, None, None, "no image frames, so no duration was measured")
        return
    slow = info["frames_with_delay_0_or_1"]
    problems = []
    if slow:
        problems.append(
            "%d frame(s) have a delay of 0 or 1 cs, which common browsers play as 10 cs: the file says %.2f s "
            "but a browser plays %.2f s (browser convention, not a GIF rule)" % (slow, nominal, effective)
        )
        if expect_duration is not None and abs(effective - expect_duration) > tol + EPS:
            problems.append(
                "effective duration %.2f s differs from the expected %.2f s by %+.3f s"
                % (effective, expect_duration, effective - expect_duration)
            )
    expected = expect_duration if expect_duration is not None else nominal
    if problems:
        report.add("effective_duration", common.WARN, expected, effective, "; ".join(problems))
    else:
        report.add(
            "effective_duration", common.PASS, expected, effective,
            "no frame has a delay of 0 or 1 cs, so browsers play the written %.2f s" % nominal,
        )


def _check_rounding_drift(report, info, fps, tol):
    if fps is None:
        report.add("rounding_drift", common.SKIP, None, None,
                   "as written the GIF plays at %s fps. Needs --fps to know the intended frame rate, so the "
                   "speed was not compared" % _implied_text(info))
        return
    count = info["frame_count"]
    if count == 0:
        report.add("rounding_drift", common.SKIP, None, None, "no image frames, so no delays were measured")
        return
    delays = info["delays_cs"]
    nominal = info["nominal_duration_s"]
    ideal = float(count / fps)
    ideal_cs = float(100 / fps)
    drift = nominal - ideal
    uniform = len(set(delays)) == 1
    summary = "total %.2f s vs %d frames / %s fps = %.2f s: drift %+.3f s, tolerance %.3f s" % (
        nominal, count, _fps_text(fps), ideal, drift, tol)
    if abs(drift) <= tol + EPS:
        shape = "all delays are %d cs" % delays[0] if uniform else "delays vary (%s)" % _histogram_text(info)
        report.add("rounding_drift", common.PASS, round(ideal, 3), nominal, "%s; %s" % (shape, summary))
    elif uniform:
        ratio = ideal / nominal if nominal > 0 else None  # above 1: plays faster than intended
        speed, advice = "", ""
        if ratio is not None:
            speed = "; " + _speed_text(ratio, _fps_text(fps))
        low = int(math.floor(ideal_cs))
        if abs(delays[0] - ideal_cs) >= 1.0:  # the delay is not even the rounded neighbour of the ideal one
            advice = ("; a delay of %d cs is %s fps, so this GIF was probably exported at a different frame rate "
                      "than %s fps (or its delays were set by hand)" % (delays[0], _delay_fps_text(delays[0]), _fps_text(fps)))
        elif abs(ideal_cs - round(ideal_cs)) > EPS:
            advice = ("; %s fps needs %.3f cs per frame, so mix %d and %d cs delays (or choose 20, 25 or 50 fps)"
                      % (_fps_text(fps), ideal_cs, low, low + 1))
        else:
            advice = "; %s fps needs %d cs per frame" % (_fps_text(fps), int(round(ideal_cs)))
        report.add(
            "rounding_drift", common.FAIL, round(ideal, 3), nominal,
            "every frame uses the same delay of %d cs; %s%s%s" % (delays[0], summary, speed, advice),
        )
    else:
        report.add(
            "rounding_drift", common.WARN, round(ideal, 3), nominal,
            "delays vary (%s), so this is not the uniform-rounding signature; %s; fine if the holds are intended"
            % (_histogram_text(info), summary),
        )


def _check_loop(report, info):
    count = info["frame_count"]
    loop = info["loop"]["loop_count"]
    expected = "loop count 0 (repeat forever)"
    if count == 0:
        report.add("loop", common.SKIP, expected, None, "no image frames")
    elif count == 1:
        report.add("loop", common.SKIP, expected, loop, "single frame: there is nothing to loop")
    elif loop is None:
        report.add("loop", common.WARN, expected, "no loop extension",
                   "no NETSCAPE2.0 loop extension, so browsers play the animation once and stop; "
                   "add one if it should repeat")
    elif loop == 0:
        report.add("loop", common.PASS, expected, 0, "NETSCAPE2.0 loop count 0: repeats forever")
    else:
        report.add("loop", common.WARN, expected, loop,
                   "NETSCAPE2.0 loop count %d: the animation stops after repeating; fine if intended" % loop)


def _implied_text(info):
    value = info["implied_fps"]
    return "unknown" if value is None else _fps_text(value)


def _delay_fps_text(delay_cs):
    return "0 (no frame rate)" if delay_cs <= 0 else _fps_text(Fraction(100, delay_cs))


def _speed_text(ratio, fps_label):
    """How fast the GIF plays compared with the intended rate. `ratio` above 1 means too fast. Small errors
    are given in percent (4.2 percent fast); large ones as a factor (2.08 times too fast)."""
    if 0.8 <= ratio <= 1.25:
        return "the GIF plays about %.1f%% %s" % (abs(ratio - 1.0) * 100.0, "fast" if ratio > 1 else "slow")
    if ratio > 1:
        return "the GIF plays about %.2f times as fast as %s fps" % (ratio, fps_label)
    return "the GIF plays about %.2f times as slow as %s fps (at %.2f of the intended speed)" % (1.0 / ratio, fps_label, ratio)


def _fps_text(fps):
    value = float(fps)
    return ("%d" % value) if value == int(value) else ("%.3f" % value).rstrip("0")


def _histogram_text(info):
    parts = ["%dx%d cs" % (n, cs) for cs, n in info["delay_histogram"].items()]
    return ", ".join(parts[:6]) + (", ..." if len(parts) > 6 else "")


def build_report(path, fps=None, expect_duration=None, expect_frames=None, tol=DEFAULT_TOL_S):
    """Parse the GIF at path, run the checks, and return a common.Report.

    fps may be a number, a decimal string or a fraction string ("24000/1001").
    Raises common.QAError subclasses (GifFormatError, UsageError) when nothing can be measured.
    """
    if fps is not None:
        fps = common.parse_fraction(fps)
        if fps <= 0:
            raise common.UsageError("--fps must be greater than 0")
    for name, value in (("--expect-duration", expect_duration), ("--tol", tol)):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise common.UsageError("%s must be a number >= 0" % name)
    if expect_frames is not None and expect_frames < 0:
        raise common.UsageError("--expect-frames must be >= 0")
    if not os.path.isfile(path):
        raise common.UsageError("%s is not a file" % path)

    gif = parse_gif(path)
    report = common.Report(SCRIPT_NAME, file=path)  # no external tools: versions stay null, no commands
    report.info = summarize(gif, path)
    _check_frame_count(report, report.info, expect_frames)
    _check_duration(report, report.info, expect_duration, tol)
    _check_effective_duration(report, report.info, expect_duration, tol)
    _check_rounding_drift(report, report.info, fps, tol)
    _check_loop(report, report.info)
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Check the frame count, duration, rounding drift and loop setting of an animated GIF.",
    )
    parser.add_argument("file", metavar="FILE.gif", help="the GIF to check")
    parser.add_argument("--fps", default=None, help="intended frame rate, e.g. 24, 23.976 or 24000/1001")
    parser.add_argument("--expect-duration", type=float, default=None, metavar="S",
                        help="intended duration in seconds")
    parser.add_argument("--expect-frames", type=int, default=None, metavar="N",
                        help="intended number of image frames")
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL_S, metavar="S",
                        help="tolerance in seconds (default %(default)s)")
    common.add_common_args(parser)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = build_report(args.file, fps=args.fps, expect_duration=args.expect_duration,
                          expect_frames=args.expect_frames, tol=args.tol)
    return common.emit(report, args.json)


if __name__ == "__main__":
    sys.exit(common.run_main(main))
