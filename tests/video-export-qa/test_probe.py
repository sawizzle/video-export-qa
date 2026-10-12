# Tests for qa_probe.py. Real ffmpeg and ffprobe, no mocks for media logic.
# All media is generated here with ffmpeg lavfi sources under the scratch folder.
# Run:  python -B -m unittest discover -s tests/video-export-qa
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

from ffmpeg_fixtures import FixturePaths, fixture_command, guarded_fixture, optional_fixture
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
WORK = ROOT / "_work" / "video-export-qa" / "probe-audio" / "media-probe"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import qa_probe  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
TOOLS = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}

# The documented fix for untagged colour (also used by make_test_clip.py).
RECIPE = (
    "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
)
PICTURE = "testsrc2=size=320x180:rate=24:duration=4"
TONE = "sine=frequency=440:sample_rate=48000:duration=4"

M = FixturePaths()  # fixture name -> Path, filled by setUpModule


# --------------------------------------------------------------------------- helpers

def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    proc = subprocess.run(fixture_command(cmd), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def ffprobe_lines(*args):
    proc = subprocess.run([FFPROBE, "-v", "error"] + [str(a) for a in args], capture_output=True)
    return proc.stdout.decode("utf-8", "replace").split()


@functools.lru_cache(maxsize=None)
def _probe(path, spec_json):
    return qa_probe.analyze(path, json.loads(spec_json), TOOLS)


def probe(name, spec=None):
    """Analyse a fixture (cached: reports are read-only in these tests)."""
    return _probe(str(M[name]), json.dumps(spec or {}, sort_keys=True))


def find(report, check_id):
    matches = [c for c in report.checks if c["id"] == check_id]
    if not matches:
        raise AssertionError("no check %r in %s" % (check_id, [c["id"] for c in report.checks]))
    return matches[0]


def status(report, check_id):
    return find(report, check_id)["status"]


def pts_in_decode_order(path):
    return [int(x) for x in ffprobe_lines("-select_streams", "v:0", "-show_entries", "packet=pts", "-of", "csv=p=0", path)]


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "dir with spaces").mkdir(exist_ok=True)
    (WORK / "png").mkdir(exist_ok=True)
    make_basic_fixtures()
    make_timing_fixtures()
    make_stream_layout_fixtures()
    make_colour_fixtures()
    make_broken_fixtures()
    make_integrity_fixtures()


def add(name, filename):
    M[name] = WORK / filename
    return str(M[name])


def make_basic_fixtures():
    # A correct export: 96 frames at 24 fps, BT.709 tagged, AAC stereo 48 kHz, 4.000 s.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, "-vf", RECIPE, "-c:v", "libx264",
           "-g", "12", "-c:a", "aac", "-ac", "2", "-b:a", "128k", add("good", "good.mp4"))
    # Same picture, container variants (mkv and mpegts keep the audio frame padding visible).
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", add("plain_mkv", "plain.mkv"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", add("plain_ts", "plain.ts"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", add("no_audio", "no_audio.mp4"))
    shutil.copy(str(M["good"]), str(WORK / "dir with spaces" / "my clip (final).mp4"))
    add("spaces", "dir with spaces/my clip (final).mp4")
    shutil.copy(str(M["good"]), str(WORK / "cl\xefp \u6e2c\u8a66.mp4"))
    add("unicode_name", "cl\xefp \u6e2c\u8a66.mp4")


def make_timing_fixtures():
    out = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    # Variable frame rate three ways: random timestamp jitter, every 5th frame removed, one frame removed.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-vf", "setpts='(N/24+(random(1)-0.5)*0.03)/TB'", "-fps_mode", "passthrough",
           *out, add("vfr_jitter", "vfr_jitter.mp4"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-vf", "select='not(eq(mod(n,5),4))'", "-fps_mode", "passthrough",
           *out, add("vfr_drop", "vfr_drop.mp4"))
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-vf", "select='not(eq(n,40))'", "-fps_mode", "passthrough",
           *out, add("vfr_hiccup", "vfr_hiccup.mp4"))
    # 23.976 fps with B-frames in three containers (timebases 1/24000, 1/1000, 1/90000).
    for ext in ("mp4", "mkv", "ts"):
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001:duration=5", "-c:v", "libx264",
               "-bf", "3", "-pix_fmt", "yuv420p", add("ntsc_" + ext, "ntsc." + ext))
    # Start offsets: B-frame delay with no edit list (start 0.083 s) and a video that starts 2 s in.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-c:v", "libx264", "-bf", "3", "-pix_fmt", "yuv420p", "-use_editlist", "0",
           add("no_editlist", "no_editlist.mp4"))
    def build_late_video():
        ffmpeg("-itsoffset", "2", "-i", M["no_audio"], "-c:v", "copy",
               add("late_video", "late_video.mp4"))
        starts = ffprobe_lines("-select_streams", "v:0", "-show_entries", "stream=start_time",
                               "-of", "csv=p=0", M["late_video"])
        if len(starts) != 1 or abs(float(starts[0]) - 2.0) > 0.05:
            raise AssertionError("late_video fixture must start near 2 s, got %r" % starts)
        return M["late_video"]
    M["late_video"] = guarded_fixture(build_late_video, "late_video fixture")
    # Intra-only clip, then cut at exact packet boundaries: the header still promises 96 frames.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-c:v", "libx264", "-g", "1", "-bf", "0", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", add("intra", "intra.mp4"))
    positions = [int(x) for x in ffprobe_lines("-select_streams", "v:0", "-show_entries", "packet=pos", "-of", "csv=p=0", M["intra"])]
    data = M["intra"].read_bytes()
    (WORK / "trunc_1_frame.mp4").write_bytes(data[:positions[95]])
    (WORK / "trunc_56_frames.mp4").write_bytes(data[:positions[40]])
    (WORK / "trunc_all_frames.mp4").write_bytes(data[:positions[0]])  # header only: no picture data survives
    add("trunc_1", "trunc_1_frame.mp4")
    add("trunc_56", "trunc_56_frames.mp4")
    add("trunc_0", "trunc_all_frames.mp4")


def make_stream_layout_fixtures():
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3", "-c:a", "aac", add("audio_only", "audio_only.m4a"))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1", "-frames:v", "1", str(WORK / "png" / "cover.png"))
    ffmpeg("-i", M["audio_only"], "-i", WORK / "png" / "cover.png", "-map", "0:a", "-map", "1:v", "-c:a", "copy",
           "-c:v", "mjpeg", "-disposition:v:0", "attached_pic", add("cover_art", "cover_art.m4a"))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           add("one_frame", "one_frame.mp4"))
    def build_rotated():
        path = add("rotated", "rotated.mp4")
        ffmpeg("-display_rotation", "90", "-i", M["good"], "-c", "copy", path)
        return Path(path)
    M["rotated"] = optional_fixture(FFMPEG, "-display_rotation", build_rotated)
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, "-f", "lavfi", "-i",
           "sine=frequency=880:sample_rate=44100:duration=4", "-map", "0:v", "-map", "1:a", "-map", "2:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", add("two_audio", "two_audio.mp4"))
    # Audio delayed by 0.5 s with -itsoffset (the audio stream then starts at about 0.478 s).
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-itsoffset", "0.5", "-f", "lavfi", "-i", TONE, "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", add("delayed_audio", "delayed_audio.mp4"))


