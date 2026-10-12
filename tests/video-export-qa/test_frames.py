#!/usr/bin/env python3
# test_frames.py - tests for qa_frames.py (real ffmpeg, no mocks).
#
# Purpose: every check of qa_frames.py gets a happy path AND a negative control that fails for
#          the right reason: dropped frames, a duplicate, reversed order, double speed, a trimmed
#          start, wrong --expect values, an all-black clip (must FAIL, never PASS), damaged
#          barcodes, variable frame rate, a shifted barcode region, bad arguments and exit codes.
# Inputs:  none. Needs ffmpeg and ffprobe on PATH. Media is generated, never copied.
# Outputs: test results on stderr; generated media under a per-run folder in _work/video-export-qa/barcode-frames/.
# Run:     python -I -B -m unittest discover -s tests/video-export-qa -p "test_frames.py"
# Exit codes: 0 = all tests passed, 1 = a test failed.

import ast
import hashlib
import json
import os
import subprocess
import sys
import unittest

from ffmpeg_fixtures import fixture_command, optional_fixture, require_fixture, private_work_folder

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPTS = os.path.join(ROOT, "packages", "video-export-qa", "scripts")
WORK = private_work_folder(ROOT, "barcode-frames", "test_frames")
sys.path.insert(0, SCRIPTS)

import barcode  # noqa: E402
import common  # noqa: E402
import make_test_clip  # noqa: E402
import qa_frames  # noqa: E402

FFMPEG = None
FFPROBE = None
SIZE = (640, 360)
FRAMES = 96
BASE = None  # path of the reference clip, set in setUpModule


def setUpModule():
    global FFMPEG, FFPROBE, BASE
    FFMPEG = common.find_tool("ffmpeg")  # a missing tool is a loud error, not a silent skip
    FFPROBE = common.find_tool("ffprobe")
    os.makedirs(WORK, exist_ok=True)
    BASE = path_in_work("base.mp4")
    result = run_script("make_test_clip.py", BASE, "--overwrite")  # the real generator, default arguments
    if result.returncode != 0:
        raise AssertionError("could not generate the reference clip: %s" % result.stderr)


# ---------------------------------------------------------------- helpers

def path_in_work(name):
    return os.path.join(WORK, name)


def run_ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"] + list(args)
    proc = subprocess.run(fixture_command(cmd), capture_output=True)
    if proc.returncode != 0:
        raise AssertionError("ffmpeg failed: %s" % proc.stderr.decode("utf-8", errors="replace"))


