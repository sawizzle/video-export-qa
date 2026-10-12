#!/usr/bin/env python3
# qa_audio.py - is there real sound in this file, how loud is it, and where is it silent?
#
# Purpose:
#   Measure the audio of a video or audio file with ffmpeg filters (ebur128 for loudness
#   and true peak, volumedetect for mean/max volume, silencedetect for gaps) and catch
#   the "silent export" trap: a file can contain an audio stream that decodes to nothing
#   but zeros, so "an audio stream exists" proves nothing. Loudness is always measured.
# Inputs:
#   FILE                 video or audio file
#   --spec SPEC.json     optional expectations: lufs_target (+ lufs_tol, default 1.0),
#                        true_peak_max_db, max_leading_silence_s, max_trailing_silence_s,
#                        max_silence_s (longest gap inside the audio), no_clipping
#   --json               print one JSON object instead of the table
#   --ffmpeg / --ffprobe optional tool paths (default: found on PATH)
# Outputs:
#   A table, or with --json one object: tool, file, overall, checks, info, provenance.
#   Nothing is written to disk.
# Exit codes: 0 = PASS or WARN only, 1 = at least one FAIL, 2 = could not run.
#
# Every audio stream is analysed (one ffmpeg pass each). The top-level info values and the
# spec checks use the first audio stream; not_silent covers all of them. With no audio
# stream, every audio check is SKIP ("no audio stream"), which is not an error.
# A measurement that returned nothing is SKIP, never PASS.
# Note: a length difference between picture and sound can be entirely leading or
# trailing silence, so silence is reported separately and never called a sync failure.

import argparse
import difflib
import json
import math
import os
import re
import sys

# python -I (isolated mode) leaves the script folder off sys.path, so add it before
# importing the modules that sit next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import qa_probe  # noqa: E402  (reuses its small parsing and spec-validation helpers)
from common import FAIL, PASS, SKIP, WARN  # noqa: E402

SCRIPT_NAME = "qa_audio.py"

# One sequential pass. asetpts makes every time relative to the first audio sample, so a
# late-starting track (or a container with a start offset) cannot shift the silence times.
# framelog=verbose moves ebur128's one-line-per-100-ms output to the "verbose" log level, which is
# not shown at ffmpeg's default level, so only the final Summary is printed. (framelog=quiet does the
# same on ffmpeg 6.0 and newer, but older builds, such as 4.4 and 5.1 that Ubuntu 22.04 and Debian 12
# ship, reject the value "quiet" and the whole measurement fails: measured 2026-10-10.)
SILENCE_NOISE_DB = "-50dB"
SILENCE_MIN_S = "0.5"
FILTER_CHAIN = (
    "asetpts=PTS-STARTPTS,ebur128=peak=true:framelog=verbose,volumedetect,"
    "silencedetect=noise=%s:d=%s" % (SILENCE_NOISE_DB, SILENCE_MIN_S)
)
NO_AUDIO = "no audio stream"

# Measured on ffmpeg 8.1.1: volumedetect reports 16-bit digital silence as -91.0 dB (not
# -inf), so anything at or below -90 dB is treated as digital silence.
DIGITAL_SILENCE_DB = -90.0
# ebur128 prints -70.0 LUFS for anything below its absolute gate, and also for audio too
# short to fill one 400 ms gating block, so that value only means "silent" for longer audio.
LUFS_FLOOR = -70.0
MIN_LOUDNESS_S = 0.4
# BS.1770 loudness ignores the LFE channel and very low frequencies, so audio with real signal only
# there reads -70 LUFS too. If the mean volume is above this level the stream holds sound that the
# loudness meter did not count, and it is reported as a WARN, not called silent.
SIGNAL_PRESENT_DB = -60.0
# A true peak at or above this is treated as clipping.
CLIP_DBTP = -0.1
# How close a silent run must be to the start or end of the audio to count as leading/trailing.
EDGE_TOLERANCE_S = 0.05
DEFAULT_LUFS_TOL = 1.0
EPSILON = 1e-9

