# Regression tests for the version 1.2 changes. Real ffmpeg and ffprobe; every clip is generated here.
#   D1  a spec key that was asked for and cannot be measured is never a PASS (NOT MEASURED, or a FAIL)
#   D2  frame_count_consistent, cfr and fps on files that record no length / no timestamps are NOT MEASURED
#   D3  loudness, peak, clipping and silence keys on a file without audio are a FAIL, not a SKIP
#   D4  video.fps on a GIF without gif.fps is NOT MEASURED
#   D5  no_clipping says what it measures (a true-peak ceiling), not "the audio clips"
#   D6  video_stream FAIL points an audio-only file to qa_audio.py
#   D10 run_qa.py puts the verdict and the Next steps before the long tables
#   D11 a spec file saved with a BOM, or as UTF-16, is read
#   D12 every unknown spec key gets its own Next steps entry; hints do not point at the wrong key;
#       a documented key is never reported as unknown
#   D13 the effective_duration row never shows two numbers that disagree next to a PASS
#   D15 the version is 1.2.2 (these changes came with 1.2.0; 1.2.1 and 1.2.2 changed no script behaviour)
#   Re-audit round:
#       every raw elementary stream (H.264, HEVC, MPEG-4, MPEG-2, MJPEG, AV1) has its cfr and fps NOT MEASURED;
#       a NOT MEASURED row gets advice that fits its cause (not "try a newer ffmpeg" for a file limit);
#       the legend says SKIP means no claim is made; a single script names the spec keys it did not evaluate;
#       an ambiguous or GIF unknown-key hint is right; spec_key_not_applied has one cell for both cases
# Every behaviour has a control that must keep its old answer, so a test cannot pass by being loose.
# Run:  python -I -B -m unittest discover -s tests/video-export-qa
import functools
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import qa_audio  # noqa: E402
import qa_gif  # noqa: E402
import qa_probe  # noqa: E402
import run_qa  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
TOOLS = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}

PICTURE = "testsrc2=size=320x180:rate=24:duration=2"
TONE = "sine=frequency=1000:sample_rate=48000:duration=2,volume=0.8,pan=stereo|c0=c0|c1=c0"  # -20 LUFS, -20 dBTP
# Colour tags written by the encoder, so that nothing but the condition under test can make a result a WARN.
TAGS = "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off"

WORK = None  # a scratch folder made in setUpModule and removed when the module is done
FFPROBE_WITHOUT_FRAME_TOTALS = None  # a stand-in ffprobe that leaves out nb_frames, like some older builds do for GIF

# A stand-in for an ffprobe that records no frame total: it runs the real ffprobe and removes "nb_frames" from the
# stream list. (Older ffprobe builds are said to leave it out for GIF; this makes that case testable on any build.)
SHIM_SCRIPT = "\n".join([
    "import json, subprocess, sys",
    "real, args = sys.argv[1], sys.argv[2:]",
    "proc = subprocess.run([real] + args, capture_output=True)",
    "out = proc.stdout",
    "if b'\"nb_frames\"' in out:",
    "    try:",
    "        data = json.loads(out.decode('utf-8'))",
    "        for stream in data.get('streams', []):",
    "            stream.pop('nb_frames', None)",
    "        out = json.dumps(data).encode('utf-8')",
    "    except ValueError:",
    "        pass",
    "sys.stdout.buffer.write(out)",
    "sys.stderr.buffer.write(proc.stderr)",
    "sys.exit(proc.returncode)",
    ""])

# The loudness, peak, clipping and silence keys: each needs an audio stream.
AUDIO_KEYS = {
    "lufs_target": -23, "true_peak_max_db": -1.0, "no_clipping": True,
    "max_silence_s": 1.0, "max_leading_silence_s": 1.0, "max_trailing_silence_s": 1.0,
}


# --------------------------------------------------------------------------- fixtures and helpers

def ffmpeg(*args, stdout_to=None):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    if stdout_to is None:
        proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    else:
        with open(str(stdout_to), "wb") as handle:
            proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE, cwd=str(WORK))
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def patch_gif_delays(src, dst, centiseconds):
    """Rewrite every Graphic Control Extension delay of a GIF (no encoder involved)."""
    data = bytearray(Path(src).read_bytes())
    marker = b"\x21\xf9\x04"
    position = data.find(marker)
    while position >= 0:
        data[position + 4:position + 6] = bytes([centiseconds, 0])
        position = data.find(marker, position + 1)
    Path(dst).write_bytes(bytes(data))


def make_ffprobe_shim():
    script = WORK / "ffprobe_without_frame_totals.py"
    script.write_text(SHIM_SCRIPT, encoding="ascii")
    if os.name == "nt":
        launcher = WORK / "ffprobe_without_frame_totals.cmd"
        launcher.write_text('@"%s" "%s" "%s" %%*\r\n' % (sys.executable, script, FFPROBE), encoding="ascii")
    else:
        launcher = WORK / "ffprobe_without_frame_totals"
        launcher.write_text('#!/bin/sh\nexec "%s" "%s" "%s" "$@"\n' % (sys.executable, script, FFPROBE), encoding="ascii")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher


def setUpModule():
    global WORK, FFPROBE_WITHOUT_FRAME_TOTALS
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK = Path(tempfile.mkdtemp(prefix="vexqa12_")).resolve()
    unittest.addModuleCleanup(shutil.rmtree, str(WORK), True)
    video = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-x264-params", TAGS]
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, *video, "-c:a", "aac", "-shortest", "tone.mp4")
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", *video, "noaudio.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-frames:v", "1", "-an", *video, "one_frame.mp4")
    # A raw H.264 stream carries no timestamps; a Matroska file written to a pipe records no length.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", *video, "-f", "h264", "raw.h264")
    ffmpeg("-i", "tone.mp4", "-an", "-c", "copy", "-f", "matroska", "pipe:1", stdout_to=WORK / "live.mkv")

    # Raw elementary streams of other codecs: ffprobe gives these evenly spaced timestamps that it made up (from a
    # frame rate it assumes or reads from a header inside the stream; the clip is 24 fps). The frame rate it prints is
    # 25/1 on ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1 and 24/1, 0/0 or 25/1 on 4.4.2, 5.1 and 5.1.9, depending on
    # the format. The H.264 stream above has no timestamps at all.
    for raw_name, codec, container in (("raw.m4v", "mpeg4", "m4v"), ("raw.m2v", "mpeg2video", "mpeg2video"),
                                       ("raw.mjpeg", "mjpeg", "mjpeg")):
        ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", codec, "-f", container, raw_name)
    for raw_name, codec, container in (("raw.hevc", "libx265", "hevc"), ("raw.obu", "libsvtav1", "obu")):
        try:  # these encoders are not in every ffmpeg build; their tests skip when the file is missing
            ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", codec, "-f", container, raw_name)
        except RuntimeError:
            pass
    # An Apple-style .m4v is an MP4 container (-f mp4) with a .m4v name: it records real timestamps and a length.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, *video, "-c:a", "aac", "-shortest", "-f", "mp4", "apple.m4v")
    # The same codecs in containers that record real timestamps: these must stay measured.
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", "mpeg2video", "-f", "mpegts", "mpeg2.ts")
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-an", "-c:v", "mpeg4", "-f", "avi", "mpeg4.avi")
    # An animated and a still image: formats that record no frame total.
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=1", "anim.apng")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-frames:v", "1", "still.png")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=2", "anim.gif")  # 20 frames of 10 cs
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1", "-c:a", "pcm_s16le", "audio_only.wav")
    # Full scale (peak 0 dBFS) and 1 dB below it.
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1,volume=8", "-c:a", "pcm_s16le", "full_scale.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=1,volume=7.1328", "-c:a", "pcm_s16le",
           "peak_minus_1.wav")
    code, _out, err = cli("make_test_clip.py", "barcode.mp4", "--frames", "24", "--fps", "24", "--size", "320x180")
    if code != 0:
        raise RuntimeError("make_test_clip.py failed: " + err)
    FFPROBE_WITHOUT_FRAME_TOTALS = make_ffprobe_shim()