def make_colour_fixtures():
    # A PNG sequence encoded the "obvious" way, with no colour flags, and with the fix recipe.
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1", "-start_number", "0", str(WORK / "png" / "f%04d.png"))
    ffmpeg("-framerate", "24", "-i", WORK / "png" / "f%04d.png", "-c:v", "libx264", "-crf", "18", add("png_plain", "png_plain.mp4"))
    ffmpeg("-framerate", "24", "-i", WORK / "png" / "f%04d.png", "-vf", RECIPE, "-c:v", "libx264", "-crf", "18",
           add("png_fixed", "png_fixed.mp4"))
    # Only the colour space tagged (container flag, no matrix conversion): partially tagged.
    ffmpeg("-framerate", "24", "-i", WORK / "png" / "f%04d.png", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
           "-c:v", "libx264", add("half_tagged", "half_tagged.mp4"))
    # Pure red, 64x64, to measure which matrix the stored YUV values follow.
    ffmpeg("-f", "lavfi", "-i", "color=c=0xFF0000:size=64x64:rate=24:duration=1", "-frames:v", "1", str(WORK / "png" / "red.png"))
    ffmpeg("-loop", "1", "-framerate", "24", "-i", WORK / "png" / "red.png", "-t", "0.25", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-crf", "10", add("red_plain", "red_plain.mp4"))
    ffmpeg("-loop", "1", "-framerate", "24", "-i", WORK / "png" / "red.png", "-t", "0.25", "-vf", RECIPE,
           "-c:v", "libx264", "-crf", "10", add("red_fixed", "red_fixed.mp4"))
    # Odd dimensions need 4:4:4. Both sides odd, then only the width odd, then only the height odd.
    for name, size in (("odd", "321x181"), ("odd_width", "321x180"), ("odd_height", "320x181")):
        def build_odd():
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=%s:rate=24:duration=1" % size, "-c:v", "libx264", "-pix_fmt",
                   "yuv444p", add(name, name + ".mp4"))
            dimensions = ffprobe_lines("-select_streams", "v:0", "-show_entries", "stream=width,height",
                                       "-of", "csv=p=0", M[name])
            expected = size.replace("x", ",")
            if dimensions != [expected]:
                raise AssertionError("%s fixture claims %s but stores %r" % (name, size, dimensions))
            return M[name]
        M[name] = guarded_fixture(build_odd, name + " fixture")


def make_integrity_fixtures():
    """Files for the frame-count check: cut-short Matroska, a Matroska that records no length, and NTSC
    rates whose duration times frame rate is not a whole number in floating point."""
    for name, source in (("trunc_mkv_av", "plain_mkv"), ("trunc_mkv_video", "ntsc_mkv")):
        data = M[source].read_bytes()
        (WORK / (name + ".mkv")).write_bytes(data[:int(len(data) * 0.6)])
        add(name, name + ".mkv")
    # Written to a pipe, ffmpeg cannot go back and fill in the length: the file records none at all.
    with open(str(WORK / "live.mkv"), "wb") as handle:
        proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(M["intra"]), "-an", "-c", "copy",
                               "-f", "matroska", "pipe:1"], stdout=handle, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: " + proc.stderr.decode("utf-8", "replace"))
    add("live_mkv", "live.mkv")
    # 120 frames at 30000/1001 last 4.004 s, and 4.004 * 29.97003 is 119.99999999999999 in floating point.
    for name, rate, frames in (("ntsc2997", "30000/1001", 120), ("ntsc5994", "60000/1001", 240)):
        source = "testsrc2=size=160x90:rate=%s" % rate
        ffmpeg("-f", "lavfi", "-i", source, "-frames:v", frames, "-c:v", "libx264", "-pix_fmt", "yuv420p", add(name, name + ".mp4"))
        ffmpeg("-f", "lavfi", "-i", source, "-frames:v", frames, "-c:v", "libx264", "-pix_fmt", "yuv420p", add(name + "_mkv", name + ".mkv"))
        ffmpeg("-f", "lavfi", "-i", source, "-frames:v", frames, "-c:v", "libvpx-vp9", "-b:v", "200k", "-deadline", "realtime",
               "-cpu-used", "8", add(name + "_webm", name + ".webm"))
    # A 5000 character title, to see how much of a tag reaches the report.
    ffmpeg("-i", M["good"], "-c", "copy", "-metadata", "title=" + "T" * 5000, add("long_title", "long_title.mp4"))


def make_broken_fixtures():
    (WORK / "garbage.mp4").write_text("this is not a video file\n")
    (WORK / "empty.mp4").write_bytes(b"")
    add("garbage", "garbage.mp4")
    add("empty", "empty.mp4")


