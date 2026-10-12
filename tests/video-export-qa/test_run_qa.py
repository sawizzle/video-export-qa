# Tests for run_qa.py (the merged report) and for the integration fixes in qa_probe.py for GIF files.
# Real ffmpeg and ffprobe, no mocks for media logic. All media is generated under the scratch folder.
# Every test that expects a PASS has a matching test that makes the same check FAIL for the right reason.
# Run:  python -B -m unittest discover -s tests/video-export-qa
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

from ffmpeg_fixtures import fixture_command
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "video-export-qa"
SCRIPTS = PACKAGE / "scripts"
EXAMPLES = PACKAGE / "examples"
WORK = ROOT / "_work" / "video-export-qa" / "integrator" / "tests-run-qa"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import run_qa  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

SECTIONS = ["# Video export QA report", "## What was checked", "## Summary", "## Checks", "## Next steps",
            "## How this report was made"]

# A spec the plain barcode clip satisfies (the clip is 640x360, 24 fps, 96 frames, a tone at -20.7 LUFS).
GOOD_SPEC = {
    "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 640, "height": 360, "fps": "24", "frames": 96,
              "even_dimensions": True, "cfr": True},
    "color": {"space": "bt709", "range": "tv"},
    "audio": {"present": True, "sample_rate": 48000, "channels": 2, "codec": "aac"},
    "duration_s": 4.0, "duration_tol_s": 0.05, "max_av_start_offset_s": 0.05,
    "lufs_target": -21, "lufs_tol": 1.5, "no_clipping": True,
}


# --------------------------------------------------------------------------- helpers

