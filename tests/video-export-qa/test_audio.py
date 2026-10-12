# Tests for qa_audio.py. Real ffmpeg and ffprobe, no mocks for media logic.
# All media is generated here with ffmpeg lavfi sources under the scratch folder.
# The parser tests use short pieces of real ffmpeg output as canned text.
# Run:  python -B -m unittest discover -s tests/video-export-qa
import functools
import hashlib
import json
import os
import math
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
WORK = ROOT / "_work" / "video-export-qa" / "probe-audio" / "media-audio"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import qa_audio  # noqa: E402
import qa_probe  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
TOOLS = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}

PICTURE = "testsrc2=size=320x180:rate=24:duration=4"
# sine defaults to amplitude 0.125, so volume=0.8 gives exactly 0.1 = -20 dBFS peak.
TONE_20 = "sine=frequency=1000:sample_rate=48000:duration=%s,volume=0.8"
STEREO = ",pan=stereo|c0=c0|c1=c0"
# Analytic loudness of a 1 kHz sine: peak -20 dBFS is -23.01 LUFS in mono, -20.00 LUFS with the
# same signal on two channels (BS.1770 sums channel powers; the K filter is ~0 dB at 1 kHz).
LUFS_MONO_20 = -20.0 - 20 * math.log10(math.sqrt(2))
LUFS_STEREO_20 = LUFS_MONO_20 + 10 * math.log10(2)

M = {}


# --------------------------------------------------------------------------- helpers

def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def add(name, filename):
    M[name] = WORK / filename
    return str(M[name])


@functools.lru_cache(maxsize=None)
def _measure(path, spec_json):
    report = qa_audio.analyze(path, json.loads(spec_json), TOOLS)
    # Generated, readable fixtures must reach measurement. A product filter failure
    # must fail an assertion here, rather than cause later arithmetic on None.
    for stream in report.info["audio_streams"]:
        if stream["error"] is not None:
            raise AssertionError("product could not measure fixture %s: %s" %
                                 (Path(path).name, stream["error"]))
    return report


def measure(name, spec=None):
    return _measure(str(M[name]), json.dumps(spec or {}, sort_keys=True))


def find(report, check_id):
    matches = [c for c in report.checks if c["id"] == check_id]
    if not matches:
        raise AssertionError("no check %r in %s" % (check_id, [c["id"] for c in report.checks]))
    return matches[0]


def status(report, check_id):
    return find(report, check_id)["status"]


def silence(duration):
    return "anullsrc=r=48000:cl=mono,atrim=duration=%s" % duration


def gaps_graph():
    """1 s silence, 1 s tone, 1 s silence, 1 s tone, 1.5 s silence (mono, -20 dBFS tone).
    Leading 0-1, internal 2-3, trailing 4-5.5: the edges differ so a mix-up cannot hide."""
    parts = [silence(1), TONE_20 % 1, silence(1), TONE_20 % 1, silence(1.5)]
    return parts


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "dir with spaces").mkdir(exist_ok=True)
    make_tone_fixtures()
    make_silence_fixtures()
    make_level_fixtures()
    make_stream_fixtures()
    make_hostile_and_special_fixtures()


def make_tone_fixtures():
    ffmpeg("-f", "lavfi", "-i", TONE_20 % 3 + STEREO, "-c:a", "pcm_s16le", add("tone_stereo_wav", "tone_stereo.wav"))
    ffmpeg("-f", "lavfi", "-i", TONE_20 % 3 + STEREO, "-c:a", "aac", add("tone_stereo_aac", "tone_stereo.m4a"))
    ffmpeg("-f", "lavfi", "-i", TONE_20 % 3, "-c:a", "pcm_s16le", add("tone_mono_wav", "tone_mono.wav"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE_20 % 4 + STEREO, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", add("video_tone", "video_tone.mp4"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", add("no_audio", "no_audio.mp4"))
    shutil.copy(str(M["video_tone"]), str(WORK / "dir with spaces" / "my clip (final).mp4"))
    add("spaces", "dir with spaces/my clip (final).mp4")
    shutil.copy(str(M["video_tone"]), str(WORK / "cl\xefp \u6e2c\u8a66.mp4"))
    add("unicode_name", "cl\xefp \u6e2c\u8a66.mp4")
    ffmpeg("-f", "lavfi", "-i", TONE_20 % 0.3, "-c:a", "pcm_s16le", add("short_tone", "short_tone.wav"))


def make_silence_fixtures():
    # The silent-export trap: real AAC streams that decode to nothing but zeros.
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", "-c:a", "aac", add("zero_aac", "zero.m4a"))
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", "-c:a", "pcm_s16le", add("zero_wav", "zero.wav"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", add("silent_video", "silent_video.mp4"))
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.3", "-c:a", "pcm_s16le", add("short_zero", "short_zero.wav"))
    # Zero samples at all: a WAV that has an audio stream and no data.
    ffmpeg("-f", "lavfi", "-i", "sine=duration=1", "-af", "atrim=end_sample=0", "-c:a", "pcm_s16le", add("empty", "empty.wav"))
    # Very quiet noise (about -85 dBFS): not digital silence, but below the -70 LUFS floor.
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=d=3:c=white:r=48000:a=0.0001", "-c:a", "pcm_s16le", add("quiet_noise", "quiet_noise.wav"))
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=d=3:c=white:r=48000:a=0.01", "-c:a", "pcm_s16le", add("soft_noise", "soft_noise.wav"))
    # Gaps: leading 0-1, internal 2-3, trailing 4-5.5.
    graph = "".join("[%d]" % i for i in range(5)) + "concat=n=5:v=0:a=1"
    inputs = []
    for part in gaps_graph():
        inputs += ["-f", "lavfi", "-i", part]
    ffmpeg(*inputs, "-filter_complex", graph, "-c:a", "pcm_s16le", add("gaps_wav", "gaps.wav"))
    ffmpeg(*inputs, "-filter_complex", graph, "-c:a", "aac", add("gaps_aac", "gaps.m4a"))
    # A 0.3 s gap is shorter than the 0.5 s silencedetect window.
    short_gap = [TONE_20 % 1, silence(0.3), TONE_20 % 1]
    short_inputs = []
    for part in short_gap:
        short_inputs += ["-f", "lavfi", "-i", part]
    ffmpeg(*short_inputs, "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "pcm_s16le", add("short_gap", "short_gap.wav"))
    # Audio that starts 0.5 s late in the container, and begins with 1 s of silence.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-itsoffset", "0.5", "-f", "lavfi", "-i", silence(1), "-f", "lavfi", "-i",
           TONE_20 % 3, "-filter_complex", "[1][2]concat=n=2:v=0:a=1[a]", "-map", "0:v", "-map", "[a]", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", add("late_start", "late_start.mp4"))
    # MPEG-TS has a 1.4 s start offset: 2 s of tone then 2 s of silence.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE_20 % 2, "-f", "lavfi", "-i", silence(2),
           "-filter_complex", "[1][2]concat=n=2:v=0:a=1[a]", "-map", "0:v", "-map", "[a]", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", add("ts_tail", "ts_tail.ts"))


def make_level_fixtures():
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2,volume=8", "-c:a", "pcm_s16le",
           add("full_scale", "full_scale.wav"))
    # -1 dBFS: volume 8 * 10^(-1/20) = 7.1328 ; -0.05 dBFS: 8 * 10^(-0.05/20) = 7.9540
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2,volume=7.1328", "-c:a", "pcm_s16le",
           add("peak_minus_1", "peak_minus_1.wav"))
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2,volume=7.9540", "-c:a", "pcm_s16le",
           add("peak_minus_005", "peak_minus_005.wav"))