def stored_yuv(path):
    """Y, U, V of the first pixel as stored in the file (no colour conversion on decode)."""
    proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True)
    data = proc.stdout
    plane = 64 * 64
    return data[0], data[plane], data[plane + plane // 4]


# --------------------------------------------------------------------------- video_stream

class VideoStreamTests(unittest.TestCase):
    def test_video_file_passes(self):
        report = probe("good")
        self.assertEqual(status(report, "video_stream"), "PASS")
        self.assertEqual(report.info["video"]["codec"], "h264")

    def test_audio_only_file_fails(self):
        report = probe("audio_only")
        self.assertEqual(status(report, "video_stream"), "FAIL")
        self.assertEqual(report.overall(), "FAIL")
        self.assertIsNone(report.info["video"])

    def test_cover_art_is_not_a_video_stream(self):
        report = probe("cover_art")
        self.assertEqual(status(report, "video_stream"), "FAIL")
        self.assertEqual(report.info["attached_pictures"], 1)
        self.assertIn("cover art", find(report, "video_stream")["detail"])

    def test_dependent_checks_are_skipped_without_video(self):
        report = probe("audio_only")
        for check_id in ("cfr", "frame_count_consistent", "color_tags"):
            self.assertEqual(status(report, check_id), "SKIP", check_id)

    def test_spec_video_keys_fail_when_there_is_no_video(self):
        report = probe("audio_only", {"video": {"codec": "h264", "width": 320, "height": 180, "fps": 24, "frames": 96,
                                                "pix_fmt": "yuv420p", "even_dimensions": True}})
        for check_id in ("codec", "width", "height", "fps", "frames", "pix_fmt", "even_dimensions"):
            self.assertEqual(status(report, check_id), "FAIL", check_id)
            self.assertIn("no video stream", find(report, check_id)["detail"])


# --------------------------------------------------------------------------- cfr

class CfrFunctionTests(unittest.TestCase):
    """analyse_cfr works on bare timestamp lists, so the tricky orderings are tested directly."""

    def test_constant_spacing_is_constant(self):
        self.assertTrue(qa_probe.analyse_cfr(list(range(0, 512 * 50, 512)))["constant"])

    def test_b_frame_decode_order_gives_the_same_verdict_as_sorted(self):
        # Packet order of an H.264 stream with B-frames: pts goes back and forth.
        decode_order = [0, 3003, 1001, 2002, 6006, 4004, 5005, 9009, 7007, 8008, 10010]
        naive_deltas = [b - a for a, b in zip(decode_order, decode_order[1:])]
        self.assertTrue(any(d < 0 for d in naive_deltas), "the input must really be non-monotonic")
        self.assertFalse(len(set(naive_deltas)) == 1, "a naive delta check would have been fooled")
        self.assertEqual(qa_probe.analyse_cfr(decode_order), qa_probe.analyse_cfr(sorted(decode_order)))
        self.assertTrue(qa_probe.analyse_cfr(decode_order)["constant"])

    def test_one_tick_rounding_is_not_variable(self):
        # 23.976 fps in a 1/1000 timebase alternates between 41 and 42 ms.
        pts = [int(round(i * 1001 / 24.0)) for i in range(100)]
        self.assertEqual({b - a for a, b in zip(pts, pts[1:])}, {41, 42})
        self.assertTrue(qa_probe.analyse_cfr(pts)["constant"])

    def test_ntsc_in_90khz_alternates_by_one_tick(self):
        pts = [int(round(i * 3003.0)) for i in range(50)]
        pts[7] += 1  # one tick of rounding noise
        self.assertTrue(qa_probe.analyse_cfr(pts)["constant"])

    def test_one_missing_frame_is_variable(self):
        pts = [i * 512 for i in range(100)]
        del pts[40]
        result = qa_probe.analyse_cfr(pts)
        self.assertFalse(result["constant"])
        self.assertEqual(result["max_delta_ticks"], 1024)

    def test_one_frame_of_jitter_beyond_one_tick_is_variable(self):
        pts = [i * 512 for i in range(100)]
        pts[50] += 40  # about 8 percent of a frame, far more than one tick or 1 percent
        self.assertFalse(qa_probe.analyse_cfr(pts)["constant"])

    def test_duplicate_timestamps_are_variable(self):
        pts = [i * 512 for i in range(50)] + [49 * 512]
        self.assertFalse(qa_probe.analyse_cfr(pts)["constant"])

    def test_fewer_than_two_timestamps_cannot_be_judged(self):
        self.assertIsNone(qa_probe.analyse_cfr([]))
        self.assertIsNone(qa_probe.analyse_cfr([0]))


class CfrFileTests(unittest.TestCase):
    def test_constant_files_pass(self):
        for name in ("good", "ntsc_mp4", "ntsc_mkv", "ntsc_ts", "intra"):
            with self.subTest(name):
                self.assertEqual(status(probe(name), "cfr"), "PASS")

    def test_ntsc_fixtures_really_have_b_frames_and_rational_timebases(self):
        pts = pts_in_decode_order(M["ntsc_mp4"])
        self.assertNotEqual(pts, sorted(pts), "packets of a B-frame stream are not in pts order")
        self.assertEqual(probe("ntsc_mp4").info["video"]["avg_frame_rate"], "24000/1001")
        self.assertEqual(probe("ntsc_mkv").info["video"]["time_base"], "1/1000")
        self.assertEqual(probe("ntsc_ts").info["video"]["time_base"], "1/90000")

    def test_variable_frame_rate_files_fail(self):
        for name in ("vfr_jitter", "vfr_drop", "vfr_hiccup"):
            with self.subTest(name):
                report = probe(name)
                self.assertEqual(status(report, "cfr"), "FAIL")
                self.assertIn("variable frame rate", find(report, "cfr")["detail"])
                self.assertEqual(report.overall(), "FAIL")

    def test_spec_can_allow_variable_frame_rate(self):
        report = probe("vfr_drop", {"video": {"cfr": False}})
        self.assertEqual(status(report, "cfr"), "SKIP")
        self.assertIn("allowed", find(report, "cfr")["detail"])

    def test_spec_cfr_true_is_the_default_behaviour(self):
        self.assertEqual(status(probe("vfr_drop", {"video": {"cfr": True}}), "cfr"), "FAIL")
        self.assertEqual(status(probe("good", {"video": {"cfr": True}}), "cfr"), "PASS")

    def test_single_frame_video_is_skipped_not_passed(self):
        report = probe("one_frame")
        self.assertEqual(status(report, "cfr"), "SKIP")
        self.assertIn("at least 2 frames", find(report, "cfr")["detail"])


# --------------------------------------------------------------------------- frame_count_consistent

class FrameCountTests(unittest.TestCase):
    def test_judge_frame_count_thresholds(self):
        # references are (label, expected count, slack in frames the container length may run past the picture)
        self.assertEqual(qa_probe.judge_frame_count(96, [("a", 96, 0), ("b", 96, 0)]), "PASS")
        self.assertEqual(qa_probe.judge_frame_count(96, [("a", 96, 0), ("b", 97, 0)]), "WARN")
        self.assertEqual(qa_probe.judge_frame_count(96, [("a", 95, 0)]), "WARN")
        self.assertEqual(qa_probe.judge_frame_count(96, [("a", 98, 0)]), "FAIL")
        self.assertEqual(qa_probe.judge_frame_count(96, [("a", 96, 0), ("b", 90, 0)]), "FAIL")

    def test_slack_only_covers_a_container_that_promises_more_than_decoded(self):
        # An audio tail makes the container longer than the picture: promising up to `slack` more frames is fine.
        self.assertEqual(qa_probe.judge_frame_count(96, [("c", 98, 2)]), "PASS")
        self.assertEqual(qa_probe.judge_frame_count(96, [("c", 99, 2)]), "WARN")
        self.assertEqual(qa_probe.judge_frame_count(96, [("c", 100, 2)]), "FAIL")
        # Slack never excuses MORE frames decoded than the container promises.
        self.assertEqual(qa_probe.judge_frame_count(96, [("c", 95, 2)]), "WARN")
        self.assertEqual(qa_probe.judge_frame_count(96, [("c", 93, 2)]), "FAIL")
        # And never excuses a truncated file: 58 decoded against 97 promised.
        self.assertEqual(qa_probe.judge_frame_count(58, [("c", 97, 2)]), "FAIL")

    def test_complete_file_passes(self):
        report = probe("good")
        self.assertEqual(status(report, "frame_count_consistent"), "PASS")
        self.assertEqual(report.info["video"]["counted_frames"], 96)
        self.assertEqual(report.info["video"]["nb_frames_metadata"], 96)

    def test_file_missing_one_frame_warns(self):
        report = probe("trunc_1")
        self.assertEqual(report.info["video"]["counted_frames"], 95)
        self.assertEqual(report.info["video"]["nb_frames_metadata"], 96)
        self.assertEqual(status(report, "frame_count_consistent"), "WARN")

    def test_file_missing_many_frames_fails(self):
        report = probe("trunc_56")
        self.assertEqual(report.info["video"]["counted_frames"], 40)
        self.assertEqual(status(report, "frame_count_consistent"), "FAIL")
        self.assertEqual(report.overall(), "FAIL")

    def test_zero_decodable_frames_is_a_failure_never_a_pass(self):
        report = probe("trunc_0")
        self.assertEqual(report.info["video"]["counted_frames"], 0)
        self.assertEqual(status(report, "frame_count_consistent"), "FAIL")
        self.assertIn("no frames decoded", find(report, "frame_count_consistent")["detail"])
        self.assertEqual(status(probe("trunc_0", {"video": {"frames": 96}}), "frames"), "FAIL")
        self.assertEqual(status(report, "cfr"), "SKIP")

    def test_matroska_without_nb_frames_compares_against_the_duration_tag_it_recorded(self):
        report = probe("ntsc_mkv")
        self.assertIsNone(report.info["video"]["nb_frames_metadata"])
        self.assertIsNone(report.info["video"]["duration_s"], "Matroska stores no stream duration")
        self.assertIsNotNone(report.info["video"]["duration_tag_s"])
        check = find(report, "frame_count_consistent")
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(list(check["expected"]), ["DURATION tag*fps"])

    def test_a_truncated_matroska_file_fails_instead_of_agreeing_with_itself(self):
        # The file was cut to 60 percent of its bytes. Its frames decode and its timestamps are even, so a
        # reference worked out from the decoded frames would match perfectly. The DURATION tag it still
        # carries is a length that was recorded before the cut.
        for name, healthy in (("trunc_mkv_av", "plain_mkv"), ("trunc_mkv_video", "ntsc_mkv")):
            with self.subTest(name):
                good, cut = probe(healthy), probe(name)
                self.assertEqual(status(good, "frame_count_consistent"), "PASS", "control: the uncut file passes")
                self.assertLess(cut.info["video"]["counted_frames"], good.info["video"]["counted_frames"] * 0.7)
                check = find(cut, "frame_count_consistent")
                self.assertEqual(check["status"], "FAIL")
                self.assertEqual(cut.overall(), "FAIL")
                self.assertGreater(list(check["expected"].values())[0], cut.info["video"]["counted_frames"] + 20)

    def test_a_file_that_records_no_length_is_skip_not_pass(self):
        report = probe("live_mkv")
        video = report.info["video"]
        self.assertEqual(video["counted_frames"], 96)
        self.assertIsNone(video["nb_frames_metadata"])
        self.assertIsNone(video["duration_s"])
        self.assertIsNone(video["duration_tag_s"])
        self.assertIsNone(report.info["format"]["duration_s"])
        check = find(report, "frame_count_consistent")
        self.assertEqual(check["status"], "SKIP")
        self.assertIn("would prove nothing", check["detail"])

    def test_a_container_that_runs_one_audio_frame_past_the_picture_passes(self):
        report = probe("plain_mkv")
        check = find(report, "frame_count_consistent")
        self.assertEqual(report.info["video"]["counted_frames"], 96)
        self.assertEqual(check["status"], "PASS")
        self.assertIn("audio", check["detail"])

    def test_references_prefer_what_the_stream_itself_says(self):
        video = {"codec": "h264", "nb_frames_metadata": 96, "avg_frame_rate": "24/1", "r_frame_rate": "24/1",
                 "duration_s": 4.0, "duration_tag_s": 4.5}
        self.assertEqual(qa_probe.frame_count_references(video, 4.2, 1), [("nb_frames", 96, 0), ("duration*fps", 96, 0)])
        mkv = dict(video, nb_frames_metadata=None, duration_s=None)
        self.assertEqual(qa_probe.frame_count_references(mkv, 4.2, 0), [("DURATION tag*fps", 108, 0)])
        self.assertEqual(qa_probe.frame_count_references(mkv, 4.2, 1), [("DURATION tag*fps", 108, 2)])
        no_tag = dict(mkv, duration_tag_s=None)
        self.assertEqual(qa_probe.frame_count_references(no_tag, 4.2, 1), [("container duration*fps", 101, 2)])
        self.assertEqual(qa_probe.frame_count_references(no_tag, None, 1), [])

    def test_ntsc_rates_whose_product_is_not_whole_in_floating_point_still_pass(self):
        # 4.004 s * 29.97003 fps is 119.99999999999999: the expected count must be rounded, not truncated.
        for name, frames in (("ntsc2997", 120), ("ntsc5994", 240)):
            with self.subTest(name):
                report = probe(name)
                self.assertEqual(report.info["video"]["counted_frames"], frames)
                self.assertEqual(status(report, "frame_count_consistent"), "PASS")
                self.assertEqual(find(report, "frame_count_consistent")["expected"]["duration*fps"], frames)
        self.assertLess(4.004 * float(Fraction(30000, 1001)), 120.0, "the floating point trap is real")

    def test_audio_padding_in_the_container_does_not_cause_a_false_warning(self):
        # mkv and mpegts report a container duration one AAC frame longer than the picture:
        # frames must be compared against the picture's own length, not the container's.
        for name in ("plain_mkv", "plain_ts"):
            with self.subTest(name):
                self.assertEqual(status(probe(name), "frame_count_consistent"), "PASS")

    def test_a_start_offset_does_not_upset_the_count_or_the_frame_rate_check(self):
        for name, minimum_start in (("no_editlist", 0.05), ("late_video", 1.5)):
            with self.subTest(name):
                report = probe(name)
                self.assertGreater(report.info["video"]["start_time_s"], minimum_start, "fixture must have an offset")
                self.assertEqual(report.info["video"]["counted_frames"], 96)
                self.assertEqual(status(report, "frame_count_consistent"), "PASS")
                self.assertEqual(status(report, "cfr"), "PASS")

    def test_one_frame_video_is_consistent(self):
        report = probe("one_frame")
        self.assertEqual(report.info["video"]["counted_frames"], 1)
        self.assertEqual(status(report, "frame_count_consistent"), "PASS")

    def test_frames_spec_counts_decoded_frames_not_metadata(self):
        self.assertEqual(status(probe("good", {"video": {"frames": 96}}), "frames"), "PASS")
        self.assertEqual(status(probe("good", {"video": {"frames": 95}}), "frames"), "FAIL")
        # The truncated file still claims 96 frames in its header but only 95 decode.
        self.assertEqual(status(probe("trunc_1", {"video": {"frames": 96}}), "frames"), "FAIL")
        self.assertEqual(status(probe("trunc_1", {"video": {"frames": 95}}), "frames"), "PASS")


# --------------------------------------------------------------------------- colour

class ColourTests(unittest.TestCase):
    def test_png_sequence_with_plain_libx264_is_untagged(self):
        report = probe("png_plain")
        video = report.info["video"]
        for field in ("color_space", "color_transfer", "color_primaries", "color_range"):
            self.assertTrue(qa_probe.is_untagged(video[field]), "%s=%r" % (field, video[field]))
        check = find(report, "color_tags")
        self.assertEqual(check["status"], "WARN")
        self.assertIn("untagged", check["detail"])
        self.assertEqual(report.overall(), "WARN")

    def test_plain_encode_stores_bt601_matrix_values(self):
        # Pure red is (Y, U, V) = (81, 90, 240) in BT.601 and (63, 102, 240) in BT.709 (limited range).
        y, u, v = stored_yuv(M["red_plain"])
        self.assertAlmostEqual(y, 81, delta=2)
        self.assertAlmostEqual(u, 90, delta=2)
        self.assertAlmostEqual(v, 240, delta=2)

    def test_recipe_stores_bt709_values_and_tags_them(self):
        y, u, v = stored_yuv(M["red_fixed"])
        self.assertAlmostEqual(y, 63, delta=2)
        self.assertAlmostEqual(u, 102, delta=2)
        self.assertAlmostEqual(v, 240, delta=2)
        video = probe("red_fixed").info["video"]
        self.assertEqual((video["color_space"], video["color_transfer"], video["color_primaries"], video["color_range"]),
                         ("bt709", "bt709", "bt709", "tv"))

    def test_recipe_output_is_tagged_and_passes(self):
        report = probe("png_fixed")
        self.assertEqual(status(report, "color_tags"), "PASS")
        self.assertEqual(report.info["video"]["pix_fmt"], "yuv420p")

    def test_recipe_output_matches_a_bt709_spec(self):
        report = probe("png_fixed", {"color": {"space": "bt709", "range": "tv"}})
        self.assertEqual(status(report, "color_tags"), "PASS")
        full = {"color": {"space": "bt709", "transfer": "bt709", "primaries": "bt709", "range": "tv"}}
        self.assertEqual(status(probe("png_fixed", full), "color_tags"), "PASS")

    def test_spec_mismatch_fails(self):
        for wrong in ({"space": "smpte170m"}, {"range": "pc"}, {"primaries": "bt2020"}, {"transfer": "smpte2084"}):
            with self.subTest(wrong):
                report = probe("png_fixed", {"color": wrong})
                self.assertEqual(status(report, "color_tags"), "FAIL")

    def test_untagged_file_fails_a_colour_spec(self):
        report = probe("png_plain", {"color": {"space": "bt709", "range": "tv"}})
        self.assertEqual(status(report, "color_tags"), "FAIL")
        self.assertIn("space is unknown", find(report, "color_tags")["detail"])

    def test_range_aliases_are_understood(self):
        self.assertEqual(status(probe("png_fixed", {"color": {"range": "limited"}}), "color_tags"), "PASS")
        self.assertEqual(status(probe("png_fixed", {"color": {"range": "full"}}), "color_tags"), "FAIL")

    def test_partially_tagged_file_warns_and_names_the_missing_fields(self):
        report = probe("half_tagged")
        check = find(report, "color_tags")
        self.assertEqual(check["status"], "WARN")
        missing = check["detail"].split("untagged:")[1].split(".")[0]
        self.assertNotIn("space", missing)  # the one tag that was written
        for field in ("transfer", "primaries"):
            self.assertIn(field, missing)


# --------------------------------------------------------------------------- spec checks

FULL_SPEC = {
    "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 320, "height": 180, "fps": "24", "frames": 96,
              "even_dimensions": True, "cfr": True},
    "color": {"space": "bt709", "range": "tv"},
    "audio": {"present": True, "sample_rate": 48000, "channels": 2, "codec": "aac"},
    "duration_s": 4.0,
    "duration_tol_s": 0.05,
    "max_av_start_offset_s": 0.05,
}