def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    proc = subprocess.run(fixture_command(cmd), capture_output=True, cwd=str(WORK))
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def cli(script, *args):
    """Run a package script the way a user does (python -I -B), in the fixture folder."""
    cmd = [sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def reject_constant(name):
    raise ValueError("strict JSON does not allow %s" % name)


def parse_strict(text):
    return json.loads(text, parse_constant=reject_constant)


@functools.lru_cache(maxsize=None)
def _qa(args):
    return cli("run_qa.py", *args)


def qa(*args):
    """(exit code, markdown, stderr) of run_qa.py with these arguments (results are cached)."""
    return _qa(tuple(args))


def qa_json(*args):
    """(exit code, parsed strict JSON report) of run_qa.py --format json."""
    code, out, err = qa(*(list(args) + ["--format", "json"]))
    if not out.strip():
        raise AssertionError("no output, exit %d, stderr: %s" % (code, err))
    return code, parse_strict(out)


def check_map(data):
    """id -> list of checks with that id (an id can appear once per source)."""
    found = {}
    for check in data["checks"]:
        found.setdefault(check["id"], []).append(check)
    return found


def one(data, check_id):
    found = check_map(data).get(check_id, [])
    if len(found) != 1:
        raise AssertionError("expected exactly one %r check, found %d in %s" % (check_id, len(found), [c["id"] for c in data["checks"]]))
    return found[0]


def write_spec(name, spec):
    with open(str(WORK / name), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(spec, indent=2))
    return name


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patch_gif_delays(src, dst, centiseconds):
    """Rewrite every Graphic Control Extension delay in a GIF (no encoder involved)."""
    data = bytearray(Path(src).read_bytes())
    marker = b"\x21\xf9\x04"
    position = data.find(marker)
    count = 0
    while position >= 0:
        data[position + 4:position + 6] = bytes([centiseconds, 0])
        count += 1
        position = data.find(marker, position + 1)
    Path(dst).write_bytes(bytes(data))
    return count


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    code, _out, err = cli("make_test_clip.py", "clip.mp4", "--overwrite")
    if code != 0:
        raise RuntimeError("make_test_clip.py failed: " + err)
    # Frames 30-35 removed by a plain ffmpeg select, audio kept.
    ffmpeg("-i", "clip.mp4", "-vf", "select='not(between(n,30,35))',setpts=N/(24*TB)", "-fps_mode", "cfr", "-r", "24",
           "-c:v", "libx264", "-crf", "14", "-g", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "dropped.mp4")
    ffmpeg("-i", "clip.mp4", "-an", "-c:v", "copy", "noaudio.mp4")
    # The picture trimmed to frames 24-95 (3 s) while the whole 4 s of audio was kept: the barcode cannot see this.
    ffmpeg("-i", "clip.mp4", "-vf", "trim=start_frame=24:end_frame=96,setpts=PTS-STARTPTS", "-fps_mode", "cfr", "-r", "24",
           "-c:v", "libx264", "-crf", "14",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "av_mismatch.mp4")
    # The same export in other containers (each keeps a different audio tail past the picture).
    for extension in ("mkv", "ts", "mov"):
        ffmpeg("-i", "clip.mp4", "-c", "copy", "clip." + extension)
    # The silent-export trap: a normal aac stream that decodes to zeros.
    ffmpeg("-i", "clip.mp4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-t", "4", "silent.mp4")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:a", "aac", "audio_only.m4a")
    # Same pictures with every colour tag removed.
    ffmpeg("-i", "clip.mp4", "-an", "-vf", "setparams=colorspace=unknown:color_primaries=unknown:color_trc=unknown:range=unknown",
           "-c:v", "libx264", "-crf", "14", "-g", "12", "untagged.mp4")
    # A 60 px bar above the picture pushes the barcode strip down.
    ffmpeg("-i", "clip.mp4", "-an", "-vf", "pad=640:420:0:60:black", "-c:v", "libx264", "-crf", "14", "-g", "12", "padded.mp4")
    shutil.copy(str(WORK / "clip.mp4"), str(WORK / "cl\u00efp \u6e2c\u8a66.mp4"))
    # Animated GIF at 15 fps, 3 s, 480x270 (what examples/spec_web_gif.json describes).
    ffmpeg("-i", "clip.mp4", "-t", "3", "-an", "-vf", "fps=15,scale=480:270", "anim.gif")
    patch_gif_delays(WORK / "anim.gif", WORK / "uniform6.gif", 6)
    data = (WORK / "anim.gif").read_bytes()
    (WORK / "truncated.gif").write_bytes(data[: len(data) // 2])
    write_spec("spec_good.json", GOOD_SPEC)
    write_spec("spec_wrong_width.json", {"video": {"width": 1920}})
    write_spec("spec_colour.json", {"color": {"space": "bt709", "range": "tv"}})
    write_spec("spec_barcode.json", {"barcode": {"expect": "0-95"}})
    write_spec("spec_barcode_list_region.json", {"barcode": {"expect": "0-95", "region": [0, 0.15, 1, 0.21]}})
    write_spec("spec_typos.json", {"colour": {"space": "bt709"}, "barcode": {"expct": "0-95"}})
    write_spec("spec_gif_on_mp4.json", {"gif": {"fps": 24}})
    write_spec("spec_gif_typo.json", {"gif": {"fps": 15, "expect_frame": 45}})


# --------------------------------------------------------------------------- a correct export

class CorrectExportTests(unittest.TestCase):
    def test_clean_clip_passes_with_spec_and_barcode(self):
        code, data = qa_json("clip.mp4", "--spec", "spec_good.json", "--barcode-expect", "0-95")
        self.assertEqual(code, 0)
        self.assertEqual(data["overall"], "PASS")
        self.assertEqual([c["id"] for c in data["checks"] if c["status"] == "FAIL"], [])
        self.assertEqual(one(data, "expect")["status"], "PASS")
        self.assertEqual(one(data, "not_silent")["status"], "PASS")
        self.assertEqual(one(data, "lufs_target")["status"], "PASS")
        self.assertEqual(data["info"]["parts_run"], ["qa_probe.py", "qa_audio.py", "qa_frames.py (expect 0-95)"])

    def test_every_check_names_the_script_that_made_it(self):
        _code, data = qa_json("clip.mp4", "--spec", "spec_good.json", "--barcode-expect", "0-95")
        sources = {c["source"] for c in data["checks"]}
        self.assertEqual(sources, {"qa_probe.py", "qa_audio.py", "qa_frames.py", "run_qa.py"})
        self.assertEqual(one(data, "av_duration_match")["source"], "run_qa.py")
        self.assertEqual(one(data, "cfr")["source"], "qa_probe.py")
        self.assertEqual(one(data, "no_clipping")["source"], "qa_audio.py")
        self.assertEqual(one(data, "expect")["source"], "qa_frames.py")

    def test_no_spec_runs_only_the_always_on_checks(self):
        code, data = qa_json("clip.mp4")
        self.assertEqual(code, 0)
        ids = set(check_map(data))
        self.assertEqual(ids, {"video_stream", "cfr", "frame_count_consistent", "color_tags", "not_silent",
                               "av_duration_match"})
        self.assertEqual(data["info"]["parts_run"], ["qa_probe.py", "qa_audio.py"])

    def test_barcode_is_not_run_unless_asked(self):
        _code, data = qa_json("clip.mp4", "--spec", "spec_good.json")
        self.assertNotIn("expect", check_map(data))
        self.assertNotIn("all_frames_decodable", check_map(data))
        self.assertNotIn("qa_frames", data["info"])

    def test_nothing_is_called_pass_that_was_skipped(self):
        # SKIP rows are counted separately, and the headline says SKIP is not a pass.
        code, md, _err = qa("clip.mp4", "--barcode-expect", "0-95")
        self.assertEqual(code, 0)
        self.assertIn("SKIP means not measured; it is never a pass", md)
        _code, data = qa_json("clip.mp4", "--barcode-expect", "0-95")
        skipped = [c for c in data["checks"] if c["status"] == "SKIP"]
        self.assertEqual({c["id"] for c in skipped}, {"no_dropped_frames", "no_duplicate_frames", "monotonic"})
        for check in skipped:
            self.assertTrue(check["detail"], "a SKIP must say why")


# --------------------------------------------------------------------------- wrong exports

class WrongExportTests(unittest.TestCase):
    def test_dropped_frames_fail_the_identity_expectation_with_the_first_mismatch(self):
        code, data = qa_json("dropped.mp4", "--barcode-expect", "0-95")
        self.assertEqual(code, 1)
        check = one(data, "expect")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("first mismatch at output frame 30: expected 30, got 36", check["detail"])
        self.assertIn("decoded 90 frames, expected 96", check["detail"])

    def test_the_same_file_passes_the_expectation_that_describes_the_cut(self):
        code, data = qa_json("dropped.mp4", "--barcode-expect", "0-29,36-95")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "expect")["status"], "PASS")

    def test_silent_audio_is_a_fail_not_a_pass(self):
        code, data = qa_json("silent.mp4", "--spec", "spec_good.json")
        self.assertEqual(code, 1)
        check = one(data, "not_silent")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("silent", check["detail"])
        # ffprobe still sees a perfectly normal aac stream: presence is not proof of sound.
        self.assertEqual(one(data, "audio.present")["status"], "PASS")
        self.assertEqual(one(data, "audio.codec")["status"], "PASS")
        self.assertEqual(one(data, "lufs_target")["status"], "FAIL")

    def test_no_audio_stream_is_skip_never_pass(self):
        code, data = qa_json("noaudio.mp4")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "not_silent")["status"], "SKIP")
        self.assertIn("no audio stream", one(data, "not_silent")["detail"])
        self.assertEqual(one(data, "av_duration_match")["status"], "SKIP")

    def test_audio_that_was_not_cut_with_the_picture_is_a_warning(self):
        # The barcode proves PICTURE frames only. Here the picture was trimmed to 3 s (frames 24-95) and the
        # 4 s of audio was kept whole; the barcode says PASS, and av_duration_match is what notices.
        code, data = qa_json("av_mismatch.mp4", "--barcode-expect", "24-95")
        self.assertEqual(one(data, "expect")["status"], "PASS")
        check = one(data, "av_duration_match")
        self.assertEqual(check["status"], "WARN")
        self.assertEqual(data["overall"], "WARN")
        self.assertEqual(code, 0)
        self.assertIn("picture 3.000 s", check["actual"])
        self.assertIn("1.0", check["detail"])
        self.assertIn("audio was not cut with it", check["detail"])

    def test_negative_control_audio_and_picture_of_the_same_length_pass(self):
        _code, data = qa_json("clip.mp4")
        check = one(data, "av_duration_match")
        self.assertEqual(check["status"], "PASS", check)
        self.assertIn("picture 4.000 s", check["actual"])

    def test_six_frames_cut_from_the_picture_but_not_from_the_audio_are_noticed(self):
        # dropped.mp4 has frames 30-35 removed (0.25 s) and the full audio: 3.75 s of picture, 4.01 s of sound.
        _code, data = qa_json("dropped.mp4", "--barcode-expect", "0-29,36-95")
        self.assertEqual(one(data, "expect")["status"], "PASS")
        check = one(data, "av_duration_match")
        self.assertEqual(check["status"], "WARN")
        self.assertIn("picture 3.750 s", check["actual"])

    def test_the_comparison_tolerates_the_audio_tail_of_other_containers(self):
        for name in ("clip.mkv", "clip.ts", "clip.mov"):
            with self.subTest(name):
                _code, data = qa_json(name)
                self.assertEqual(one(data, "av_duration_match")["status"], "PASS", one(data, "av_duration_match"))

    def test_the_audio_length_check_is_not_run_for_a_gif(self):
        _code, data = qa_json("anim.gif")
        self.assertNotIn("av_duration_match", check_map(data))

    def test_picture_and_sound_lengths_come_from_decoding(self):
        probe = {"counted_frames": 72, "avg_frame_rate": "24/1", "r_frame_rate": "24/1", "codec": "h264"}
        self.assertEqual(run_qa.picture_seconds(probe), 3.0)
        self.assertIsNone(run_qa.picture_seconds(dict(probe, counted_frames=0)))
        self.assertIsNone(run_qa.picture_seconds(dict(probe, codec="gif")), "a GIF has no frame rate to divide by")
        self.assertIsNone(run_qa.picture_seconds(None))

    def test_a_spec_that_asks_for_audio_fails_when_there_is_none(self):
        code, data = qa_json("noaudio.mp4", "--spec", "spec_good.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "audio.present")["status"], "FAIL")
        self.assertEqual(one(data, "audio.sample_rate")["status"], "FAIL")

    def test_wrong_picture_size_fails_in_the_spec(self):
        code, data = qa_json("clip.mp4", "--spec", "spec_wrong_width.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "width")["status"], "FAIL")
        self.assertEqual(one(data, "width")["actual"], 640)

    def test_untagged_colour_warns_without_a_spec_and_exits_zero(self):
        code, data = qa_json("untagged.mp4")
        self.assertEqual(code, 0)
        self.assertEqual(data["overall"], "WARN")
        self.assertEqual(one(data, "color_tags")["status"], "WARN")

    def test_untagged_colour_fails_when_the_spec_requires_tags(self):
        code, data = qa_json("untagged.mp4", "--spec", "spec_colour.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "color_tags")["status"], "FAIL")

    def test_audio_only_file_with_a_barcode_request_is_a_fail_row_not_a_crash(self):
        code, data = qa_json("audio_only.m4a", "--barcode-expect", "0-95")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "video_stream")["status"], "FAIL")
        check = one(data, "expect")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("no video stream", check["detail"])
        self.assertEqual(one(data, "not_silent")["status"], "PASS")

    def test_exit_code_follows_the_overall_result(self):
        for args, expected_code, expected_overall in (
            (("clip.mp4",), 0, "PASS"),
            (("untagged.mp4",), 0, "WARN"),
            (("dropped.mp4", "--barcode-expect", "0-95"), 1, "FAIL"),
        ):
            code, data = qa_json(*args)
            self.assertEqual((code, data["overall"]), (expected_code, expected_overall), args)


# --------------------------------------------------------------------------- barcode options

class BarcodeOptionTests(unittest.TestCase):
    def test_expectation_can_come_from_the_spec(self):
        code, data = qa_json("clip.mp4", "--spec", "spec_barcode.json")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "expect")["status"], "PASS")

    def test_command_line_expectation_wins_over_the_spec_and_says_so(self):
        # The spec says 0-95, the command line describes the cut: the cut file passes only the command line.
        code, data = qa_json("dropped.mp4", "--spec", "spec_barcode.json", "--barcode-expect", "0-29,36-95")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "expect")["status"], "PASS")
        self.assertTrue(any("barcode.expect in the spec was not used" in n for n in data["info"]["notes"]))
        # Negative control: with only the spec value the same file fails.
        code, data = qa_json("dropped.mp4", "--spec", "spec_barcode.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "expect")["status"], "FAIL")

    def test_moved_barcode_strip_is_undecodable_without_a_region_and_read_with_one(self):
        code, data = qa_json("padded.mp4", "--barcode-expect", "0-95")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "all_frames_decodable")["status"], "FAIL")
        code, data = qa_json("padded.mp4", "--barcode-expect", "0-95", "--barcode-region", "0,0.15,1,0.21")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "expect")["status"], "PASS")

    def test_a_region_without_an_expectation_is_reported_as_unused(self):
        code, data = qa_json("clip.mp4", "--barcode-region", "0,0.15,1,0.21")
        self.assertEqual(code, 0)
        warning = one(data, "unknown_spec_key")
        self.assertEqual((warning["status"], warning["actual"]), ("WARN", "barcode.region"))
        self.assertNotIn("expect", check_map(data))

    def test_region_can_be_a_list_in_the_spec(self):
        code, data = qa_json("padded.mp4", "--spec", "spec_barcode_list_region.json")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "expect")["status"], "PASS")