SPOOF_COMMENT = "silence_start: 0 silence_end: 0.1"
# Text that imitates every measurement qa_audio reads, hidden in a tag.
FORGED_VOLUME_COMMENT = "n_samples: 99 mean_volume: -10.0 dB max_volume: -3.0 dB Summary: I: -23.0 LUFS Peak: -3.0 dBFS"


def make_hostile_and_special_fixtures():
    # 3 s of silence then 2 s of tone; the "spoof" copy differs only in a comment tag that imitates
    # a silencedetect line (the copy is a stream copy, so the audio is bit-identical).
    parts = [silence(3), TONE_20 % 2]
    inputs = []
    for part in parts:
        inputs += ["-f", "lavfi", "-i", part]
    ffmpeg(*inputs, "-filter_complex", "[0][1]concat=n=2:v=0:a=1", "-c:a", "aac", add("lead3_plain", "lead3_plain.m4a"))
    ffmpeg("-i", M["lead3_plain"], "-c", "copy", "-metadata", "comment=" + SPOOF_COMMENT, add("lead3_spoof", "lead3_spoof.m4a"))
    # A silent file whose tags claim a healthy measurement.
    ffmpeg("-i", M["zero_aac"], "-c", "copy", "-metadata", "comment=" + FORGED_VOLUME_COMMENT, "-metadata", "title=Summary:",
           add("zero_forged", "zero_forged.m4a"))
    # A 60 Hz tone at -20 dBFS peak in the LFE channel only, and the same tone in the centre channel.
    low = "sine=frequency=60:sample_rate=48000:duration=3,volume=0.8"
    ffmpeg("-f", "lavfi", "-i", low, "-af", "pan=5.1|LFE=c0", "-c:a", "pcm_s16le", add("lfe_only", "lfe_only.wav"))
    ffmpeg("-f", "lavfi", "-i", low, "-af", "pan=5.1|FC=c0", "-c:a", "pcm_s16le", add("centre_only", "centre_only.wav"))
    # A 12 kHz sine whose samples never land on the waveform peaks: the sample peak is well below the true peak.
    ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.85*sin(2*PI*12000*t+PI/4):s=48000:d=2", "-c:a", "pcm_s16le", add("isp", "isp.wav"))


def make_stream_fixtures():
    zeros = ["-t", "4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    tone = ["-f", "lavfi", "-i", TONE_20 % 4]
    video = ["-f", "lavfi", "-i", PICTURE]
    out = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "4"]
    ffmpeg(*video, *tone, *zeros, "-map", "0:v", "-map", "1:a", "-map", "2:a", *out, add("good_then_silent", "good_then_silent.mp4"))
    ffmpeg(*video, *zeros, *tone, "-map", "0:v", "-map", "1:a", "-map", "2:a", *out, add("silent_then_good", "silent_then_good.mp4"))
    ffmpeg(*video, *tone, "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=4,volume=0.8", "-map", "0:v",
           "-map", "1:a", "-map", "2:a", *out, add("two_good", "two_good.mp4"))


# --------------------------------------------------------------------------- canned ffmpeg text

TONE_LOG = """\
[Parsed_volumedetect_1 @ 000001bb92203380] n_samples: 0
Stream mapping:
  Stream #0:0 -> #0:0 (aac (native) -> pcm_s16le (native))
[Parsed_ebur128_0 @ 000001bb92203380] Summary:

  Integrated loudness:
    I:         -41.1 LUFS
    Threshold: -51.1 LUFS

  Loudness range:
    LRA:        20.0 LU
    Threshold: -61.1 LUFS
    LRA low:   -61.1 LUFS
    LRA high:  -41.1 LUFS

  True peak:
    Peak:      -37.5 dBFS
[Parsed_volumedetect_1 @ 000001bb92202f40] n_samples: 144384
[Parsed_volumedetect_1 @ 000001bb92202f40] mean_volume: -41.1 dB
[Parsed_volumedetect_1 @ 000001bb92202f40] max_volume: -37.5 dB
[Parsed_volumedetect_1 @ 000001bb92202f40] histogram_37db: 201
"""

ZERO_LOG = """\
[Parsed_volumedetect_1 @ 0000021c05c44500] n_samples: 0
[Parsed_silencedetect_2 @ 0000021c05c44b80] silence_start: 0
[Parsed_ebur128_0 @ 0000021c05c44940] Summary:

  Integrated loudness:
    I:         -70.0 LUFS
    Threshold:   0.0 LUFS

  Loudness range:
    LRA:         0.0 LU
    Threshold:   0.0 LUFS
    LRA low:     0.0 LUFS
    LRA high:    0.0 LUFS

  True peak:
    Peak:       -inf dBFS
[Parsed_silencedetect_2 @ 0000021c05c44b80] silence_end: 3.008 | silence_duration: 3.008
[Parsed_volumedetect_1 @ 0000021c05c43dc0] n_samples: 288768
[Parsed_volumedetect_1 @ 0000021c05c43dc0] mean_volume: -91.0 dB
[Parsed_volumedetect_1 @ 0000021c05c43dc0] max_volume: -91.0 dB
[Parsed_volumedetect_1 @ 0000021c05c43dc0] histogram_91db: 288768
"""

EMPTY_LOG = """\
[Parsed_volumedetect_1 @ 0000025531143bc0] n_samples: 0
[Parsed_ebur128_0 @ 0000025531143000] Summary:

  Integrated loudness:
    I:         -70.0 LUFS
    Threshold:   0.0 LUFS

  Loudness range:
    LRA:         0.0 LU

  True peak:
    Peak:        0.0 dBFS
[Parsed_volumedetect_1 @ 0000025531143000] n_samples: 0
"""


# The log prefix ffmpeg puts in front of a filter's own lines (the hex number is a memory address).
SD = "[Parsed_silencedetect_3 @ 0000019b48224a80] "
VD = "[Parsed_volumedetect_2 @ 0000019b48225640] "


def measurement(**changes):
    """A measurement dict with typical values for a healthy 3 s stereo stream."""
    base = qa_audio.empty_measurement(0, {"index": 1, "codec_name": "aac", "sample_rate": "48000", "channels": 2})
    base.update(samples_decoded=288000, audio_duration_s=3.0, integrated_lufs=-20.0, loudness_range_lu=0.0,
                true_peak_dbtp=-19.9, max_volume_db=-20.0, mean_volume_db=-23.0, silence_segments=[],
                leading_silence_s=0.0, trailing_silence_s=0.0, longest_internal_silence_s=0.0)
    base.update(changes)
    return base