SPEC_KEYS = (
    "lufs_target", "lufs_tol", "true_peak_max_db", "max_leading_silence_s",
    "max_trailing_silence_s", "max_silence_s", "no_clipping",
)
# Keys that belong to sibling scripts, accepted so one shared spec can drive run_qa.
SIBLING_SPEC_KEYS = (
    "video", "color", "audio", "duration_s", "duration_tol_s", "max_av_start_offset_s",
    "barcode", "gif",
)

# ffmpeg prints these on stderr. Text that a FILE supplies (its title, comment and other tags) is
# printed on stderr too, in the input and output metadata dumps, and could imitate a measurement
# ("silence_start: 0"). So each filter value is read only from a line that BEGINS with the filter's
# own log prefix, "[Parsed_silencedetect_3 @ 0000019b48224a80] silence_start: 0": a metadata dump
# line always begins with spaces or a label, never with "[". (Patterns are used with re.MULTILINE.)
FILTER_PREFIX = r"^\[(?:Parsed_)?%s[_0-9]* @ [^\]]+\]\s+"
EBU_SUMMARY = FILTER_PREFIX % "ebur128" + r"Summary:"
EBU_INTEGRATED = r"^\s*I:\s+(\S+)\s+LUFS"
EBU_RANGE = r"^\s*LRA:\s+(\S+)\s+LU\s*$"
EBU_PEAK = r"^\s*Peak:\s+(\S+)\s+dBFS"
VOL_SAMPLES = FILTER_PREFIX % "volumedetect" + r"n_samples:\s*(\d+)"
VOL_MEAN = FILTER_PREFIX % "volumedetect" + r"mean_volume:\s*(\S+)\s*dB"
VOL_MAX = FILTER_PREFIX % "volumedetect" + r"max_volume:\s*(\S+)\s*dB"
SILENCE_EVENT = FILTER_PREFIX % "silencedetect" + r"silence_(start|end):\s*(\S+)"


# --------------------------------------------------------------------------- small helpers