# (name, spec change, the one check id that must fail)
SPEC_MUTATIONS = [
    ("codec", {"video": {"codec": "hevc"}}, "codec"),
    ("pix_fmt", {"video": {"pix_fmt": "yuv444p"}}, "pix_fmt"),
    ("width", {"video": {"width": 1920}}, "width"),
    ("height", {"video": {"height": 1080}}, "height"),
    ("fps", {"video": {"fps": "25"}}, "fps"),
    ("frames", {"video": {"frames": 95}}, "frames"),
    ("color", {"color": {"space": "smpte170m"}}, "color_tags"),
    ("duration", {"duration_s": 5.0}, "duration_s"),
    ("audio_absent", {"audio": {"present": False}}, "audio.present"),
    ("sample_rate", {"audio": {"sample_rate": 44100}}, "audio.sample_rate"),
    ("channels", {"audio": {"channels": 1}}, "audio.channels"),
    ("audio_codec", {"audio": {"codec": "mp3"}}, "audio.codec"),
]


def merged(base, change):
    result = json.loads(json.dumps(base))
    for key, value in change.items():
        if isinstance(value, dict):
            result.setdefault(key, {}).update(value)
        else:
            result[key] = value
    return result


class SpecTests(unittest.TestCase):
    def test_a_correct_file_passes_the_full_spec(self):
        report = probe("good", FULL_SPEC)
        self.assertEqual(report.overall(), "PASS", [c for c in report.checks if c["status"] != "PASS"])
        ids = {c["id"] for c in report.checks}
        for expected in ("codec", "pix_fmt", "width", "height", "even_dimensions", "fps", "frames", "duration_s",
                         "audio.present", "audio.sample_rate", "audio.channels", "audio.codec", "max_av_start_offset_s"):
            self.assertIn(expected, ids)
        self.assertEqual(sum(1 for c in report.checks if c["status"] == "SKIP"), 0)

    def test_each_wrong_value_fails_exactly_its_own_check(self):
        for name, change, failing in SPEC_MUTATIONS:
            with self.subTest(name):
                report = probe("good", merged(FULL_SPEC, change))
                failed = [c["id"] for c in report.checks if c["status"] == "FAIL"]
                self.assertEqual(failed, [failing])
                self.assertEqual(report.overall(), "FAIL")

    def test_checks_run_only_for_keys_present_in_the_spec(self):
        report = probe("good", {"video": {"width": 320}})
        ids = [c["id"] for c in report.checks]
        self.assertIn("width", ids)
        for absent in ("height", "codec", "fps", "frames", "duration_s", "audio.present", "max_av_start_offset_s"):
            self.assertNotIn(absent, ids)

    def test_fps_as_number_is_compared_within_a_thousandth(self):
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": 23.976}}), "fps"), "PASS")
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": "23.976"}}), "fps"), "PASS")
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": 24}}), "fps"), "FAIL")
        self.assertEqual(status(probe("good", {"video": {"fps": 23.976}}), "fps"), "FAIL")
        self.assertEqual(status(probe("good", {"video": {"fps": 24}}), "fps"), "PASS")

    def test_fps_as_fraction_is_compared_exactly_or_within_a_container_rounding(self):
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": "24000/1001"}}), "fps"), "PASS")
        self.assertEqual(status(probe("ntsc_mkv", {"video": {"fps": "24000/1001"}}), "fps"), "PASS")
        self.assertEqual(find(probe("ntsc_mp4", {"video": {"fps": "24000/1001"}}), "fps")["detail"], "exact fraction match")
        self.assertEqual(status(probe("good", {"video": {"fps": "48/2"}}), "fps"), "PASS")
        # 23976/1000 is 23.976, within 0.01 percent of 24000/1001 (23.976024): the same rate to a container.
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": "23976/1000"}}), "fps"), "PASS")
        # But neighbouring rates are still told apart (negative controls): 24 and 23.976 differ by 0.1 percent,
        # 23.98 by 0.017 percent.
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": "24/1"}}), "fps"), "FAIL")
        self.assertEqual(status(probe("ntsc_mp4", {"video": {"fps": "2398/100"}}), "fps"), "FAIL")
        self.assertEqual(status(probe("good", {"video": {"fps": "24000/1001"}}), "fps"), "FAIL")

    def test_a_59_94_fps_matroska_or_webm_file_passes_the_exact_fraction_spec(self):
        # ffprobe reports 59.94 fps as 19001/317 in these containers (1 ms timebase) and 60000/1001 in mp4.
        spec = {"video": {"fps": "60000/1001"}}
        self.assertEqual(probe("ntsc5994").info["video"]["avg_frame_rate"], "60000/1001")
        for name in ("ntsc5994", "ntsc5994_mkv", "ntsc5994_webm"):
            with self.subTest(name):
                check = find(probe(name, spec), "fps")
                self.assertEqual(check["status"], "PASS", check)
        rounded = find(probe("ntsc5994_mkv", spec), "fps")
        self.assertNotEqual(probe("ntsc5994_mkv").info["video"]["avg_frame_rate"], "60000/1001", "the container did round it")
        self.assertIn("container timebase rounded", rounded["detail"])
        # Negative control: the same files fail a 60 fps spec.
        for name in ("ntsc5994_mkv", "ntsc5994_webm"):
            self.assertEqual(status(probe(name, {"video": {"fps": "60/1"}}), "fps"), "FAIL", name)

    def test_a_failing_fps_row_states_the_tolerance_and_the_gap_not_a_pass_reason(self):
        for spec, tolerance in (({"video": {"fps": 30}}, "tolerance 0.001 fps"),
                                ({"video": {"fps": "30/1"}}, "tolerance 0.01 percent")):
            with self.subTest(spec):
                check = find(probe("good", spec), "fps")
                self.assertEqual(check["status"], "FAIL")
                self.assertIn(tolerance, check["detail"])
                self.assertIn("differs by 6.000000 fps", check["detail"])
                self.assertNotEqual(check["detail"], "within 0.001 fps")
        self.assertEqual(find(probe("good", {"video": {"fps": 24}}), "fps")["detail"], "within 0.001 fps")

    def test_even_dimensions(self):
        self.assertEqual(status(probe("good", {"video": {"even_dimensions": True}}), "even_dimensions"), "PASS")
        report = probe("odd", {"video": {"even_dimensions": True}})
        self.assertEqual(status(report, "even_dimensions"), "FAIL")
        self.assertEqual((report.info["video"]["width"], report.info["video"]["height"]), (321, 181))
        # One odd side is enough to fail (a mutation from "and" to "or" would let these through).
        for name in ("odd_width", "odd_height"):
            self.assertEqual(status(probe(name, {"video": {"even_dimensions": True}}), "even_dimensions"), "FAIL", name)
        # even_dimensions false means "not required": no check at all.
        self.assertNotIn("even_dimensions", [c["id"] for c in probe("odd", {"video": {"even_dimensions": False}}).checks])

    def test_codec_comparison_ignores_case(self):
        self.assertEqual(status(probe("good", {"video": {"codec": "H264"}}), "codec"), "PASS")

    def test_missing_audio_with_present_true_fails(self):
        report = probe("no_audio", {"audio": {"present": True}})
        self.assertEqual(status(report, "audio.present"), "FAIL")
        self.assertEqual(report.overall(), "FAIL")

    def test_missing_audio_with_present_false_passes(self):
        self.assertEqual(status(probe("no_audio", {"audio": {"present": False}}), "audio.present"), "PASS")

    def test_audio_property_requirements_fail_when_there_is_no_audio(self):
        report = probe("no_audio", {"audio": {"sample_rate": 48000, "channels": 2, "codec": "aac"}})
        for check_id in ("audio.sample_rate", "audio.channels", "audio.codec"):
            self.assertEqual(status(report, check_id), "FAIL", check_id)
            self.assertEqual(find(report, check_id)["detail"], "no audio stream")

    def test_audio_spec_uses_the_first_stream_and_names_the_others(self):
        report = probe("two_audio", {"audio": {"present": True, "sample_rate": 48000}})
        self.assertEqual(status(report, "audio.sample_rate"), "PASS")
        self.assertIn("first of 2 audio streams", find(report, "audio.sample_rate")["detail"])
        self.assertIn("44100", find(report, "audio.sample_rate")["detail"])
        # The second stream is 44100 Hz: a spec for that rate must fail against the first stream.
        self.assertEqual(status(probe("two_audio", {"audio": {"sample_rate": 44100}}), "audio.sample_rate"), "FAIL")
        self.assertEqual(len(report.info["audio"]), 2)

    def test_duration_check_passes_within_tolerance_and_fails_outside(self):
        self.assertEqual(status(probe("good", {"duration_s": 4.0}), "duration_s"), "PASS")
        self.assertEqual(status(probe("good", {"duration_s": 4.03, "duration_tol_s": 0.05}), "duration_s"), "PASS")
        self.assertEqual(status(probe("good", {"duration_s": 4.03, "duration_tol_s": 0.01}), "duration_s"), "FAIL")
        self.assertEqual(status(probe("good", {"duration_s": 4.5}), "duration_s"), "FAIL")

    def test_duration_default_tolerance_is_documented_value(self):
        self.assertEqual(qa_probe.DEFAULT_DURATION_TOL_S, 0.02)
        self.assertEqual(status(probe("good", {"duration_s": 4.019}), "duration_s"), "PASS")
        self.assertEqual(status(probe("good", {"duration_s": 4.03}), "duration_s"), "FAIL")