# --------------------------------------------------------------------------- parsers

class ParserTests(unittest.TestCase):
    def test_ebur128_summary(self):
        values = qa_audio.parse_ebur128(TONE_LOG)
        self.assertEqual(values, {"integrated_lufs": -41.1, "loudness_range_lu": 20.0, "true_peak_dbtp": -37.5})

    def test_ebur128_minus_infinity_peak(self):
        values = qa_audio.parse_ebur128(ZERO_LOG)
        self.assertEqual(values["integrated_lufs"], -70.0)
        self.assertEqual(values["true_peak_dbtp"], -math.inf)

    def test_ebur128_uses_the_last_summary_and_ignores_frame_log_lines(self):
        text = ("[Parsed_ebur128_0 @ 1] t: 0.0999 TARGET:-23 LUFS    M: -120.7 S: -120.7     I: -70.0 LUFS       LRA:   0.0 LU\n"
                + EMPTY_LOG + "\n" + TONE_LOG)
        self.assertEqual(qa_audio.parse_ebur128(text)["integrated_lufs"], -41.1)

    def test_ebur128_without_summary_gives_nothing(self):
        self.assertEqual(qa_audio.parse_ebur128("no filter output here"),
                         {"integrated_lufs": None, "loudness_range_lu": None, "true_peak_dbtp": None})

    def test_volumedetect_takes_the_real_report_not_the_early_zero_line(self):
        self.assertEqual(qa_audio.parse_volumedetect(TONE_LOG),
                         {"n_samples": 144384, "mean_volume_db": -41.1, "max_volume_db": -37.5})

    def test_volumedetect_digital_silence_reads_minus_91(self):
        values = qa_audio.parse_volumedetect(ZERO_LOG)
        self.assertEqual((values["n_samples"], values["mean_volume_db"]), (288768, -91.0))

    def test_volumedetect_with_nothing_decoded(self):
        values = qa_audio.parse_volumedetect(EMPTY_LOG)
        self.assertEqual(values, {"n_samples": 0, "mean_volume_db": None, "max_volume_db": None})

    def test_volumedetect_with_no_output_at_all(self):
        self.assertEqual(qa_audio.parse_volumedetect(""), {"n_samples": None, "mean_volume_db": None, "max_volume_db": None})

    def test_volumedetect_accepts_minus_infinity(self):
        text = (VD + "n_samples: 100\n" + VD + "mean_volume: -inf dB\n" + VD + "max_volume: -inf dB\n")
        self.assertEqual(qa_audio.parse_volumedetect(text)["mean_volume_db"], -math.inf)

    def test_silence_segments(self):
        text = (SD + "silence_start: 0\n" + SD + "silence_end: 1.000021 | silence_duration: 1.000021\n"
                + SD + "silence_start: 2\n" + SD + "silence_end: 3.000021 | silence_duration: 1.000021\n"
                + SD + "silence_start: 4.000708\n" + SD + "silence_end: 5.013333 | silence_duration: 1.012625\n")
        self.assertEqual(qa_audio.parse_silence_segments(text, 5.0133),
                         [[0.0, 1.000021], [2.0, 3.000021], [4.000708, 5.013333]])

    def test_the_older_unnumbered_filter_prefix_is_read_too(self):
        text = "[silencedetect @ 0x55d0c8a1b2c0] silence_start: 1.5\n[silencedetect @ 0x55d0c8a1b2c0] silence_end: 2.5 | silence_duration: 1\n"
        self.assertEqual(qa_audio.parse_silence_segments(text, 5.0), [[1.5, 2.5]])

    def test_open_silence_is_closed_at_the_audio_end(self):
        self.assertEqual(qa_audio.parse_silence_segments(SD + "silence_start: 4.5\n", 5.0), [[4.5, 5.0]])
        # Negative control: with no duration to close it with, the unfinished silence is dropped.
        self.assertEqual(qa_audio.parse_silence_segments(SD + "silence_start: 4.5\n", None), [])

    def test_negative_times_are_clamped_to_zero(self):
        text = SD + "silence_start: -0.021\n" + SD + "silence_end: 1.0\n"
        self.assertEqual(qa_audio.parse_silence_segments(text, 3.0), [[0.0, 1.0]])

    def test_text_that_only_looks_like_a_filter_line_is_ignored(self):
        # A file can carry any text in its tags, and ffmpeg prints tags on stderr in the metadata dump.
        # Only a line that BEGINS with the filter's own log prefix is a measurement.
        forged = ("  Metadata:\n    comment         : silence_start: 0 silence_end: 0.1\n"
                  "                  : silence_start: 0\n"
                  "    title           : [Parsed_silencedetect_9 @ 1] silence_start: 0\n")
        self.assertEqual(qa_audio.parse_silence_segments(forged, 5.0), [])
        # Negative control: the same words on a real filter line are read.
        self.assertEqual(qa_audio.parse_silence_segments(forged + SD + "silence_start: 4\n", 5.0), [[4.0, 5.0]])
        self.assertEqual(qa_audio.parse_silence_segments("silence_start: 0\nsilence_end: 1\n", 5.0), [], "no prefix, no measurement")

    def test_forged_volume_and_loudness_lines_are_ignored(self):
        forged = ("    comment         : n_samples: 99 mean_volume: -3.0 dB max_volume: -1.0 dB\n"
                  "    title           : Summary:\n"
                  "                      I:         -23.0 LUFS\n"
                  "    other           : Peak:       -1.0 dBFS\n")
        self.assertEqual(qa_audio.parse_volumedetect(forged), {"n_samples": None, "mean_volume_db": None, "max_volume_db": None})
        self.assertEqual(qa_audio.parse_ebur128(forged),
                         {"integrated_lufs": None, "loudness_range_lu": None, "true_peak_dbtp": None})
        # The real report that follows the forged text is still the one that is read.
        self.assertEqual(qa_audio.parse_ebur128(forged + TONE_LOG)["integrated_lufs"], -41.1)
        self.assertEqual(qa_audio.parse_volumedetect(forged + TONE_LOG)["mean_volume_db"], -41.1)

    def test_no_silence_lines_means_no_segments(self):
        self.assertEqual(qa_audio.parse_silence_segments(TONE_LOG, 3.0), [])

    def test_unreadable_numbers_are_none(self):
        for token in ("abc", "nan", "", None):
            self.assertIsNone(qa_audio.parse_number(token))
        self.assertEqual(qa_audio.parse_number("-0.0"), 0.0)

    def test_jsonable_turns_infinity_into_strings(self):
        self.assertEqual(qa_audio.jsonable({"a": -math.inf, "b": [math.inf, math.nan, 1.5], "c": "x"}),
                         {"a": "-inf", "b": ["inf", None, 1.5], "c": "x"})
        json.dumps(qa_audio.jsonable({"a": -math.inf}), allow_nan=False)  # strict JSON accepts it


