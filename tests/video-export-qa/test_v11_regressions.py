# Regression tests for the version 1.1 changes. Real ffmpeg and ffprobe; media is generated here.
#   1. A check that applies but could not be measured (an old ffmpeg that rejects the loudness option,
#      audio too short for loudness, a container with no duration, ...) makes the overall result at best a
#      WARN, never a PASS (it was a PASS with exit code 0 in 1.0). Every place that sets the flag has a test.
#   2. The audio filter chain does not use the option that ffmpeg 4.4 and 5.1 reject (framelog=quiet).
#   3. ffmpeg and ffprobe are never taken from the current folder, however that folder is spelled.
#   4. Terminal colour codes in ffmpeg's messages (AV_LOG_FORCE_COLOR) no longer break the parsing.
# Run:  python -I -B -m unittest discover -s tests/video-export-qa
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
WORK = ROOT / "_work" / "video-export-qa" / "v11"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import qa_audio  # noqa: E402
import qa_compare  # noqa: E402
import qa_frames  # noqa: E402
import qa_probe  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
PICTURE = "testsrc2=size=320x180:rate=24:duration=3"
TONE = "sine=frequency=1000:sample_rate=48000:duration=3,volume=0.8,pan=stereo|c0=c0|c1=c0"
SHORT_PICTURE = "testsrc2=size=320x180:rate=24:duration=0.3"
SHORT_TONE = "sine=frequency=1000:sample_rate=48000:duration=0.3"
# Colour tags written by the encoder, so that nothing but the audio can make the overall result a WARN.
TAGS = "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off"

M = {}