# --------------------------------------------------------------------------- start times and duration quirks

class StartOffsetTests(unittest.TestCase):
    def test_mp4_edit_lists_hide_aac_priming(self):
        report = probe("good")
        self.assertEqual(report.info["av_start_offset_s"], 0.0)
        self.assertEqual(report.info["audio"][0]["start_time_s"], 0.0)

    def test_mpegts_shows_aac_priming_as_one_audio_frame(self):
        offset = probe("plain_ts").info["av_start_offset_s"]
        self.assertAlmostEqual(offset, 1024 / 48000.0, delta=0.002)
        self.assertGreater(offset, 0, "positive means the audio starts first")

    def test_normal_priming_passes_a_0_05_second_limit(self):
        report = probe("plain_ts", {"max_av_start_offset_s": 0.05})
        self.assertEqual(status(report, "max_av_start_offset_s"), "PASS")

    def test_default_limit_tolerates_priming(self):
        report = probe("plain_ts", {"max_av_start_offset_s": True})
        check = find(report, "max_av_start_offset_s")
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(check["expected"], qa_probe.DEFAULT_MAX_AV_START_OFFSET_S)
        self.assertEqual(qa_probe.DEFAULT_MAX_AV_START_OFFSET_S, 0.05)

    def test_a_tight_limit_fails_the_same_priming(self):
        # Negative control: the same file must fail when the limit is below one AAC frame.
        report = probe("plain_ts", {"max_av_start_offset_s": 0.01})
        self.assertEqual(status(report, "max_av_start_offset_s"), "FAIL")

    def test_half_second_audio_delay_fails_with_numeric_and_default_limit(self):
        for limit in (0.05, True):
            with self.subTest(limit):
                report = probe("delayed_audio", {"max_av_start_offset_s": limit})
                self.assertEqual(status(report, "max_av_start_offset_s"), "FAIL")
        offset = probe("delayed_audio").info["av_start_offset_s"]
        self.assertAlmostEqual(offset, -0.478, delta=0.01)
        self.assertLess(offset, 0, "negative means the audio starts after the video")

    def test_false_means_no_check(self):
        report = probe("delayed_audio", {"max_av_start_offset_s": False})
        self.assertNotIn("max_av_start_offset_s", [c["id"] for c in report.checks])

    def test_skipped_without_both_streams(self):
        for name in ("no_audio", "audio_only", "one_frame"):
            with self.subTest(name):
                report = probe(name, {"max_av_start_offset_s": 0.05})
                self.assertEqual(status(report, "max_av_start_offset_s"), "SKIP")