class SilenceSummaryTests(unittest.TestCase):
    def test_leading_internal_trailing(self):
        leading, trailing, internal = qa_audio.summarise_silence([[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]], 5.0)
        self.assertEqual((leading, trailing, internal), (1.0, 1.0, [[2.0, 3.0]]))

    def test_leading_only(self):
        self.assertEqual(qa_audio.summarise_silence([[0.0, 1.5]], 5.0), (1.5, 0.0, []))

    def test_trailing_only(self):
        self.assertEqual(qa_audio.summarise_silence([[3.0, 5.0]], 5.0), (0.0, 2.0, []))

    def test_internal_only(self):
        self.assertEqual(qa_audio.summarise_silence([[1.0, 2.0]], 5.0), (0.0, 0.0, [[1.0, 2.0]]))

    def test_no_silence(self):
        self.assertEqual(qa_audio.summarise_silence([], 5.0), (0.0, 0.0, []))

    def test_a_run_near_but_not_at_the_edge_is_internal(self):
        leading, trailing, internal = qa_audio.summarise_silence([[0.2, 1.2], [3.7, 4.7]], 5.0)
        self.assertEqual((leading, trailing, len(internal)), (0.0, 0.0, 2))

    def test_edge_tolerance_absorbs_codec_padding(self):
        leading, trailing, _ = qa_audio.summarise_silence([[0.021, 1.0], [4.0, 5.0]], 5.013)
        self.assertEqual((leading, trailing), (round(1.0 - 0.021, 6), 1.0))

    def test_fully_silent_file_counts_as_everything(self):
        leading, trailing, internal = qa_audio.summarise_silence([[0.0, 3.008]], 3.008)
        self.assertEqual((leading, trailing, internal), (3.008, 3.008, [[0.0, 3.008]]))

    def test_unknown_duration_never_reports_trailing(self):
        self.assertEqual(qa_audio.summarise_silence([[4.0, 5.0]], None)[1], 0.0)


# --------------------------------------------------------------------------- the silence judgement

class JudgeSilenceTests(unittest.TestCase):
    def test_healthy_audio_passes(self):
        self.assertEqual(qa_audio.judge_silence(measurement())[0], "PASS")

    def test_digital_silence_fails(self):
        for mean in (-91.0, -90.0, -math.inf):
            with self.subTest(mean):
                status_, _actual, detail = qa_audio.judge_silence(measurement(mean_volume_db=mean, integrated_lufs=-70.0))
                self.assertEqual(status_, "FAIL")
                self.assertIn("silent", detail)

    def test_just_above_the_digital_silence_floor_is_not_digital_silence(self):
        self.assertEqual(qa_audio.judge_silence(measurement(mean_volume_db=-89.9, integrated_lufs=-45.0))[0], "PASS")

    def test_loudness_floor_fails_for_audio_long_enough_to_measure(self):
        status_, _a, detail = qa_audio.judge_silence(measurement(mean_volume_db=-80.0, integrated_lufs=-70.0))
        self.assertEqual(status_, "FAIL")
        self.assertIn("measurement floor", detail)

    def test_loudness_floor_means_nothing_for_audio_under_400_ms(self):
        short = measurement(mean_volume_db=-23.0, integrated_lufs=-70.0, audio_duration_s=0.3)
        self.assertEqual(qa_audio.judge_silence(short)[0], "PASS")
        # Negative control: the same short audio is silent if its mean volume says so.
        self.assertEqual(qa_audio.judge_silence(measurement(mean_volume_db=-91.0, integrated_lufs=-70.0, audio_duration_s=0.3))[0], "FAIL")

    def test_zero_samples_is_skip_never_pass(self):
        for samples in (0, None):
            with self.subTest(samples):
                self.assertEqual(qa_audio.judge_silence(measurement(samples_decoded=samples))[0], "SKIP")

    def test_missing_mean_volume_is_skip(self):
        self.assertEqual(qa_audio.judge_silence(measurement(mean_volume_db=None))[0], "SKIP")

    def test_ffmpeg_error_is_skip_with_the_reason(self):
        status_, _a, detail = qa_audio.judge_silence(measurement(error="Invalid data found"))
        self.assertEqual(status_, "SKIP")
        self.assertIn("Invalid data found", detail)

    def test_missing_loudness_is_not_a_failure_when_the_mean_is_fine(self):
        self.assertEqual(qa_audio.judge_silence(measurement(integrated_lufs=None))[0], "PASS")

    def test_combine_statuses(self):
        self.assertEqual(qa_audio.combine_statuses(["PASS", "PASS"]), "PASS")
        self.assertEqual(qa_audio.combine_statuses(["PASS", "FAIL"]), "FAIL")
        self.assertEqual(qa_audio.combine_statuses(["SKIP", "FAIL"]), "FAIL")
        self.assertEqual(qa_audio.combine_statuses(["PASS", "SKIP"]), "WARN")
        self.assertEqual(qa_audio.combine_statuses(["SKIP", "SKIP"]), "SKIP")


# --------------------------------------------------------------------------- the silent export trap

class SilentExportTests(unittest.TestCase):
    def test_a_normal_tone_passes(self):
        report = measure("tone_stereo_aac")
        self.assertEqual(status(report, "not_silent"), "PASS")
        self.assertEqual(report.overall(), "PASS")

    def test_an_aac_track_of_all_zero_samples_fails(self):
        report = measure("zero_aac")
        check = find(report, "not_silent")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("present but silent", check["detail"])
        self.assertEqual(report.overall(), "FAIL")
        self.assertEqual(report.exit_code(), 1)

    def test_silent_audio_in_a_video_export_fails(self):
        report = measure("silent_video")
        self.assertEqual(status(report, "not_silent"), "FAIL")

    def test_the_audio_stream_does_exist_which_is_why_existence_proves_nothing(self):
        probe_report = qa_probe.analyze(str(M["silent_video"]), {"audio": {"present": True, "codec": "aac"}}, TOOLS)
        self.assertEqual([c["status"] for c in probe_report.checks if c["id"].startswith("audio.")], ["PASS", "PASS"])
        self.assertEqual(status(measure("silent_video"), "not_silent"), "FAIL")

    def test_zero_valued_wav_fails_too(self):
        self.assertEqual(status(measure("zero_wav"), "not_silent"), "FAIL")

    def test_digital_silence_reads_minus_91_db_on_this_build(self):
        info = measure("zero_aac").info
        self.assertEqual(info["mean_volume_db"], -91.0)
        self.assertEqual(info["integrated_lufs"], -70.0)
        self.assertEqual(info["true_peak_dbtp"], "-inf")

    def test_very_quiet_noise_below_the_loudness_floor_fails(self):
        report = measure("quiet_noise")
        self.assertGreater(report.info["mean_volume_db"], qa_audio.DIGITAL_SILENCE_DB, "not digital silence")
        check = find(report, "not_silent")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("measurement floor", check["detail"])

    def test_soft_but_audible_noise_passes(self):
        # Negative control for the line above: amplitude 0.01 is about -45 dBFS peak, well above the floor.
        report = measure("soft_noise")
        self.assertGreater(report.info["integrated_lufs"], -70.0)
        self.assertEqual(status(report, "not_silent"), "PASS")

    def test_a_short_tone_is_not_called_silent(self):
        report = measure("short_tone")
        self.assertEqual(report.info["integrated_lufs"], -70.0, "ebur128 cannot measure under 400 ms")
        self.assertEqual(status(report, "not_silent"), "PASS")

    def test_a_short_run_of_zeros_is_still_silent(self):
        self.assertEqual(status(measure("short_zero"), "not_silent"), "FAIL")

    def test_a_stream_that_decodes_to_nothing_is_skip_not_pass(self):
        report = measure("empty", {"lufs_target": -23, "true_peak_max_db": -1, "max_leading_silence_s": 1,
                                   "max_trailing_silence_s": 1, "max_silence_s": 1, "no_clipping": True})
        self.assertEqual(report.info["audio_stream_count"], 1)
        statuses = {c["id"]: c["status"] for c in report.checks}
        self.assertEqual(set(statuses.values()), {"SKIP"}, statuses)
        self.assertEqual(len(statuses), 7)
        self.assertIn("no audio samples", find(report, "not_silent")["detail"])
        self.assertIsNone(report.info["true_peak_dbtp"], "ebur128 invents a 0 dB peak for empty input; it must not leak")

    def test_a_measurement_failure_is_skip_with_the_reason(self):
        facts = {"index": 1, "codec_name": "aac", "sample_rate": "48000", "channels": 2}
        report = common.Report("qa_audio.py", file=str(M["tone_stereo_aac"]), tools=TOOLS)
        broken = qa_audio.measure_stream(FFMPEG, str(M["tone_stereo_aac"]), 5, facts, report)  # stream a:5 does not exist
        self.assertIsNotNone(broken["error"])
        self.assertEqual(qa_audio.judge_silence(broken)[0], "SKIP")