def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    M["tagged"] = WORK / "tagged_with_tone.mp4"
    ffmpeg("-f", "lavfi", "-i", PICTURE, "-f", "lavfi", "-i", TONE, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-x264-params", TAGS, "-c:a", "aac", "-shortest", M["tagged"])
    M["short"] = WORK / "short_with_tone.mov"
    ffmpeg("-f", "lavfi", "-i", SHORT_PICTURE, "-f", "lavfi", "-i", SHORT_TONE, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-x264-params", TAGS, "-c:a", "aac", "-shortest", M["short"])
    make_shim("old_ffmpeg", "any_audio_filter")
    make_shim("quiet_rejecting_ffmpeg", "framelog_quiet")


SHIM_SCRIPTS = {
    # Every run that uses an audio filter fails: the whole audio measurement is lost.
    "any_audio_filter": (
        "import subprocess, sys\n"
        "real, args = sys.argv[1], sys.argv[2:]\n"
        "if '-af' in args:\n"
        "    sys.stderr.write('Unable to parse option value \"quiet\"\\nConversion failed!\\n')\n"
        "    sys.exit(1)\n"
        "sys.exit(subprocess.call([real] + args))\n"),
    # Only the option that ffmpeg 4.4 and 5.1 really reject (ebur128 framelog=quiet) fails; the rest is real ffmpeg.
    "framelog_quiet": (
        "import subprocess, sys\n"
        "real, args = sys.argv[1], sys.argv[2:]\n"
        "for i, arg in enumerate(args[:-1]):\n"
        "    if arg == '-af' and 'framelog=quiet' in args[i + 1]:\n"
        "        sys.stderr.write('[Parsed_ebur128_0 @ 0000] Unable to parse option value \"quiet\"\\n"
        "Error setting option framelog to value quiet.\\nConversion failed!\\n')\n"
        "        sys.exit(1)\n"
        "sys.exit(subprocess.call([real] + args))\n"),
}


def make_shim(name, mode):
    """A fake 'old ffmpeg' that forwards everything to the real ffmpeg except what its mode rejects."""
    script = WORK / (name + ".py")
    script.write_text(SHIM_SCRIPTS[mode], encoding="utf-8")
    if os.name == "nt":
        launcher = WORK / (name + ".cmd")
        launcher.write_text('@"%s" "%s" "%s" %%*\r\n' % (sys.executable, script, FFMPEG), encoding="ascii")
    else:
        launcher = WORK / name
        launcher.write_text('#!/bin/sh\nexec "%s" "%s" "%s" "$@"\n' % (sys.executable, script, FFMPEG), encoding="ascii")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    M[name] = launcher


def run_qa(path, spec, ffmpeg_path=None, as_json=True):
    spec_file = WORK / "spec_v11.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    cmd = [sys.executable, "-I", "-B", str(SCRIPTS / "run_qa.py"), str(path), "--spec", str(spec_file)]
    if as_json:
        cmd.append("--json")
    if ffmpeg_path:
        cmd += ["--ffmpeg", str(ffmpeg_path)]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def by_id(checks, check_id):
    return [c for c in checks if c["id"] == check_id][0]


# --------------------------------------------------------------------------- 1. unmeasured verdict

class UnmeasuredVerdictTests(unittest.TestCase):
    def report(self, *rows):
        report = common.Report("sample.py", file="x.mp4")
        for check_id, status, unmeasured in rows:
            report.add(check_id, status, "e", None, "d", unmeasured=unmeasured)
        return report

    def test_a_pass_plus_a_check_that_could_not_be_measured_is_a_warn(self):
        report = self.report(("a", common.PASS, False), ("not_silent", common.SKIP, True))
        self.assertEqual(report.overall(), common.WARN)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(report.unmeasured_ids(), ["not_silent"])

    def test_negative_control_a_skip_that_is_correct_by_design_stays_a_pass(self):
        report = self.report(("a", common.PASS, False), ("not_silent", common.SKIP, False))
        self.assertEqual(report.overall(), common.PASS)

    def test_a_fail_still_dominates(self):
        report = self.report(("a", common.FAIL, False), ("b", common.SKIP, True))
        self.assertEqual(report.overall(), common.FAIL)
        self.assertEqual(report.exit_code(), 1)

    def test_when_nothing_at_all_was_measured_the_result_is_still_skip(self):
        report = self.report(("a", common.SKIP, True), ("b", common.SKIP, False))
        self.assertEqual(report.overall(), common.SKIP)

    def test_the_flag_appears_in_the_json_only_on_flagged_skip_rows(self):
        report = self.report(("a", common.PASS, False), ("b", common.SKIP, True), ("c", common.SKIP, False),
                             ("d", common.PASS, True))  # the flag means nothing on a PASS
        rows = {c["id"]: c for c in report.to_dict()["checks"]}
        self.assertTrue(rows["b"]["unmeasured"])
        for check_id in ("a", "c", "d"):
            self.assertNotIn("unmeasured", rows[check_id])

    def test_the_table_names_the_checks_that_could_not_be_measured(self):
        text = self.report(("a", common.PASS, False), ("not_silent", common.SKIP, True)).table()
        self.assertIn("could not be measured", text)
        self.assertIn("not_silent", text.split("could not be measured")[1].splitlines()[0])
        self.assertNotIn("could not be measured", self.report(("a", common.PASS, False)).table())

    def test_the_table_row_of_such_a_check_says_NOT_MEASURED_and_a_skip_by_design_does_not(self):
        rows = self.report(("a", common.PASS, False), ("not_silent", common.SKIP, True),
                           ("by_design", common.SKIP, False)).table().splitlines()
        flagged = [r for r in rows if r.startswith("[SKIP]") and "not_silent" in r][0]
        plain = [r for r in rows if r.startswith("[SKIP]") and "by_design" in r][0]
        self.assertIn("NOT MEASURED:", flagged)
        self.assertNotIn("NOT MEASURED", plain)


# --------------------------------------------------------------------------- 1b. every flag has a test

class UnmeasuredFlagTests(unittest.TestCase):
    """Each place that marks a SKIP as 'could not be measured' is called on its own, with the situation that
    causes it, and with the neighbouring situation that must NOT be marked (a negative control)."""
    VIDEO = {"codec": "h264", "width": 320, "height": 180}
    AUDIO = {"codec": "aac"}

    def row(self, report, check_id):
        return by_id(report.checks, check_id)

    def test_cfr_when_the_frames_could_not_be_read(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_cfr_check(report, self.VIDEO, {"error": "ffprobe failed"}, {})
        self.assertTrue(self.row(report, "cfr").get("unmeasured"))

    def test_cfr_negative_control_variable_frame_rate_allowed_by_the_spec_is_not_flagged(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_cfr_check(report, self.VIDEO, {"error": "ffprobe failed"}, {"cfr": False})
        self.assertEqual(self.row(report, "cfr")["status"], common.SKIP)
        self.assertNotIn("unmeasured", self.row(report, "cfr"))

    def test_frame_count_consistent_when_the_frames_could_not_be_counted(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_frame_count_check(report, self.VIDEO, {"error": "ffprobe failed"}, None, 0)
        self.assertTrue(self.row(report, "frame_count_consistent").get("unmeasured"))

    def test_frame_count_consistent_negative_control_no_video_is_not_flagged(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_frame_count_check(report, None, {"error": "ffprobe failed"}, None, 1)
        self.assertNotIn("unmeasured", self.row(report, "frame_count_consistent"))

    def test_frames_when_the_frames_could_not_be_counted(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_frames_check(report, 24, self.VIDEO, {"error": "ffprobe failed"})
        self.assertTrue(self.row(report, "frames").get("unmeasured"))

    def test_frames_negative_control_a_counted_clip_is_a_pass_without_the_flag(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_frames_check(report, 24, self.VIDEO, {"error": None, "counted": 24})
        self.assertEqual(self.row(report, "frames")["status"], common.PASS)
        self.assertNotIn("unmeasured", self.row(report, "frames"))

    def test_duration_when_the_container_reports_none(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_duration_check(report, {"duration_s": 3}, {"duration_s": None}, self.VIDEO, [])
        self.assertTrue(self.row(report, "duration_s").get("unmeasured"))

    def test_duration_negative_control_a_reported_duration_is_judged(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_duration_check(report, {"duration_s": 3}, {"duration_s": 3.0}, self.VIDEO, [])
        self.assertEqual(self.row(report, "duration_s")["status"], common.PASS)

    def test_audio_video_start_offset_when_a_start_time_is_not_reported(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_av_offset_check(report, True, self.VIDEO, [self.AUDIO], None)
        self.assertTrue(self.row(report, "max_av_start_offset_s").get("unmeasured"))

    def test_audio_video_start_offset_negative_control_a_missing_stream_is_not_flagged(self):
        report = common.Report("qa_probe.py")
        qa_probe.add_av_offset_check(report, True, self.VIDEO, [], None)
        self.assertEqual(self.row(report, "max_av_start_offset_s")["status"], common.SKIP)
        self.assertNotIn("unmeasured", self.row(report, "max_av_start_offset_s"))

    def test_compare_identical_when_a_file_decoded_no_frames(self):
        report = common.Report("qa_compare.py")
        qa_compare._check_identical(report, {"hashes": [], "width": None, "height": None},
                                    {"hashes": ["x"], "width": 2, "height": 2})
        self.assertTrue(self.row(report, "identical").get("unmeasured"))

    def test_compare_psnr_when_a_file_decoded_no_frames(self):
        report = common.Report("qa_compare.py")
        qa_compare._check_psnr(report, "ffmpeg", "a", "b", {"hashes": [], "width": None, "height": None},
                               {"hashes": ["x"], "width": 2, "height": 2}, 30)
        self.assertTrue(self.row(report, "psnr").get("unmeasured"))

    def test_compare_psnr_when_the_psnr_measurement_itself_fails(self):
        report = common.Report("qa_compare.py")
        a = {"hashes": ["x"], "width": 2, "height": 2}
        with mock.patch.object(qa_compare, "measure_psnr", return_value=(None, "the psnr filter failed")):
            qa_compare._check_psnr(report, "ffmpeg", "a", "b", a, dict(a), 30)
        self.assertTrue(self.row(report, "psnr").get("unmeasured"))

    def test_compare_psnr_negative_control_files_of_different_length_are_a_skip_by_design(self):
        report = common.Report("qa_compare.py")
        qa_compare._check_psnr(report, "ffmpeg", "a", "b", {"hashes": ["x"], "width": 2, "height": 2},
                               {"hashes": ["x", "y"], "width": 2, "height": 2}, 30)
        self.assertEqual(self.row(report, "psnr")["status"], common.SKIP)
        self.assertNotIn("unmeasured", self.row(report, "psnr"))

    def test_lufs_target_names_the_real_cause_for_audio_that_is_too_short(self):
        report = common.Report("qa_audio.py")
        qa_audio.add_lufs_check(report, {"lufs_target": -20, "lufs_tol": 1},
                                {"audio_duration_s": 0.2, "integrated_lufs": None}, None)
        row = self.row(report, "lufs_target")
        self.assertTrue(row.get("unmeasured"))
        self.assertIn("too short", row["detail"])

    def test_lufs_target_does_not_blame_the_length_when_ffmpeg_reported_no_loudness_for_long_audio(self):
        report = common.Report("qa_audio.py")
        qa_audio.add_lufs_check(report, {"lufs_target": -20, "lufs_tol": 1},
                                {"audio_duration_s": 3.0, "integrated_lufs": None}, None)
        row = self.row(report, "lufs_target")
        self.assertTrue(row.get("unmeasured"))
        self.assertNotIn("too short", row["detail"])
        self.assertIn("did not report", row["detail"])

    def test_lufs_target_negative_control_no_audio_stream_is_a_skip_by_design(self):
        report = common.Report("qa_audio.py")
        qa_audio.add_lufs_check(report, {"lufs_target": -20, "lufs_tol": 1}, None, qa_audio.NO_AUDIO)
        self.assertEqual(self.row(report, "lufs_target")["status"], common.SKIP)
        self.assertNotIn("unmeasured", self.row(report, "lufs_target"))


class ShortAudioEndToEndTests(unittest.TestCase):
    """A 0.3 s clip with a loudness target: loudness cannot be measured, the report must say why and must
    not tell the reader to upgrade ffmpeg as if the tool were at fault."""

    def test_the_report_blames_the_length_and_offers_a_longer_file_or_dropping_the_check(self):
        code, out, err = run_qa(M["short"], {"lufs_target": -20}, as_json=False)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("NOT MEASURED", out)
        self.assertIn("shorter than 0.4 s", out)
        self.assertIn("drop that check from the spec", out)


# --------------------------------------------------------------------------- 2. old ffmpeg

class OldFfmpegGuardTests(unittest.TestCase):
    """ffmpeg 4.4 and 5.1 reject ebur128 framelog=quiet. The fix is that version 1.1 never asks for it."""
    SPEC = {"audio": {"present": True}, "lufs_target": -20, "lufs_tol": 3, "true_peak_max_db": -1.0}

    def test_the_filter_chain_does_not_use_the_option_that_old_ffmpeg_rejects(self):
        self.assertNotIn("framelog=quiet", qa_audio.FILTER_CHAIN)
        self.assertIn("framelog=verbose", qa_audio.FILTER_CHAIN)

    def test_negative_control_the_fake_old_ffmpeg_really_rejects_the_version_1_0_chain(self):
        proc = subprocess.run([str(M["quiet_rejecting_ffmpeg"]), "-hide_banner", "-nostdin", "-i", str(M["tagged"]), "-vn",
                               "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"Conversion failed", proc.stderr)

    def test_an_ffmpeg_that_rejects_only_the_old_option_still_gets_every_audio_check_measured(self):
        code, out, err = run_qa(M["tagged"], self.SPEC, ffmpeg_path=M["quiet_rejecting_ffmpeg"])
        self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data["overall"], "PASS", [(c["id"], c["status"], c["detail"]) for c in data["checks"]])
        self.assertEqual(code, 0)
        self.assertEqual(by_id(data["checks"], "not_silent")["status"], "PASS")
        self.assertFalse([c for c in data["checks"] if c.get("unmeasured")])


class OldFfmpegEndToEndTests(unittest.TestCase):
    """The original defect: when the audio filter cannot run at all, a silent-audio or any other export
    was reported as 'Overall: PASS' with exit code 0 while every requested audio check was SKIP."""
    SPEC = {"audio": {"present": True}, "lufs_target": -20, "lufs_tol": 3, "true_peak_max_db": -1.0}

    def test_negative_control_with_a_working_ffmpeg_the_same_file_is_a_clean_pass(self):
        code, out, err = run_qa(M["tagged"], self.SPEC)
        self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data["overall"], "PASS", [(c["id"], c["status"], c["detail"]) for c in data["checks"]])
        self.assertEqual(code, 0)
        self.assertEqual(by_id(data["checks"], "not_silent")["status"], "PASS")
        self.assertFalse([c for c in data["checks"] if c.get("unmeasured")])

    def test_when_the_audio_filter_fails_the_overall_result_is_a_warn_not_a_pass(self):
        code, out, err = run_qa(M["tagged"], self.SPEC, ffmpeg_path=M["old_ffmpeg"])
        self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data["overall"], "WARN", [(c["id"], c["status"]) for c in data["checks"]])
        self.assertEqual(code, 0, "a WARN exits with 0; the Overall line is what tells the reader")
        silent = by_id(data["checks"], "not_silent")
        self.assertEqual(silent["status"], "SKIP")
        self.assertTrue(silent["unmeasured"])
        self.assertIn("Conversion failed", silent["detail"])
        unmeasured = {c["id"] for c in data["checks"] if c.get("unmeasured")}
        self.assertLessEqual({"not_silent", "lufs_target", "true_peak_max_db"}, unmeasured)
        # the picture checks that do not need the audio filter were still measured
        self.assertEqual(by_id(data["checks"], "audio.present")["status"], "PASS")

    def test_the_markdown_report_says_what_could_not_be_measured_and_what_to_do(self):
        code, out, err = run_qa(M["tagged"], self.SPEC, ffmpeg_path=M["old_ffmpeg"], as_json=False)
        self.assertEqual(err, "")
        self.assertIn("**Overall: WARN**", out)
        self.assertIn("could not be measured", out)
        self.assertIn("NOT MEASURED", out)
        self.assertIn("ffmpeg -version", out)
        row = [line for line in out.splitlines() if line.startswith("| SKIP |") and "`not_silent`" in line][0]
        self.assertIn("NOT MEASURED: ", row)


# --------------------------------------------------------------------------- 3. tool lookup

class ToolLookupTests(unittest.TestCase):
    NAME = "vexqa_fake_tool"

    def setUp(self):
        self._cwd = os.getcwd()
        self._path = os.environ.get("PATH")
        self.cwd_dir = Path(tempfile.mkdtemp(prefix="vexqa_cwd_")).resolve()
        self.path_dir = Path(tempfile.mkdtemp(prefix="vexqa_path_")).resolve()

    def tearDown(self):
        os.chdir(self._cwd)
        if self._path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._path
        shutil.rmtree(str(self.cwd_dir), ignore_errors=True)
        shutil.rmtree(str(self.path_dir), ignore_errors=True)

    def make_tool(self, folder):
        path = folder / (self.NAME + (".exe" if os.name == "nt" else ""))
        path.write_text("not a real program", encoding="ascii")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def search(self, path_value):
        os.environ["PATH"] = path_value
        os.chdir(str(self.cwd_dir))
        return common.find_on_path(self.NAME)

    def make_link_to(self, target, link):
        """A symbolic link, or on Windows a junction (which needs no special rights). SkipTest if neither works."""
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
            return
        except (OSError, NotImplementedError, AttributeError):
            pass
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            if proc.returncode == 0:
                return
        self.skipTest("this system cannot make a link or junction to a folder")

    def test_a_tool_that_sits_only_in_the_current_folder_is_not_used(self):
        self.make_tool(self.cwd_dir)
        self.assertIsNone(self.search(str(self.path_dir)))

    def test_the_current_folder_is_not_used_even_when_it_is_listed_in_PATH(self):
        self.make_tool(self.cwd_dir)
        self.assertIsNone(self.search(str(self.cwd_dir)))

    def test_the_current_folder_is_not_used_when_PATH_names_it_through_a_link(self):
        self.make_tool(self.cwd_dir)
        link = self.path_dir / "link_to_cwd"
        self.make_link_to(self.cwd_dir, link)
        self.assertIsNone(self.search(str(link)))

    def test_relative_and_empty_entries_in_PATH_are_ignored(self):
        self.make_tool(self.cwd_dir)
        self.assertIsNone(self.search(os.pathsep.join([os.curdir, "", "."])))

    def test_a_relative_entry_that_leads_to_a_folder_holding_the_tool_is_ignored(self):
        sub = self.cwd_dir / "sub"
        sub.mkdir()
        self.make_tool(sub)
        self.assertIsNone(self.search("sub"))

    def test_negative_control_a_tool_in_another_absolute_folder_is_found(self):
        expected = self.make_tool(self.path_dir)
        self.assertEqual(Path(self.search(str(self.path_dir))), expected)

    def test_when_both_exist_the_one_outside_the_current_folder_wins(self):
        self.make_tool(self.cwd_dir)
        expected = self.make_tool(self.path_dir)
        found = self.search(os.pathsep.join([str(self.cwd_dir), str(self.path_dir)]))
        self.assertEqual(Path(found), expected)

    @unittest.skipIf(os.name == "nt", "Windows does not let a process delete its own current folder")
    def test_a_deleted_current_folder_does_not_stop_the_tool_search(self):
        expected = self.make_tool(self.path_dir)
        gone = Path(tempfile.mkdtemp(prefix="vexqa_gone_")).resolve()
        os.chdir(str(gone))
        gone.rmdir()
        os.environ["PATH"] = str(self.path_dir)
        self.assertEqual(Path(common.find_on_path(self.NAME)), expected)

    def test_find_tool_names_the_rule_when_nothing_is_found_and_still_honours_an_explicit_path(self):
        explicit = self.make_tool(self.cwd_dir)
        os.environ["PATH"] = str(self.path_dir)
        os.chdir(str(self.cwd_dir))
        with self.assertRaises(common.ToolMissing) as caught:
            common.find_tool(self.NAME)
        self.assertIn("current folder is never searched", str(caught.exception))
        self.assertEqual(Path(common.find_tool(self.NAME, override=str(explicit))), explicit)


# --------------------------------------------------------------------------- 4. colour codes

class ColourCodeTests(unittest.TestCase):
    CODED = "\x1b[0;33m[Parsed_ebur128_1 @ 000001]\x1b[0m   Summary:\x1b[K"

    def test_colour_and_cursor_codes_are_removed(self):
        self.assertEqual(common.strip_ansi(self.CODED), "[Parsed_ebur128_1 @ 000001]   Summary:")

    def test_negative_control_text_without_codes_is_unchanged(self):
        plain = "[Parsed_ebur128_1 @ 000001]   Summary: [1;2] 50% done"
        self.assertEqual(common.strip_ansi(plain), plain)

    def test_run_returns_text_without_the_codes(self):
        code, out, err = common.run([sys.executable, "-c",
                                     "import sys; sys.stdout.write('\\x1b[31mred\\x1b[0m'); sys.stderr.write('\\x1b[1mbold\\x1b[0m')"])
        self.assertEqual((code, out, err), (0, "red", "bold"))

    def test_the_barcode_decoder_returns_its_messages_without_the_codes(self):
        decoded = qa_frames.decode_once([sys.executable, "-c", "import sys; sys.stderr.write('\\x1b[31mred\\x1b[0m')"], [])
        self.assertEqual(decoded.stderr, "red")

    def test_a_forced_colour_environment_no_longer_breaks_the_audio_measurement(self):
        key, before = "AV_LOG_FORCE_COLOR", os.environ.get("AV_LOG_FORCE_COLOR")
        os.environ[key] = "1"
        try:
            probe = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(M["tagged"]), "-vn", "-af",
                                    "ebur128=peak=true:framelog=verbose", "-f", "null", "-"], capture_output=True)
            if b"\x1b[" not in probe.stderr:
                self.skipTest("this ffmpeg build ignores AV_LOG_FORCE_COLOR, so the condition cannot be made")
            report = qa_audio.analyze(str(M["tagged"]), {}, {"ffmpeg": FFMPEG, "ffprobe": FFPROBE})
        finally:
            if before is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = before
        statuses = {c["id"]: c["status"] for c in report.checks}
        self.assertEqual(statuses.get("not_silent"), "PASS", statuses)


if __name__ == "__main__":
    unittest.main()
