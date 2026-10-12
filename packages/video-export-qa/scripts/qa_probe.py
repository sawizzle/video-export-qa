#!/usr/bin/env python3
# qa_probe.py - what is really inside a video file, and does it match what you intended?
#
# Purpose:
#   Read a video export with ffprobe and report the facts that decide whether it is
#   usable: codec, size, frame rate (and whether it is truly constant), the number of
#   frames that actually decode, colour tags, audio streams and stream start times.
#   Optionally compare those facts with a JSON spec.
# Inputs:
#   FILE                 the file to inspect (video, or audio only)
#   --spec SPEC.json     optional expectations (keys are documented in SKILL.md)
#   --json               print one JSON object instead of the table
#   --ffmpeg / --ffprobe optional tool paths (default: found on PATH)
# Outputs:
#   A table, or with --json one object: tool, file, overall, checks, info, provenance.
#   Nothing is written to disk.
# Exit codes: 0 = PASS or WARN only, 1 = at least one FAIL, 2 = could not run
#   (missing tool, unreadable input, bad arguments or bad spec).
#
# Always-on checks: video_stream, cfr, frame_count_consistent, color_tags.
# Spec checks run only for keys that are present in the spec.
# A check that cannot be measured is SKIP with a reason; it is never a PASS.

import argparse
import difflib
import json
import math
import os
import re
import statistics
import sys
from fractions import Fraction

# python -I (isolated mode) leaves the script folder off sys.path, so add it before
# importing the shared module that sits next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from common import FAIL, PASS, SKIP, WARN  # noqa: E402

SCRIPT_NAME = "qa_probe.py"

# Default tolerance for the duration_s spec check (seconds). One AAC frame at 48 kHz
# is 0.0213 s, so audio-bearing mkv/ts files often need duration_tol_s of 0.05.
DEFAULT_DURATION_TOL_S = 0.02

# Used when the spec says "max_av_start_offset_s": true. Measured on plain ffmpeg
# exports: AAC encoder priming shows up as a 0.0213 s audio lead in MPEG-TS, so 0.05 s
# tolerates normal priming but is still well under two frames at 24 fps (0.083 s).
DEFAULT_MAX_AV_START_OFFSET_S = 0.05

# Two timestamps count as "the same spacing" if they differ by at most this fraction
# of the median spacing (the other accepted case is a difference of one timebase tick).
CFR_RELATIVE_TOLERANCE = 0.01

# Values ffprobe prints (or we treat) as "this colour field is not tagged".
UNTAGGED_VALUES = ("", "unknown", "unspecified", "reserved", "n/a")

# Spec words people use for the same thing ffprobe calls "tv" / "pc".
RANGE_ALIASES = {"limited": "tv", "mpeg": "tv", "full": "pc", "jpeg": "pc"}

# GIF stores one delay per frame in whole centiseconds and has no real frame rate: measured on
# ffmpeg 8.1.1, a healthy 24 fps GIF (delays of 4 and 5 cs) is reported as avg_frame_rate 25/1,
# so duration * fps predicts 100 frames for a file that correctly holds 96. For these codecs the
# fps-based frame-count reference and the fps spec check are not used; qa_gif.py judges GIF speed.
FRAME_RATE_UNRELIABLE_CODECS = ("gif",)

# GIF has no way to carry colour tags, so the always-on color_tags WARN ("untagged") would fire
# on every GIF and could never be fixed. For these codecs it is a SKIP unless a spec asks for tags.
NO_COLOUR_TAG_CODECS = ("gif",)

# Format/stream tags worth showing so embedded provenance is visible.
TAG_NAMES = ("encoder", "comment", "title", "creation_time")

# How far past the last picture a container's own length may run when the file also holds audio: the
# last audio packet can end after the last frame (one AAC frame is 0.021 s, an Opus frame up to 0.06 s).
AUDIO_TAIL_S = 0.07

# The fps spec given as a fraction is compared with this relative tolerance (0.01 percent). Matroska and
# WebM count time in whole milliseconds, so ffprobe reports 59.94 fps as 19001/317 there; 0.01 percent
# still separates 24 from 23.976 (a 0.1 percent step) and 30 from 29.97.
FPS_RELATIVE_TOLERANCE = 1e-4