class NoAudioTests(unittest.TestCase):
    def test_every_check_is_skip_with_the_reason_and_it_is_not_an_error(self):
        report = measure("no_audio", {"lufs_target": -23, "true_peak_max_db": -1, "max_leading_silence_s": 1,
                                      "max_trailing_silence_s": 1, "max_silence_s": 1, "no_clipping": True})
        for check in report.checks:
            self.assertEqual(check["status"], "SKIP", check["id"])
            self.assertEqual(check["detail"], "no audio stream")
        self.assertEqual(len(report.checks), 7)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(report.info["audio_stream_count"], 0)
        self.assertIsNone(report.info["integrated_lufs"])

    def test_when_nothing_was_measured_the_overall_result_is_skip_never_pass(self):
        # Hard rule: nothing is printed as PASS that was not measured. All checks SKIP means no PASS.
        report = measure("no_audio")
        self.assertEqual(report.overall(), "SKIP")
        self.assertEqual(report.to_dict()["overall"], "SKIP")
        self.assertEqual(report.exit_code(), 0, "nothing failed, so the exit code stays 0")
        text = report.table()
        self.assertIn("qa_audio.py: SKIP", text.splitlines()[0])
        self.assertIn("Nothing was measured", text)
        self.assertIn("Not measured (SKIP, proves nothing): not_silent", text)

    def test_negative_control_one_real_measurement_makes_the_overall_result_a_pass_again(self):
        self.assertEqual(measure("tone_stereo_wav").overall(), "PASS")
        self.assertNotIn("Nothing was measured", measure("tone_stereo_wav").table())

    def test_a_stream_that_decodes_to_nothing_is_skip_overall_too(self):
        self.assertEqual(measure("empty").overall(), "SKIP")


# --------------------------------------------------------------------------- loudness, peak

class LoudnessTests(unittest.TestCase):
    def test_stereo_tone_matches_the_analytic_loudness_within_one_lu(self):
        self.assertAlmostEqual(LUFS_STEREO_20, -20.0, places=2)
        for name in ("tone_stereo_wav", "tone_stereo_aac", "video_tone"):
            with self.subTest(name):
                self.assertAlmostEqual(measure(name).info["integrated_lufs"], LUFS_STEREO_20, delta=1.0)

    def test_the_lossless_file_is_much_closer_than_one_lu(self):
        self.assertAlmostEqual(measure("tone_stereo_wav").info["integrated_lufs"], LUFS_STEREO_20, delta=0.2)

    def test_mono_tone_is_3_lu_quieter_than_the_same_tone_on_two_channels(self):
        mono = measure("tone_mono_wav").info["integrated_lufs"]
        self.assertAlmostEqual(mono, LUFS_MONO_20, delta=0.2)
        self.assertAlmostEqual(measure("tone_stereo_wav").info["integrated_lufs"] - mono, 3.01, delta=0.2)

    def test_true_peak_and_volume_values(self):
        info = measure("tone_stereo_wav").info
        self.assertAlmostEqual(info["true_peak_dbtp"], -20.0, delta=0.3)
        self.assertAlmostEqual(info["max_volume_db"], -20.0, delta=0.3)
        self.assertAlmostEqual(info["mean_volume_db"], -23.0, delta=0.3)

    def test_audio_duration_is_the_decoded_length(self):
        self.assertAlmostEqual(measure("tone_stereo_wav").info["audio_duration_s"], 3.0, delta=0.001)
        # AAC decodes with encoder padding: slightly longer than the 3.000 s the container reports.
        self.assertAlmostEqual(measure("tone_stereo_aac").info["audio_duration_s"], 3.0, delta=0.03)

    def test_lufs_target_passes_inside_the_tolerance_and_fails_outside(self):
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -20}), "lufs_target"), "PASS")
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -23}), "lufs_target"), "FAIL")
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -14}), "lufs_target"), "FAIL")

    def test_lufs_tolerance_defaults_to_one_and_can_be_changed(self):
        self.assertEqual(qa_audio.DEFAULT_LUFS_TOL, 1.0)
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -20.8}), "lufs_target"), "PASS")
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -21.5}), "lufs_target"), "FAIL")
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -20.8, "lufs_tol": 0.2}), "lufs_target"), "FAIL")
        self.assertEqual(status(measure("tone_stereo_wav", {"lufs_target": -22, "lufs_tol": 3}), "lufs_target"), "PASS")

    def test_lufs_target_is_skipped_for_audio_too_short_to_measure(self):
        report = measure("short_tone", {"lufs_target": -23})
        self.assertEqual(status(report, "lufs_target"), "SKIP")