def run_script(name, *args):
    cmd = [sys.executable, "-I", "-B", os.path.join(SCRIPTS, name)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def run_frames(path, *args):
    """Run qa_frames.py with --json. Returns (exit_code, parsed_result)."""
    require_fixture(path)
    proc = run_script("qa_frames.py", path, "--json", *args)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data


def checks_by_id(data):
    return {c["id"]: c for c in data["checks"]}


def statuses(data):
    return {c["id"]: c["status"] for c in data["checks"]}


def edit(name, vf, *extra):
    """Apply an ffmpeg filter to the reference clip and re-encode near-losslessly (barcode stays crisp)."""
    out = path_in_work(name)
    run_ffmpeg("-i", BASE, "-an", "-vf", vf, *extra, "-c:v", "libx264", "-crf", "14", out)
    return out


def compress(indices):
    """A list of indices in --expect notation (runs of +1 only). Used to state expectations."""
    parts = []
    start = previous = indices[0]
    for value in indices[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append("%d-%d" % (start, previous))
        start = previous = value
    parts.append("%d-%d" % (start, previous))
    return ",".join(parts)


def write_indices(name, indices, blank_at=()):
    """A clip whose frames carry exactly these indices; frames listed in blank_at are flat grey."""
    width, height = SIZE
    blank = bytes([128, 128, 128]) * (width * height)

    def source():
        for position, index in enumerate(indices):
            yield blank if position in blank_at else barcode.render_frame_rgb(index, width, height)

    path = path_in_work(name)
    make_test_clip.generate_clip(FFMPEG, path, source(), len(indices), "24", SIZE, tone=0, crf=14, overwrite=True)
    return path


# ---------------------------------------------------------------- the --expect grammar (pure)

class ExpectGrammarTests(unittest.TestCase):
    def test_contract_examples(self):
        self.assertEqual(qa_frames.parse_expect("0-95"), list(range(96)))
        self.assertEqual(qa_frames.parse_expect("95-48:-1"), list(range(95, 47, -1)))
        self.assertEqual(qa_frames.parse_expect("0-94:2"), list(range(0, 95, 2)))
        self.assertEqual(qa_frames.parse_expect("0-47,24-71"), list(range(48)) + list(range(24, 72)))

    def test_lengths(self):
        self.assertEqual(len(qa_frames.parse_expect("95-48:-1")), 48)
        self.assertEqual(len(qa_frames.parse_expect("0-94:2")), 48)
        self.assertEqual(len(qa_frames.parse_expect("0-47,24-71")), 96)

    def test_default_step_follows_direction(self):
        self.assertEqual(qa_frames.parse_expect("7-3"), [7, 6, 5, 4, 3])
        self.assertEqual(qa_frames.parse_expect("3-7"), [3, 4, 5, 6, 7])

    def test_single_frame_and_whitespace(self):
        self.assertEqual(qa_frames.parse_expect("5"), [5])
        self.assertEqual(qa_frames.parse_expect("5-5"), [5])
        self.assertEqual(qa_frames.parse_expect(" 0-2 , 5-6 "), [0, 1, 2, 5, 6])

    def test_range_stops_at_or_before_the_end(self):
        self.assertEqual(qa_frames.parse_expect("0-10:3"), [0, 3, 6, 9])
        self.assertEqual(qa_frames.parse_expect("0-9:3"), [0, 3, 6, 9])

    def test_whole_index_range(self):
        self.assertEqual(len(qa_frames.parse_expect("0-65535")), 65536)

    def test_a_short_string_cannot_expand_into_an_unbounded_list(self):
        # 7 bytes expand into 65536 numbers; repeating the segment multiplies that, so the total is capped
        # at four times the longest barcode clip BEFORE the list is built.
        limit = qa_frames.MAX_EXPECT_FRAMES
        self.assertEqual(limit, 4 * 65536)
        self.assertEqual(len(qa_frames.parse_expect(",".join(["0-65535"] * 4))), limit)  # at the limit: fine
        with self.assertRaises(common.UsageError) as caught:
            qa_frames.parse_expect(",".join(["0-65535"] * 5))
        self.assertIn("not a real edit", str(caught.exception))
        with self.assertRaises(common.UsageError):
            qa_frames.parse_expect(",".join(["0-65535"] * 50))  # 400 bytes of text, would be 3.3 million numbers

    def test_bad_grammar_is_a_usage_error(self):
        for bad in ("", "   ", "a", "0-", "-5", "0-95:0", "0-95:-1", "95-0:1", "0-70000", "0-5,,7", "0-95:2:3", "0..5"):
            with self.assertRaises(common.UsageError, msg=repr(bad)):
                qa_frames.parse_expect(bad)

    def test_region_parsing(self):
        self.assertEqual(qa_frames.parse_region(None), barcode.DEFAULT_REGION)
        self.assertEqual(qa_frames.parse_region("0,0.1,1,0.2"), (0.0, 0.1, 1.0, 0.2))
        for bad in ("1,2,3", "0,0,1.5,1", "0.5,0,0.5,1", "0,0.6,1,0.5", "a,b,c,d", "-0.1,0,1,1"):
            with self.assertRaises(common.UsageError, msg=repr(bad)):
                qa_frames.parse_region(bad)


# ---------------------------------------------------------------- the index-sequence analysis (pure)

class SequenceAnalysisTests(unittest.TestCase):
    def test_identity_is_one_run(self):
        runs = qa_frames.find_runs(list(range(10)))
        self.assertEqual(runs, [{"from": 0, "to": 9, "step": 1, "count": 10, "at_output_frame": 0}])

    def test_dropped_frames_are_located(self):
        events = qa_frames.find_dropped([0, 1, 2, 5, 6])
        self.assertEqual(events, [{"output_frame": 3, "after_index": 2, "next_index": 5,
                                   "missing_count": 2, "missing_from": 3, "missing_to": 4}])

    def test_an_undecodable_frame_is_not_a_drop_but_a_real_gap_beside_it_is(self):
        self.assertEqual(qa_frames.find_dropped([0, 1, None, 3, 4]), [])
        events = qa_frames.find_dropped([0, 1, None, 4])
        self.assertEqual([(e["missing_from"], e["missing_to"]) for e in events], [(3, 3)])

    def test_duplicates(self):
        self.assertEqual(qa_frames.find_duplicates([0, 1, 1, 2]), [{"index": 1, "output_frames": [1, 2], "count": 2}])
        self.assertEqual(qa_frames.find_duplicates([0, 1, 2, 3]), [])
        far = qa_frames.find_duplicates([0, 1, 2, 1, 2])  # repeated but not adjacent (overlap)
        self.assertEqual([d["index"] for d in far], [1, 2])

    def test_backwards_steps(self):
        self.assertEqual(len(qa_frames.find_backwards([5, 4, 3])), 2)
        self.assertEqual(qa_frames.find_backwards([0, 1, 2]), [])

    def test_runs_with_steps_holds_and_breaks(self):
        self.assertEqual(qa_frames.describe_sequence(qa_frames.find_runs([95, 94, 93]), 3), "95-93")
        runs = qa_frames.find_runs(list(range(0, 95, 2)))
        self.assertEqual(len(runs), 1)
        self.assertEqual(qa_frames.describe_sequence(runs, 48), "0-94:2")
        self.assertEqual(qa_frames.describe_sequence(qa_frames.find_runs([5, 5, 5]), 3), "5x3")
        seq = list(range(0, 30)) + list(range(36, 96))
        self.assertEqual(qa_frames.describe_sequence(qa_frames.find_runs(seq), len(seq)), "0-29,36-95")

    def test_undecodable_frames_show_as_question_marks(self):
        seq = [0, 1, 2, None, 4, 5, None, None, 8]
        self.assertEqual(qa_frames.describe_sequence(qa_frames.find_runs(seq), len(seq)), "0-2,?,4-5,?x2,8")

    def test_comparison_counts_and_first_mismatch(self):
        self.assertEqual(qa_frames.compare_with_expected([0, 1, 2], [0, 1, 2]), (0, None))
        self.assertEqual(qa_frames.compare_with_expected([0, 1, 9], [0, 1, 2]), (1, 2))
        self.assertEqual(qa_frames.compare_with_expected([0, 1], [0, 1, 2]), (1, 2))  # missing tail
        self.assertEqual(qa_frames.compare_with_expected([0, None, 2], [0, 1, 2]), (1, 1))  # None never matches


# ---------------------------------------------------------------- real media: the edits and their expectations

class EditTests(unittest.TestCase):
    """Each edit is defined independently of the tool (the expected indices follow from the edit),
    then qa_frames must report it: the matching --expect passes, a wrong one fails, and without
    --expect the right continuity check fails for the right reason."""

    @classmethod
    def setUpClass(cls):
        cls.identity = BASE
        cls.dropped = edit("dropped_30_35.mp4", "select='not(between(n,30,35))',setpts=N/FRAME_RATE/TB")
        cls.reversed = edit("reversed.mp4", "reverse,setpts=N/FRAME_RATE/TB")
        cls.double = edit("double_speed.mp4", "select='not(mod(n,2))',setpts=N/FRAME_RATE/TB")
        cls.trimmed = edit("trimmed_24.mp4", "trim=start_frame=24,setpts=N/FRAME_RATE/TB")
        # one frame (40) played twice: frames 0..40 then 40..95, built with split/trim/concat
        cls.duplicate = path_in_work("duplicate_40.mp4")
        run_ffmpeg("-i", BASE, "-an", "-filter_complex",
                   "[0:v]split=2[a][b];[a]trim=end_frame=41,setpts=PTS-STARTPTS[a1];"
                   "[b]trim=start_frame=40,setpts=PTS-STARTPTS[b1];[a1][b1]concat=n=2:v=1:a=0,setpts=N/(24*TB)",
                   "-fps_mode", "cfr", "-r", "24", "-c:v", "libx264", "-crf", "14", cls.duplicate)

    def assert_expect_pair(self, path, right, wrong):
        code, data = run_frames(path, "--expect", right)
        self.assertEqual(code, 0, data["checks"])
        self.assertEqual(checks_by_id(data)["expect"]["status"], "PASS")
        self.assertEqual(data["overall"], "PASS")
        code, data = run_frames(path, "--expect", wrong)
        self.assertEqual(code, 1)
        self.assertEqual(checks_by_id(data)["expect"]["status"], "FAIL")
        self.assertEqual(data["overall"], "FAIL")
        return data

    # identity ---------------------------------------------------------

    def test_identity_passes_every_check(self):
        code, data = run_frames(self.identity)
        self.assertEqual(code, 0)
        self.assertEqual(data["overall"], "PASS")
        self.assertEqual(statuses(data), {
            "all_frames_decodable": "PASS", "low_block_margin": "PASS", "no_dropped_frames": "PASS",
            "no_duplicate_frames": "PASS", "monotonic": "PASS",
        })
        info = data["info"]
        self.assertEqual((info["frames_total"], info["frames_decoded"], info["frames_undecodable"]), (96, 96, 0))
        self.assertEqual((info["first_index"], info["last_index"]), (0, 95))
        self.assertEqual(info["runs"], [{"from": 0, "to": 95, "step": 1, "count": 96, "at_output_frame": 0}])
        self.assertEqual((info["dropped"], info["duplicated"], info["reversed_runs"]), ([], [], []))

    def test_identity_expect_pair(self):
        self.assert_expect_pair(self.identity, "0-95", "0-94")

    def test_wrong_expects_name_the_first_mismatch(self):
        _, data = run_frames(self.identity, "--expect", "1-96")
        detail = checks_by_id(data)["expect"]["detail"]
        self.assertIn("first mismatch at output frame 0: expected 1, got 0", detail)
        self.assertIn("96 mismatching", detail)
        _, data = run_frames(self.identity, "--expect", "0-99")
        detail = checks_by_id(data)["expect"]["detail"]
        self.assertIn("decoded 96 frames, expected 100", detail)
        self.assertIn("first mismatch at output frame 96: expected 96, got end of file", detail)
        _, data = run_frames(self.identity, "--expect", "0-94")
        self.assertIn("expected end of file, got 95", checks_by_id(data)["expect"]["detail"])

    def test_expect_makes_the_continuity_checks_skip_with_a_reason(self):
        _, data = run_frames(self.identity, "--expect", "0-95")
        for check_id in ("no_dropped_frames", "no_duplicate_frames", "monotonic"):
            check = checks_by_id(data)[check_id]
            self.assertEqual(check["status"], "SKIP")
            self.assertIn("--expect", check["detail"])

    # dropped frames 30-35 ---------------------------------------------

    def test_dropped_frames_are_found_without_expect(self):
        code, data = run_frames(self.dropped)
        self.assertEqual(code, 1)
        self.assertEqual(statuses(data)["no_dropped_frames"], "FAIL")
        self.assertEqual(statuses(data)["no_duplicate_frames"], "PASS")
        self.assertEqual(statuses(data)["monotonic"], "PASS")
        self.assertEqual(data["info"]["frames_total"], 90)
        self.assertEqual(data["info"]["dropped"], [{
            "output_frame": 30, "after_index": 29, "next_index": 36,
            "missing_count": 6, "missing_from": 30, "missing_to": 35,
        }])
        self.assertIn("source 30-35 missing", checks_by_id(data)["no_dropped_frames"]["detail"])

    def test_dropped_frames_expect_pair(self):
        data = self.assert_expect_pair(self.dropped, "0-29,36-95", "0-95")
        self.assertIn("decoded 90 frames, expected 96", checks_by_id(data)["expect"]["detail"])
        self.assertIn("first mismatch at output frame 30: expected 30, got 36", checks_by_id(data)["expect"]["detail"])

    def test_dropped_frames_same_length_wrong_expect(self):
        _, data = run_frames(self.dropped, "--expect", "0-89")
        detail = checks_by_id(data)["expect"]["detail"]
        self.assertNotIn("decoded 90 frames, expected", detail)  # same length: only indices differ
        self.assertIn("60 mismatching", detail)

    # one duplicated frame ---------------------------------------------

    def test_duplicate_is_found_without_expect(self):
        code, data = run_frames(self.duplicate)
        self.assertEqual(code, 1)
        self.assertEqual(statuses(data)["no_duplicate_frames"], "FAIL")
        self.assertEqual(statuses(data)["no_dropped_frames"], "PASS")
        self.assertEqual(data["info"]["frames_total"], 97)
        self.assertEqual(data["info"]["duplicated"], [{"index": 40, "output_frames": [40, 41], "count": 2}])

    def test_duplicate_expect_pair(self):
        self.assert_expect_pair(self.duplicate, "0-40,40-95", "0-95")

    # reversed ----------------------------------------------------------

    def test_reversed_is_a_warning_not_a_failure(self):
        code, data = run_frames(self.reversed)
        self.assertEqual(code, 0)  # WARN-only exits 0
        self.assertEqual(data["overall"], "WARN")
        self.assertEqual(statuses(data)["monotonic"], "WARN")
        self.assertEqual(statuses(data)["no_dropped_frames"], "PASS")
        self.assertEqual(statuses(data)["no_duplicate_frames"], "PASS")
        info = data["info"]
        self.assertEqual(info["backward_steps_total"], 95)
        self.assertEqual(info["reversed_runs"], [{"from": 95, "to": 0, "step": -1, "count": 96, "at_output_frame": 0}])

    def test_reversed_expect_pair(self):
        self.assert_expect_pair(self.reversed, "95-0", "0-95")

    # double speed ------------------------------------------------------

    def test_double_speed_drops_every_other_frame(self):
        code, data = run_frames(self.double)
        self.assertEqual(code, 1)
        self.assertEqual(statuses(data)["no_dropped_frames"], "FAIL")
        info = data["info"]
        self.assertEqual(info["frames_total"], 48)
        self.assertEqual(info["dropped_frames_total"], 47)
        self.assertEqual(len(info["dropped"]), 47)
        self.assertEqual(info["runs"], [{"from": 0, "to": 94, "step": 2, "count": 48, "at_output_frame": 0}])

    def test_double_speed_expect_pair(self):
        self.assert_expect_pair(self.double, "0-94:2", "0-95")

    # trimmed start -----------------------------------------------------

    def test_a_trim_at_the_start_is_flagged_without_expect_and_the_first_index_is_reported(self):
        # Without --expect the clip is assumed to start at source frame 0, so a first readable frame of
        # 24 means 24 frames were cut from the start: a WARN (it may be intended), never a silent PASS.
        code, data = run_frames(self.trimmed)
        self.assertEqual(code, 0)  # WARN only
        self.assertEqual(data["overall"], "WARN")
        check = checks_by_id(data)["no_dropped_frames"]
        self.assertEqual(check["status"], "WARN")
        self.assertIn("source frame(s) 0-23 are missing from the start", check["detail"])
        self.assertIn("--expect", check["detail"])
        self.assertEqual(statuses(data)["no_duplicate_frames"], "PASS")
        self.assertEqual((data["info"]["first_index"], data["info"]["last_index"]), (24, 95))
        self.assertEqual(data["info"]["frames_total"], 72)

    def test_a_clip_that_starts_at_source_zero_gets_no_start_warning(self):
        # Negative control for the start check: the untouched clip, and a reversed clip (which starts at
        # 95 but is judged by `monotonic`, not called a trim).
        _, data = run_frames(self.identity)
        self.assertEqual(statuses(data)["no_dropped_frames"], "PASS")
        _, data = run_frames(self.reversed)
        self.assertEqual(statuses(data)["no_dropped_frames"], "PASS")

    def test_a_cut_at_the_start_and_a_gap_are_both_named_when_both_happen(self):
        path = write_indices("start_cut_and_gap.mp4", list(range(5, 20)) + list(range(25, 40)))
        _, data = run_frames(path)
        check = checks_by_id(data)["no_dropped_frames"]
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("source 20-24 missing", check["detail"])
        self.assertIn("source frame(s) 0-4 are missing before it", check["detail"])

    def test_trimmed_start_expect_pair(self):
        data = self.assert_expect_pair(self.trimmed, "24-95", "0-95")
        self.assertIn("first mismatch at output frame 0: expected 0, got 24", checks_by_id(data)["expect"]["detail"])

    def test_the_wrong_edit_is_not_accepted_for_another_edit(self):
        # every edit must FAIL against the expectation of every other edit
        edits = {
            "dropped": (self.dropped, "0-29,36-95"), "duplicate": (self.duplicate, "0-40,40-95"),
            "reversed": (self.reversed, "95-0"), "double": (self.double, "0-94:2"),
            "trimmed": (self.trimmed, "24-95"), "identity": (self.identity, "0-95"),
        }
        for name, (path, _) in edits.items():
            for other, (_, other_expect) in edits.items():
                if name == other:
                    continue
                code, data = run_frames(path, "--expect", other_expect)
                self.assertEqual(code, 1, "%s clip must not satisfy the %s expectation" % (name, other))


# ---------------------------------------------------------------- real media: things that must FAIL, never pass

class FaintBarcodeTests(unittest.TestCase):
    """Frame blends can decode to a wrong but valid frame number, so a faint barcode is a WARN
    (low_block_margin) and the findings that depend on it carry a caution. Every legitimate transform
    measured here keeps a margin well above the warning level (negative control)."""

    @classmethod
    def setUpClass(cls):
        cls.blend = edit("blend_tblend.mp4", "tblend=all_mode=average", "-pix_fmt", "yuv420p")
        cls.crf51 = path_in_work("crf51.mp4")
        run_ffmpeg("-i", BASE, "-an", "-c:v", "libx264", "-crf", "51", "-pix_fmt", "yuv420p", cls.crf51)
        cls.dark = path_in_work("brightness_minus_02.mp4")
        run_ffmpeg("-i", BASE, "-an", "-vf", "eq=brightness=-0.2", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", cls.dark)
        cls.rotated = path_in_work("rotated_90.mp4")
        rotated = cls.rotated
        def build_rotated():
            run_ffmpeg("-display_rotation", "90", "-i", BASE, "-c", "copy", rotated)
            return rotated
        cls.rotated = optional_fixture(FFMPEG, "-display_rotation", build_rotated)
        cls.small = path_in_work("small_34x20.mp4")
        run_ffmpeg("-i", BASE, "-an", "-vf", "scale=34:20:flags=bilinear", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", cls.small)
        cls.thin = path_in_work("thin_30x18.mp4")
        run_ffmpeg("-i", BASE, "-an", "-vf", "scale=30:18:flags=bilinear", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", cls.thin)
        cls.black = path_in_work("black.mp4")
        run_ffmpeg("-f", "lavfi", "-i", "color=c=black:s=640x360:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.black)

    def test_a_blend_is_flagged_as_faint(self):
        code, data = run_frames(self.blend)
        check = checks_by_id(data)["low_block_margin"]
        self.assertEqual(check["status"], "WARN")
        self.assertLess(check["actual"], qa_frames.LOW_MARGIN)
        self.assertIn("blend", check["detail"].lower())
        self.assertEqual(code, 1)  # the unreadable frames fail the file; the WARN explains why

    def test_blended_frames_decode_to_wrong_valid_numbers_not_only_to_nothing(self):
        # The reason the check exists: some blended frames are NOT undecodable. They read as a valid number
        # that belongs to neither source frame next to them.
        decoded = qa_frames.decode_video(FFMPEG, self.blend, barcode.DEFAULT_REGION, [])
        wrong = [(k, index) for k, index in enumerate(decoded.sequence)
                 if index is not None and index not in (k - 1, k, k + 1)]
        self.assertTrue(wrong, "expected at least one blended frame that decodes to an unrelated number")
        self.assertTrue(any(index is None for index in decoded.sequence), "and some that do not decode at all")

    def test_the_findings_carry_a_caution_when_the_barcode_is_damaged(self):
        _, data = run_frames(self.blend)
        for check_id in ("no_dropped_frames", "no_duplicate_frames"):
            self.assertIn("Caution", checks_by_id(data)[check_id]["detail"], check_id)
        _, data = run_frames(self.blend, "--expect", "0-94")
        self.assertIn("Caution", checks_by_id(data)["expect"]["detail"])

    def test_no_caution_on_a_clean_clip(self):
        _, data = run_frames(self.crf51, "--expect", "0-95")
        self.assertEqual(checks_by_id(data)["expect"]["status"], "PASS")
        self.assertNotIn("Caution", json.dumps(data["checks"]))

    def test_clean_but_harsh_transforms_keep_the_margin_above_the_warning_level(self):
        for label, path in (("crf 51", self.crf51), ("brightness minus 0.2", self.dark)):
            with self.subTest(label):
                code, data = run_frames(path, "--expect", "0-95")
                self.assertEqual(code, 0)
                self.assertEqual(checks_by_id(data)["low_block_margin"]["status"], "PASS")
                self.assertGreaterEqual(data["info"]["min_block_margin"], qa_frames.LOW_MARGIN)

    def test_a_clip_with_no_barcode_has_no_margin_to_judge(self):
        _, data = run_frames(self.black)
        self.assertEqual(checks_by_id(data)["low_block_margin"]["status"], "SKIP")
        self.assertEqual(checks_by_id(data)["all_frames_decodable"]["status"], "FAIL")

    def test_rotation_metadata_does_not_hide_the_barcode(self):
        code, data = run_frames(self.rotated, "--expect", "0-95")
        self.assertEqual(code, 0, checks_by_id(data)["all_frames_decodable"])
        self.assertEqual(data["info"]["frames_undecodable"], 0)

    def test_negative_control_without_noautorotate_a_rotated_file_is_unreadable(self):
        require_fixture(self.rotated)
        cmd = qa_frames.build_decode_command(FFMPEG, self.rotated, barcode.DEFAULT_REGION, qa_frames.SYNC_OPTIONS[0])
        self.assertIn("-noautorotate", cmd)
        cmd.remove("-noautorotate")
        decoded = qa_frames.decode_once(cmd, [])
        self.assertEqual(decoded.returncode, 0)
        self.assertTrue(decoded.sequence and all(index is None for index in decoded.sequence))

    def test_small_exports_are_still_read(self):
        code, data = run_frames(self.small, "--expect", "0-95")
        self.assertEqual(code, 0, data["checks"])

    def test_a_strip_too_thin_to_crop_stops_with_a_hint_and_exit_code_2(self):
        proc = run_script("qa_frames.py", self.thin, "--expect", "0-95")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Hint: the barcode strip is thinner than one pixel row", proc.stderr)
        self.assertEqual(proc.stdout, "")


class NoEmptyPassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.black = path_in_work("all_black.mp4")
        run_ffmpeg("-f", "lavfi", "-i", "color=c=black:s=640x360:r=24:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   cls.black)

    def test_all_black_clip_fails_and_never_passes(self):
        code, data = run_frames(self.black)
        self.assertEqual(code, 1)
        self.assertEqual(data["overall"], "FAIL")
        self.assertEqual(statuses(data)["all_frames_decodable"], "FAIL")
        info = data["info"]
        self.assertEqual((info["frames_total"], info["frames_decoded"], info["frames_undecodable"]), (96, 0, 96))
        self.assertEqual(info["undecodable_reasons"], {"marker mismatch": 96})
        self.assertNotIn("PASS", [c["status"] for c in data["checks"]])  # nothing may pass on an empty measurement
        for check_id in ("no_dropped_frames", "no_duplicate_frames", "monotonic"):
            self.assertEqual(statuses(data)[check_id], "SKIP")

    def test_all_black_clip_fails_every_expect(self):
        for expect in ("0-95", "0-0", "5"):
            code, data = run_frames(self.black, "--expect", expect)
            self.assertEqual(code, 1)
            self.assertEqual(checks_by_id(data)["expect"]["status"], "FAIL")
            self.assertIn("first mismatch at output frame 0", checks_by_id(data)["expect"]["detail"])

    def test_all_white_clip_also_fails(self):
        white = path_in_work("all_white.mp4")
        run_ffmpeg("-f", "lavfi", "-i", "color=c=white:s=640x360:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", white)
        code, data = run_frames(white)
        self.assertEqual(code, 1)
        self.assertEqual(data["info"]["frames_decoded"], 0)

    def test_a_clip_without_the_barcode_but_with_picture_content_fails(self):
        testsrc = path_in_work("testsrc.mp4")
        run_ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", testsrc)
        code, data = run_frames(testsrc, "--expect", "0-47")
        self.assertEqual(code, 1)
        self.assertEqual(data["info"]["frames_decoded"], 0)

    def test_mirrored_clip_fails_with_marker_mismatch(self):
        mirrored = edit("mirrored.mp4", "hflip")
        code, data = run_frames(mirrored)
        self.assertEqual(code, 1)
        self.assertEqual(data["info"]["undecodable_reasons"], {"marker mismatch": 96})

    def test_a_damaged_frame_in_the_middle(self):
        path = write_indices("blank_at_10.mp4", list(range(10)) + [10] + list(range(11, 96)), blank_at=(10,))
        code, data = run_frames(path)
        self.assertEqual(code, 1)
        self.assertEqual(statuses(data)["all_frames_decodable"], "FAIL")
        self.assertEqual(data["info"]["undecodable_first_output_frames"], [10])
        # neighbours 9 and 11 are consistent with a single undecodable frame between them
        self.assertEqual(statuses(data)["no_dropped_frames"], "PASS")
        code, data = run_frames(path, "--expect", "0-95")
        self.assertEqual(code, 1)
        detail = checks_by_id(data)["expect"]["detail"]
        self.assertIn("first mismatch at output frame 10: expected 10, got undecodable", detail)
        self.assertIn("0-9,?,11-95", checks_by_id(data)["expect"]["actual"])

    def test_a_missing_frame_next_to_a_damaged_one_is_still_reported_as_dropped(self):
        path = write_indices("blank_then_skip.mp4", list(range(10)) + [10] + list(range(12, 96)), blank_at=(10,))
        _, data = run_frames(path)
        dropped = data["info"]["dropped"]
        self.assertEqual([(e["missing_from"], e["missing_to"]) for e in dropped], [(11, 11)])

    def test_overlapping_segments_expect_grammar(self):
        indices = list(range(48)) + list(range(24, 72))
        path = write_indices("overlap.mp4", indices)
        code, data = run_frames(path, "--expect", "0-47,24-71")
        self.assertEqual(code, 0)
        self.assertEqual(checks_by_id(data)["expect"]["status"], "PASS")
        code, data = run_frames(path)  # without --expect the repeat is a duplicate and the jump back a warning
        self.assertEqual(statuses(data)["no_duplicate_frames"], "FAIL")
        self.assertEqual(statuses(data)["monotonic"], "WARN")
        code, data = run_frames(path, "--expect", "0-71")
        self.assertEqual(code, 1)


class EmptyMeasurementTests(unittest.TestCase):
    """Rule: an empty or missing measurement is never a pass. A real clip with zero decodable
    frames cannot be built with ffmpeg, so the zero-frame branch is driven directly."""

    def build_report(self, expect=None):
        report = common.Report("qa_frames.py", file="empty.mp4")
        decoded = qa_frames.Decoded()  # nothing was decoded
        facts = {"dropped": [], "duplicated": [], "backwards": []}
        qa_frames.add_decodable_check(report, decoded)
        qa_frames.add_continuity_checks(report, decoded.sequence, expect is not None, facts)
        if expect is not None:
            qa_frames.add_expect_check(report, decoded.sequence, [], expect, qa_frames.parse_expect(expect))
        return report

    def test_zero_frames_fail_and_nothing_passes(self):
        report = self.build_report()
        by_id = {c["id"]: c["status"] for c in report.checks}
        self.assertEqual(by_id["all_frames_decodable"], "FAIL")
        self.assertEqual(by_id["no_dropped_frames"], "SKIP")
        self.assertEqual(by_id["no_duplicate_frames"], "SKIP")
        self.assertEqual(by_id["monotonic"], "SKIP")
        self.assertEqual(report.overall(), "FAIL")
        self.assertEqual(report.exit_code(), 1)
        self.assertNotIn("PASS", by_id.values())

    def test_zero_frames_fail_the_expect_check_too(self):
        report = self.build_report(expect="0-95")
        by_id = {c["id"]: c for c in report.checks}
        self.assertEqual(by_id["expect"]["status"], "FAIL")
        self.assertIn("empty measurement", by_id["expect"]["detail"])
        self.assertEqual(report.exit_code(), 1)

    def test_a_single_decodable_frame_is_not_enough_to_pass_continuity(self):
        report = common.Report("qa_frames.py", file="one.mp4")
        qa_frames.add_continuity_checks(report, [7], False, {"dropped": [], "duplicated": [], "backwards": []})
        self.assertEqual([c["status"] for c in report.checks], ["SKIP", "SKIP", "SKIP"])

    def test_negative_control_the_same_report_passes_when_frames_exist(self):
        report = common.Report("qa_frames.py", file="ok.mp4")
        decoded = qa_frames.Decoded()
        for index in range(5):
            decoded.add(index, barcode.OK, 120.0)
        qa_frames.add_decodable_check(report, decoded)
        self.assertEqual(report.checks[0]["status"], "PASS")

    def test_audio_only_file_cannot_be_checked_exit_2(self):
        wav = path_in_work("audio_only.wav")
        run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", wav)
        proc = run_script("qa_frames.py", wav)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")  # no table claiming anything


# ---------------------------------------------------------------- variable frame rate, region, resolution

class DecodeConditionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A genuinely variable-frame-rate clip: every fifth frame (3, 8, 13, ...) is removed but the
        # remaining frames keep their original timestamps, so the file has gaps of two different sizes.
        cls.kept = [i for i in range(FRAMES) if i % 5 != 3]
        cls.vfr = path_in_work("vfr_gaps.mp4")
        run_ffmpeg("-i", BASE, "-an", "-vf", "select='not(eq(mod(n,5),3))'", "-fps_mode", "vfr",
                   "-c:v", "libx264", "-crf", "14", cls.vfr)

    def raw_frame_count(self, *sync_args):
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", self.vfr, "-map", "0:v:0",
               "-vf", qa_frames.decode_filter(barcode.DEFAULT_REGION)] + list(sync_args) + ["-f", "rawvideo", "-"]
        proc = subprocess.run(fixture_command(cmd), capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(len(proc.stdout) % barcode.DECODE_FRAME_BYTES, 0)
        return len(proc.stdout) // barcode.DECODE_FRAME_BYTES

    def test_the_vfr_clip_really_is_variable_frame_rate(self):
        proc = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                               "-of", "csv=p=0", self.vfr], capture_output=True, text=True)
        times = sorted(float(t.strip(",")) for t in proc.stdout.split() if t.strip(","))
        gaps = {round(b - a, 4) for a, b in zip(times, times[1:])}
        self.assertEqual(len(gaps), 2, gaps)
        self.assertEqual(len(times), len(self.kept))

    def test_variable_frame_rate_is_decoded_without_inventing_or_dropping_frames(self):
        code, data = run_frames(self.vfr, "--expect", compress(self.kept))
        self.assertEqual(code, 0, checks_by_id(data)["expect"])
        self.assertEqual(data["info"]["frames_total"], len(self.kept))

    def test_negative_control_cfr_conversion_while_decoding_invents_frames(self):
        self.assertGreater(self.raw_frame_count("-fps_mode", "cfr", "-r", "24"), len(self.kept))

    def test_negative_control_rawvideo_default_is_not_exact_on_vfr(self):
        # ffmpeg's rawvideo default is NOT passthrough: this is why qa_frames passes the option explicitly
        self.assertNotEqual(self.raw_frame_count(), len(self.kept))
        self.assertEqual(self.raw_frame_count("-fps_mode", "passthrough"), len(self.kept))

    def test_region_option_reads_a_letterboxed_export(self):
        letterbox = edit("letterbox.mp4", "pad=640:420:0:60:black")  # 60 px black bar on top
        code, data = run_frames(letterbox, "--expect", "0-95")
        self.assertEqual(code, 1)  # the default region now sees only the black bar
        self.assertEqual(data["info"]["frames_decoded"], 0)
        code, data = run_frames(letterbox, "--expect", "0-95", "--region", "0,0.15,1,0.21")
        self.assertEqual(code, 0, checks_by_id(data)["expect"])
        self.assertEqual(data["info"]["barcode"]["region"], [0.0, 0.15, 1.0, 0.21])

    def test_rescaled_exports_decode_at_other_resolutions(self):
        for name, scale in (("down180.mp4", "scale=320:180"), ("up1080.mp4", "scale=1920:1080")):
            path = edit(name, scale)
            code, data = run_frames(path, "--expect", "0-95")
            self.assertEqual(code, 0, name)

    def test_barcode_survives_another_codec_and_container(self):
        out = path_in_work("mpeg4.mkv")
        run_ffmpeg("-i", BASE, "-an", "-c:v", "mpeg4", out)
        code, data = run_frames(out, "--expect", "0-95")
        self.assertEqual(code, 0)


# ---------------------------------------------------------------- command line contract

class CommandLineTests(unittest.TestCase):
    def test_table_output_and_show_runs(self):
        proc = run_script("qa_frames.py", BASE, "--show-runs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("qa_frames.py: PASS", proc.stdout)
        self.assertIn("[PASS] all_frames_decodable", proc.stdout)
        self.assertIn("output 0-95 -> source 0-95  (96 frames)", proc.stdout)
        plain = run_script("qa_frames.py", BASE)
        self.assertNotIn("runs (output frames", plain.stdout)

    def test_json_result_has_the_contract_shape(self):
        code, data = run_frames(BASE, "--expect", "0-95")
        self.assertEqual(set(data), {"tool", "file", "overall", "checks", "info", "provenance"})
        self.assertEqual(data["tool"], "qa_frames.py")
        self.assertEqual(data["file"], BASE)
        self.assertIn(data["overall"], ("PASS", "WARN", "FAIL"))
        for check in data["checks"]:
            self.assertEqual(set(check), {"id", "status", "expected", "actual", "detail"})
            self.assertIn(check["status"], ("PASS", "WARN", "FAIL", "SKIP"))
        for key in ("frames_total", "frames_decoded", "frames_undecodable", "runs", "dropped", "duplicated",
                    "reversed_runs", "first_index", "last_index", "min_block_margin"):
            self.assertIn(key, data["info"])

    def test_provenance_is_complete_and_the_hash_is_right(self):
        _, data = run_frames(BASE)
        prov = data["provenance"]
        self.assertEqual(prov["script"], "qa_frames.py")
        self.assertEqual(prov["version"], common.VERSION)
        self.assertTrue(prov["python"].startswith("3."))
        self.assertTrue(prov["ffmpeg_version"].startswith("ffmpeg version"))
        self.assertTrue(prov["ffprobe_version"].startswith("ffprobe version"))
        self.assertIsNone(prov["spec_sha256"])
        with open(BASE, "rb") as fh:
            self.assertEqual(prov["input"]["sha256"], hashlib.sha256(fh.read()).hexdigest())
        self.assertEqual(prov["input"]["size_bytes"], os.path.getsize(BASE))
        self.assertTrue(prov["commands"])
        for cmd in prov["commands"]:
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(part, str) for part in cmd))

    def test_negative_control_provenance_hash_changes_with_the_file(self):
        _, first = run_frames(BASE)
        _, second = run_frames(edit("trimmed_for_hash.mp4", "trim=start_frame=24,setpts=N/FRAME_RATE/TB"))
        self.assertNotEqual(first["provenance"]["input"]["sha256"], second["provenance"]["input"]["sha256"])

    def test_exit_code_2_for_input_that_cannot_be_read(self):
        text = path_in_work("not_a_video.txt")
        with open(text, "w") as fh:
            fh.write("this is not a video")
        empty = path_in_work("empty.mp4")
        open(empty, "wb").close()
        for target in (path_in_work("no_such_file.mp4"), WORK, text, empty):
            proc = run_script("qa_frames.py", target)
            self.assertEqual(proc.returncode, 2, "%s -> %s %s" % (target, proc.stdout, proc.stderr))
            self.assertTrue(proc.stderr.startswith("error:"), proc.stderr)

    def test_exit_code_2_for_bad_arguments(self):
        for args in (["--region", "1,2,3"], ["--expect", "nonsense"], ["--expect", ""],
                     ["--ffmpeg", path_in_work("no_such_ffmpeg")]):
            proc = run_script("qa_frames.py", BASE, *args)
            self.assertEqual(proc.returncode, 2, "%s -> %s" % (args, proc.stderr))
        self.assertEqual(run_script("qa_frames.py").returncode, 2)  # no file given

    def test_exit_codes_0_and_1(self):
        self.assertEqual(run_script("qa_frames.py", BASE).returncode, 0)
        self.assertEqual(run_script("qa_frames.py", BASE, "--expect", "1-96").returncode, 1)

    def test_bad_expect_fails_before_any_decoding(self):
        proc = run_script("qa_frames.py", path_in_work("no_such_file.mp4"), "--expect", "nonsense")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--expect", proc.stderr)  # the argument is reported, not the missing file

    def test_script_runs_under_isolated_mode_from_another_directory(self):
        proc = subprocess.run([sys.executable, "-I", "-B", os.path.join(SCRIPTS, "qa_frames.py"), BASE, "--json"],
                              capture_output=True, text=True, cwd=WORK)
        self.assertEqual(proc.returncode, 0, proc.stderr)


# ---------------------------------------------------------------- sequential decoding, no seeking

class SequentialDecodeTests(unittest.TestCase):
    SEEK_OPTIONS = {"-ss", "-sseof", "-t", "-to", "-itsoffset", "-accurate_seek", "-seek_timestamp",
                    "-noaccurate_seek", "-seek2any"}

    def test_decode_command_never_seeks(self):
        for option in qa_frames.SYNC_OPTIONS:
            cmd = qa_frames.build_decode_command("ffmpeg", "input.mp4", barcode.DEFAULT_REGION, option)
            self.assertFalse(self.SEEK_OPTIONS & set(cmd), cmd)
            self.assertEqual(cmd.count("-i"), 1)
            self.assertEqual(cmd[cmd.index("-i") + 1], "input.mp4")
            self.assertIn("passthrough", cmd)

    def test_the_recorded_commands_of_a_real_run_never_seek(self):
        _, data = run_frames(BASE)
        for cmd in data["provenance"]["commands"]:
            self.assertFalse(self.SEEK_OPTIONS & set(cmd), cmd)
            self.assertIn("-protocol_whitelist", cmd)  # local files only: no network from a playlist

    def test_filter_matches_the_contract(self):
        vf = qa_frames.decode_filter(barcode.DEFAULT_REGION)
        self.assertTrue(vf.startswith("crop="))
        self.assertTrue(vf.endswith("scale=208:4:flags=area,format=gray"))

    def test_no_script_calls_seek(self):
        for name in ("qa_frames.py", "barcode.py", "make_test_clip.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            seeks = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("seek", "tell")]
            self.assertEqual(seeks, [], name)

    def test_negative_control_the_seek_detectors_can_fire(self):
        tree = ast.parse("stream.seek(10)")
        self.assertTrue([n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "seek"])
        cmd = qa_frames.build_decode_command("ffmpeg", "input.mp4", barcode.DEFAULT_REGION, qa_frames.SYNC_OPTIONS[0])
        self.assertTrue(self.SEEK_OPTIONS & set(cmd + ["-ss", "5"]))
        self.assertFalse(self.SEEK_OPTIONS & set(cmd))

    def test_every_frame_is_examined(self):
        # a 300-frame clip: the reader returns one entry per frame, not a sample
        path = write_indices("long300.mp4", list(range(300)))
        code, data = run_frames(path, "--expect", "0-299")
        self.assertEqual(code, 0)
        self.assertEqual(data["info"]["frames_total"], 300)
        self.assertEqual(data["info"]["frames_decoded"], 300)


if __name__ == "__main__":
    unittest.main()