class ContainerDurationTests(unittest.TestCase):
    def test_mkv_container_duration_includes_audio_padding(self):
        report = probe("plain_mkv")
        self.assertIsNone(report.info["video"]["duration_s"])
        self.assertAlmostEqual(report.info["format"]["duration_s"], 4.021, delta=0.01)

    def test_default_tolerance_is_just_under_one_aac_frame_of_padding(self):
        self.assertEqual(status(probe("plain_mkv", {"duration_s": 4.0}), "duration_s"), "FAIL")
        self.assertEqual(status(probe("plain_mkv", {"duration_s": 4.0, "duration_tol_s": 0.05}), "duration_s"), "PASS")

    def test_hint_explains_padding_vs_a_long_audio_tail(self):
        padding = find(probe("plain_ts", {"duration_s": 4.0}), "duration_s")
        self.assertEqual(padding["status"], "FAIL")
        self.assertIn("audio padding", padding["detail"])
        tail = find(probe("delayed_audio", {"duration_s": 4.0}), "duration_s")
        self.assertEqual(tail["status"], "FAIL")
        self.assertIn("past the picture", tail["detail"])


# --------------------------------------------------------------------------- unknown keys and bad specs

class UnknownKeyTests(unittest.TestCase):
    def warnings(self, report):
        return [c for c in report.checks if c["id"] == "unknown_spec_key"]

    def test_unknown_top_level_key_warns(self):
        report = probe("good", {"video": {"width": 320}, "colour": {"space": "bt709"}})
        found = self.warnings(report)
        self.assertEqual([c["actual"] for c in found], ["colour"])
        self.assertEqual(found[0]["status"], "WARN")

    def test_unknown_nested_key_warns(self):
        found = self.warnings(probe("good", {"video": {"widht": 320}, "audio": {"rate": 48000}}))
        self.assertEqual(sorted(c["actual"] for c in found), ["audio.rate", "video.widht"])

    def test_flat_video_key_gets_a_hint(self):
        found = self.warnings(probe("good", {"width": 320}))
        self.assertIn('"video"', found[0]["detail"])

    def test_a_misspelt_key_names_the_nearest_documented_key(self):
        found = self.warnings(probe("good", {"duration": 4, "max_av_start_offset": 0.05, "colour": {"space": "bt709"}}))
        details = {c["actual"]: c["detail"] for c in found}
        self.assertIn('did you mean "duration_s"', details["duration"])
        self.assertIn('did you mean "max_av_start_offset_s"', details["max_av_start_offset"])
        nested = self.warnings(probe("good", {"video": {"widht": 320}}))
        self.assertIn('did you mean "width"', nested[0]["detail"])
        # Negative control: a key with nothing close gets no invented suggestion.
        other = self.warnings(probe("good", {"zzzzzz": 1}))
        self.assertNotIn("did you mean", other[0]["detail"])

    def test_a_sample_rate_in_khz_is_explained(self):
        check = find(probe("good", {"audio": {"sample_rate": 48}}), "audio.sample_rate")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("in Hz (48000 for 48 kHz)", check["detail"])
        # Negative controls: a wrong value in Hz gets no unit hint, and the right value passes.
        self.assertNotIn("in Hz", find(probe("good", {"audio": {"sample_rate": 44100}}), "audio.sample_rate")["detail"])
        self.assertEqual(status(probe("good", {"audio": {"sample_rate": 48000}}), "audio.sample_rate"), "PASS")

    def test_known_and_sibling_keys_do_not_warn(self):
        sibling = dict(FULL_SPEC, lufs_target=-23, true_peak_max_db=-1, no_clipping=True, max_silence_s=1,
                       max_leading_silence_s=1, max_trailing_silence_s=1, lufs_tol=1, barcode={"expect": "0-95"})
        report = probe("good", sibling)
        self.assertEqual(self.warnings(report), [])

    def test_unknown_key_makes_overall_warn_but_not_fail(self):
        report = probe("good", {"colour": 1})
        self.assertEqual(report.overall(), "WARN")
        self.assertEqual(report.exit_code(), 0)