class ClippingTests(unittest.TestCase):
    def test_full_scale_sine_fails_no_clipping(self):
        report = measure("full_scale", {"no_clipping": True})
        self.assertEqual(status(report, "no_clipping"), "FAIL")
        self.assertGreaterEqual(report.info["true_peak_dbtp"], qa_audio.CLIP_DBTP)

    def test_a_peak_just_under_the_threshold_passes(self):
        report = measure("peak_minus_1", {"no_clipping": True})
        self.assertAlmostEqual(report.info["true_peak_dbtp"], -1.0, delta=0.15)
        self.assertEqual(status(report, "no_clipping"), "PASS")

    def test_a_peak_just_over_the_threshold_fails(self):
        report = measure("peak_minus_005", {"no_clipping": True})
        self.assertGreaterEqual(report.info["true_peak_dbtp"], -0.1)
        self.assertEqual(status(report, "no_clipping"), "FAIL")

    def test_quiet_tone_passes_and_no_clipping_false_is_no_check(self):
        self.assertEqual(status(measure("tone_stereo_wav", {"no_clipping": True}), "no_clipping"), "PASS")
        self.assertNotIn("no_clipping", [c["id"] for c in measure("full_scale", {"no_clipping": False}).checks])

    def test_true_peak_limit(self):
        self.assertEqual(status(measure("tone_stereo_wav", {"true_peak_max_db": -1.0}), "true_peak_max_db"), "PASS")
        self.assertEqual(status(measure("tone_stereo_wav", {"true_peak_max_db": -25.0}), "true_peak_max_db"), "FAIL")
        self.assertEqual(status(measure("full_scale", {"true_peak_max_db": -1.0}), "true_peak_max_db"), "FAIL")


# --------------------------------------------------------------------------- silence gaps

class SilenceGapTests(unittest.TestCase):
    def assert_segments(self, segments, expected, delta):
        self.assertEqual(len(segments), len(expected), segments)
        for got, want in zip(segments, expected):
            self.assertAlmostEqual(got[0], want[0], delta=delta)
            self.assertAlmostEqual(got[1], want[1], delta=delta)

    def test_known_gaps_are_found_at_the_right_times_in_wav(self):
        info = measure("gaps_wav").info
        self.assert_segments(info["silence_segments"], [[0, 1], [2, 3], [4, 5.5]], 0.01)
        self.assertAlmostEqual(info["leading_silence_s"], 1.0, delta=0.01)
        self.assertAlmostEqual(info["trailing_silence_s"], 1.5, delta=0.01)
        self.assertAlmostEqual(info["longest_internal_silence_s"], 1.0, delta=0.01)
        self.assertAlmostEqual(info["audio_duration_s"], 5.5, delta=0.001)

    def test_known_gaps_survive_aac_encoding(self):
        info = measure("gaps_aac").info
        self.assert_segments(info["silence_segments"], [[0, 1], [2, 3], [4, 5.51]], 0.06)
        self.assertAlmostEqual(info["leading_silence_s"], 1.0, delta=0.06)
        self.assertAlmostEqual(info["trailing_silence_s"], 1.5, delta=0.06)

    def test_leading_silence_limit(self):
        self.assertEqual(status(measure("gaps_wav", {"max_leading_silence_s": 1.2}), "max_leading_silence_s"), "PASS")
        self.assertEqual(status(measure("gaps_wav", {"max_leading_silence_s": 0.5}), "max_leading_silence_s"), "FAIL")

    def test_trailing_silence_limit(self):
        self.assertEqual(status(measure("gaps_wav", {"max_trailing_silence_s": 2.0}), "max_trailing_silence_s"), "PASS")
        self.assertEqual(status(measure("gaps_wav", {"max_trailing_silence_s": 1.2}), "max_trailing_silence_s"), "FAIL")

    def test_internal_gap_limit_ignores_the_edges(self):
        self.assertEqual(status(measure("gaps_wav", {"max_silence_s": 1.5}), "max_silence_s"), "PASS")
        self.assertEqual(status(measure("gaps_wav", {"max_silence_s": 0.5}), "max_silence_s"), "FAIL")
        # A tone with no gaps passes even a zero limit.
        self.assertEqual(status(measure("tone_stereo_wav", {"max_silence_s": 0.0}), "max_silence_s"), "PASS")

    def test_a_fully_silent_file_fails_every_silence_limit(self):
        report = measure("zero_wav", {"max_leading_silence_s": 1, "max_trailing_silence_s": 1, "max_silence_s": 1})
        for check_id in ("max_leading_silence_s", "max_trailing_silence_s", "max_silence_s"):
            self.assertEqual(status(report, check_id), "FAIL", check_id)

    def test_gaps_shorter_than_the_detection_window_are_not_reported(self):
        # silencedetect only reports silences of 0.5 s or more; a 0.3 s dropout is invisible by design.
        self.assertEqual(measure("short_gap").info["silence_segments"], [])

    def test_a_late_starting_track_does_not_shift_the_silence_times(self):
        # The audio starts 0.5 s into the container and opens with 1 s of silence: the silence is 1 s long,
        # not 1.5 s, because times are measured from the first audio sample.
        info = measure("late_start").info
        self.assertAlmostEqual(info["leading_silence_s"], 1.0, delta=0.06)
        self.assertEqual(status(measure("late_start", {"max_leading_silence_s": 1.1}), "max_leading_silence_s"), "PASS")

    def test_trailing_silence_in_mpegts_despite_its_start_offset(self):
        info = measure("ts_tail").info
        self.assertAlmostEqual(info["trailing_silence_s"], 2.0, delta=0.08)
        self.assertAlmostEqual(info["leading_silence_s"], 0.0, delta=0.001)
        self.assertEqual(status(measure("ts_tail", {"max_trailing_silence_s": 2.5}), "max_trailing_silence_s"), "PASS")
        self.assertEqual(status(measure("ts_tail", {"max_trailing_silence_s": 1.0}), "max_trailing_silence_s"), "FAIL")


# --------------------------------------------------------------------------- several audio streams

class MultiStreamTests(unittest.TestCase):
    def test_every_stream_is_measured(self):
        report = measure("two_good")
        self.assertEqual(report.info["audio_stream_count"], 2)
        self.assertEqual([s["sample_rate"] for s in report.info["audio_streams"]], [48000, 44100])
        self.assertEqual(status(report, "not_silent"), "PASS")

    def test_a_silent_second_stream_fails_the_file(self):
        report = measure("good_then_silent")
        check = find(report, "not_silent")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("audio stream 1: FAIL", check["detail"])
        self.assertIn("audio stream 0: PASS", check["detail"])

    def test_a_silent_first_stream_fails_the_file(self):
        report = measure("silent_then_good")
        self.assertEqual(status(report, "not_silent"), "FAIL")
        self.assertIn("audio stream 0: FAIL", find(report, "not_silent")["detail"])

    def test_spec_checks_use_the_first_stream_and_say_so(self):
        report = measure("silent_then_good", {"lufs_target": -23})
        self.assertEqual(report.info["spec_checks_use"], "audio stream 0 of 2")
        self.assertEqual(status(report, "lufs_target"), "FAIL")  # stream 0 is the silent one
        self.assertEqual(status(measure("good_then_silent", {"true_peak_max_db": -10}), "true_peak_max_db"), "PASS")


# --------------------------------------------------------------------------- spec handling