# Spec layout. Anything else produces a WARN unknown_spec_key.
VIDEO_SPEC_KEYS = ("codec", "pix_fmt", "width", "height", "fps", "frames", "even_dimensions", "cfr")
AUDIO_SPEC_KEYS = ("present", "sample_rate", "channels", "codec")
COLOR_SPEC_KEYS = ("space", "range", "transfer", "primaries")
TOP_SPEC_KEYS = ("video", "color", "audio", "duration_s", "duration_tol_s", "max_av_start_offset_s")
# Keys that belong to sibling scripts (qa_audio, qa_frames, qa_gif) so that one shared
# spec file can drive run_qa without this script calling them unknown.
SIBLING_SPEC_KEYS = (
    "barcode", "gif", "lufs_target", "lufs_tol", "true_peak_max_db",
    "max_leading_silence_s", "max_trailing_silence_s", "max_silence_s", "no_clipping",
)


# --------------------------------------------------------------------------- parsing helpers

def first_line(text):
    """First non-empty line of a text block (for short error messages), cut to a fixed length because
    ffprobe may quote text that the file itself supplied."""
    for line in (text or "").splitlines():
        if line.strip():
            return common.clip_text(line.strip())
    return ""


def as_float(value):
    """ffprobe prints missing numbers as 'N/A' or leaves them out. Returns float or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_ratio(text):
    """'24000/1001' -> Fraction. '0/0', 'N/A', zero, negative or missing -> None."""
    if text is None:
        return None
    try:
        ratio = Fraction(str(text).strip())
    except (ValueError, ZeroDivisionError):
        return None
    return ratio if ratio > 0 else None


def rounded(value, places=6):
    return None if value is None else round(value, places)


def pick_tags(tags):
    """Keep only the provenance-style tags, matched case-insensitively (mkv upper-cases them)."""
    lowered = {str(key).lower(): value for key, value in (tags or {}).items()}
    # The text was written by whoever made the file: it is cut short and is data, never instructions.
    return {name: common.clip_text(lowered[name]) for name in TAG_NAMES if name in lowered}


CLOCK_TEXT = r"(\d{1,6}):(\d{2}):(\d{2}(?:\.\d{1,9})?)"


def duration_tag_seconds(tags):
    """The Matroska/WebM per-stream DURATION tag ("00:00:04.021000000") in seconds, or None. Muxers
    write it at the end of the file; it is the file's own record of how long the stream is."""
    for key, value in (tags or {}).items():
        name = str(key).lower()
        if name == "duration" or name.startswith("duration-"):
            match = re.fullmatch(CLOCK_TEXT, str(value).strip())
            if match:
                hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
                return hours * 3600 + minutes * 60 + seconds
    return None


def is_untagged(value):
    return value is None or str(value).strip().lower() in UNTAGGED_VALUES


def stream_rotation(stream):
    """Rotation in degrees from the display matrix (new ffprobe) or the rotate tag (old)."""
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            degrees = as_float(item["rotation"])
            return None if degrees is None else int(round(degrees))
    degrees = as_float((stream.get("tags") or {}).get("rotate"))
    return None if degrees is None else int(round(degrees))


# --------------------------------------------------------------------------- reading the file

def is_real_video(stream):
    """A video stream that is not just cover art attached to an audio file."""
    if stream.get("codec_type") != "video":
        return False
    return not (stream.get("disposition") or {}).get("attached_pic")


def probe_streams(ffprobe, path, report):
    """One cheap ffprobe call (no decoding): container and per-stream facts as a dict."""
    cmd = [ffprobe, "-v", "error"] + common.LOCAL_FILES_ONLY + ["-show_format", "-show_streams", "-of", "json", path]
    code, out, err = common.run(cmd, report)
    if code != 0:
        raise common.UsageError("ffprobe could not read %s: %s" % (path, first_line(err) or "unknown error"))
    try:
        return json.loads(out)
    except ValueError:
        raise common.UsageError("ffprobe gave output that is not JSON for %s" % path)


def read_frame_timing(ffprobe, path, stream_index, report):
    """One decoding pass over one video stream.

    Returns a dict: counted (frames that decoded, from -count_frames), pts (integer
    presentation timestamps in stream timebase units, in whatever order ffprobe listed
    them), error (text or None) and messages (the first few decoder warnings).
    """
    result = {"counted": None, "pts": [], "error": None, "messages": []}
    cmd = [ffprobe, "-v", "error"] + common.LOCAL_FILES_ONLY + [
        "-select_streams", str(stream_index), "-count_frames",
        "-show_entries", "stream=nb_read_frames:frame=pts,best_effort_timestamp",
        "-of", "json=compact=1", path,
    ]
    code, out, err = common.run(cmd, report)
    result["messages"] = [common.clip_text(line.strip()) for line in err.splitlines() if line.strip()][:5]
    if code != 0:
        result["error"] = first_line(err) or "ffprobe exited with code %d while decoding" % code
        return result
    try:
        data = json.loads(out)
    except ValueError:
        result["error"] = "ffprobe gave unreadable output while counting frames"
        return result
    frames = data.get("frames") or []
    stream = (data.get("streams") or [{}])[0]
    counted = as_int(stream.get("nb_read_frames"))
    result["counted"] = counted if counted is not None else len(frames)
    for frame in frames:
        pts = as_int(frame.get("pts"))
        if pts is None:
            pts = as_int(frame.get("best_effort_timestamp"))
        if pts is not None:
            result["pts"].append(pts)
    return result