class BadSpecTests(unittest.TestCase):
    def assert_usage_error(self, spec):
        with self.assertRaises(common.UsageError, msg=json.dumps(spec)):
            qa_probe.analyze(str(M["good"]), spec, TOOLS)

    def test_wrong_types_are_usage_errors(self):
        for spec in (
            {"video": {"width": "1920"}}, {"video": {"width": 1920.5}}, {"video": {"width": True}},
            {"video": {"fps": True}}, {"video": {"fps": "fast"}}, {"video": {"fps": 0}},
            {"video": {"codec": 264}}, {"video": {"even_dimensions": "yes"}}, {"video": {"cfr": "no"}},
            {"video": ["not", "an", "object"]}, {"audio": 5}, {"color": "bt709"},
            {"duration_s": "4"}, {"duration_s": 4, "duration_tol_s": -1}, {"max_av_start_offset_s": "0.05"},
            {"max_av_start_offset_s": None}, {"audio": {"present": "yes"}}, {"audio": {"channels": 2.0}},
        ):
            with self.subTest(spec):
                self.assert_usage_error(spec)

    def test_a_bad_spec_is_rejected_before_any_tool_runs(self):
        # The ffprobe path below does not exist: reaching it would raise OSError, not UsageError.
        tools = {"ffprobe": str(WORK / "no_such_ffprobe.exe")}
        for spec in ({"video": {"width": "1920"}}, {"audio": {"channels": 2.5}}, {"duration_s": "4"},
                     {"max_av_start_offset_s": None}, {"video": {"fps": "fast"}}):
            with self.subTest(spec):
                with self.assertRaises(common.UsageError):
                    qa_probe.analyze(str(M["good"]), spec, tools)

    def test_bool_is_not_accepted_as_a_number(self):
        # json true would otherwise be read as 1 second or 1 pixel.
        self.assert_usage_error({"duration_s": True})
        self.assert_usage_error({"duration_s": 4, "duration_tol_s": True})


# --------------------------------------------------------------------------- small helpers

class HelperTests(unittest.TestCase):
    def test_as_ratio(self):
        from fractions import Fraction
        self.assertEqual(qa_probe.as_ratio("24000/1001"), Fraction(24000, 1001))
        self.assertEqual(qa_probe.as_ratio("24/1"), 24)
        for bad in ("0/0", "N/A", "", None, "0/1", "-24/1", "abc"):
            self.assertIsNone(qa_probe.as_ratio(bad), bad)

    def test_numbers_that_ffprobe_leaves_out(self):
        self.assertIsNone(qa_probe.as_float("N/A"))
        self.assertIsNone(qa_probe.as_float(None))
        self.assertIsNone(qa_probe.as_float("nan"))
        self.assertEqual(qa_probe.as_float("4.021333"), 4.021333)
        self.assertIsNone(qa_probe.as_int("N/A"))
        self.assertEqual(qa_probe.as_int("96"), 96)

    def test_tags_are_matched_case_insensitively_and_filtered(self):
        tags = {"ENCODER": "Lavf", "Comment": "made by a test", "TITLE": "t", "creation_time": "2026-01-01T00:00:00Z",
                "com.example.private": "should not be copied"}
        self.assertEqual(qa_probe.pick_tags(tags), {"encoder": "Lavf", "comment": "made by a test", "title": "t",
                                                    "creation_time": "2026-01-01T00:00:00Z"})
        self.assertEqual(qa_probe.pick_tags(None), {})

    def test_untagged_words(self):
        for word in (None, "", "unknown", "Unspecified", "reserved", "N/A"):
            self.assertTrue(qa_probe.is_untagged(word), word)
        for word in ("bt709", "tv", "pc", "smpte170m"):
            self.assertFalse(qa_probe.is_untagged(word), word)

    def test_rotation_from_display_matrix_or_old_tag(self):
        self.assertEqual(qa_probe.stream_rotation({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}), -90)
        self.assertEqual(qa_probe.stream_rotation({"tags": {"rotate": "270"}}), 270)
        self.assertIsNone(qa_probe.stream_rotation({}))

    def test_rotated_file_reports_its_rotation(self):
        self.assertEqual(probe("rotated").info["video"]["rotation"], 90)
        self.assertIsNone(probe("good").info["video"]["rotation"])

    def test_width_and_height_checks_name_the_rotation_instead_of_a_bare_mismatch(self):
        # good.mp4 is stored 320x180; with a 90 degree flag players show it as 180x320.
        report = probe("rotated", {"video": {"width": 180, "height": 320}})
        for check_id in ("width", "height"):
            check = find(report, check_id)
            self.assertEqual(check["status"], "FAIL", check_id)
            self.assertIn("display rotation of 90 degrees", check["detail"])
            self.assertIn("players show it as 180x320", check["detail"])
            self.assertIn("compares the stored size", check["detail"])
        self.assertEqual(find(report, "width")["actual"], 320)
        # The stored size still passes, and the note is there to explain it too.
        report = probe("rotated", {"video": {"width": 320, "height": 180}})
        self.assertEqual([status(report, "width"), status(report, "height")], ["PASS", "PASS"])
        # Negative control: an unrotated file gets no rotation note.
        self.assertEqual(find(probe("good", {"video": {"width": 180}}), "width")["detail"], "")

    def test_rotation_note_for_a_half_turn_does_not_swap_the_sides(self):
        video = {"width": 320, "height": 180, "rotation": 180}
        note = qa_probe.rotation_note(video)
        self.assertIn("180 degrees", note)
        self.assertIn("unchanged", note)
        self.assertEqual(qa_probe.rotation_note(dict(video, rotation=None)), "")
        self.assertEqual(qa_probe.rotation_note(None), "")

    def test_text_copied_from_tags_is_cut_short(self):
        # A tag is text written by whoever made the file. It is kept as data and cut to a fixed length.
        title = probe("long_title").info["format"]["tags"]["title"]
        self.assertEqual(len(title), common.MAX_FILE_TEXT)
        self.assertTrue(title.endswith("..."))
        self.assertEqual(qa_probe.pick_tags({"title": "short"}), {"title": "short"})  # short text is untouched
        self.assertLess(len(json.dumps(probe("long_title").to_dict())), 20000, "the 5000 character title is not in the report")


# --------------------------------------------------------------------------- odd inputs