class SpecTests(unittest.TestCase):
    def test_checks_run_only_for_keys_present(self):
        ids = [c["id"] for c in measure("tone_stereo_wav", {"max_silence_s": 1}).checks]
        self.assertEqual(ids, ["not_silent", "max_silence_s"])

    def test_no_spec_gives_only_the_always_on_check(self):
        self.assertEqual([c["id"] for c in measure("tone_stereo_wav").checks], ["not_silent"])

    def test_wrong_types_are_usage_errors(self):
        for spec in ({"lufs_target": "-23"}, {"lufs_target": True}, {"lufs_tol": -1}, {"true_peak_max_db": None},
                     {"max_silence_s": -1}, {"max_leading_silence_s": "1"}, {"no_clipping": "yes"},
                     {"no_clipping": 1}, {"max_trailing_silence_s": float("inf")}):
            with self.subTest(spec):
                with self.assertRaises(common.UsageError):
                    qa_audio.analyze(str(M["tone_stereo_wav"]), spec, TOOLS)

    def test_a_bad_spec_is_rejected_before_any_tool_runs(self):
        tools = {"ffmpeg": str(WORK / "no_such_ffmpeg.exe"), "ffprobe": str(WORK / "no_such_ffprobe.exe")}
        with self.assertRaises(common.UsageError):
            qa_audio.analyze(str(M["tone_stereo_wav"]), {"lufs_target": "loud"}, tools)

    def test_unknown_key_warns_with_a_suggestion(self):
        report = measure("tone_stereo_wav", {"lufs_targt": -23})
        warn = find(report, "unknown_spec_key")
        self.assertEqual((warn["status"], warn["actual"]), ("WARN", "lufs_targt"))
        self.assertIn("lufs_target", warn["detail"])
        self.assertEqual(report.overall(), "WARN")

    def test_keys_of_the_other_scripts_do_not_warn(self):
        shared = {"video": {"width": 320}, "audio": {"present": True}, "color": {"space": "bt709"}, "duration_s": 3,
                  "duration_tol_s": 0.1, "max_av_start_offset_s": 0.05, "barcode": {"expect": "0-95"},
                  "lufs_target": -20, "no_clipping": True}
        report = measure("tone_stereo_wav", shared)
        self.assertNotIn("unknown_spec_key", [c["id"] for c in report.checks])
        self.assertEqual(report.overall(), "PASS")


# --------------------------------------------------------------------------- odd inputs

class RobustnessTests(unittest.TestCase):
    def test_path_with_spaces_and_parentheses(self):
        report = measure("spaces", {"lufs_target": -20})
        self.assertEqual(report.overall(), "PASS")
        self.assertIn("my clip (final).mp4", report.file)

    def test_non_ascii_file_name(self):
        self.assertEqual(measure("unicode_name").overall(), "PASS")
        proc = run_cli(M["unicode_name"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.strip(), "")
        run_cli(M["unicode_name"], "--json").stdout.encode("ascii")

    def test_relative_name_starting_with_a_dash_is_not_taken_for_an_option(self):
        shutil.copy(str(M["video_tone"]), str(WORK / "-dash lead.mp4"))
        here = os.getcwd()
        os.chdir(str(WORK))
        try:
            report = qa_audio.analyze("-dash lead.mp4", {}, TOOLS)
        finally:
            os.chdir(here)
        self.assertIn(report.overall(), ("PASS", "WARN"))

    def test_audio_only_file_has_no_video_requirement(self):
        self.assertEqual(measure("tone_stereo_aac").overall(), "PASS")

    def test_every_fixture_gives_a_json_safe_result(self):
        for name in M:
            with self.subTest(name):
                report = measure(name)
                self.assertIn(report.overall(), ("PASS", "WARN", "FAIL", "SKIP"))
                json.dumps(report.to_dict(), allow_nan=False)  # no Infinity or NaN tokens anywhere

    def test_garbage_and_empty_files_are_usage_errors(self):
        (WORK / "garbage.bin").write_text("not media\n")
        (WORK / "empty.bin").write_bytes(b"")
        for name in ("garbage.bin", "empty.bin"):
            with self.subTest(name):
                with self.assertRaises(common.UsageError):
                    qa_audio.analyze(str(WORK / name), {}, TOOLS)

    def test_info_lists_the_filters_used(self):
        info = measure("tone_stereo_wav").info
        for part in ("ebur128=peak=true", "volumedetect", "silencedetect=noise=-50dB:d=0.5"):
            self.assertIn(part, info["filters"])
        for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp", "max_volume_db", "mean_volume_db",
                    "silence_segments", "leading_silence_s", "trailing_silence_s", "audio_duration_s"):
            self.assertIn(key, info)


# --------------------------------------------------------------------------- hostile tags, LFE, inter-sample peaks

class TagSpoofingTests(unittest.TestCase):
    """ffmpeg prints a file's tags on stderr (the same stderr the measurements come from). A tag that
    imitates a measurement must not change a result."""

    def test_the_spoof_text_really_is_in_the_file_and_in_ffmpeg_output(self):
        # Fixture sanity: without this the tests below could pass because the spoof never got in.
        tag = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format_tags=comment", "-of", "csv=p=0", str(M["lead3_spoof"])],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(tag, SPOOF_COMMENT)
        proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(M["lead3_spoof"]), "-f", "null", "-"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertIn(SPOOF_COMMENT, proc.stderr)

    def test_a_forged_silence_line_in_a_tag_does_not_change_the_silence_measurement(self):
        plain, spoof = measure("lead3_plain"), measure("lead3_spoof")
        self.assertEqual(spoof.info["silence_segments"], plain.info["silence_segments"])
        self.assertAlmostEqual(spoof.info["leading_silence_s"], 3.0, delta=0.06)

    def test_a_file_with_three_seconds_of_leading_silence_fails_the_limit_even_with_the_spoof(self):
        spec = {"max_leading_silence_s": 1.0}
        self.assertEqual(status(measure("lead3_plain", spec), "max_leading_silence_s"), "FAIL", "control: the limit works")
        report = measure("lead3_spoof", spec)
        self.assertEqual(status(report, "max_leading_silence_s"), "FAIL")
        self.assertEqual(report.exit_code(), 1)

    def test_forged_volume_and_loudness_text_cannot_hide_a_silent_stream(self):
        self.assertEqual(status(measure("zero_aac"), "not_silent"), "FAIL", "control: the silent file fails")
        report = measure("zero_forged")
        self.assertEqual(status(report, "not_silent"), "FAIL")
        self.assertEqual(report.info["mean_volume_db"], -91.0)
        self.assertEqual(report.info["integrated_lufs"], -70.0)