def last_line(text):
    """Last non-empty line of a text block (ffmpeg puts its error message last), cut to a fixed length
    because ffmpeg may quote text that the file itself supplied."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return common.clip_text(lines[-1]) if lines else ""


def jsonable(value):
    """JSON has no -Infinity: show infinities as the strings "inf" / "-inf" and NaN as null."""
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


# --------------------------------------------------------------------------- parsing ffmpeg output

def parse_number(token):
    """'-20.1' -> -20.1, '-inf' -> -inf, anything unreadable (or nan) -> None."""
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def last_match(pattern, text):
    """Last match of a one-group pattern, or None. Patterns are anchored per line."""
    found = re.findall(pattern, text, re.MULTILINE)
    return found[-1] if found else None


def parse_ebur128(text):
    """Loudness values from the LAST 'Summary:' block (None when there is no summary)."""
    values = {"integrated_lufs": None, "loudness_range_lu": None, "true_peak_dbtp": None}
    starts = [m.start() for m in re.finditer(EBU_SUMMARY, text, re.MULTILINE)]
    if not starts:
        return values
    block = text[starts[-1]:]
    for key, pattern in (("integrated_lufs", EBU_INTEGRATED), ("loudness_range_lu", EBU_RANGE),
                         ("true_peak_dbtp", EBU_PEAK)):
        token = last_match(pattern, block)
        values[key] = parse_number(token) if token is not None else None
    return values


def parse_volumedetect(text):
    """n_samples, mean and max volume. ffmpeg builds the filter graph twice, so an early
    'n_samples: 0' line can precede the real report: always take the last of each."""
    samples = last_match(VOL_SAMPLES, text)
    mean = last_match(VOL_MEAN, text)
    peak = last_match(VOL_MAX, text)
    return {
        "n_samples": int(samples) if samples is not None else None,
        "mean_volume_db": parse_number(mean) if mean is not None else None,
        "max_volume_db": parse_number(peak) if peak is not None else None,
    }


def parse_silence_segments(text, audio_duration):
    """[[start, end], ...] in seconds from the silencedetect lines. A silence still open
    at the end (older ffmpeg does not flush it) is closed at the audio duration."""
    segments, open_start = [], None
    for kind, token in re.findall(SILENCE_EVENT, text, re.MULTILINE):
        value = parse_number(token)
        if value is None:
            continue
        value = max(value, 0.0)
        if kind == "start":
            open_start = value
        else:
            start = open_start if open_start is not None else 0.0
            segments.append([round(start, 6), round(max(value, start), 6)])
            open_start = None
    if open_start is not None and audio_duration is not None and audio_duration > open_start:
        segments.append([round(open_start, 6), round(audio_duration, 6)])
    return segments


def summarise_silence(segments, audio_duration):
    """Split silence into (leading_s, trailing_s, internal_segments).

    A run counts as leading if it starts within EDGE_TOLERANCE_S of the first sample and as
    trailing if it ends within EDGE_TOLERANCE_S of the last. A file that is silent from end
    to end reports its whole length as both leading and trailing, and as one internal gap.
    """
    if not segments:
        return 0.0, 0.0, []
    first, last = segments[0], segments[-1]
    touches_start = first[0] <= EDGE_TOLERANCE_S
    touches_end = audio_duration is not None and last[1] >= audio_duration - EDGE_TOLERANCE_S
    if touches_start and touches_end and first is last:
        length = round(last[1] - first[0], 6)
        return length, length, [first]
    leading = first[1] - first[0] if touches_start else 0.0
    trailing = last[1] - last[0] if touches_end else 0.0
    internal = [
        seg for seg in segments
        if not ((seg is first and touches_start) or (seg is last and touches_end))
    ]
    return round(leading, 6), round(trailing, 6), internal


# --------------------------------------------------------------------------- measuring

def list_audio_streams(ffprobe, path, report):
    """Audio streams in file order, from one cheap ffprobe call (no decoding)."""
    cmd = [ffprobe, "-v", "error"] + common.LOCAL_FILES_ONLY + [
        "-select_streams", "a", "-show_entries",
        "stream=index,codec_name,sample_rate,channels", "-of", "json", path,
    ]
    code, out, err = common.run(cmd, report)
    if code != 0:
        raise common.UsageError("ffprobe could not read %s: %s" % (path, qa_probe.first_line(err) or "unknown error"))
    try:
        return json.loads(out).get("streams") or []
    except ValueError:
        raise common.UsageError("ffprobe gave output that is not JSON for %s" % path)


def decoded_duration_s(samples, facts):
    """Seconds of audio that decoding produced (volumedetect counts samples of all channels)."""
    channels = qa_probe.as_int(facts.get("channels"))
    rate = qa_probe.as_int(facts.get("sample_rate"))
    if not samples or not channels or not rate:
        return None
    return samples / float(channels * rate)


def empty_measurement(position, facts):
    return {
        "audio_index": position,
        "stream_index": facts.get("index"),
        "codec": facts.get("codec_name"),
        "sample_rate": qa_probe.as_int(facts.get("sample_rate")),
        "channels": qa_probe.as_int(facts.get("channels")),
        "samples_decoded": None,
        "audio_duration_s": None,
        "integrated_lufs": None,
        "loudness_range_lu": None,
        "true_peak_dbtp": None,
        "max_volume_db": None,
        "mean_volume_db": None,
        "silence_segments": [],
        "leading_silence_s": None,
        "trailing_silence_s": None,
        "longest_internal_silence_s": None,
        "error": None,
    }


def fill_measurement(measurement, text, facts):
    """Copy everything ffmpeg printed into the measurement dict."""
    volume = parse_volumedetect(text)
    samples = volume["n_samples"]
    measurement["samples_decoded"] = samples
    if samples is None:
        measurement["error"] = "ffmpeg printed no volume measurement"
        return
    if samples == 0:
        # Nothing decoded. ebur128 prints made-up numbers for empty input (for example a
        # 0 dB peak), so none of them are kept.
        return
    duration = decoded_duration_s(samples, facts)
    measurement["audio_duration_s"] = qa_probe.rounded(duration)
    measurement.update(parse_ebur128(text))
    measurement["mean_volume_db"] = volume["mean_volume_db"]
    measurement["max_volume_db"] = volume["max_volume_db"]
    segments = parse_silence_segments(text, duration)
    leading, trailing, internal = summarise_silence(segments, duration)
    measurement["silence_segments"] = segments
    measurement["leading_silence_s"] = leading
    measurement["trailing_silence_s"] = trailing
    longest = max((end - start for start, end in internal), default=0.0)
    measurement["longest_internal_silence_s"] = round(longest, 6)


def measure_stream(ffmpeg, path, position, facts, report):
    """One sequential ffmpeg pass over audio stream number `position` (0-based)."""
    measurement = empty_measurement(position, facts)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-nostats"] + common.LOCAL_FILES_ONLY + [
        "-i", path, "-map", "0:a:%d" % position, "-af", FILTER_CHAIN, "-f", "null", "-",
    ]
    code, _out, err = common.run(cmd, report)
    err = err.replace("\r\n", "\n")
    if code != 0:
        measurement["error"] = last_line(err) or "ffmpeg exited with code %d" % code
        return measurement
    fill_measurement(measurement, err, facts)
    return measurement


# --------------------------------------------------------------------------- judging

def loudness_measurable(measurement):
    """Integrated loudness needs at least one 400 ms gating block of audio."""
    duration = measurement["audio_duration_s"]
    return duration is not None and duration >= MIN_LOUDNESS_S


def judge_silence(measurement):
    """Decide one stream: returns (status, actual, detail). FAIL = present but silent."""
    if measurement["error"]:
        return SKIP, None, "ffmpeg could not measure this stream: " + measurement["error"]
    if not measurement["samples_decoded"]:
        return SKIP, None, "no audio samples were decoded (volumedetect counted 0), nothing to judge"
    mean, lufs = measurement["mean_volume_db"], measurement["integrated_lufs"]
    if mean is None:
        return SKIP, None, "volumedetect returned no mean volume, nothing to judge"
    actual = {"mean_volume_db": mean, "integrated_lufs": lufs}
    if mean <= DIGITAL_SILENCE_DB:
        return FAIL, actual, "audio stream present but silent: mean volume %.1f dB is digital silence" % mean
    measurable = loudness_measurable(measurement)
    if measurable and lufs is not None and lufs <= LUFS_FLOOR:
        if mean > SIGNAL_PRESENT_DB:
            return WARN, actual, (
                "integrated loudness is at the measurement floor (%.1f LUFS) but the audio is not silent "
                "(mean volume %.1f dB). BS.1770 loudness does not count the LFE channel or very low "
                "frequencies, so the loudness numbers say nothing about this audio" % (lufs, mean))
        return FAIL, actual, ("audio stream present but silent: integrated loudness %.1f LUFS is at the "
                              "measurement floor" % lufs)
    if not measurable:
        return PASS, actual, ("mean volume %.1f dB; audio is shorter than 0.4 s so integrated loudness "
                              "is not defined and was not used" % mean)
    loudness = "n/a" if lufs is None else "%.1f" % lufs
    return PASS, actual, "mean volume %.1f dB, integrated loudness %s LUFS" % (mean, loudness)


def combine_statuses(statuses):
    """Several streams, one verdict: any FAIL fails; any WARN warns; all PASS passes; a mix of PASS
    and SKIP is a WARN (part of the audio could not be measured); all SKIP stays SKIP."""
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    if all(status == PASS for status in statuses):
        return PASS
    return WARN if PASS in statuses else SKIP


def add_not_silent_check(report, measurements):
    expected = "mean volume above %.0f dB and integrated loudness above %.0f LUFS" % (DIGITAL_SILENCE_DB, LUFS_FLOOR)
    if not measurements:
        report.add("not_silent", SKIP, expected, None, "no audio stream")
        return
    verdicts = [judge_silence(m) for m in measurements]
    if len(verdicts) == 1:
        status, actual, detail = verdicts[0]
        # An audio stream exists, so a SKIP here means it could not be measured, not that it is absent.
        report.add("not_silent", status, expected, jsonable(actual), detail, unmeasured=(status == SKIP))
        return
    status = combine_statuses([v[0] for v in verdicts])
    actual = {"audio_stream_%d" % i: jsonable(v[1]) for i, v in enumerate(verdicts)}
    detail = "; ".join("audio stream %d: %s %s" % (i, v[0], v[2]) for i, v in enumerate(verdicts))
    report.add("not_silent", status, expected, actual, detail, unmeasured=(status == SKIP))


def unusable_reason(primary):
    """Why spec checks cannot be measured (None when the first stream has measurements)."""
    if primary is None:
        return NO_AUDIO
    if primary["error"]:
        return "ffmpeg could not measure the audio: " + primary["error"]
    if not primary["samples_decoded"]:
        return "no audio samples were decoded"
    return None


def add_limit_check(report, check_id, limit, actual, reason, what):
    """PASS when the measured value is at or below the limit; SKIP when it was not measured."""
    if reason or actual is None:
        report.add(check_id, SKIP, limit, None, reason or "%s was not reported" % what,
                   unmeasured=(reason != NO_AUDIO))
        return
    ok = actual <= limit + EPSILON
    verdict = "within the limit" if ok else "over the limit"
    report.add(check_id, PASS if ok else FAIL, limit, jsonable(actual), "%s %s" % (what, verdict))


def add_lufs_check(report, values, primary, reason):
    target, tol = values["lufs_target"], values["lufs_tol"]
    expected = {"target": target, "tol": tol}
    if reason:
        report.add("lufs_target", SKIP, expected, None, reason, unmeasured=(reason != NO_AUDIO))
    elif not loudness_measurable(primary):
        report.add("lufs_target", SKIP, expected, None,
                   "integrated loudness is not defined for audio shorter than 0.4 s, so the file is too short "
                   "for this check", unmeasured=True)
    elif primary["integrated_lufs"] is None:
        report.add("lufs_target", SKIP, expected, None,
                   "ffmpeg's loudness filter did not report an integrated loudness for this stream", unmeasured=True)
    else:
        lufs = primary["integrated_lufs"]
        ok = abs(lufs - target) <= tol + EPSILON
        report.add("lufs_target", PASS if ok else FAIL, expected, lufs,
                   "measured %.1f LUFS, %.1f LU from the target" % (lufs, abs(lufs - target)))


def add_clipping_check(report, primary, reason):
    expected = "true peak below %.1f dBTP" % CLIP_DBTP
    peak = None if reason else primary["true_peak_dbtp"]
    if reason or peak is None:
        report.add("no_clipping", SKIP, expected, None, reason or "true peak was not reported",
                   unmeasured=(reason != NO_AUDIO))
        return
    clipped = peak >= CLIP_DBTP
    report.add("no_clipping", FAIL if clipped else PASS, expected, jsonable(peak),
               "true peak %.1f dBTP" % peak + (": at or above full scale, the audio clips" if clipped else ""))


# Checks of the form "measured value must not exceed the limit": (spec key, measurement field, label).
LIMIT_CHECKS = (
    ("true_peak_max_db", "true_peak_dbtp", "true peak"),
    ("max_leading_silence_s", "leading_silence_s", "leading silence"),
    ("max_trailing_silence_s", "trailing_silence_s", "trailing silence"),
    ("max_silence_s", "longest_internal_silence_s", "longest silent gap"),
)


def add_spec_checks(report, values, measurements):
    primary = measurements[0] if measurements else None
    reason = unusable_reason(primary)
    if len(measurements) > 1 and reason is None:
        report.info["spec_checks_use"] = "audio stream 0 of %d" % len(measurements)
    if "lufs_target" in values:
        add_lufs_check(report, values, primary, reason)
    for key, field, label in LIMIT_CHECKS:
        if key in values:
            actual = None if reason else primary[field]
            add_limit_check(report, key, values[key], actual, reason, label)
    if values.get("no_clipping"):
        add_clipping_check(report, primary, reason)


# --------------------------------------------------------------------------- spec reading

def read_spec(spec):
    """Validate the qa_audio keys up front (a bad spec is exit code 2, before any decoding)."""
    values = {}
    if "lufs_target" in spec:
        values["lufs_target"] = qa_probe.spec_number(spec["lufs_target"], "lufs_target", -math.inf)
    values["lufs_tol"] = qa_probe.spec_number(spec.get("lufs_tol", DEFAULT_LUFS_TOL), "lufs_tol")
    if "true_peak_max_db" in spec:
        values["true_peak_max_db"] = qa_probe.spec_number(spec["true_peak_max_db"], "true_peak_max_db", -math.inf)
    for key in ("max_leading_silence_s", "max_trailing_silence_s", "max_silence_s"):
        if key in spec:
            values[key] = qa_probe.spec_number(spec[key], key)
    if "no_clipping" in spec:
        values["no_clipping"] = qa_probe.spec_bool(spec["no_clipping"], "no_clipping")
    return values


def add_unknown_key_warnings(report, spec):
    for key in spec:
        if key in SPEC_KEYS or key in SIBLING_SPEC_KEYS:
            continue
        close = difflib.get_close_matches(key, SPEC_KEYS, n=1)
        hint = 'not a documented key, so it was ignored' + (' (did you mean "%s"?)' % close[0] if close else "")
        report.add("unknown_spec_key", WARN, "a documented key", key, hint)


# --------------------------------------------------------------------------- orchestration

def build_info(measurements):
    """Top-level values come from the first audio stream; every stream is listed in full."""
    primary = measurements[0] if measurements else None
    info = {
        "audio_stream_count": len(measurements),
        "filters": FILTER_CHAIN,
        "audio_streams": jsonable(measurements),
    }
    for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp", "max_volume_db", "mean_volume_db",
                "silence_segments", "leading_silence_s", "trailing_silence_s", "longest_internal_silence_s",
                "audio_duration_s"):
        info[key] = jsonable(primary[key]) if primary else None
    return info


def analyze(path, spec, tools, spec_sha256=None):
    """Measure every audio stream of one file and return a common.Report (used by main and run_qa)."""
    report = common.Report(SCRIPT_NAME, file=path, tools=tools)
    report.spec_sha256 = spec_sha256
    values = read_spec(spec)
    abs_path = os.path.abspath(path)  # absolute, so a name starting with "-" is never read as an option
    streams = list_audio_streams(tools["ffprobe"], abs_path, report)
    measurements = [
        measure_stream(tools["ffmpeg"], abs_path, position, facts, report)
        for position, facts in enumerate(streams)
    ]
    report.info = build_info(measurements)
    add_not_silent_check(report, measurements)
    add_unknown_key_warnings(report, spec)
    add_spec_checks(report, values, measurements)
    return report


def find_tools(args):
    return {
        "ffmpeg": common.find_tool("ffmpeg", args.ffmpeg),
        "ffprobe": common.find_tool("ffprobe", args.ffprobe),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Measure loudness and silence of a file's audio and catch silent exports.",
    )
    parser.add_argument("file", help="video or audio file to measure")
    parser.add_argument("--spec", default=None, help="JSON file with expectations (see SKILL.md)")
    common.add_common_args(parser)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.file):
        raise common.UsageError("input file not found: %s" % args.file)
    spec, spec_sha = common.load_spec(args.spec)
    report = analyze(args.file, spec, find_tools(args), spec_sha)
    qa_probe.make_stdout_safe()
    return common.emit(report, args.json)


if __name__ == "__main__":
    sys.exit(common.run_main(main))