# --------------------------------------------------------------------------- describing streams

def describe_format(fmt):
    return {
        "format_name": fmt.get("format_name"),
        "format_long_name": fmt.get("format_long_name"),
        "duration_s": rounded(as_float(fmt.get("duration"))),
        "start_time_s": rounded(as_float(fmt.get("start_time"))),
        "size_bytes": as_int(fmt.get("size")),
        "bit_rate": as_int(fmt.get("bit_rate")),
        "nb_streams": as_int(fmt.get("nb_streams")),
        "tags": pick_tags(fmt.get("tags")),
    }


def describe_video(stream, timing):
    fps = as_ratio(stream.get("avg_frame_rate")) or as_ratio(stream.get("r_frame_rate"))
    return {
        "index": stream.get("index"),
        "codec": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "pix_fmt": stream.get("pix_fmt"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "r_frame_rate": stream.get("r_frame_rate"),
        "fps": rounded(float(fps)) if fps else None,
        "field_order": stream.get("field_order"),
        "color_range": stream.get("color_range"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "rotation": stream_rotation(stream),
        "bit_rate": as_int(stream.get("bit_rate")),
        "time_base": stream.get("time_base"),
        "start_time_s": rounded(as_float(stream.get("start_time"))),
        "duration_s": rounded(as_float(stream.get("duration"))),
        "duration_tag_s": rounded(duration_tag_seconds(stream.get("tags"))),
        "nb_frames_metadata": as_int(stream.get("nb_frames")),
        "counted_frames": timing["counted"] if timing else None,
        "tags": pick_tags(stream.get("tags")),
    }


def describe_audio(stream):
    return {
        "index": stream.get("index"),
        "codec": stream.get("codec_name"),
        "sample_rate": as_int(stream.get("sample_rate")),
        "channels": as_int(stream.get("channels")),
        "channel_layout": stream.get("channel_layout"),
        "bit_rate": as_int(stream.get("bit_rate")),
        "time_base": stream.get("time_base"),
        "start_time_s": rounded(as_float(stream.get("start_time"))),
        "duration_s": rounded(as_float(stream.get("duration"))),
        "tags": pick_tags(stream.get("tags")),
    }


def av_start_offset(video, audio_list):
    """Video start minus first-audio start, in seconds. Positive = audio starts first."""
    if video is None or not audio_list:
        return None
    video_start, audio_start = video["start_time_s"], audio_list[0]["start_time_s"]
    if video_start is None or audio_start is None:
        return None
    return round(video_start - audio_start, 6)


# --------------------------------------------------------------------------- frame timing logic

def analyse_cfr(pts_values):
    """Judge constant frame rate from presentation timestamps (integers, any order).

    The timestamps are sorted first: with B-frames the packets arrive in decode order,
    where pts is not monotonic, so deltas taken in arrival order would be nonsense.
    Constant = all spacings differ by at most one timebase tick, or all are within
    CFR_RELATIVE_TOLERANCE of the median spacing. Returns None with fewer than 2 values.
    """
    ordered = sorted(pts_values)
    deltas = [later - earlier for earlier, later in zip(ordered, ordered[1:])]
    if not deltas:
        return None
    smallest, largest = min(deltas), max(deltas)
    median = statistics.median(deltas)
    within_one_tick = (largest - smallest) <= 1
    within_percent = median > 0 and all(abs(d - median) <= CFR_RELATIVE_TOLERANCE * median for d in deltas)
    return {
        "constant": bool(within_one_tick or within_percent),
        "intervals": len(deltas),
        "min_delta_ticks": smallest,
        "max_delta_ticks": largest,
        "median_delta_ticks": median,
        "span_ticks": ordered[-1] - ordered[0],
    }


def reference_gap(counted, expected, slack):
    """How many frames the decoded count is off from one reference. `slack` is the number of frames a
    container-level length may run past the decoded picture (an audio tail); inside it the gap is 0."""
    extra = expected - counted  # positive: the file promises more frames than were decoded
    if extra < 0:
        return -extra
    return max(extra - slack, 0)


def judge_frame_count(counted, references):
    """references = [(label, expected_count, slack_frames), ...]. 0 apart = PASS, 1 = WARN, more = FAIL."""
    worst = max(reference_gap(counted, expected, slack) for _label, expected, slack in references)
    if worst == 0:
        return PASS
    return WARN if worst == 1 else FAIL


# --------------------------------------------------------------------------- always-on checks

def add_video_stream_check(report, video, video_count, attached_pictures):
    if video is None:
        note = "only cover art is attached" if attached_pictures else "no video stream in this file"
        report.add("video_stream", FAIL, "at least one video stream", 0, note)
        return
    detail = "%s %sx%s" % (video["codec"], video["width"], video["height"])
    if video_count > 1:
        detail += "; %d video streams, the first one is examined" % video_count
    report.add("video_stream", PASS, "at least one video stream", video_count, detail)


def add_cfr_check(report, video, timing, video_spec):
    expected = "constant frame rate"
    if video is None:
        report.add("cfr", SKIP, expected, None, "no video stream")
    elif video_spec.get("cfr") is False:
        report.add("cfr", SKIP, expected, None, "spec says video.cfr is false, so variable frame rate is allowed")
    elif timing["error"]:
        report.add("cfr", SKIP, expected, None, "frames could not be read: " + timing["error"], unmeasured=True)
    else:
        add_cfr_verdict(report, video, timing, expected)


def add_cfr_verdict(report, video, timing, expected):
    stats = analyse_cfr(timing["pts"])
    if stats is None:
        report.add("cfr", SKIP, expected, None,
                   "needs at least 2 frames with timestamps, found %d" % len(timing["pts"]))
        return
    time_base = as_ratio(video["time_base"])
    unit = float(time_base) if time_base else None
    low, high = stats["min_delta_ticks"], stats["max_delta_ticks"]
    if unit:
        actual = "min %.4f s, max %.4f s" % (low * unit, high * unit)
        detail = "%d frame intervals, median %.4f s, one tick = %.6f s" % (
            stats["intervals"], float(stats["median_delta_ticks"]) * unit, unit)
    else:
        actual = "min %d, max %d ticks" % (low, high)
        detail = "%d frame intervals, timebase unknown" % stats["intervals"]
    if stats["constant"]:
        report.add("cfr", PASS, expected, actual, detail)
    else:
        report.add("cfr", FAIL, expected, actual,
                   "variable frame rate: spacing between frames changes. " + detail)


def add_frame_count_check(report, video, timing, fmt_duration_s, audio_count):
    expected = "decoded count agrees with the file's own bookkeeping"
    if video is None:
        report.add("frame_count_consistent", SKIP, expected, None, "no video stream")
        return
    if timing["error"]:
        report.add("frame_count_consistent", SKIP, expected, None,
                   "frames could not be counted: " + timing["error"], unmeasured=True)
        return
    counted = timing["counted"]
    if counted == 0:
        report.add("frame_count_consistent", FAIL, expected, 0, "no frames decoded from the video stream")
        return
    references = frame_count_references(video, fmt_duration_s, audio_count)
    if not references:
        report.add("frame_count_consistent", SKIP, expected, counted,
                   "%d frames decoded but the file records no length to compare with (no nb_frames, no stream "
                   "duration, no DURATION tag, no container duration, or no frame rate). Counting the frames "
                   "that decode and comparing that with itself would prove nothing, so this is not a PASS"
                   % counted)
        return
    status = judge_frame_count(counted, references)
    wording = ", ".join("%s %d" % (label, number) for label, number, _slack in references)
    detail = "decoded %d; references: %s" % (counted, wording)
    loose = [slack for _label, _number, slack in references if slack]
    if loose:
        detail += ("; the container length can run past the picture because the file holds audio, so up to %d "
                   "frame(s) more than decoded are accepted" % max(loose))
    report.add("frame_count_consistent", status, {label: number for label, number, _slack in references},
               counted, detail)


def has_real_frame_rate(video):
    """False for codecs whose 'frame rate' is only a guess (see FRAME_RATE_UNRELIABLE_CODECS)."""
    return str(video["codec"]).lower() not in FRAME_RATE_UNRELIABLE_CODECS


def frame_count_references(video, fmt_duration_s, audio_count):
    """Independent frame counts to compare the decoded count with: [(label, count, slack_frames)].

    Only lengths the FILE recorded count. A length worked out from the decoded frames themselves would
    be the decoded count again, and a truncated file would agree with itself. So:
      nb_frames metadata (exact);
      the video stream's own duration times the frame rate (exact);
      else, for Matroska and WebM which store no stream duration, the stream DURATION tag, else the
      container duration, each times the frame rate. When the file also holds audio those two may run
      past the picture (an audio tail), so they carry AUDIO_TAIL_S of slack.
    """
    references = []
    if video["nb_frames_metadata"] is not None:
        references.append(("nb_frames", video["nb_frames_metadata"], 0))
    if not has_real_frame_rate(video):
        return references  # duration * fps would flag healthy files, so only nb_frames is used
    fps = as_ratio(video["avg_frame_rate"]) or as_ratio(video["r_frame_rate"])
    if not fps:
        return references
    if video["duration_s"] is not None:
        label, seconds, slack = "duration*fps", video["duration_s"], 0
    else:
        if video["duration_tag_s"] is not None:
            label, seconds = "DURATION tag*fps", video["duration_tag_s"]
        elif fmt_duration_s is not None:
            label, seconds = "container duration*fps", fmt_duration_s
        else:
            return references
        slack = int(math.ceil(AUDIO_TAIL_S * float(fps))) if audio_count else 0
    references.append((label, int(math.floor(seconds * float(fps) + 0.5)), slack))
    return references


def normalise_colour(field, value):
    text = str(value).strip().lower()
    return RANGE_ALIASES.get(text, text) if field == "range" else text


def add_color_check(report, video, color_spec):
    if video is None:
        report.add("color_tags", SKIP, "colour tags present", None, "no video stream")
        return
    raw = {
        "space": video["color_space"], "transfer": video["color_transfer"],
        "primaries": video["color_primaries"], "range": video["color_range"],
    }
    actual = {name: ("unknown" if is_untagged(value) else value) for name, value in raw.items()}
    if color_spec:
        add_color_spec_verdict(report, color_spec, actual)
        return
    if str(video["codec"]).lower() in NO_COLOUR_TAG_CODECS:
        report.add("color_tags", SKIP, "colour tags present", actual,
                   "%s cannot carry colour tags (browsers treat it as sRGB), so there is nothing to tag"
                   % str(video["codec"]).upper())
        return
    missing =[name for name, value in actual.items() if is_untagged(value)]
    if missing:
        report.add("color_tags", WARN, "all four colour fields tagged", actual,
                   "untagged: %s. Players guess; HD is usually guessed as BT.709." % ", ".join(missing))
    else:
        report.add("color_tags", PASS, "all four colour fields tagged", actual, "")


def add_color_spec_verdict(report, color_spec, actual):
    wrong = []
    expected = {}
    for field, wanted in color_spec.items():
        wanted_text = normalise_colour(field, wanted)
        expected[field] = wanted_text
        if is_untagged(actual[field]) or normalise_colour(field, actual[field]) != wanted_text:
            wrong.append("%s is %s, spec wants %s" % (field, actual[field], wanted_text))
    if wrong:
        report.add("color_tags", FAIL, expected, {k: actual[k] for k in expected}, "; ".join(wrong))
    else:
        report.add("color_tags", PASS, expected, {k: actual[k] for k in expected}, "matches the spec")


# --------------------------------------------------------------------------- spec validation

def spec_section(spec, key):
    value = spec.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise common.UsageError('spec key "%s" must be a JSON object' % key)
    return value


def spec_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise common.UsageError('spec key "%s" must be a whole number, got %s' % (name, common.short_repr(value)))
    return value


def spec_number(value, name, minimum=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise common.UsageError('spec key "%s" must be a number, got %s' % (name, common.short_repr(value)))
    if value < minimum:
        raise common.UsageError('spec key "%s" must be at least %s, got %s' % (name, minimum, common.short_repr(value)))
    return float(value)


def spec_bool(value, name):
    if not isinstance(value, bool):
        raise common.UsageError('spec key "%s" must be true or false, got %s' % (name, common.short_repr(value)))
    return value


def spec_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise common.UsageError('spec key "%s" must be a non-empty string, got %s' % (name, common.short_repr(value)))
    return value.strip().lower()


def spec_fps(value, name):
    """Returns (exact, Fraction). A fraction string such as '24000/1001' is compared exactly;
    a plain number such as 23.976 is compared within 0.001 fps."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise common.UsageError('spec key "%s" must be a number or a fraction string, got %s'
                                % (name, common.short_repr(value)))
    try:
        ratio = common.parse_fraction(value)
    except common.UsageError as exc:
        raise common.UsageError('spec key "%s": %s' % (name, exc))
    if ratio <= 0:
        raise common.UsageError('spec key "%s" must be greater than zero, got %s' % (name, common.short_repr(value)))
    return (isinstance(value, str) and "/" in value), ratio


def validate_spec(spec):
    """Reject a malformed spec before any tool runs: a clear exit code 2 now beats a crash
    after a long decoding pass. The check functions validate again as they read each value."""
    video, audio = spec_section(spec, "video"), spec_section(spec, "audio")
    spec_section(spec, "color")
    for key in ("codec", "pix_fmt"):
        if key in video:
            spec_text(video[key], "video." + key)
    for key in ("width", "height", "frames"):
        if key in video:
            spec_int(video[key], "video." + key)
    for key in ("even_dimensions", "cfr"):
        if key in video:
            spec_bool(video[key], "video." + key)
    if "fps" in video:
        spec_fps(video["fps"], "video.fps")
    if "present" in audio:
        spec_bool(audio["present"], "audio.present")
    for key in ("sample_rate", "channels"):
        if key in audio:
            spec_int(audio[key], "audio." + key)
    if "codec" in audio:
        spec_text(audio["codec"], "audio.codec")
    for key in ("duration_s", "duration_tol_s"):
        if key in spec:
            spec_number(spec[key], key)
    if "max_av_start_offset_s" in spec and not isinstance(spec["max_av_start_offset_s"], bool):
        spec_number(spec["max_av_start_offset_s"], "max_av_start_offset_s")


def add_unknown_key_warnings(report, spec):
    nested = {"video": VIDEO_SPEC_KEYS, "audio": AUDIO_SPEC_KEYS, "color": COLOR_SPEC_KEYS}
    for key in spec:
        if key in nested:
            for inner in spec_section(spec, key):
                if inner not in nested[key]:
                    report.add("unknown_spec_key", WARN, "a documented key", "%s.%s" % (key, inner),
                               "not a known key inside %s; allowed: %s%s"
                               % (key, ", ".join(nested[key]), did_you_mean(inner, nested[key])))
        elif key not in TOP_SPEC_KEYS and key not in SIBLING_SPEC_KEYS:
            report.add("unknown_spec_key", WARN, "a documented key", key, unknown_key_hint(key))


def did_you_mean(key, candidates):
    """' (did you mean "duration_s"?)' for the documented key that is nearest to a misspelt one, else ''."""
    close = difflib.get_close_matches(str(key), list(candidates), n=1)
    return ' (did you mean "%s"?)' % close[0] if close else ""


def unknown_key_hint(key):
    if key in VIDEO_SPEC_KEYS:
        return 'not a top-level key; did you mean "video": {"%s": ...}?' % key
    if key in ("sample_rate", "channels"):
        return 'not a top-level key; did you mean "audio": {"%s": ...}?' % key
    return "not a documented key, so it was ignored" + did_you_mean(key, TOP_SPEC_KEYS + SIBLING_SPEC_KEYS)


# --------------------------------------------------------------------------- spec checks

def add_equal(report, check_id, expected, actual, equal, detail=""):
    """PASS when equal, otherwise FAIL. A stream the spec asked for but the file lacks is a
    FAIL too: the requirement is unmet, so it is not a SKIP."""
    report.add(check_id, PASS if equal else FAIL, expected, actual, detail)


def add_video_spec_checks(report, vspec, video, timing):
    gone = "" if video else "no video stream"
    for key in ("codec", "pix_fmt"):
        if key in vspec:
            wanted = spec_text(vspec[key], "video." + key)
            actual = video[key] if video else None
            add_equal(report, key, wanted, actual, str(actual).lower() == wanted, gone)
    for key in ("width", "height"):
        if key in vspec:
            wanted = spec_int(vspec[key], "video." + key)
            actual = video[key] if video else None
            add_equal(report, key, wanted, actual, actual == wanted, gone or rotation_note(video))
    if vspec.get("even_dimensions") is not None and spec_bool(vspec["even_dimensions"], "video.even_dimensions"):
        add_even_dimensions(report, video)
    if "fps" in vspec:
        add_fps_check(report, vspec["fps"], video)
    if "frames" in vspec:
        add_frames_check(report, spec_int(vspec["frames"], "video.frames"), video, timing)


def rotation_note(video):
    """Width and height are the STORED size. A file with rotation metadata is shown turned by players,
    so say so next to those two checks instead of leaving a confusing mismatch."""
    rotation = video["rotation"] if video else None
    if not rotation:
        return ""
    stored = "%sx%s" % (video["width"], video["height"])
    if abs(rotation) % 180 == 90:
        shown = "%sx%s" % (video["height"], video["width"])
        return ("the file stores %s with a display rotation of %d degrees, so players show it as %s; "
                "this check compares the stored size" % (stored, rotation, shown))
    return "the file has a display rotation of %d degrees (the stored size %s is unchanged by it)" % (rotation, stored)


def add_even_dimensions(report, video):
    if video is None or video["width"] is None or video["height"] is None:
        report.add("even_dimensions", FAIL, "width and height both even", None, "no video stream")
        return
    width, height = video["width"], video["height"]
    even = width % 2 == 0 and height % 2 == 0
    detail = "" if even else "odd dimensions break many encoders and players (yuv420 needs even sizes)"
    report.add("even_dimensions", PASS if even else FAIL, "width and height both even",
               "%dx%d" % (width, height), detail)


def add_fps_check(report, raw, video):
    exact, wanted = spec_fps(raw, "video.fps")
    if video is not None and not has_real_frame_rate(video):
        report.add("fps", SKIP, str(raw), None,
                   "a %s file stores whole-centisecond delays, not a frame rate; "
                   "use qa_gif.py --fps (or gif.fps in the spec) to judge its speed" % str(video["codec"]).upper())
        return
    actual = (as_ratio(video["avg_frame_rate"]) or as_ratio(video["r_frame_rate"])) if video else None
    if actual is None:
        report.add("fps", FAIL, str(raw), None, "no video stream" if video is None else "frame rate not reported")
        return
    shown = "%s (%.6f)" % (video["avg_frame_rate"], float(actual))
    gap = abs(float(actual) - float(wanted))
    if exact:
        # A fraction spec: exact, or within FPS_RELATIVE_TOLERANCE (the container timebase may round it).
        equal = actual == wanted or gap <= FPS_RELATIVE_TOLERANCE * float(wanted)
        rule = "tolerance 0.01 percent of the exact fraction"
        how = "exact fraction match" if actual == wanted else (
            "matches the fraction within 0.01 percent; the container timebase rounded the rate")
    else:
        equal = gap <= 0.001
        rule = "tolerance 0.001 fps"
        how = "within 0.001 fps"
    report.add("fps", PASS if equal else FAIL, str(raw), shown,
               how if equal else "%s, differs by %.6f fps" % (rule, gap))


def add_frames_check(report, wanted, video, timing):
    if video is None:
        report.add("frames", FAIL, wanted, None, "no video stream")
    elif timing["error"]:
        report.add("frames", SKIP, wanted, None, "frames could not be counted: " + timing["error"], unmeasured=True)
    else:
        add_equal(report, "frames", wanted, timing["counted"], timing["counted"] == wanted)


def add_duration_check(report, spec, fmt_info, video, audio_list):
    wanted = spec_number(spec["duration_s"], "duration_s")
    tol = spec_number(spec.get("duration_tol_s", DEFAULT_DURATION_TOL_S), "duration_tol_s")
    actual = fmt_info["duration_s"]
    if actual is None:
        report.add("duration_s", SKIP, wanted, None, "the container reports no duration", unmeasured=True)
        return
    ok = abs(actual - wanted) <= tol + 1e-9
    detail = "container duration; tolerance %.3f s" % tol
    if not ok:
        detail += duration_hint(wanted, tol, video, audio_list, actual)
    report.add("duration_s", PASS if ok else FAIL, wanted, actual, detail)


def duration_hint(wanted, tol, video, audio_list, container):
    """When the picture alone matches but the container is longer, say why."""
    if not (video and video["duration_s"] is not None and audio_list):
        return ""
    if abs(video["duration_s"] - wanted) > tol + 1e-9:
        return ""
    excess = container - video["duration_s"]
    if excess <= 0.05:
        return ("; the video stream alone is %.3f s, the extra %.3f s is audio padding "
                "(one AAC frame is 0.021 s), raise duration_tol_s if that is acceptable"
                % (video["duration_s"], excess))
    return "; the video stream alone is %.3f s, the audio runs %.3f s past the picture" % (video["duration_s"], excess)


def add_audio_spec_checks(report, aspec, audio_list):
    if "present" in aspec:
        wanted = spec_bool(aspec["present"], "audio.present")
        have = len(audio_list) > 0
        report.add("audio.present", PASS if have == wanted else FAIL, wanted, have,
                   "%d audio stream(s)" % len(audio_list))
    checks = (("sample_rate", spec_int), ("channels", spec_int), ("codec", spec_text))
    for key, parse in checks:
        if key not in aspec:
            continue
        wanted = parse(aspec[key], "audio." + key)
        if not audio_list:
            report.add("audio." + key, FAIL, wanted, None, "no audio stream")
            continue
        actual = audio_list[0][key]
        equal = (str(actual).lower() == wanted) if key == "codec" else (actual == wanted)
        note = other_streams_note(audio_list, key)
        if key == "sample_rate" and not equal and wanted < 1000:
            hint = "audio.sample_rate is in Hz (48000 for 48 kHz), not kHz"
            note = note + "; " + hint if note else hint
        report.add("audio." + key, PASS if equal else FAIL, wanted, actual, note)


def other_streams_note(audio_list, key):
    """The spec is checked against the first audio stream; say what the others have."""
    if len(audio_list) < 2:
        return ""
    others = ", ".join(str(item[key]) for item in audio_list[1:])
    return "first of %d audio streams; the others have %s" % (len(audio_list), others)


def add_av_offset_check(report, raw, video, audio_list, offset):
    if raw is False:
        return
    if raw is True:
        limit = DEFAULT_MAX_AV_START_OFFSET_S
    else:
        limit = spec_number(raw, "max_av_start_offset_s")
    if video is None or not audio_list:
        report.add("max_av_start_offset_s", SKIP, limit, None, "needs both a video stream and an audio stream")
    elif offset is None:
        report.add("max_av_start_offset_s", SKIP, limit, None, "a stream start time is not reported",
                   unmeasured=True)
    else:
        report.add("max_av_start_offset_s", PASS if abs(offset) <= limit + 1e-9 else FAIL, limit, abs(offset),
                   "video start minus audio start = %+.4f s (positive means audio starts first)" % offset)


# --------------------------------------------------------------------------- orchestration

def analyze(path, spec, tools, spec_sha256=None):
    """Probe one file and return a common.Report. Used by main() and by run_qa."""
    validate_spec(spec)
    report = common.Report(SCRIPT_NAME, file=path, tools=tools)
    report.spec_sha256 = spec_sha256
    video_spec = spec_section(spec, "video")
    color_spec = spec_section(spec, "color")
    audio_spec = spec_section(spec, "audio")
    abs_path = os.path.abspath(path)  # absolute, so a name starting with "-" is never read as an option

    data = probe_streams(tools["ffprobe"], abs_path, report)
    streams = data.get("streams") or []
    real_video = [s for s in streams if is_real_video(s)]
    audio_raw = [s for s in streams if s.get("codec_type") == "audio"]
    attached = [s for s in streams if s.get("codec_type") == "video" and not is_real_video(s)]

    timing = None
    if real_video:
        timing = read_frame_timing(tools["ffprobe"], abs_path, real_video[0].get("index"), report)
    video = describe_video(real_video[0], timing) if real_video else None
    audio_list = [describe_audio(s) for s in audio_raw]
    fmt_info = describe_format(data.get("format") or {})
    offset = av_start_offset(video, audio_list)

    report.info = {
        "format": fmt_info,
        "video": video,
        "audio": audio_list,
        "attached_pictures": len(attached),
        "av_start_offset_s": offset,
        "decoder_messages": timing["messages"] if timing else [],
    }

    add_video_stream_check(report, video, len(real_video), len(attached))
    add_cfr_check(report, video, timing, video_spec)
    add_frame_count_check(report, video, timing, fmt_info["duration_s"], len(audio_list))
    add_color_check(report, video, {k: v for k, v in color_spec.items() if k in COLOR_SPEC_KEYS})
    add_unknown_key_warnings(report, spec)
    add_video_spec_checks(report, video_spec, video, timing)
    if "duration_s" in spec:
        add_duration_check(report, spec, fmt_info, video, audio_list)
    add_audio_spec_checks(report, audio_spec, audio_list)
    if "max_av_start_offset_s" in spec:
        add_av_offset_check(report, spec["max_av_start_offset_s"], video, audio_list, offset)
    return report


def find_tools(args):
    """ffprobe is required. ffmpeg is only recorded for provenance, so a missing ffmpeg is
    ignored unless the user pointed --ffmpeg at a path that does not exist."""
    tools = {"ffprobe": common.find_tool("ffprobe", args.ffprobe)}
    try:
        tools["ffmpeg"] = common.find_tool("ffmpeg", args.ffmpeg)
    except common.ToolMissing:
        if args.ffmpeg:
            raise
    return tools


def make_stdout_safe():
    """Kept for the scripts that import it from here; the work is done in common (run_main calls it too)."""
    common.make_stdout_safe()


def build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Report what is inside a video file (ffprobe) and optionally check it against a JSON spec.",
    )
    parser.add_argument("file", help="video (or audio) file to inspect")
    parser.add_argument("--spec", default=None, help="JSON file with expectations (see SKILL.md)")
    common.add_common_args(parser)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.file):
        raise common.UsageError("input file not found: %s" % args.file)
    spec, spec_sha = common.load_spec(args.spec)
    report = analyze(args.file, spec, find_tools(args), spec_sha)
    make_stdout_safe()
    return common.emit(report, args.json)


if __name__ == "__main__":
    sys.exit(common.run_main(main))