class LowFrequencyAudioTests(unittest.TestCase):
    def test_sound_only_in_the_lfe_channel_is_a_warning_not_silence(self):
        # BS.1770 loudness ignores the LFE channel, so this stream reads -70 LUFS although it is not silent.
        report = measure("lfe_only")
        self.assertEqual(report.info["integrated_lufs"], -70.0)
        self.assertGreater(report.info["mean_volume_db"], qa_audio.SIGNAL_PRESENT_DB)
        check = find(report, "not_silent")
        self.assertEqual(check["status"], "WARN")
        self.assertIn("LFE", check["detail"])
        self.assertNotIn("present but silent", check["detail"])
        self.assertEqual(report.exit_code(), 0)

    def test_the_same_tone_in_the_centre_channel_is_measured_normally(self):
        report = measure("centre_only")
        self.assertGreater(report.info["integrated_lufs"], -70.0)
        self.assertEqual(status(report, "not_silent"), "PASS")

    def test_truly_silent_audio_still_fails_at_the_loudness_floor(self):
        # Negative control for the new rule: the signal level, not the loudness floor alone, decides.
        for mean, expected in ((-59.9, "WARN"), (-60.0, "FAIL"), (-61.0, "FAIL"), (-80.0, "FAIL")):
            with self.subTest(mean=mean):
                self.assertEqual(qa_audio.judge_silence(measurement(mean_volume_db=mean, integrated_lufs=-70.0))[0], expected)
        self.assertEqual(status(measure("quiet_noise"), "not_silent"), "FAIL")
        self.assertEqual(status(measure("zero_aac"), "not_silent"), "FAIL")

    def test_a_warning_among_streams_is_a_warning_overall(self):
        self.assertEqual(qa_audio.combine_statuses(["WARN", "PASS"]), "WARN")
        self.assertEqual(qa_audio.combine_statuses(["WARN", "SKIP"]), "WARN")
        self.assertEqual(qa_audio.combine_statuses(["WARN", "WARN"]), "WARN")
        self.assertEqual(qa_audio.combine_statuses(["WARN", "FAIL"]), "FAIL")
        self.assertEqual(qa_audio.combine_statuses(["SKIP", "SKIP"]), "SKIP")


class InterSamplePeakTests(unittest.TestCase):
    """The true peak (ebur128, 4x oversampled) is higher than the highest sample when the waveform
    peaks fall between samples. A limit judged on the sample peak would pass this file."""

    def test_the_true_peak_is_well_above_the_sample_peak(self):
        info = measure("isp").info
        self.assertGreater(info["true_peak_dbtp"], info["max_volume_db"] + 2.0)
        self.assertLess(info["max_volume_db"], -1.0)
        self.assertGreater(info["true_peak_dbtp"], -1.0)

    def test_a_true_peak_limit_fails_where_the_sample_peak_would_pass(self):
        report = measure("isp", {"true_peak_max_db": -1.0})
        self.assertEqual(status(report, "true_peak_max_db"), "FAIL")
        self.assertLess(report.info["max_volume_db"], -1.0, "the sample peak alone would have passed the limit")

    def test_negative_control_a_looser_limit_passes(self):
        self.assertEqual(status(measure("isp", {"true_peak_max_db": 0.0}), "true_peak_max_db"), "PASS")


# --------------------------------------------------------------------------- command line

SCRIPT = SCRIPTS / "qa_audio.py"


def run_cli(*args):
    cmd = [sys.executable, "-I", "-B", str(SCRIPT)] + [str(a) for a in args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def write_spec(spec, name="audio_spec.json"):
    path = WORK / name
    path.write_text(json.dumps(spec))
    return str(path)


def strict_loads(text):
    def refuse(token):
        raise ValueError("non-standard JSON constant %s" % token)
    return json.loads(text, parse_constant=refuse)


class CommandLineTests(unittest.TestCase):
    def test_exit_0_for_a_healthy_file_and_for_no_audio(self):
        proc = run_cli(M["video_tone"], "--spec", write_spec({"lufs_target": -20, "no_clipping": True}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout.splitlines()[0])
        self.assertEqual(run_cli(M["no_audio"]).returncode, 0)

    def test_exit_1_for_the_silent_export(self):
        proc = run_cli(M["silent_video"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL", proc.stdout.splitlines()[0])
        self.assertIn("not_silent", proc.stdout)

    def test_exit_1_for_a_missed_spec_value(self):
        self.assertEqual(run_cli(M["video_tone"], "--spec", write_spec({"lufs_target": -14})).returncode, 1)

    def test_exit_2_when_it_could_not_run(self):
        cases = {
            "missing file": [WORK / "nope.wav"],
            "directory": [WORK],
            "not media": [WORK / "garbage.bin"],
            "spec not json": [M["video_tone"], "--spec", str(WORK / "garbage.bin")],
            "spec missing": [M["video_tone"], "--spec", str(WORK / "nope.json")],
            "spec wrong type": [M["video_tone"], "--spec", write_spec({"lufs_target": "loud"}, "bad_type.json")],
            "ffmpeg path wrong": [M["video_tone"], "--ffmpeg", str(WORK / "no_such_ffmpeg.exe")],
            "ffprobe path wrong": [M["video_tone"], "--ffprobe", str(WORK / "no_such_ffprobe.exe")],
            "no arguments": [],
        }
        (WORK / "garbage.bin").write_text("not media\n")
        for label, args in cases.items():
            with self.subTest(label):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                if label != "no arguments":
                    self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)

    def test_json_is_strict_and_has_the_documented_shape(self):
        proc = run_cli(M["zero_aac"], "--json")  # contains -inf internally
        data = strict_loads(proc.stdout)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(sorted(data), ["checks", "file", "info", "overall", "provenance", "tool"])
        self.assertEqual((data["tool"], data["overall"]), ("qa_audio.py", "FAIL"))
        self.assertEqual(data["info"]["true_peak_dbtp"], "-inf")
        for check in data["checks"]:
            self.assertEqual(sorted(check), ["actual", "detail", "expected", "id", "status"])

    def test_provenance_records_how_the_report_was_made(self):
        spec_path = write_spec({"lufs_target": -20})
        data = strict_loads(run_cli(M["video_tone"], "--spec", spec_path, "--json").stdout)
        prov = data["provenance"]
        self.assertEqual((prov["script"], prov["version"]), ("qa_audio.py", common.VERSION))
        self.assertEqual(prov["input"]["sha256"], hashlib.sha256(M["video_tone"].read_bytes()).hexdigest())
        self.assertEqual(prov["spec_sha256"], hashlib.sha256(Path(spec_path).read_bytes()).hexdigest())
        self.assertTrue(prov["ffmpeg_version"].startswith("ffmpeg version"))
        self.assertTrue(prov["ffprobe_version"].startswith("ffprobe version"))
        names = [Path(cmd[0]).name.lower() for cmd in prov["commands"]]
        self.assertTrue(any(n.startswith("ffprobe") for n in names))
        self.assertTrue(any(n.startswith("ffmpeg") for n in names))
        filters = [cmd[cmd.index("-af") + 1] for cmd in prov["commands"] if "-af" in cmd]
        self.assertEqual(filters, [qa_audio.FILTER_CHAIN])
        for cmd in prov["commands"]:
            self.assertIsInstance(cmd, list)
            self.assertIn("-nostdin" if "-af" in cmd else "-v", cmd)

    def test_table_output_is_ascii(self):
        run_cli(M["zero_aac"]).stdout.encode("ascii")

    def test_nothing_is_written_next_to_the_input(self):
        before = sorted(p.name for p in WORK.iterdir())
        run_cli(M["video_tone"], "--json")
        self.assertEqual(sorted(p.name for p in WORK.iterdir()), before)

    def test_help_exits_0(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--spec", proc.stdout)


if __name__ == "__main__":
    unittest.main()