class RobustnessTests(unittest.TestCase):
    def test_path_with_spaces_and_parentheses(self):
        report = probe("spaces", FULL_SPEC)
        self.assertEqual(report.overall(), "PASS")
        self.assertIn("my clip (final).mp4", report.file)

    def test_non_ascii_file_name(self):
        report = probe("unicode_name", {"video": {"width": 320}})
        self.assertEqual(report.overall(), "PASS")
        proc = run_cli(M["unicode_name"], "--spec", write_spec({"video": {"width": 320}}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.strip(), "")
        json_proc = run_cli(M["unicode_name"], "--json")
        json_proc.stdout.encode("ascii")  # JSON escapes the name, so the output stays ASCII

    def test_relative_name_starting_with_a_dash_is_not_taken_for_an_option(self):
        shutil.copy(str(M["good"]), str(WORK / "-dash lead.mp4"))
        here = os.getcwd()
        os.chdir(str(WORK))
        try:
            report = qa_probe.analyze("-dash lead.mp4", {}, TOOLS)
        finally:
            os.chdir(here)
        self.assertIn(report.overall(), ("PASS", "WARN"))

    def test_single_frame_video_does_not_crash(self):
        report = probe("one_frame", {"video": {"frames": 1, "width": 320}})
        self.assertEqual(status(report, "frames"), "PASS")
        self.assertEqual(report.info["video"]["counted_frames"], 1)

    def test_no_audio_stream_is_reported_not_crashed(self):
        report = probe("no_audio")
        self.assertEqual(report.info["audio"], [])
        self.assertEqual(report.overall(), "WARN")  # untagged colour only

    def test_multiple_audio_streams_are_all_listed(self):
        report = probe("two_audio")
        self.assertEqual([a["sample_rate"] for a in report.info["audio"]], [48000, 44100])
        self.assertEqual(report.info["video"]["counted_frames"], 96)

    def test_every_fixture_gives_a_result_without_crashing(self):
        for name in M:
            if name in ("garbage", "empty"):
                continue
            with self.subTest(name):
                report = probe(name)
                self.assertIn(report.overall(), ("PASS", "WARN", "FAIL"))
                json.dumps(report.to_dict())

    def test_unreadable_files_are_usage_errors(self):
        for name in ("garbage", "empty"):
            with self.subTest(name):
                with self.assertRaises(common.UsageError) as caught:
                    qa_probe.analyze(str(M[name]), {}, TOOLS)
                self.assertIn("ffprobe could not read", str(caught.exception))

    def test_info_exposes_the_documented_facts(self):
        info = probe("good").info
        for key in ("format_name", "duration_s", "start_time_s", "tags"):
            self.assertIn(key, info["format"])
        for key in ("codec", "profile", "pix_fmt", "width", "height", "avg_frame_rate", "r_frame_rate", "field_order",
                    "color_range", "color_space", "color_transfer", "color_primaries", "rotation", "bit_rate",
                    "start_time_s", "duration_s", "counted_frames"):
            self.assertIn(key, info["video"])
        for key in ("codec", "sample_rate", "channels", "channel_layout", "start_time_s", "duration_s"):
            self.assertIn(key, info["audio"][0])
        self.assertIn("encoder", info["format"]["tags"])  # embedded provenance is visible
        self.assertEqual(info["audio"][0]["channel_layout"], "stereo")


# --------------------------------------------------------------------------- command line

SCRIPT = SCRIPTS / "qa_probe.py"


def run_cli(*args):
    cmd = [sys.executable, "-I", "-B", str(SCRIPT)] + [str(a) for a in args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def write_spec(spec, name="spec.json"):
    path = WORK / name
    path.write_text(json.dumps(spec))
    return str(path)


class CommandLineTests(unittest.TestCase):
    def test_exit_0_for_pass_and_for_warn_only(self):
        proc = run_cli(M["good"], "--spec", write_spec(FULL_SPEC))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout.splitlines()[0])
        warn = run_cli(M["png_plain"])
        self.assertEqual(warn.returncode, 0)
        self.assertIn("WARN", warn.stdout.splitlines()[0])

    def test_exit_1_for_a_failed_check(self):
        proc = run_cli(M["good"], "--spec", write_spec(merged(FULL_SPEC, {"video": {"width": 1920}})))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL", proc.stdout)

    def test_exit_2_when_it_could_not_run(self):
        cases = {
            "missing file": [WORK / "does_not_exist.mp4"],
            "directory": [WORK],
            "garbage file": [M["garbage"]],
            "empty file": [M["empty"]],
            "spec not json": [M["good"], "--spec", str(M["garbage"])],
            "spec missing": [M["good"], "--spec", str(WORK / "nope.json")],
            "spec wrong type": [M["good"], "--spec", write_spec({"video": {"width": "wide"}}, "bad_type.json")],
            "ffprobe path wrong": [M["good"], "--ffprobe", str(WORK / "no_such_ffprobe.exe")],
            "no arguments": [],
        }
        for label, args in cases.items():
            with self.subTest(label):
                proc = run_cli(*args)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                if label != "no arguments":
                    self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)

    def test_json_output_has_the_documented_shape(self):
        proc = run_cli(M["good"], "--spec", write_spec(FULL_SPEC), "--json")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(sorted(data), ["checks", "file", "info", "overall", "provenance", "tool"])
        self.assertEqual(data["tool"], "qa_probe.py")
        self.assertEqual(data["overall"], "PASS")
        for check in data["checks"]:
            self.assertEqual(sorted(check), ["actual", "detail", "expected", "id", "status"])
            self.assertIn(check["status"], ("PASS", "WARN", "FAIL", "SKIP"))
        # overall follows the rule: FAIL if any FAIL, else WARN if any WARN, else PASS
        statuses = {c["status"] for c in data["checks"]}
        self.assertNotIn("FAIL", statuses)
        self.assertNotIn("WARN", statuses)

    def test_json_overall_is_fail_when_a_check_fails(self):
        data = json.loads(run_cli(M["trunc_56"], "--json").stdout)
        self.assertEqual(data["overall"], "FAIL")
        self.assertIn("FAIL", [c["status"] for c in data["checks"]])

    def test_provenance_records_how_the_report_was_made(self):
        spec_path = write_spec(FULL_SPEC)
        data = json.loads(run_cli(M["good"], "--spec", spec_path, "--json").stdout)
        prov = data["provenance"]
        self.assertEqual(prov["script"], "qa_probe.py")
        self.assertEqual(prov["version"], common.VERSION)
        self.assertRegex(prov["python"], r"^3\.\d+\.\d+")
        self.assertTrue(prov["ffprobe_version"].startswith("ffprobe version"))
        self.assertTrue(prov["ffmpeg_version"].startswith("ffmpeg version"))
        self.assertEqual(prov["input"]["sha256"], hashlib.sha256(M["good"].read_bytes()).hexdigest())
        self.assertEqual(prov["input"]["size_bytes"], M["good"].stat().st_size)
        self.assertEqual(prov["spec_sha256"], hashlib.sha256(Path(spec_path).read_bytes()).hexdigest())
        self.assertGreaterEqual(len(prov["commands"]), 2)
        for command in prov["commands"]:
            self.assertIsInstance(command, list)
            self.assertTrue(all(isinstance(part, str) for part in command))
            self.assertIn("ffprobe", Path(command[0]).name.lower())

    def test_provenance_hash_changes_when_the_file_changes(self):
        first = json.loads(run_cli(M["good"], "--json").stdout)["provenance"]["input"]["sha256"]
        second = json.loads(run_cli(M["no_audio"], "--json").stdout)["provenance"]["input"]["sha256"]
        self.assertNotEqual(first, second)

    def test_without_spec_hash_is_null(self):
        data = json.loads(run_cli(M["good"], "--json").stdout)
        self.assertIsNone(data["provenance"]["spec_sha256"])

    def test_table_output_is_ascii_and_lists_every_check(self):
        proc = run_cli(M["good"], "--spec", write_spec(FULL_SPEC))
        proc.stdout.encode("ascii")
        self.assertEqual(sum(1 for line in proc.stdout.splitlines() if line.startswith("[")),
                         len(json.loads(run_cli(M["good"], "--spec", write_spec(FULL_SPEC), "--json").stdout)["checks"]))

    def test_nothing_is_written_next_to_the_input(self):
        before = sorted(p.name for p in WORK.iterdir())
        run_cli(M["good"], "--json")
        self.assertEqual(sorted(p.name for p in WORK.iterdir()), before)

    def test_help_exits_0(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--spec", proc.stdout)


if __name__ == "__main__":
    unittest.main()