def cli(script, *args):
    """Run a package script the way a user does (python -I -B), in the scratch folder."""
    cmd = [sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def write_spec(name, spec):
    (WORK / name).write_text(json.dumps(spec), encoding="utf-8")
    return name


def qa_json(media, spec=None, *extra):
    args = [media, "--format", "json"] + list(extra)
    if spec is not None:
        args += ["--spec", write_spec("spec_%d.json" % len(list(WORK.glob("spec_*.json"))), spec)]
    code, out, err = cli("run_qa.py", *args)
    if not out.strip():
        raise AssertionError("run_qa.py printed nothing, exit %d: %s" % (code, err))
    return code, json.loads(out)


def qa_markdown(media, spec=None, *extra):
    args = [media] + list(extra)
    if spec is not None:
        args += ["--spec", write_spec("spec_%d.json" % len(list(WORK.glob("spec_*.json"))), spec)]
    code, out, err = cli("run_qa.py", *args)
    return code, out.replace("\r\n", "\n"), err


@functools.lru_cache(maxsize=None)
def _probe(name, spec_json, gif_fps):
    extra = {} if gif_fps is None else {"gif_fps": gif_fps}  # the keyword exists only from 1.2 on
    return qa_probe.analyze(str(WORK / name), json.loads(spec_json), TOOLS, **extra)


def probe(name, spec=None, gif_fps=None):
    return _probe(name, json.dumps(spec or {}, sort_keys=True), gif_fps)


def audio(name, spec=None):
    return qa_audio.analyze(str(WORK / name), spec or {}, TOOLS)


def row(report_or_data, check_id):
    checks = report_or_data.checks if hasattr(report_or_data, "checks") else report_or_data["checks"]
    found = [c for c in checks if c["id"] == check_id]
    if not found:
        raise AssertionError("no check %r in %s" % (check_id, [c["id"] for c in checks]))
    return found[0]


def rows(data, check_id):
    return [c for c in data["checks"] if c["id"] == check_id]


# --------------------------------------------------------------------------- D15 the version

class VersionTests(unittest.TestCase):
    def test_the_version_is_1_2_2(self):
        self.assertEqual(common.VERSION, "1.2.2")

    def test_a_report_records_it(self):
        _code, data = qa_json("tone.mp4")
        self.assertEqual(data["provenance"]["version"], "1.2.2")
        _code, md, _err = qa_markdown("tone.mp4")
        self.assertIn("run_qa.py 1.2.2 on Python", md)


# --------------------------------------------------------------------------- D1, D2 nothing unmeasured is a PASS

class FileWithoutTimestampsOrLengthTests(unittest.TestCase):
    def test_fixture_guard_the_raw_stream_has_decoded_frames_and_no_timestamps(self):
        timing = qa_probe.read_frame_timing(FFPROBE, str(WORK / "raw.h264"), 0, common.Report("x.py"))
        self.assertGreater(timing["counted"], 1)
        self.assertEqual(timing["pts"], [])

    def test_cfr_of_a_raw_stream_is_not_measured_and_the_overall_result_is_a_warn(self):
        report = probe("raw.h264")
        check = row(report, "cfr")
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"), check)
        self.assertEqual(report.overall(), "WARN")

    def test_a_raw_stream_has_no_length_to_compare_with_either(self):
        report = probe("raw.h264")
        check = row(report, "frame_count_consistent")
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"), check)
        self.assertEqual(sorted(report.unmeasured_ids()), ["cfr", "frame_count_consistent"])

    def test_a_matroska_file_that_records_no_length_is_not_measured(self):
        report = probe("live.mkv")
        self.assertIsNone(report.info["video"]["duration_s"])
        self.assertIsNone(report.info["format"]["duration_s"])
        self.assertTrue(row(report, "frame_count_consistent").get("unmeasured"))
        self.assertEqual(row(report, "cfr")["status"], "PASS", "this file has timestamps, so cfr is measured")
        self.assertEqual(report.unmeasured_ids(), ["frame_count_consistent"])
        self.assertEqual(report.overall(), "WARN")

    def test_the_command_line_says_so_and_still_exits_zero(self):
        code, out, err = cli("qa_probe.py", "raw.h264")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("qa_probe.py: WARN", out)
        self.assertIn("NOT MEASURED", out)

    def test_run_qa_names_both_in_the_verdict_and_in_next_steps(self):
        code, md, err = qa_markdown("raw.h264")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("**Overall: WARN**", md)
        verdict = md.split("## Next steps")[0]
        self.assertIn("NOT MEASURED", verdict)
        self.assertIn("`cfr`", verdict)
        self.assertIn("`frame_count_consistent`", verdict)
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        self.assertIn("**cfr** (NOT MEASURED", steps)
        self.assertIn("**frame_count_consistent** (NOT MEASURED", steps)

    def test_fps_that_ffprobe_only_assumed_for_a_raw_stream_is_not_measured(self):
        # ffprobe prints some rate for a raw stream that was really made at 24: an assumed 25/1 (ffmpeg 6.1.1 and
        # the newer builds measured) or the 24/1 that the encoder stored in the stream (4.4.2, 5.1, 5.1.9). Neither
        # one is measured, so it is neither a PASS (spec 25) nor a FAIL (spec 24) whatever the build prints.
        video = probe("raw.h264").info["video"]
        self.assertIsNotNone(qa_probe.as_ratio(video["avg_frame_rate"]) or qa_probe.as_ratio(video["r_frame_rate"]),
                             "ffprobe prints some rate for the stream")
        for wanted in (25, 24):
            with self.subTest(spec_fps=wanted):
                report = probe("raw.h264", {"video": {"fps": wanted}})
                check = row(report, "fps")
                self.assertEqual(check["status"], "SKIP", check)
                self.assertTrue(check.get("unmeasured"), check)
                self.assertIn("no timestamps", check["detail"])
                self.assertEqual(report.overall(), "WARN")

    # ---- controls: these keep their old answers
    def test_control_a_one_frame_video_stays_a_pass(self):
        report = probe("one_frame.mp4")
        self.assertEqual(report.info["video"]["counted_frames"], 1)
        self.assertEqual(report.overall(), "PASS")
        self.assertEqual(report.unmeasured_ids(), [])
        check = row(report, "cfr")
        self.assertEqual(check["status"], "SKIP", "one frame has no rate to judge")
        self.assertIn("at least 2 frames", check["detail"])
        self.assertNotIn("unmeasured", check)
        self.assertEqual(row(report, "frame_count_consistent")["status"], "PASS")

    def test_control_the_frame_rate_of_a_one_frame_video_is_still_measured(self):
        report = probe("one_frame.mp4", {"video": {"fps": 24}})
        check = row(report, "fps")
        self.assertEqual(check["status"], "PASS", check)
        self.assertEqual(report.overall(), "PASS")

    def test_control_a_normal_mp4_measures_every_always_on_check(self):
        report = probe("tone.mp4", {"video": {"fps": 24}})
        self.assertEqual(report.overall(), "PASS")
        self.assertEqual(report.unmeasured_ids(), [])
        for check_id in ("cfr", "frame_count_consistent", "fps", "color_tags"):
            self.assertEqual(row(report, check_id)["status"], "PASS", check_id)

    def test_control_a_gif_keeps_its_always_on_checks_as_they_were(self):
        report = probe("anim.gif")
        self.assertEqual(report.unmeasured_ids(), [])
        self.assertEqual(row(report, "cfr")["status"], "PASS")
        self.assertEqual(row(report, "frame_count_consistent")["status"], "PASS")
        self.assertEqual(report.overall(), "PASS")

    def test_control_an_audio_only_file_is_still_not_a_video_and_its_cfr_is_still_by_design(self):
        report = probe("audio_only.wav")
        self.assertEqual(row(report, "cfr")["status"], "SKIP")
        self.assertNotIn("unmeasured", row(report, "cfr"))


class GifOnAnFfprobeWithoutFrameTotalsTests(unittest.TestCase):
    """Some ffprobe builds leave nb_frames out for a GIF. A GIF has no frame rate to work a length out from, so the
    check would have nothing to compare with and every GIF would become a WARN. It is exempt (qa_gif.py counts the
    image frames); a normal video on the same ffprobe is still compared, with the length it records."""
    TOOLS = None

    @classmethod
    def setUpClass(cls):
        cls.TOOLS = {"ffmpeg": FFMPEG, "ffprobe": str(FFPROBE_WITHOUT_FRAME_TOTALS)}

    def test_fixture_guard_the_stand_in_really_removes_the_frame_total(self):
        real = qa_probe.analyze(str(WORK / "anim.gif"), {}, TOOLS)
        without = qa_probe.analyze(str(WORK / "anim.gif"), {}, self.TOOLS)
        self.assertEqual(real.info["video"]["nb_frames_metadata"], 20)
        self.assertIsNone(without.info["video"]["nb_frames_metadata"])

    def test_a_gif_without_a_frame_total_stays_a_pass(self):
        report = qa_probe.analyze(str(WORK / "anim.gif"), {}, self.TOOLS)
        check = row(report, "frame_count_consistent")
        self.assertEqual(check["status"], "SKIP")
        self.assertNotIn("unmeasured", check)
        self.assertEqual(report.unmeasured_ids(), [])
        self.assertEqual(report.overall(), "PASS")

    def test_control_a_video_on_the_same_ffprobe_is_still_compared_with_the_length_it_records(self):
        report = qa_probe.analyze(str(WORK / "tone.mp4"), {}, self.TOOLS)
        self.assertIsNone(report.info["video"]["nb_frames_metadata"])
        self.assertEqual(row(report, "frame_count_consistent")["status"], "PASS")
        self.assertEqual(report.overall(), "PASS")

    def test_control_a_raw_stream_on_the_same_ffprobe_is_still_not_measured(self):
        report = qa_probe.analyze(str(WORK / "raw.h264"), {}, self.TOOLS)
        self.assertTrue(row(report, "frame_count_consistent").get("unmeasured"))


class FrameCountDecisionTests(unittest.TestCase):
    """The decision points on their own, without a file."""
    VIDEO = {"codec": "h264", "width": 320, "height": 180, "nb_frames_metadata": None, "duration_s": None,
             "duration_tag_s": None, "avg_frame_rate": "24/1", "r_frame_rate": "24/1"}

    def frame_count(self, video, counted=48, fmt_duration=None):
        report = common.Report("qa_probe.py")
        qa_probe.add_frame_count_check(report, video, {"error": None, "counted": counted, "pts": []}, fmt_duration, 0)
        return row(report, "frame_count_consistent")

    def test_no_recorded_length_is_not_measured(self):
        check = self.frame_count(self.VIDEO)
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"))
        self.assertIn("would prove nothing", check["detail"])

    def test_no_frame_rate_at_all_is_not_measured_either(self):
        check = self.frame_count(dict(self.VIDEO, avg_frame_rate="0/0", r_frame_rate="0/0"), fmt_duration=2.0)
        self.assertTrue(check.get("unmeasured"))

    def test_control_a_gif_that_an_older_ffprobe_gives_no_frame_total_is_exempt_and_says_why(self):
        check = self.frame_count(dict(self.VIDEO, codec="gif"))
        self.assertEqual(check["status"], "SKIP")
        self.assertNotIn("unmeasured", check)
        self.assertIn("qa_gif.py", check["detail"])

    def test_control_a_recorded_length_is_compared(self):
        check = self.frame_count(dict(self.VIDEO, nb_frames_metadata=48))
        self.assertEqual(check["status"], "PASS")

    def cfr(self, timing):
        report = common.Report("qa_probe.py")
        qa_probe.add_cfr_check(report, {"codec": "h264", "time_base": "1/24"}, timing, {})
        return row(report, "cfr")

    def test_frames_without_timestamps_are_not_measured(self):
        for pts in ([], [0]):
            with self.subTest(timestamps=len(pts)):
                check = self.cfr({"error": None, "counted": 5, "pts": pts})
                self.assertEqual(check["status"], "SKIP")
                self.assertTrue(check.get("unmeasured"))

    def test_control_one_frame_with_or_without_a_timestamp_is_by_design(self):
        for pts in ([], [0]):
            with self.subTest(timestamps=len(pts)):
                check = self.cfr({"error": None, "counted": 1, "pts": pts})
                self.assertEqual(check["status"], "SKIP")
                self.assertNotIn("unmeasured", check)

    def test_control_two_frames_with_timestamps_are_judged(self):
        self.assertEqual(self.cfr({"error": None, "counted": 2, "pts": [0, 1]})["status"], "PASS")


# --------------------------------------------------------------------------- D4 GIF speed

class GifSpeedTests(unittest.TestCase):
    def test_video_fps_for_a_gif_without_gif_fps_is_not_measured(self):
        code, data = qa_json("anim.gif", {"video": {"fps": 10}})
        check = row(data, "fps")
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"), check)
        self.assertIn("gif.fps", check["detail"])
        self.assertEqual(data["overall"], "WARN")
        self.assertEqual(code, 0)

    def test_the_same_in_qa_probe_alone_because_it_has_no_gif_speed_check(self):
        report = probe("anim.gif", {"video": {"fps": 10}})
        self.assertTrue(row(report, "fps").get("unmeasured"))

    def test_control_with_gif_fps_as_well_the_speed_is_measured(self):
        code, data = qa_json("anim.gif", {"video": {"fps": 10}, "gif": {"fps": 10}})
        check = row(data, "fps")
        self.assertEqual(check["status"], "SKIP")
        self.assertNotIn("unmeasured", check)
        self.assertIn("rounding_drift", check["detail"])
        self.assertEqual(row(data, "rounding_drift")["status"], "PASS")
        self.assertEqual(data["overall"], "PASS")
        self.assertEqual(code, 0)

    def test_control_a_wrong_speed_is_still_found_when_both_are_given(self):
        _code, data = qa_json("anim.gif", {"video": {"fps": 24}, "gif": {"fps": 24}})
        self.assertEqual(row(data, "rounding_drift")["status"], "FAIL")
        self.assertEqual(data["overall"], "FAIL")

    def test_video_fps_and_gif_fps_that_disagree_leave_video_fps_unmeasured(self):
        _code, data = qa_json("anim.gif", {"video": {"fps": 24}, "gif": {"fps": 10}})
        check = row(data, "fps")
        self.assertTrue(check.get("unmeasured"), check)
        self.assertIn("24", check["detail"])
        self.assertIn("10", check["detail"])

    def test_the_same_rate_written_two_ways_counts_as_the_same(self):
        report = probe("anim.gif", {"video": {"fps": 10}}, gif_fps="10/1")
        self.assertNotIn("unmeasured", row(report, "fps"))
        report = probe("anim.gif", {"video": {"fps": "24000/1001"}}, gif_fps=23.976)
        self.assertNotIn("unmeasured", row(report, "fps"))

    def test_control_a_video_with_the_wrong_frame_rate_is_still_a_fail(self):
        report = probe("tone.mp4", {"video": {"fps": 25}})
        self.assertEqual(row(report, "fps")["status"], "FAIL")
        self.assertNotIn("unmeasured", row(report, "fps"))


# --------------------------------------------------------------------------- D3 audio keys without audio

class NoAudioStreamTests(unittest.TestCase):
    def test_every_audio_key_is_a_fail_when_the_file_has_no_audio(self):
        for key, value in AUDIO_KEYS.items():
            with self.subTest(key=key):
                report = audio("noaudio.mp4", {key: value})
                check = row(report, key)
                self.assertEqual(check["status"], "FAIL", check)
                self.assertEqual(check["detail"], "no audio stream, so the requested %s cannot be met" % key)
                self.assertNotIn("unmeasured", check)
                self.assertEqual(report.overall(), "FAIL")
                self.assertEqual(report.exit_code(), 1)

    def test_the_failure_keeps_the_requirement_in_the_expected_column(self):
        check = row(audio("noaudio.mp4", {"max_silence_s": 1.0}), "max_silence_s")
        self.assertEqual(check["expected"], 1.0)
        self.assertIsNone(check["actual"])
        lufs = row(audio("noaudio.mp4", {"lufs_target": -23, "lufs_tol": 0.5}), "lufs_target")
        self.assertEqual(lufs["expected"], {"target": -23, "tol": 0.5})

    def test_the_always_on_check_nobody_asked_for_stays_a_skip_by_design(self):
        report = audio("noaudio.mp4", {"lufs_target": -23})
        silent = row(report, "not_silent")
        self.assertEqual(silent["status"], "SKIP")
        self.assertNotIn("unmeasured", silent)

    def test_control_without_a_spec_nothing_was_measured_and_it_is_not_an_error(self):
        report = audio("noaudio.mp4")
        self.assertEqual([c["id"] for c in report.checks], ["not_silent"])
        self.assertEqual(report.overall(), "SKIP")
        self.assertEqual(report.exit_code(), 0)
        code, out, err = cli("qa_audio.py", "noaudio.mp4")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Nothing was measured", out)

    def test_control_keys_that_ask_for_nothing_add_no_row(self):
        report = audio("noaudio.mp4", {"no_clipping": False, "lufs_tol": 0.5})
        self.assertEqual([c["id"] for c in report.checks], ["not_silent"])

    def test_control_the_same_keys_on_a_file_with_audio_are_measured(self):
        report = audio("tone.mp4", {"lufs_target": -20, "lufs_tol": 1, "true_peak_max_db": -1.0, "no_clipping": True,
                                    "max_silence_s": 1.0, "max_leading_silence_s": 1.0, "max_trailing_silence_s": 1.0})
        for key in AUDIO_KEYS:
            self.assertEqual(row(report, key)["status"], "PASS", key)
        self.assertEqual(report.overall(), "PASS")

    def test_control_a_stream_that_could_not_be_measured_is_still_not_measured_not_failed(self):
        report = common.Report("qa_audio.py")
        qa_audio.add_lufs_check(report, {"lufs_target": -20, "lufs_tol": 1}, None, "ffmpeg could not measure the audio: x")
        qa_audio.add_limit_check(report, "max_silence_s", 1.0, None, "ffmpeg could not measure the audio: x", "silence")
        qa_audio.add_clipping_check(report, None, "ffmpeg could not measure the audio: x")
        for check in report.checks:
            self.assertEqual(check["status"], "SKIP", check)
            self.assertTrue(check.get("unmeasured"), check)

    def test_run_qa_ends_in_a_fail_with_exit_code_1_and_next_steps_for_each_key(self):
        code, data = qa_json("noaudio.mp4", AUDIO_KEYS)
        self.assertEqual((code, data["overall"]), (1, "FAIL"))
        for key in AUDIO_KEYS:
            self.assertEqual(row(data, key)["status"], "FAIL", key)
        code, md, _err = qa_markdown("noaudio.mp4", AUDIO_KEYS)
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        for key in AUDIO_KEYS:
            self.assertIn("**%s** (FAIL" % key, steps)
        self.assertNotIn("Nothing to fix", steps)

    def test_control_no_audio_key_requested_on_a_file_without_audio_is_a_pass(self):
        code, data = qa_json("noaudio.mp4")
        self.assertEqual((code, data["overall"]), (0, "PASS"))
        self.assertEqual(row(data, "not_silent")["status"], "SKIP")
        self.assertEqual(row(data, "av_duration_match")["status"], "SKIP")
        self.assertFalse([c for c in data["checks"] if c.get("unmeasured")])

    def test_control_a_normal_mp4_with_audio_and_a_loudness_spec_is_a_pass(self):
        code, data = qa_json("tone.mp4", {"audio": {"present": True}, "lufs_target": -20, "lufs_tol": 1,
                                          "true_peak_max_db": -1.0, "video": {"fps": 24}})
        self.assertEqual((code, data["overall"]), (0, "PASS"), [(c["id"], c["status"]) for c in data["checks"]])
        self.assertFalse([c for c in data["checks"] if c.get("unmeasured")])


# --------------------------------------------------------------------------- D1b the start offset is never a PASS unless compared

class StartOffsetNeedsBothStreamsTests(unittest.TestCase):
    """max_av_start_offset_s compares the start time of a picture stream with that of a sound stream. On a file that lacks
    one of them nothing is compared. The first 1.2 build left that as a plain SKIP, so the run ended in Overall PASS with
    "Nothing to fix" for a limit nobody checked. It is NOT MEASURED now (Overall at least WARN). The one case that stays a
    plain SKIP is a spec that itself says the file has no audio (audio.present false): the limit is then vacuous."""

    def test_a_limit_on_a_file_without_audio_is_not_measured_and_the_overall_result_is_a_warn(self):
        for limit in (True, 0.1):
            with self.subTest(limit=limit):
                code, data = qa_json("noaudio.mp4", {"max_av_start_offset_s": limit})
                check = row(data, "max_av_start_offset_s")
                self.assertEqual(check["status"], "SKIP")
                self.assertTrue(check.get("unmeasured"), check)
                self.assertIn("no audio stream", check["detail"])
                self.assertTrue(check.get("remedy"), "the row carries advice that fits its cause")
                self.assertEqual((code, data["overall"]), (0, "WARN"))

    def test_the_report_lists_it_in_the_verdict_and_in_next_steps_instead_of_saying_nothing_to_fix(self):
        code, md, _err = qa_markdown("noaudio.mp4", {"max_av_start_offset_s": True})
        verdict, rest = md.split("## Next steps")
        self.assertIn("**Overall: WARN**", verdict)
        self.assertIn("`max_av_start_offset_s`", verdict.split("**Overall: WARN**")[1])
        steps = rest.split("## What was checked")[0]
        self.assertIn("**max_av_start_offset_s** (NOT MEASURED", steps)
        self.assertNotIn("Nothing to fix", steps)
        self.assertIn("What to do:", steps.split("**max_av_start_offset_s**")[1])

    def test_qa_probe_on_its_own_says_so_too(self):
        report = probe("noaudio.mp4", {"max_av_start_offset_s": True})
        self.assertEqual(report.unmeasured_ids(), ["max_av_start_offset_s"])
        self.assertEqual(report.overall(), "WARN")
        code, out, err = cli("qa_probe.py", "noaudio.mp4", "--spec", write_spec("spec_offset_only.json", {"max_av_start_offset_s": True}))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("NOT MEASURED (a check that applies could not be measured, so the result is not a PASS): "
                      "max_av_start_offset_s", out)
        self.assertNotIn("Skipped, no claim made", out, "this SKIP is not one made by design")

    def test_a_file_without_a_picture_is_not_measured_either(self):
        report = probe("audio_only.wav", {"max_av_start_offset_s": True})
        check = row(report, "max_av_start_offset_s")
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"), check)
        self.assertIn("no video stream", check["detail"])
        self.assertEqual(report.overall(), "FAIL", "video_stream fails on an audio-only file anyway")

    def test_audio_present_false_makes_the_limit_vacuous_and_a_plain_skip(self):
        code, data = qa_json("noaudio.mp4", {"max_av_start_offset_s": True, "audio": {"present": False}})
        check = row(data, "max_av_start_offset_s")
        self.assertEqual(check["status"], "SKIP")
        self.assertNotIn("unmeasured", check)
        self.assertIn("audio.present", check["detail"])
        self.assertEqual(row(data, "audio.present")["status"], "PASS")
        self.assertEqual((code, data["overall"]), (0, "PASS"))
        _code, md, _err = qa_markdown("noaudio.mp4", {"max_av_start_offset_s": 0.1, "audio": {"present": False}})
        self.assertNotIn("(NOT MEASURED", md)

    def test_a_vacuous_limit_is_listed_among_the_checks_skipped_by_design(self):
        spec = {"max_av_start_offset_s": True, "audio": {"present": False}}
        code, out, err = cli("qa_probe.py", "noaudio.mp4", "--spec", write_spec("spec_vacuous.json", spec))
        self.assertEqual((code, err), (0, ""))
        line = [l for l in out.splitlines() if l.startswith("Skipped, no claim made")][0]
        self.assertIn("max_av_start_offset_s", line)
        self.assertNotIn("NOT MEASURED (", out)

    def test_control_audio_present_true_or_other_audio_keys_do_not_make_the_limit_vacuous(self):
        for audio_spec in ({"present": True}, {"sample_rate": 48000}, {"channels": 2}):
            with self.subTest(audio=audio_spec):
                code, data = qa_json("noaudio.mp4", {"max_av_start_offset_s": True, "audio": audio_spec})
                self.assertTrue(row(data, "max_av_start_offset_s").get("unmeasured"))
                self.assertEqual((code, data["overall"]), (1, "FAIL"), "the audio key itself fails: there is no audio")

    def test_control_a_file_with_audio_is_measured_whatever_audio_present_says(self):
        code, data = qa_json("tone.mp4", {"max_av_start_offset_s": 0.1})
        check = row(data, "max_av_start_offset_s")
        self.assertEqual(check["status"], "PASS")
        self.assertNotIn("unmeasured", check)
        self.assertEqual((code, data["overall"]), (0, "PASS"))
        code, data = qa_json("tone.mp4", {"max_av_start_offset_s": 0.1, "audio": {"present": False}})
        check = row(data, "max_av_start_offset_s")
        self.assertEqual(check["status"], "PASS", "the file has audio, so the start times are compared")
        self.assertEqual(row(data, "audio.present")["status"], "FAIL")

    def test_control_a_start_time_the_file_does_not_report_is_still_not_measured_with_its_own_advice(self):
        report = common.Report("qa_probe.py")
        video = {"codec": "h264"}
        qa_probe.add_av_offset_check(report, True, video, [{"codec": "aac"}], None)
        unreported = row(report, "max_av_start_offset_s")
        self.assertTrue(unreported.get("unmeasured"))
        report = common.Report("qa_probe.py")
        qa_probe.add_av_offset_check(report, True, video, [], None)
        missing = row(report, "max_av_start_offset_s")
        self.assertTrue(missing.get("unmeasured"))
        self.assertNotEqual(unreported["remedy"], missing["remedy"], "a missing stream is not fixed by a different container")
        self.assertNotEqual(unreported["detail"], missing["detail"])

    def test_control_false_is_still_no_check_at_all(self):
        report = probe("noaudio.mp4", {"max_av_start_offset_s": False})
        self.assertNotIn("max_av_start_offset_s", [c["id"] for c in report.checks])
        self.assertEqual(report.overall(), "PASS")


# --------------------------------------------------------------------------- D5 no_clipping wording

class NoClippingWordingTests(unittest.TestCase):
    def test_the_failure_names_the_ceiling_and_does_not_claim_clipping(self):
        check = row(audio("full_scale.wav", {"no_clipping": True}), "no_clipping")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("at or above the -0.1 dBTP ceiling (clip risk)", check["detail"])
        for claim in ("the audio clips", "at or above full scale"):
            self.assertNotIn(claim, check["detail"])

    def test_control_audio_below_the_ceiling_passes_without_that_wording(self):
        check = row(audio("peak_minus_1.wav", {"no_clipping": True}), "no_clipping")
        self.assertEqual(check["status"], "PASS")
        self.assertNotIn("clip risk", check["detail"])
        self.assertIn("ceiling", check["detail"])


# --------------------------------------------------------------------------- D6 audio-only files

class AudioOnlyTests(unittest.TestCase):
    def test_the_video_stream_failure_points_to_qa_audio(self):
        code, data = qa_json("audio_only.wav")
        check = row(data, "video_stream")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("(for an audio-only file use qa_audio.py)", check["detail"])
        self.assertEqual((code, data["overall"]), (1, "FAIL"))

    def test_the_cover_art_case_says_the_same(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_video_stream_check(report, None, 0, 1)
        detail = row(report, "video_stream")["detail"]
        self.assertIn("only cover art is attached", detail)
        self.assertIn("qa_audio.py", detail)

    def test_control_a_file_with_a_picture_gets_no_such_pointer(self):
        check = row(probe("tone.mp4"), "video_stream")
        self.assertEqual(check["status"], "PASS")
        self.assertNotIn("qa_audio.py", check["detail"])

    def test_control_qa_audio_still_passes_an_audio_only_file(self):
        code, out, err = cli("qa_audio.py", "audio_only.wav")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("qa_audio.py: PASS", out)


# --------------------------------------------------------------------------- D10 report order

class ReportOrderTests(unittest.TestCase):
    ORDER = ["# Video export QA report", "**Overall:", "## Next steps", "## What was checked", "## Summary", "## Checks",
             "## Barcode runs", "## How this report was made"]

    def report(self):
        # a wrong width gives one FAIL; the barcode clip gives the Barcode runs section
        return qa_markdown("barcode.mp4", {"video": {"width": 1920}}, "--barcode-expect", "0-23")

    def test_verdict_and_next_steps_come_before_the_tables_the_provenance_and_the_barcode_runs(self):
        code, md, err = self.report()
        self.assertEqual((code, err), (1, ""))
        positions = [md.find(marker) for marker in self.ORDER]
        self.assertTrue(all(p >= 0 for p in positions), positions)
        self.assertEqual(positions, sorted(positions))

    def test_a_report_cut_off_at_the_provenance_block_still_has_what_a_reader_acts_on(self):
        _code, md, _err = self.report()
        head = md[:md.index("## What was checked")]
        self.assertIn("**Overall: FAIL**", head)
        self.assertIn("**width** (FAIL", head)
        self.assertIn("   - What to do:", head)

    def test_every_row_and_the_provenance_are_still_in_the_report(self):
        _code, md, _err = self.report()
        _code, data = qa_json("barcode.mp4", {"video": {"width": 1920}}, "--barcode-expect", "0-23")
        table = md.split("## Checks")[1].split("## Barcode runs")[0]
        table_rows = [line for line in table.splitlines() if line.startswith("| ") and "`" in line]
        self.assertEqual(len(table_rows), len(data["checks"]))
        self.assertIn("SHA-256 of the file", md)
        self.assertIn("run_qa.py 1.2.2 on Python", md)
        self.assertIn("output 0-23 -> source 0-23", md)

    def test_the_note_under_the_table_makes_only_claims_that_are_true(self):
        _code, md, _err = self.report()
        note = md.split("## Checks")[1].split("## Barcode runs")[0]
        self.assertIn("complete text of every FAIL, WARN and NOT MEASURED row is repeated under Next steps", note)
        self.assertIn("report.json", note)

    def test_control_without_a_barcode_the_order_is_the_same_minus_the_runs(self):
        _code, md, _err = qa_markdown("tone.mp4")
        self.assertNotIn("## Barcode runs", md)
        order = [marker for marker in self.ORDER if marker != "## Barcode runs"]
        positions = [md.find(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(min(positions), -1)


# --------------------------------------------------------------------------- D11 spec file encodings

class SpecFileEncodingTests(unittest.TestCase):
    TEXT = '{"video": {"width": 320}}\n'

    def run_probe(self, raw):
        (WORK / "spec_enc.json").write_bytes(raw)
        return cli("qa_probe.py", "tone.mp4", "--spec", "spec_enc.json", "--json")

    def test_utf8_with_a_byte_order_mark_is_read(self):
        code, out, err = self.run_probe(b"\xef\xbb\xbf" + self.TEXT.encode("utf-8"))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(row(json.loads(out), "width")["status"], "PASS")

    def test_utf16_as_windows_powershell_writes_it_is_read(self):
        for label, raw in (("little endian", self.TEXT.encode("utf-16")),                      # BOM FF FE
                           ("big endian", b"\xfe\xff" + self.TEXT.encode("utf-16-be"))):
            with self.subTest(label):
                code, out, err = self.run_probe(raw)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(row(json.loads(out), "width")["status"], "PASS")

    def test_the_recorded_hash_is_the_hash_of_the_bytes_as_given(self):
        raw = self.TEXT.encode("utf-16")
        _code, out, _err = self.run_probe(raw)
        self.assertEqual(json.loads(out)["provenance"]["spec_sha256"], hashlib.sha256(raw).hexdigest())

    def test_every_script_that_takes_a_spec_reads_them(self):
        raw = b"\xef\xbb\xbf" + b'{"lufs_target": -20, "lufs_tol": 3}\n'
        (WORK / "spec_enc.json").write_bytes(raw)
        for script in ("qa_audio.py", "run_qa.py"):
            with self.subTest(script):
                code, out, err = cli(script, "tone.mp4", "--spec", "spec_enc.json", "--json")
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(row(json.loads(out), "lufs_target")["status"], "PASS")

    def test_control_plain_utf8_still_works(self):
        code, out, err = self.run_probe(self.TEXT.encode("utf-8"))
        self.assertEqual((code, err), (0, ""))

    def test_text_in_another_encoding_is_refused_with_a_clear_message(self):
        code, out, err = self.run_probe('{"title": "caf\xe9"}'.encode("latin-1"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("spec file spec_enc.json is not UTF-8 text", err)
        self.assertIn("save it as UTF-8", err)
        self.assertNotIn("codec", err)

    def test_utf16_without_a_byte_order_mark_and_utf32_are_refused_with_a_clear_message(self):
        code, _out, err = self.run_probe(self.TEXT.encode("utf-16-le"))
        self.assertEqual(code, 2)
        self.assertIn("byte order mark", err)
        code, _out, err = self.run_probe(self.TEXT.encode("utf-32"))
        self.assertEqual(code, 2)
        self.assertIn("UTF-32", err)

    def test_control_a_file_that_is_not_json_is_still_reported_as_not_json(self):
        code, _out, err = self.run_probe(b"{nope")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_load_spec_returns_the_dictionary_and_the_hash(self):
        (WORK / "spec_enc.json").write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}')
        spec, digest = common.load_spec(str(WORK / "spec_enc.json"))
        self.assertEqual(spec, {"a": 1})
        self.assertEqual(digest, hashlib.sha256(b"\xef\xbb\xbf" + b'{"a": 1}').hexdigest())


# --------------------------------------------------------------------------- D12 unknown spec keys

class UnknownKeyHintTests(unittest.TestCase):
    def warnings(self, spec):
        report = common.Report("qa_probe.py")
        qa_probe.add_unknown_key_warnings(report, spec)
        return {c["actual"]: c["detail"] for c in report.checks}

    def test_hints_point_at_the_key_that_was_meant(self):
        found = self.warnings({"video": {"framerate": 24}, "lufs": -23, "audio_codec": "aac", "audio": {"rate": 48000}})
        self.assertIn('did you mean "fps"', found["video.framerate"])
        self.assertNotIn('"frames"', found["video.framerate"])
        self.assertIn('did you mean "lufs_target"', found["lufs"])
        self.assertNotIn("lufs_tol", found["lufs"])
        self.assertIn('did you mean "audio": {"codec"', found["audio_codec"])
        self.assertNotIn('did you mean "audio"?', found["audio_codec"])
        self.assertIn('did you mean "sample_rate"', found["audio.rate"])

    def test_more_words_people_use(self):
        found = self.warnings({"loudness": -23, "truepeak": -1, "leading_silence": 1, "silence": 1, "clipping": True,
                               "frame_rate": 24, "present": True, "range": "tv"})
        self.assertIn('"lufs_target"', found["loudness"])
        self.assertIn('"true_peak_max_db"', found["truepeak"])
        self.assertIn('"max_leading_silence_s"', found["leading_silence"])
        self.assertIn('"max_silence_s"', found["silence"])
        self.assertIn('"no_clipping"', found["clipping"])
        self.assertIn('"video": {"fps"', found["frame_rate"])
        self.assertIn('"audio": {"present"', found["present"])
        self.assertIn('"color": {"range"', found["range"])

    def test_a_key_in_the_wrong_section_names_the_section_it_belongs_to(self):
        found = self.warnings({"video": {"sample_rate": 48000}, "audio": {"width": 320}})
        self.assertIn('"audio.sample_rate"', found["video.sample_rate"])
        self.assertIn('"video.width"', found["audio.width"])

    def test_control_plain_misspellings_still_get_the_nearest_key(self):
        found = self.warnings({"duration": 4, "max_av_start_offset": 0.05, "colour": {"space": "bt709"},
                               "video": {"widht": 320}})
        self.assertIn('did you mean "duration_s"', found["duration"])
        self.assertIn('did you mean "max_av_start_offset_s"', found["max_av_start_offset"])
        self.assertIn('did you mean "color"', found["colour"])
        self.assertIn('did you mean "width"', found["video.widht"])
        self.assertIn('"video"', self.warnings({"width": 320})["width"])

    def test_control_a_key_with_nothing_close_gets_no_invented_suggestion(self):
        self.assertNotIn("did you mean", self.warnings({"zzzzzz": 1})["zzzzzz"])
        self.assertNotIn("did you mean", self.warnings({"video": {"qqqq": 1}})["video.qqqq"])

    def test_the_audio_script_gives_the_same_hint_when_run_alone(self):
        report = common.Report("qa_audio.py")
        qa_audio.add_unknown_key_warnings(report, {"lufs": -23})
        self.assertIn('did you mean "lufs_target"', row(report, "unknown_spec_key")["detail"])
        self.assertNotIn("lufs_tol", row(report, "unknown_spec_key")["detail"])


class DocumentedKeysAreNeverUnknownTests(unittest.TestCase):
    FULL = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 320, "height": 180, "fps": 24, "frames": 48,
                  "even_dimensions": True, "cfr": True},
        "color": {"space": "bt709", "transfer": "bt709", "primaries": "bt709", "range": "tv"},
        "audio": {"present": True, "sample_rate": 48000, "channels": 2, "codec": "aac"},
        "duration_s": 2.0, "duration_tol_s": 0.1, "max_av_start_offset_s": True,
        "lufs_target": -20, "lufs_tol": 3, "true_peak_max_db": -1, "no_clipping": True,
        "max_leading_silence_s": 1, "max_trailing_silence_s": 1, "max_silence_s": 1,
    }

    def test_every_documented_key_of_the_spec_table_is_known(self):
        _code, data = qa_json("tone.mp4", self.FULL)
        self.assertEqual(rows(data, "unknown_spec_key"), [])
        self.assertEqual(data["overall"], "PASS", [(c["id"], c["status"]) for c in data["checks"] if c["status"] != "PASS"])

    def test_a_gif_section_for_a_file_that_is_not_a_gif_is_not_called_unknown(self):
        code, data = qa_json("tone.mp4", {"gif": {"fps": 24}})
        self.assertEqual(rows(data, "unknown_spec_key"), [])
        check = row(data, "spec_key_not_applied")
        self.assertEqual((check["status"], check["actual"]), ("WARN", "gif"))
        self.assertIn("documented", check["detail"])
        self.assertEqual((code, data["overall"]), (0, "WARN"))

    def test_barcode_region_without_a_barcode_expectation_is_not_called_unknown(self):
        _code, data = qa_json("tone.mp4", {"barcode": {"region": "0,0,1,0.0833"}})
        self.assertEqual(rows(data, "unknown_spec_key"), [])
        check = row(data, "spec_key_not_applied")
        self.assertEqual((check["status"], check["actual"]), ("WARN", "barcode.region"))
        self.assertEqual(data["overall"], "WARN")

    def test_control_a_misspelt_key_inside_the_gif_or_barcode_section_is_still_unknown_and_gets_a_hint(self):
        _code, data = qa_json("anim.gif", {"gif": {"fpss": 10}, "barcode": {"expct": "0-1"}})
        found = {c["actual"]: c["detail"] for c in rows(data, "unknown_spec_key")}
        self.assertEqual(sorted(found), ["barcode.expct", "gif.fpss"])
        self.assertIn('did you mean "fps"', found["gif.fpss"])
        self.assertIn('did you mean "expect"', found["barcode.expct"])
        self.assertEqual(rows(data, "spec_key_not_applied"), [])

    def test_control_a_gif_section_for_a_gif_is_applied(self):
        _code, data = qa_json("anim.gif", {"gif": {"fps": 10, "expect_frames": 20}})
        self.assertEqual(rows(data, "spec_key_not_applied"), [])
        self.assertEqual(row(data, "frame_count")["status"], "PASS")


class NextStepsEntryPerRowTests(unittest.TestCase):
    SPEC = {"video": {"framerate": 24}, "lufs": -23, "audio": {"rate": 48000}}

    def test_each_unknown_key_gets_its_own_entry_with_its_own_hint(self):
        _code, md, _err = qa_markdown("tone.mp4", self.SPEC)
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        self.assertEqual(steps.count("**unknown_spec_key** ("), 3, steps)
        entries = [part for part in re.split(r"\n(?=\d+\. \*\*)", steps.strip())]
        self.assertEqual(len(entries), 3, entries)
        for key, hint in (("video.framerate", '"fps"'), ("lufs", '"lufs_target"'), ("audio.rate", '"sample_rate"')):
            own = [entry for entry in entries if "Measured: %s\n" % key in entry + "\n"]
            self.assertEqual(len(own), 1, key)
            self.assertIn(hint, own[0])
            self.assertIn("   - What to do:", own[0])

    def test_the_entries_are_numbered_one_after_the_other(self):
        _code, md, _err = qa_markdown("tone.mp4", self.SPEC)
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        for number in (1, 2, 3):
            self.assertIn("\n%d. **unknown_spec_key**" % number, "\n" + steps.strip())

    def test_control_a_check_id_that_appears_once_still_has_one_entry(self):
        _code, md, _err = qa_markdown("tone.mp4", {"video": {"width": 1920}})
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        self.assertEqual(steps.count("**width** (FAIL"), 1)

    def test_rows_of_the_same_id_are_not_dropped_by_the_unit(self):
        checks = [{"id": "x_check", "status": "WARN", "expected": "e", "actual": "a%d" % n, "detail": "d%d" % n,
                   "source": "x.py"} for n in range(3)]
        lines = run_qa.next_steps(checks, {"x_check": {"meaning": "m", "causes": "c", "action": "do it"}})
        self.assertEqual(len([line for line in lines if line.startswith(("1.", "2.", "3."))]), 3)
        self.assertEqual(len([line for line in lines if "What to do: do it" in line]), 3)


class UnmeasuredGuidanceTests(unittest.TestCase):
    def steps_for_an_unmeasured_row(self):
        checks = [{"id": "lufs_target", "status": "SKIP", "unmeasured": True, "expected": None, "actual": None,
                   "detail": "too short", "source": "qa_audio.py"}]
        return "\n".join(run_qa.next_steps(checks, {}))

    def test_the_agent_is_not_told_to_drop_the_requirement_itself(self):
        text = self.steps_for_an_unmeasured_row()
        self.assertNotIn("drop that check from the spec", text)
        self.assertIn("ask the user whether this requirement can be left out", text)
        self.assertIn("Never remove a check from the spec yourself", text)
        self.assertIn("its requirement was not verified", text)

    def test_control_the_other_advice_is_still_there(self):
        text = self.steps_for_an_unmeasured_row()
        self.assertIn("ffmpeg -version", text)
        self.assertIn("--ffmpeg PATH --ffprobe PATH", text)


class FlagNameInScriptMessagesTests(unittest.TestCase):
    """qa_frames.py spells its flags --expect and --region; run_qa.py spells them --barcode-expect and
    --barcode-region. A qa_frames message that can reach a run_qa user names both."""

    def test_a_bad_region_names_both_spellings(self):
        code, out, err = cli("run_qa.py", "barcode.mp4", "--barcode-expect", "0-23", "--barcode-region", "1,2,3")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--region", err)
        self.assertIn("--barcode-region", err)

    def test_control_the_script_alone_still_says_its_own_flag(self):
        code, _out, err = cli("qa_frames.py", "barcode.mp4", "--region", "1,2,3")
        self.assertEqual(code, 2)
        self.assertIn("--region", err)


# --------------------------------------------------------------------------- D13 effective_duration

class EffectiveDurationRowTests(unittest.TestCase):
    def test_a_pass_row_never_shows_two_numbers_that_disagree(self):
        # 2.00 s as written, 3.0 s expected: the duration check fails, effective_duration is not about that.
        report = qa_gif.build_report(str(WORK / "anim.gif"), expect_duration=3.0)
        self.assertEqual(row(report, "duration")["status"], "FAIL")
        check = row(report, "effective_duration")
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(check["expected"], "no frame with a delay of 0 or 1 cs")
        self.assertEqual(check["actual"], 0)
        self.assertIn("2.00 s", check["detail"])

    def test_the_pass_row_is_the_same_with_and_without_an_expected_duration(self):
        plain = row(qa_gif.build_report(str(WORK / "anim.gif")), "effective_duration")
        asked = row(qa_gif.build_report(str(WORK / "anim.gif"), expect_duration=3.0), "effective_duration")
        self.assertEqual((plain["expected"], plain["actual"]), (asked["expected"], asked["actual"]))

    def test_the_warn_row_counts_the_frames_that_browsers_slow_down_and_keeps_the_seconds_in_the_detail(self):
        slow = WORK / "delay_1.gif"
        patch_gif_delays(WORK / "anim.gif", slow, 1)   # written 0.20 s, played 2.00 s by a browser
        report = qa_gif.build_report(str(slow), expect_duration=0.2)
        check = row(report, "effective_duration")
        self.assertEqual(check["status"], "WARN")
        self.assertEqual(check["expected"], "no frame with a delay of 0 or 1 cs")
        self.assertEqual(check["actual"], 20)
        self.assertIn("0.20 s", check["detail"])
        self.assertIn("2.00 s", check["detail"])


# --------------------------------------------------------------------------- re-audit round: raw streams of every kind

RAW_FORMATS = ("raw.h264", "raw.m4v", "raw.m2v", "raw.mjpeg", "raw.hevc", "raw.obu")


def raw_fixture(name):
    path = WORK / name
    if not path.exists():
        raise unittest.SkipTest("this ffmpeg build cannot make %s (no encoder)" % name)
    return name


class EveryRawElementaryStreamIsNotMeasuredTests(unittest.TestCase):
    """ffprobe invents timestamps for a bare MPEG-4, MPEG-2, MJPEG or AV1 stream and prints a frame rate for it that nobody
    measured (an assumed 25/1 on ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1; on 4.4.2, 5.1 and 5.1.9 the rate stored in the
    stream, an assumed 25/1 or none, depending on the format), so on the first group a spec of 24 fps FAILed a correct
    file and a spec of 25 fps PASSed a rate that nobody measured (raw H.264 and HEVC were already NOT MEASURED because
    their frames carry no timestamp at all)."""

    MPEG4_START_CODE = b"\x00\x00\x01\xb0"

    def test_fixture_guard_the_raw_mpeg_4_stream_has_no_container_and_ffprobe_does_not_measure_its_rate(self):
        # What holds on every build (4.4.2, 5.1, 6.1.1, 7.1, 8.x, 9.x): the fixture is a bare MPEG-4 stream, it carries
        # made-up timestamps, and the average rate ffprobe prints for this 24 fps clip is either the assumed 25/1 or
        # none (0/0), never the clip's own 24/1. The guard fails if the fixture stops being a bare stream (a container
        # starts with its own header, not with the MPEG-4 start code) or if ffprobe starts printing the true rate.
        name = raw_fixture("raw.m4v")
        self.assertEqual((WORK / name).read_bytes()[:4], self.MPEG4_START_CODE, "a bare MPEG-4 stream starts with its start code")
        report = probe(name)
        self.assertEqual(report.info["format"]["format_name"], "m4v")
        self.assertTrue(qa_probe.is_raw_stream(report.info["format"]["format_name"]))
        self.assertIn(report.info["video"]["avg_frame_rate"], ("25/1", "0/0"))
        timing = qa_probe.read_frame_timing(FFPROBE, str(WORK / name), 0, common.Report("x.py"))
        self.assertGreater(len(timing["pts"]), 1, "this stream does carry (made-up) timestamps, unlike raw H.264")
        # negative controls: the MP4 container with the same extension fails the first four of those statements
        # (it does carry real timestamps too, which is why the last one is not a control)
        twin = probe("apple.m4v")
        self.assertNotEqual((WORK / "apple.m4v").read_bytes()[:4], self.MPEG4_START_CODE)
        self.assertNotEqual(twin.info["format"]["format_name"], "m4v")
        self.assertFalse(qa_probe.is_raw_stream(twin.info["format"]["format_name"]))
        self.assertNotIn(twin.info["video"]["avg_frame_rate"], ("25/1", "0/0"))

    def test_cfr_and_fps_and_the_frame_count_are_not_measured_for_every_raw_format(self):
        for name in RAW_FORMATS:
            for wanted in (24, 25):
                with self.subTest(file=name, spec_fps=wanted):
                    report = probe(raw_fixture(name), {"video": {"fps": wanted}})
                    for check_id in ("cfr", "fps", "frame_count_consistent"):
                        check = row(report, check_id)
                        self.assertEqual(check["status"], "SKIP", (check_id, check))
                        self.assertTrue(check.get("unmeasured"), (check_id, check))
                    self.assertEqual(report.overall(), "WARN")
                    self.assertEqual(report.exit_code(), 0)

    def test_the_assumed_rate_is_neither_a_pass_nor_a_fail_in_run_qa(self):
        for wanted in (24, 25):
            with self.subTest(spec_fps=wanted):
                code, data = qa_json(raw_fixture("raw.m2v"), {"video": {"fps": wanted}})
                self.assertEqual((code, data["overall"]), (0, "WARN"))
                self.assertEqual([c["status"] for c in rows(data, "fps")], ["SKIP"])

    def test_the_detail_says_the_stream_has_no_timestamps_of_its_own(self):
        for name in RAW_FORMATS:
            with self.subTest(file=name):
                report = probe(raw_fixture(name), {"video": {"fps": 24}})
                self.assertIn("a raw ", row(report, "fps")["detail"])
                self.assertIn("no container", row(report, "fps")["detail"])
                self.assertIn("no timestamps of their own", row(report, "fps")["detail"])
                self.assertIn("raw", row(report, "cfr")["detail"])
                self.assertIn("timestamp", row(report, "cfr")["detail"])

    def test_the_next_steps_for_a_raw_stream_point_at_the_container_not_at_a_newer_ffmpeg(self):
        code, md, err = qa_markdown(raw_fixture("raw.m4v"), {"video": {"fps": 24}})
        self.assertEqual((code, err), (0, ""))
        steps = md.split("## Next steps")[1].split("## What was checked")[0]
        self.assertIn("**cfr** (NOT MEASURED", steps)
        self.assertIn("**fps** (NOT MEASURED", steps)
        self.assertIn("Check the container file this stream was taken from", steps)
        self.assertNotIn("newer ffmpeg", steps)

    # ---- controls: a container that records real timestamps stays measured, whatever the codec
    def test_control_the_same_codecs_in_containers_with_timestamps_are_still_measured(self):
        for name in ("tone.mp4", "mpeg2.ts", "mpeg4.avi"):
            with self.subTest(file=name):
                report = probe(name, {"video": {"fps": 24}})
                self.assertEqual(row(report, "cfr")["status"], "PASS")
                self.assertEqual(row(report, "fps")["status"], "PASS")
                self.assertEqual(report.unmeasured_ids(), [])

    def test_control_a_wrong_rate_in_such_a_container_is_still_a_fail(self):
        self.assertEqual(row(probe("mpeg4.avi", {"video": {"fps": 25}}), "fps")["status"], "FAIL")

    def test_the_format_test_reads_a_comma_list_and_names_only_raw_streams(self):
        for raw in ("h264", "hevc", "m4v", "mpegvideo", "mjpeg", "obu", "av1", "jpeg_pipe", "png_pipe", "image2"):
            self.assertTrue(qa_probe.is_raw_stream(raw), raw)
        for container in ("mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "mpegts", "mpeg", "avi", "ivf", "yuv4mpegpipe",
                          "gif", "apng", None, ""):
            self.assertFalse(qa_probe.is_raw_stream(container), container)

    def test_one_raw_frame_has_no_rate_to_judge_for_cfr_but_its_assumed_fps_is_still_not_measured(self):
        timing = {"error": None, "counted": 1, "pts": [0], "raw_stream": True, "format_name": "m4v"}
        report = common.Report("qa_probe.py")
        qa_probe.add_cfr_check(report, {"codec": "mpeg4", "time_base": "1/1200000"}, timing, {})
        self.assertNotIn("unmeasured", row(report, "cfr"))
        report = common.Report("qa_probe.py")
        qa_probe.add_fps_check(report, 24, {"codec": "mpeg4", "avg_frame_rate": "25/1", "r_frame_rate": "25/1"}, timing)
        self.assertTrue(row(report, "fps").get("unmeasured"))
        # a raw stream for which ffprobe prints no rate at all (0/0 twice) is NOT MEASURED as well, not "frame rate not reported"
        for counted, pts in ((48, []), (48, list(range(48)))):
            with self.subTest(raw_frames=counted, timestamps=len(pts)):
                report = common.Report("qa_probe.py")
                qa_probe.add_fps_check(report, 24, {"codec": "mpeg4", "avg_frame_rate": "0/0", "r_frame_rate": "0/0"},
                                       {"error": None, "counted": counted, "pts": pts, "raw_stream": True, "format_name": "m4v"})
                check = row(report, "fps")
                self.assertEqual(check["status"], "SKIP")
                self.assertTrue(check.get("unmeasured"), check)
        # control: the same two rates on a file that is not a raw stream still FAIL (the rate is simply not reported)
        report = common.Report("qa_probe.py")
        qa_probe.add_fps_check(report, 24, {"codec": "mpeg4", "avg_frame_rate": "0/0", "r_frame_rate": "0/0"},
                               {"error": None, "counted": 48, "pts": list(range(48)), "raw_stream": False, "format_name": "avi"})
        self.assertEqual((row(report, "fps")["status"], row(report, "fps")["detail"]), ("FAIL", "frame rate not reported"))


    def test_a_raw_stream_is_not_counted_against_a_duration_that_ffprobe_only_estimated(self):
        video = {"codec": "mpeg4", "width": 320, "height": 180, "nb_frames_metadata": None, "duration_s": 2.0,
                 "duration_tag_s": None, "avg_frame_rate": "24/1", "r_frame_rate": "24/1"}
        timing = {"error": None, "counted": 48, "pts": list(range(48)), "raw_stream": True, "format_name": "m4v"}
        report = common.Report("qa_probe.py")
        qa_probe.add_frame_count_check(report, video, timing, 2.0, 0)
        check = row(report, "frame_count_consistent")
        self.assertEqual(check["status"], "SKIP")
        self.assertTrue(check.get("unmeasured"), check)
        # control: the same numbers for a file that is not a raw stream are compared, and agree
        report = common.Report("qa_probe.py")
        qa_probe.add_frame_count_check(report, video, dict(timing, raw_stream=False), 2.0, 0)
        self.assertEqual(row(report, "frame_count_consistent")["status"], "PASS")


# --------------------------------------------------------------------------- re-review: .m4v is a name, not a format

class AppleStyleM4vIsAnMp4Tests(unittest.TestCase):
    """A file called .m4v can be an MP4 container (Apple's video files) or a raw MPEG-4 elementary stream. ffprobe names
    them differently, and so does the skill: the container is measured normally, only the raw stream is NOT MEASURED."""

    def test_fixture_guard_the_two_files_are_different_formats_despite_the_same_extension(self):
        self.assertTrue(WORK.joinpath("apple.m4v").exists() and WORK.joinpath("raw.m4v").exists())
        self.assertIn("mp4", probe("apple.m4v").info["format"]["format_name"])
        self.assertEqual(probe(raw_fixture("raw.m4v")).info["format"]["format_name"], "m4v")
        self.assertFalse(qa_probe.is_raw_stream(probe("apple.m4v").info["format"]["format_name"]))
        self.assertTrue(qa_probe.is_raw_stream(probe(raw_fixture("raw.m4v")).info["format"]["format_name"]))

    def test_the_mp4_container_is_measured_normally(self):
        report = probe("apple.m4v", {"video": {"fps": 24}})
        for check_id in ("cfr", "fps", "frame_count_consistent"):
            self.assertEqual(row(report, check_id)["status"], "PASS", check_id)
        self.assertEqual(report.unmeasured_ids(), [])
        self.assertEqual(report.overall(), "PASS")
        code, data = qa_json("apple.m4v", {"video": {"fps": 24}})
        self.assertEqual((code, data["overall"]), (0, "PASS"))
        self.assertFalse([c for c in data["checks"] if c.get("unmeasured")])

    def test_a_wrong_rate_in_the_mp4_container_is_a_fail_not_a_skip(self):
        check = row(probe("apple.m4v", {"video": {"fps": 25}}), "fps")
        self.assertEqual(check["status"], "FAIL")
        self.assertNotIn("unmeasured", check)

    def test_control_the_raw_mpeg_4_stream_with_the_same_name_is_not_measured(self):
        report = probe(raw_fixture("raw.m4v"), {"video": {"fps": 24}})
        self.assertEqual(sorted(report.unmeasured_ids()), ["cfr", "fps", "frame_count_consistent"])
        self.assertEqual(report.overall(), "WARN")


# --------------------------------------------------------------------------- re-review: the NOT MEASURED wording fits every cause

class NotMeasuredWordingFitsEveryCauseTests(unittest.TestCase):
    def test_the_paragraph_under_the_overall_line_names_a_file_without_timestamps_or_length(self):
        code, md, _err = qa_markdown("raw.h264")
        verdict = md.split("## Next steps")[0]
        self.assertIn("NOT MEASURED: some checks that apply could not be measured", verdict)
        self.assertIn("or a file that records no timestamps or length)", verdict)
        # the earlier examples are still there
        self.assertIn("an ffmpeg that is too old or built without a filter", verdict)
        self.assertIn("a file too short or damaged for that measurement", verdict)

    def test_control_a_run_that_measured_everything_has_no_such_paragraph(self):
        _code, md, _err = qa_markdown("tone.mp4")
        self.assertNotIn("NOT MEASURED", md.split("## Next steps")[0])
        self.assertNotIn("records no timestamps or length", md)

    def test_the_cfr_detail_of_a_raw_stream_says_so(self):
        check = row(probe("raw.h264"), "cfr")
        self.assertIn("(a raw stream has no timestamps of its own)", check["detail"])
        self.assertEqual(check["remedy"], qa_probe.REMEDY_RAW_STREAM)

    def test_the_cfr_detail_of_a_file_that_is_not_a_raw_stream_does_not_call_it_one(self):
        for pts in ([], [0]):
            with self.subTest(timestamps=len(pts)):
                timing = {"error": None, "counted": 48, "pts": pts, "raw_stream": False,
                          "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}
                report = common.Report("qa_probe.py")
                qa_probe.add_cfr_check(report, {"codec": "h264", "time_base": "1/12288"}, timing, {})
                check = row(report, "cfr")
                self.assertTrue(check.get("unmeasured"))
                self.assertIn("48 frames decoded but", check["detail"])
                self.assertIn("(this file does not record enough of them)", check["detail"])
                self.assertNotIn("raw", check["detail"])
                self.assertEqual(check["remedy"], qa_probe.REMEDY_NO_TIMESTAMPS)
                self.assertNotEqual(check["remedy"], qa_probe.REMEDY_RAW_STREAM)
        # negative control: the same numbers marked as a raw stream get the raw wording
        report = common.Report("qa_probe.py")
        qa_probe.add_cfr_check(report, {"codec": "h264", "time_base": "1/12288"},
                               {"error": None, "counted": 48, "pts": [], "raw_stream": True, "format_name": "h264"}, {})
        self.assertIn("a raw stream has no timestamps of its own", row(report, "cfr")["detail"])
        self.assertNotIn("does not record enough", row(report, "cfr")["detail"])


# --------------------------------------------------------------------------- re-audit round: advice that fits the cause

class AdviceFitsTheCauseTests(unittest.TestCase):
    def steps(self, media, spec=None, *extra):
        _code, md, _err = qa_markdown(media, spec, *extra)
        return md.split("## Next steps")[1].split("## What was checked")[0]

    def test_a_file_that_records_no_length_is_told_to_use_a_container_that_does(self):
        steps = self.steps("live.mkv")
        self.assertIn("**frame_count_consistent** (NOT MEASURED", steps)
        self.assertIn("a container that records a length", steps)
        self.assertNotIn("newer ffmpeg", steps)

    def test_a_still_or_animated_image_is_told_the_warn_cannot_be_cleared(self):
        for name in ("anim.apng", "still.png"):
            with self.subTest(file=name):
                report = probe(name)
                self.assertTrue(row(report, "frame_count_consistent").get("unmeasured"))
                self.assertIn("cannot be cleared", row(report, "frame_count_consistent")["remedy"])
                steps = self.steps(name)
                self.assertIn("cannot be cleared for this file", steps)
                self.assertNotIn("container that records a length", steps)

    def test_a_gif_without_gif_fps_is_told_to_set_it_and_that_the_name_must_end_in_gif(self):
        steps = self.steps("anim.gif", {"video": {"fps": 10}})
        self.assertIn("**fps** (NOT MEASURED", steps)
        self.assertIn("Set gif.fps in the spec", steps)
        self.assertIn("ends in .gif", steps)
        self.assertNotIn("newer ffmpeg", steps)

    def test_a_video_fps_that_differs_from_gif_fps_is_told_to_make_them_equal(self):
        steps = self.steps("anim.gif", {"video": {"fps": 24}, "gif": {"fps": 10}})
        self.assertIn("same rate", steps)

    def test_control_a_tool_failure_still_gets_the_general_advice(self):
        text = "\n".join(run_qa.next_steps([{"id": "not_silent", "status": "SKIP", "unmeasured": True, "expected": None,
                                             "actual": None, "detail": "ffmpeg could not measure this stream: x",
                                             "source": "qa_audio.py"}], {}))
        self.assertIn("newer ffmpeg", text)
        self.assertIn("Never remove a check from the spec yourself", text)

    def test_a_remedy_is_kept_in_the_json_only_for_a_row_that_has_one(self):
        report = probe("raw.h264")
        self.assertIn("remedy", row(report, "cfr"))
        report = common.Report("x.py")
        report.add("a", "SKIP", unmeasured=True)
        report.add("b", "PASS", remedy="ignored: only an unmeasured SKIP carries a remedy")
        self.assertNotIn("remedy", report.checks[0])
        self.assertNotIn("remedy", report.checks[1])


# --------------------------------------------------------------------------- re-audit round: the report's own words

class LegendWordingTests(unittest.TestCase):
    """The report said "SKIP means not measured" and the SKILL.md says NOT MEASURED is a narrower thing."""

    def test_the_overall_line_says_skip_means_no_claim_is_made(self):
        _code, md, _err = qa_markdown("noaudio.mp4")
        self.assertIn("SKIP means no claim is made; it is never a pass", md)
        self.assertNotIn("SKIP means not measured", md)

    def test_the_nothing_to_fix_line_does_not_call_a_skip_not_measured(self):
        _code, md, _err = qa_markdown("noaudio.mp4")
        self.assertIn("Nothing to fix: no check failed or warned. Checks marked SKIP made no claim and prove nothing.", md)
        self.assertNotIn("were not measured", md)

    def test_the_table_lists_by_design_skips_and_not_measured_checks_apart(self):
        code, out, _err = cli("qa_probe.py", "raw.h264")
        self.assertEqual(code, 0)
        self.assertIn("NOT MEASURED (a check that applies could not be measured, so the result is not a PASS): "
                      "cfr, frame_count_consistent", out)
        self.assertNotIn("Skipped, no claim made", out, "nothing was skipped by design on this file")
        self.assertNotIn("Not measured (SKIP", out)
        # a SKIP that is by design on a file whose spec says it has no audio: the start-offset limit is vacuous
        spec = {"max_av_start_offset_s": True, "audio": {"present": False}}
        code, out, _err = cli("qa_probe.py", "noaudio.mp4", "--spec", write_spec("spec_av.json", spec))
        line = [l for l in out.splitlines() if l.startswith("Skipped, no claim made")][0]
        self.assertIn("max_av_start_offset_s", line)
        self.assertNotIn("NOT MEASURED (", out)

    def test_the_legend_is_the_same_in_the_shipped_examples(self):
        examples = ROOT / "packages" / "video-export-qa" / "examples"
        for name in ("sample_report.md", "example_session.md"):
            text = (examples / name).read_text(encoding="ascii")
            with self.subTest(example=name):
                self.assertIn("SKIP means no claim is made; it is never a pass", text)
                self.assertNotIn("SKIP means not measured", text)


# --------------------------------------------------------------------------- re-audit round: keys another script reads

class KeysForOtherScriptsNoteTests(unittest.TestCase):
    """qa_probe.py alone printed PASS for a spec whose loudness keys it never read, and qa_audio.py alone printed PASS
    for audio.sample_rate it never read. The note goes to stderr, so --json stays pure JSON."""

    def test_qa_audio_names_the_keys_it_did_not_evaluate(self):
        spec = write_spec("spec_note.json", {"audio": {"sample_rate": 44100}, "lufs_target": -20.7, "duration_s": 2})
        code, out, err = cli("qa_audio.py", "tone.mp4", "--spec", spec)
        self.assertIn("note: qa_audio.py did not evaluate these spec keys", err)
        self.assertTrue(err.strip().endswith(": audio, duration_s"), err)
        self.assertIn("qa_probe.py --json", err)
        self.assertEqual(code, 0)

    def test_qa_probe_names_the_keys_it_did_not_evaluate(self):
        spec = write_spec("spec_note.json", {"video": {"width": 320}, "lufs_target": -23, "true_peak_max_db": -1,
                                             "barcode": {"expect": "0-1"}})
        code, out, err = cli("qa_probe.py", "tone.mp4", "--spec", spec)
        self.assertIn("note: qa_probe.py did not evaluate these spec keys", err)
        self.assertTrue(err.strip().endswith(": lufs_target, true_peak_max_db, barcode"), err)
        self.assertEqual(code, 0)

    def test_the_json_on_stdout_stays_pure_json(self):
        spec = write_spec("spec_note.json", {"lufs_target": -23})
        code, out, err = cli("qa_probe.py", "tone.mp4", "--spec", spec, "--json")
        self.assertEqual(json.loads(out)["tool"], "qa_probe.py")
        self.assertIn("lufs_target", err)

    def test_control_a_spec_with_only_its_own_keys_prints_nothing_extra(self):
        spec = write_spec("spec_note.json", {"video": {"width": 320}, "duration_s": 2})
        code, out, err = cli("qa_probe.py", "tone.mp4", "--spec", spec)
        self.assertEqual(err, "")
        spec = write_spec("spec_note.json", {"lufs_target": -20.7, "no_clipping": True})
        code, out, err = cli("qa_audio.py", "tone.mp4", "--spec", spec)
        self.assertEqual(err, "")

    def test_control_run_qa_reads_every_key_and_prints_no_such_note(self):
        spec = write_spec("spec_note.json", {"lufs_target": -20.7, "lufs_tol": 3, "audio": {"present": True}})
        code, out, err = cli("run_qa.py", "tone.mp4", "--spec", spec)
        self.assertEqual(err, "")


# --------------------------------------------------------------------------- re-audit round: unknown-key hints

class UnknownKeyHintAmbiguityTests(unittest.TestCase):
    def hint(self, key, gif=False):
        return qa_probe.unknown_key_hint(key, gif)

    def test_a_name_two_sections_share_names_both(self):
        text = self.hint("codec")
        self.assertIn('"video": {"codec": ...}', text)
        self.assertIn('"audio": {"codec": ...}', text)

    def test_control_a_name_one_section_owns_names_that_section(self):
        self.assertIn('"video": {"width": ...}', self.hint("width"))
        self.assertNotIn('"audio"', self.hint("width"))

    def test_for_a_gif_a_top_level_fps_is_pointed_at_gif_fps(self):
        self.assertIn('"gif": {"fps": ...}', self.hint("fps", gif=True))
        self.assertNotIn('"gif"', self.hint("fps", gif=False))

    def test_run_qa_gives_the_gif_hint_only_for_a_gif(self):
        _code, data = qa_json("anim.gif", {"fps": 10})
        self.assertIn('"gif": {"fps": ...}', row(data, "unknown_spec_key")["detail"])
        _code, data = qa_json("tone.mp4", {"fps": 24})
        self.assertNotIn('"gif"', row(data, "unknown_spec_key")["detail"])
        self.assertIn('"video": {"fps": ...}', row(data, "unknown_spec_key")["detail"])


# --------------------------------------------------------------------------- re-audit round: spec_key_not_applied text

class SpecKeyNotAppliedGuidanceTests(unittest.TestCase):
    def guidance(self):
        return run_qa.load_guidance(str(ROOT / "packages" / "video-export-qa" / "references" / "interpreting-results.md"))

    def test_one_cell_that_is_right_for_each_of_the_two_cases(self):
        row_ = self.guidance()["spec_key_not_applied"]
        self.assertIn("For gif:", row_["action"])
        self.assertIn("For barcode.region:", row_["action"])
        self.assertIn("rename a real GIF", row_["action"])
        self.assertIn("barcode.expect", row_["action"])

    def test_the_detail_for_a_gif_section_on_a_misnamed_file_says_what_to_rename(self):
        _code, data = qa_json("tone.mp4", {"gif": {"fps": 24}})
        self.assertIn("needs renaming first", row(data, "spec_key_not_applied")["detail"])


# --------------------------------------------------------------------------- re-audit round: runs must not share clips

class PrivateWorkFolderTests(unittest.TestCase):
    """Two runs of the suite that overlap used to overwrite each other's clips in a fixed folder under _work
    ("moov atom not found" in test_probe.py during a review)."""

    def test_two_requests_give_two_different_folders_named_with_the_process(self):
        import ffmpeg_fixtures
        first = ffmpeg_fixtures.private_work_folder(ROOT, "group", "name")
        second = ffmpeg_fixtures.private_work_folder(ROOT, "group", "name")
        self.assertNotEqual(first, second)
        for folder in (first, second):
            self.assertTrue(folder.startswith(str(ROOT / "_work" / "video-export-qa" / "group")), folder)
            self.assertIn("-%d-" % os.getpid(), folder)

    def test_no_test_module_still_uses_a_fixed_work_folder(self):
        checked = 0
        for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
            for line in path.read_bytes().decode("utf-8").splitlines():
                if line.startswith("WORK = ") and not line.startswith("WORK = None"):
                    checked += 1
                    with self.subTest(module=path.name):
                        self.assertIn("private_work_folder(", line)
                        self.assertNotIn('"_work"', line)
        self.assertGreaterEqual(checked, 10)


if __name__ == "__main__":
    unittest.main()