# --------------------------------------------------------------------------- GIF files

class GifTests(unittest.TestCase):
    def test_gif_runs_the_gif_checks_and_passes_the_example_spec(self):
        shutil.copy(str(EXAMPLES / "spec_web_gif.json"), str(WORK / "spec_web_gif.json"))
        code, data = qa_json("anim.gif", "--spec", "spec_web_gif.json")
        self.assertEqual(code, 0, [c for c in data["checks"] if c["status"] == "FAIL"])
        self.assertIn("qa_gif.py", data["info"]["parts_run"])
        for check_id in ("frame_count", "duration", "rounding_drift", "loop"):
            self.assertEqual(one(data, check_id)["status"], "PASS", check_id)
        self.assertEqual(one(data, "frame_count")["actual"], 45)

    def test_gif_with_every_delay_rounded_the_same_way_fails_rounding_drift(self):
        # 15 fps needs 6.67 centiseconds; all frames at 6 make 45 frames last 2.70 s instead of 3.00 s.
        code, data = qa_json("uniform6.gif", "--spec", "spec_web_gif.json")
        self.assertEqual(code, 1)
        drift = one(data, "rounding_drift")
        self.assertEqual(drift["status"], "FAIL")
        self.assertIn("same delay of 6 cs", drift["detail"])
        self.assertEqual(one(data, "duration")["status"], "FAIL")

    def test_healthy_gif_is_not_flagged_by_the_video_checks(self):
        # Regression: ffprobe reports avg_frame_rate 25/1 for a 15 fps GIF, which used to make
        # frame_count_consistent fail (duration*fps) and every GIF warn about colour tags.
        code, data = qa_json("anim.gif")
        self.assertEqual(code, 0)
        self.assertEqual(one(data, "frame_count_consistent")["status"], "PASS")
        self.assertEqual(one(data, "color_tags")["status"], "SKIP")
        self.assertEqual(one(data, "cfr")["status"], "PASS")

    def test_fps_in_the_video_section_is_skipped_for_a_gif_not_failed(self):
        write_spec("spec_gif_fps.json", {"video": {"fps": 15}})
        code, data = qa_json("anim.gif", "--spec", "spec_gif_fps.json")
        self.assertEqual(code, 0)
        check = one(data, "fps")
        self.assertEqual(check["status"], "SKIP")
        self.assertIn("gif.fps", check["detail"])

    def test_the_fps_check_still_fails_for_a_video_with_the_wrong_rate(self):
        # Negative control for the GIF special case: it must not leak into other codecs.
        write_spec("spec_fps25.json", {"video": {"fps": 25}})
        code, data = qa_json("clip.mp4", "--spec", "spec_fps25.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "fps")["status"], "FAIL")

    def test_colour_tag_spec_on_a_gif_still_fails(self):
        # GIF cannot carry tags, so a spec that demands them cannot be met and is not waved through.
        code, data = qa_json("anim.gif", "--spec", "spec_colour.json")
        self.assertEqual(code, 1)
        self.assertEqual(one(data, "color_tags")["status"], "FAIL")

    def test_gif_section_in_the_spec_of_a_non_gif_file_is_a_warning(self):
        code, data = qa_json("clip.mp4", "--spec", "spec_gif_on_mp4.json")
        self.assertEqual(code, 0)
        warning = one(data, "unknown_spec_key")
        self.assertEqual((warning["status"], warning["actual"]), ("WARN", "gif"))
        self.assertNotIn("qa_gif.py", data["info"]["parts_run"])

    def test_unknown_key_inside_the_gif_section_is_reported(self):
        code, data = qa_json("anim.gif", "--spec", "spec_gif_typo.json")
        self.assertEqual(code, 0)
        warning = one(data, "unknown_spec_key")
        self.assertEqual(warning["actual"], "gif.expect_frame")

    def test_truncated_gif_stops_with_exit_2_and_a_clear_message(self):
        code, out, err = qa("truncated.gif")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("truncated GIF", err)


# --------------------------------------------------------------------------- specs and arguments

class SpecAndArgumentTests(unittest.TestCase):
    def test_a_misspelled_key_is_reported_once_even_though_two_parts_see_it(self):
        code, data = qa_json("clip.mp4", "--spec", "spec_typos.json")
        self.assertEqual(code, 0)
        rows = check_map(data)["unknown_spec_key"]
        self.assertEqual(sorted(r["actual"] for r in rows), ["barcode.expct", "colour"])
        self.assertTrue(all(r["status"] == "WARN" for r in rows))

    def test_example_specs_contain_only_known_keys(self):
        for name, media in (("spec_h264_1080p24.json", "clip.mp4"), ("spec_web_gif.json", "anim.gif")):
            shutil.copy(str(EXAMPLES / name), str(WORK / name))
            _code, data = qa_json(media, "--spec", name)
            self.assertNotIn("unknown_spec_key", check_map(data), name)

    def test_bad_arguments_exit_2_with_a_message_on_stderr_and_no_report(self):
        (WORK / "not_json.json").write_text("{nope", encoding="utf-8")
        (WORK / "not_object.json").write_text("[1, 2]", encoding="utf-8")
        write_spec("spec_bad_type.json", {"video": {"width": "wide"}})
        write_spec("spec_bad_barcode.json", {"barcode": {"expect": "0-95-3"}})
        write_spec("spec_bad_gif.json", {"gif": {"fps": "fast"}})
        cases = (
            (("missing.mp4",), "not found"),
            (("clip.mp4", "--spec", "missing.json"), "cannot read spec"),
            (("clip.mp4", "--spec", "not_json.json"), "not valid JSON"),
            (("clip.mp4", "--spec", "not_object.json"), "must contain a JSON object"),
            (("clip.mp4", "--spec", "spec_bad_type.json"), "video.width"),
            (("clip.mp4", "--spec", "spec_bad_barcode.json"), "cannot read segment"),
            (("anim.gif", "--spec", "spec_bad_gif.json"), "gif.fps"),
            (("clip.mp4", "--barcode-expect", "banana"), "cannot read segment"),
            (("clip.mp4", "--barcode-expect", "0-95", "--barcode-region", "1,2,3"), "--region"),
            (("clip.mp4", "--json", "--format", "md"), "contradict"),
            (("clip.mp4", "--ffmpeg", "no/such/ffmpeg"), "not found at the path given"),
        )
        for args, fragment in cases:
            code, out, err = qa(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(out, "", "no report may be printed when the run cannot happen: %s" % (args,))
            self.assertIn(fragment, err, args)

    def test_json_flag_is_the_same_as_format_json(self):
        _code, via_flag, _ = qa("clip.mp4", "--json")
        _code, via_format, _ = qa("clip.mp4", "--format", "json")
        a, b = parse_strict(via_flag), parse_strict(via_format)
        self.assertEqual([(c["id"], c["status"]) for c in a["checks"]], [(c["id"], c["status"]) for c in b["checks"]])

    def test_a_file_with_a_non_ascii_name_is_reported(self):
        name = "cl\u00efp \u6e2c\u8a66.mp4"
        code, md, _err = qa(name)
        self.assertEqual(code, 0)
        self.assertIn("**Overall: PASS**", md)


# --------------------------------------------------------------------------- the files it writes

class OutputFileTests(unittest.TestCase):
    def setUp(self):
        self.out_dir = WORK / "out_case"
        if self.out_dir.exists():
            shutil.rmtree(str(self.out_dir))  # test scratch folder created by this test class

    def run_qa_direct(self, *args):
        return cli("run_qa.py", *args)

    def test_out_dir_gets_report_md_and_report_json_and_nothing_else(self):
        before = sha256(WORK / "clip.mp4")
        listing = sorted(os.listdir(str(WORK)))
        code, md, err = self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(sorted(os.listdir(str(self.out_dir))), ["report.json", "report.md"])
        # stdout of a Windows console program uses CRLF line ends; the file always uses LF.
        self.assertEqual((self.out_dir / "report.md").read_text(encoding="utf-8").replace("\r\n", "\n"),
                         md.replace("\r\n", "\n"))
        self.assertNotIn(b"\r", (self.out_dir / "report.md").read_bytes())
        data = parse_strict((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["tool"], "run_qa.py")
        self.assertEqual(sha256(WORK / "clip.mp4"), before, "the input must never change")
        self.assertEqual(sorted(os.listdir(str(WORK))), sorted(listing + ["out_case"]), "no other file may appear")

    def test_second_run_refuses_to_overwrite_and_changes_nothing(self):
        self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
        first = {name: sha256(self.out_dir / name) for name in ("report.md", "report.json")}
        code, out, err = self.run_qa_direct("dropped.mp4", "--out-dir", "out_case")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("already exists; pass --overwrite", err)
        self.assertEqual({name: sha256(self.out_dir / name) for name in first}, first)

    def test_one_existing_file_is_enough_to_refuse_and_nothing_new_is_written(self):
        self.out_dir.mkdir()
        (self.out_dir / "report.json").write_text("keep me", encoding="utf-8")
        code, _out, err = self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
        self.assertEqual(code, 2)
        self.assertIn("report.json", err)
        self.assertEqual(sorted(os.listdir(str(self.out_dir))), ["report.json"])
        self.assertEqual((self.out_dir / "report.json").read_text(encoding="utf-8"), "keep me")

    def test_overwrite_flag_replaces_the_reports(self):
        self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
        code, _out, err = self.run_qa_direct("dropped.mp4", "--barcode-expect", "0-95", "--out-dir", "out_case", "--overwrite")
        self.assertEqual((code, err), (1, ""))
        data = parse_strict((self.out_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["file"], "dropped.mp4")
        self.assertEqual(data["overall"], "FAIL")

    def test_out_dir_that_is_a_file_is_refused(self):
        (WORK / "out_case").write_text("a file", encoding="utf-8")
        try:
            code, _out, err = self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
            self.assertEqual(code, 2)
            self.assertIn("not a folder", err)
        finally:
            (WORK / "out_case").unlink()

    def test_without_out_dir_nothing_is_written(self):
        listing = sorted(os.listdir(str(WORK)))
        self.run_qa_direct("untagged.mp4")
        self.assertEqual(sorted(os.listdir(str(WORK))), listing)

    def test_a_rerun_into_a_new_folder_keeps_the_first_report_as_the_before(self):
        # The workflow in SKILL.md: after the export is fixed, run again with a NEW --out-dir.
        other = WORK / "out_case_after"
        if other.exists():
            shutil.rmtree(str(other))  # test scratch folder created by this test
        try:
            self.run_qa_direct("dropped.mp4", "--barcode-expect", "0-95", "--out-dir", "out_case")
            before = parse_strict((self.out_dir / "report.json").read_text(encoding="utf-8"))
            code, _out, err = self.run_qa_direct("clip.mp4", "--barcode-expect", "0-95", "--out-dir", "out_case_after")
            self.assertEqual((code, err), (0, ""))
            after = parse_strict((other / "report.json").read_text(encoding="utf-8"))
            self.assertEqual((before["overall"], after["overall"]), ("FAIL", "PASS"))
            self.assertEqual(parse_strict((self.out_dir / "report.json").read_text(encoding="utf-8")), before)
            # The same command again WITHOUT a new folder is refused, which is why the workflow says "new".
            code, _out, err = self.run_qa_direct("clip.mp4", "--barcode-expect", "0-95", "--out-dir", "out_case")
            self.assertEqual(code, 2)
            self.assertIn("pass --overwrite", err)
        finally:
            if other.exists():
                shutil.rmtree(str(other))

    def test_a_report_may_never_replace_the_file_being_checked_even_with_overwrite(self):
        # The export sits where the report would go: --overwrite must not turn it into its own report.
        self.out_dir.mkdir()
        for name in ("report.json", "report.md"):
            with self.subTest(name):
                trap = self.out_dir / name
                shutil.copy(str(WORK / "clip.mp4"), str(trap))
                before = sha256(trap)
                code, out, err = self.run_qa_direct("out_case/" + name, "--out-dir", "out_case", "--overwrite")
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "")
                self.assertIn("is the file being checked", err)
                self.assertEqual(sha256(trap), before, "the export must be untouched")
                trap.unlink()

    def test_negative_control_overwrite_still_replaces_an_ordinary_old_report(self):
        self.run_qa_direct("clip.mp4", "--out-dir", "out_case")
        code, _out, err = self.run_qa_direct("clip.mp4", "--out-dir", "out_case", "--overwrite")
        self.assertEqual((code, err), (0, ""))

    def test_a_folder_that_cannot_be_created_fails_at_once_with_exit_code_2_not_a_traceback(self):
        # A name Windows refuses ("?") or a path below a regular file. The run must not decode anything first.
        bad_paths = ["clip.mp4/below_a_file"]
        if os.name == "nt":
            bad_paths.insert(0, "bad?name")
        for bad in bad_paths:
            with self.subTest(bad):
                code, out, err = self.run_qa_direct("clip.mp4", "--out-dir", bad)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "")
                self.assertNotIn("Traceback", err)
                self.assertTrue(err.startswith("error: cannot create --out-dir"), err)


# --------------------------------------------------------------------------- the Markdown report

class MarkdownTests(unittest.TestCase):
    def test_sections_come_in_order(self):
        _code, md, _err = qa("dropped.mp4", "--spec", "spec_good.json", "--barcode-expect", "0-95")
        positions = [md.find(heading) for heading in SECTIONS]
        self.assertTrue(all(p >= 0 for p in positions), positions)
        self.assertEqual(positions, sorted(positions))

    def test_summary_counts_and_check_rows_match_the_json(self):
        args = ("dropped.mp4", "--spec", "spec_good.json", "--barcode-expect", "0-95")
        _code, md, _err = qa(*args)
        _code, data = qa_json(*args)
        counts = {s: sum(1 for c in data["checks"] if c["status"] == s) for s in ("PASS", "WARN", "FAIL", "SKIP")}
        self.assertIn("| %d | %d | %d | %d |" % (counts["PASS"], counts["WARN"], counts["FAIL"], counts["SKIP"]), md)
        table = md.split("## Checks")[1].split("## Next steps")[0]
        rows = [line for line in table.splitlines() if line.startswith("| ") and "`" in line]
        self.assertEqual(len(rows), len(data["checks"]))
        for row, check in zip(rows, data["checks"]):
            self.assertTrue(row.startswith("| %s | `%s` | %s |" % (check["status"], check["id"], check["source"])), row)

    def test_header_carries_the_provenance(self):
        _code, md, _err = qa("clip.mp4", "--spec", "spec_good.json")
        _code, data = qa_json("clip.mp4", "--spec", "spec_good.json")
        self.assertIn(sha256(WORK / "clip.mp4"), md)
        self.assertIn(sha256(WORK / "spec_good.json"), md)
        self.assertIn(data["provenance"]["ffmpeg_version"], md)
        self.assertIn(data["provenance"]["ffprobe_version"], md)
        self.assertIn("run_qa.py %s on Python" % common.VERSION, md)

    def test_report_text_has_no_folder_names_but_the_json_keeps_exact_commands(self):
        _code, md, _err = qa("clip.mp4", "--barcode-expect", "0-95")
        _code, data = qa_json("clip.mp4", "--barcode-expect", "0-95")
        self.assertNotIn(str(WORK).replace("\\", "/"), md.replace("\\", "/"))
        tool_folder = os.path.dirname(FFMPEG).replace("\\", "/")
        self.assertNotIn(tool_folder, md.replace("\\", "/"))
        # Negative control: the json provenance is the audit trail and does keep the real paths.
        flat = json.dumps(data["provenance"]["commands"]).replace("\\\\", "/")
        self.assertIn(str(WORK).replace("\\", "/"), flat)
        self.assertIn(os.path.dirname(FFMPEG).replace("\\", "/"), flat)

    def test_json_output_is_strict_json_even_with_infinite_values(self):
        # Silent audio has a true peak of -inf: it must appear as text, not as the bare token -Infinity.
        code, out, _err = qa("silent.mp4", "--format", "json")
        self.assertEqual(code, 1)
        self.assertNotIn("Infinity", out)
        parse_strict(out)

    def test_long_values_are_shortened_in_the_table_but_kept_whole_under_next_steps(self):
        report = common.Report("run_qa.py", file=None, tools={})
        report.add("long_check", "FAIL", "e" * 200, "a" * 200, "d" * 500)
        report.checks[-1]["source"] = "x.py"
        report.info = {"parts_run": ["x.py"], "notes": []}
        md = run_qa.markdown_report(report.to_dict(), {}, {}, None)
        table = md.split("## Checks")[1].split("## Next steps")[0]
        steps = md.split("## Next steps")[1].split("## How this report was made")[0]
        self.assertNotIn("e" * 200, table)
        self.assertIn("e" * (run_qa.EXPECTED_ACTUAL_LIMIT - 3) + "...", table)
        self.assertIn("d" * (run_qa.DETAIL_LIMIT - 3) + "...", table)
        for whole in ("e" * 200, "a" * 200, "d" * 500):
            self.assertIn(whole, steps)

    def test_the_barcode_runs_are_in_the_report_so_a_failed_expectation_shows_what_the_export_holds(self):
        _code, md, _err = qa("dropped.mp4", "--barcode-expect", "0-95")
        self.assertIn("## Barcode runs", md)
        runs = md.split("## Barcode runs")[1].split("## Next steps")[0]
        self.assertIn("output 0-29 -> source 0-29  (30 frames)", runs)
        self.assertIn("output 30-89 -> source 36-95  (60 frames)", runs)
        # The section sits between the checks and the next steps.
        self.assertLess(md.find("## Checks"), md.find("## Barcode runs"))
        self.assertLess(md.find("## Barcode runs"), md.find("## Next steps"))
        # Negative controls: no barcode run, no section; an audio-only file has no runs to show either.
        self.assertNotIn("## Barcode runs", qa("clip.mp4")[1])
        self.assertNotIn("## Barcode runs", qa("audio_only.m4a", "--barcode-expect", "0-95")[1])

    def test_the_table_note_says_where_the_complete_text_is(self):
        _code, md, _err = qa("dropped.mp4", "--barcode-expect", "0-95")
        note = md.split("## Checks")[1].split("## Barcode runs")[0]
        self.assertIn("complete text of every FAIL and WARN is repeated under Next steps", note)
        self.assertIn("report.json", note)

    def test_a_report_in_which_nothing_was_measured_says_so_and_is_not_a_pass(self):
        report = common.Report("run_qa.py", file=None, tools={})
        report.add("alpha", "SKIP", None, None, "no audio stream")
        report.add("beta", "SKIP", None, None, "no video stream")
        for check in report.checks:
            check["source"] = "x.py"
        report.info = {"parts_run": ["x.py"], "notes": []}
        data = report.to_dict()
        self.assertEqual(data["overall"], "SKIP")
        md = run_qa.markdown_report(data, {}, {}, None)
        self.assertIn("**Overall: SKIP**", md)
        self.assertIn("Nothing was measured: every check was skipped", md)
        self.assertNotIn("Nothing to fix", md)
        self.assertNotIn("**Overall: PASS**", md)
        # Negative control: one measured PASS and the same SKIPs say PASS and may say nothing to fix.
        report.add("gamma", "PASS", 1, 1, "")
        report.checks[-1]["source"] = "x.py"
        md = run_qa.markdown_report(report.to_dict(), {}, {}, None)
        self.assertIn("**Overall: PASS**", md)
        self.assertIn("Nothing to fix", md)

    def test_table_cells_cannot_break_the_table(self):
        self.assertEqual(run_qa.clean("a|b\r\nc"), "a/b  c")
        self.assertEqual(len(run_qa.shorten("x" * 100, 40)), 40)
        self.assertEqual(run_qa.shorten("short", 40), "short")


# --------------------------------------------------------------------------- next steps and guidance

class NextStepsTests(unittest.TestCase):
    def test_every_failing_check_gets_one_entry_with_the_guidance_text(self):
        args = ("silent.mp4", "--spec", "spec_good.json")
        _code, md, _err = qa(*args)
        _code, data = qa_json(*args)
        steps = md.split("## Next steps")[1].split("## How this report was made")[0]
        guidance = run_qa.load_guidance()
        failing = [c["id"] for c in data["checks"] if c["status"] in ("FAIL", "WARN")]
        self.assertIn("not_silent", failing)
        for check_id in set(failing):
            self.assertEqual(steps.count("**%s** (" % check_id), 1, check_id)
            self.assertIn(guidance[check_id]["action"], steps)
        # PASS and SKIP rows get no entry.
        self.assertNotIn("**cfr**", steps)

    def test_fails_are_listed_before_warnings(self):
        write_spec("spec_fail_and_warn.json", {"video": {"width": 1920}, "colour": {}})
        _code, md, _err = qa("untagged.mp4", "--spec", "spec_fail_and_warn.json")
        steps = md.split("## Next steps")[1]
        self.assertLess(steps.find("**width** (FAIL"), steps.find("**color_tags** (WARN"))
        self.assertLess(steps.find("**width** (FAIL"), steps.find("**unknown_spec_key** (WARN"))

    def test_a_clean_report_says_there_is_nothing_to_fix(self):
        _code, md, _err = qa("clip.mp4")
        steps = md.split("## Next steps")[1].split("## How this report was made")[0]
        self.assertIn("Nothing to fix", steps)
        self.assertNotIn("**", steps)

    def test_an_id_without_guidance_is_said_to_have_none_instead_of_inventing_text(self):
        checks = [{"id": "made_up_check", "status": "FAIL", "expected": 1, "actual": 2, "detail": "d", "source": "x.py"}]
        lines = run_qa.next_steps(checks, {})
        self.assertTrue(any("No guidance is listed" in line for line in lines))
        self.assertFalse(any("What to do" in line for line in lines))

    def test_guidance_table_is_parsed_from_a_markdown_file(self):
        sample = WORK / "guide_sample.md"
        sample.write_text("| id | a | b | c |\n| --- | --- | --- | --- |\n"
                          "| `alpha` | meaning one | causes one | do one |\n| not backticked | x | y | z |\n"
                          "| `beta.sub` | m | c | d |\n", encoding="utf-8")
        guide = run_qa.load_guidance(str(sample))
        self.assertEqual(sorted(guide), ["alpha", "beta.sub"])
        self.assertEqual(guide["alpha"], {"meaning": "meaning one", "causes": "causes one", "action": "do one"})
        self.assertEqual(run_qa.load_guidance(str(WORK / "no_such_file.md")), {})


# --------------------------------------------------------------------------- merging (unit)

class MergeTests(unittest.TestCase):
    def make_part(self, script, rows):
        report = common.Report(script)
        for check_id, status, actual in rows:
            report.add(check_id, status, "e", actual, "d")
        report.commands.append([script, "arg"])
        return run_qa.Part(script, report, script)

    def test_same_unknown_key_from_two_parts_is_listed_once_but_other_duplicates_stay(self):
        parts = [self.make_part("a.py", [("unknown_spec_key", "WARN", "typo"), ("x", "PASS", 1)]),
                 self.make_part("b.py", [("unknown_spec_key", "WARN", "typo"), ("unknown_spec_key", "WARN", "other")])]
        merged = run_qa.merge("f.mp4", {}, None, parts, [], [])
        keys = [(c["id"], c["actual"], c["source"]) for c in merged.checks]
        self.assertEqual(keys, [("unknown_spec_key", "typo", "a.py"), ("x", 1, "a.py"), ("unknown_spec_key", "other", "b.py")])
        self.assertEqual(merged.commands, [["a.py", "arg"], ["b.py", "arg"]])

    def test_overall_is_the_worst_status_over_all_parts(self):
        for statuses, expected in ((("PASS", "PASS"), "PASS"), (("PASS", "WARN"), "WARN"), (("WARN", "FAIL"), "FAIL")):
            parts = [self.make_part("a.py", [("c1", statuses[0], 1)]), self.make_part("b.py", [("c2", statuses[1], 1)])]
            self.assertEqual(run_qa.merge("f", {}, None, parts, [], []).overall(), expected)

    def test_value_formatting(self):
        self.assertEqual(run_qa.show_value(None), "-")
        self.assertEqual(run_qa.show_value(True), "true")
        self.assertEqual(run_qa.show_value({"a": 1, "b": None, "c": False}), "a=1, b=-, c=false")
        self.assertEqual(run_qa.show_value([1, 2]), "[1, 2]")
        self.assertEqual(run_qa.show_value({"a": [1]}), '{"a": [1]}')

    def test_commands_are_shortened_for_display_only(self):
        tools = {"ffmpeg": "/opt/tools/ffmpeg", "ffprobe": "/opt/tools/ffprobe"}
        shown = run_qa.display_commands([["/opt/tools/ffprobe", "-i", os.path.abspath("a clip.mp4"), "-vf", "crop=iw*1:ih"]],
                                        "a clip.mp4", tools)
        self.assertEqual(shown, ['ffprobe -i "a clip.mp4" -vf "crop=iw*1:ih"'])


if __name__ == "__main__":
    unittest.main()
