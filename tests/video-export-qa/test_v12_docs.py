# Tests that the documents say what the code does (version 1.2 documentation pass).
#   * every command in the docs can be run as written (full <skill-dir>/scripts/ path, the flag of the right script)
#   * the always-on checks, the audio-only route, the tone level and the full-range / HDR notes are written down
#   * the commands and values the guidance tells a reader to use really work (tagging recipe, range conversion,
#     the barcode region formula, the barcode-run spec, the HDR colour strings)
#   * the test counts in the docs are the real count, and the listing source carries no stale 1.0 / 1.1 claim
#   * (re-audit round) the step order, the output format and the example answer agree; "a requested key is never a PASS"
#     is written without an exception wherever that sentence is (the audio start offset on a file without audio or video
#     is NOT MEASURED; only a spec that says audio.present is false makes it vacuous); the numbers the pitfalls quote
#     (no_clipping figures, small frames, mp4 flavours, full-range names) are reproduced; the old listing files are marked
#     superseded; the New text of the proposal is exactly the text in the listing source
# Every scanner has a negative control, so a pass means something.
# Portability: the public repository holds only packages/ and tests/. Whatever reads the listing source (listing/) skips
# with a stated reason when that folder is absent and runs when it is present; a count of matches counts only the
# documents that are available. What a tool prints about a clip (a true peak, a pixel format) is accepted when it is
# one of the values the documents give, which name the builds each value was measured on; the tests do not check
# which build produced which value. Each listing-reading block that can skip is the LAST statement of its test: a
# skipped subtest makes `unittest -f` (failfast) abandon whatever follows it.
# Run:  python -I -B -m unittest discover -s tests/video-export-qa
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "video-export-qa"
SCRIPTS = PACKAGE / "scripts"
REFERENCES = PACKAGE / "references"
EXAMPLES = PACKAGE / "examples"
LISTING = ROOT / "listing" / "video-export-qa"
# The listing proposal that is in force. listing_update_v1.2_PROPOSAL.md is the record of 1.2; its own text is still
# pinned below, but the listing source now carries the 1.2.1 text, so the New blocks are compared with this one.
PROPOSAL_IN_FORCE = "update_v1.2.1_PROPOSAL.md"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import run_qa  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

LISTING_ABSENT = "the listing source folder (listing/) is not part of this checkout; the public repository has only packages/ and tests/"

LOUDNESS_KEYS = {"lufs_target", "lufs_tol", "true_peak_max_db", "no_clipping"}
ALWAYS_ON = ("video_stream", "cfr", "frame_count_consistent", "color_tags", "not_silent", "av_duration_match")


def read(path):
    return Path(path).read_bytes().decode("ascii")


def doc_files():
    return ([PACKAGE / "SKILL.md", PACKAGE / "README.md"] + sorted(REFERENCES.glob("*.md"))
            + sorted(EXAMPLES.glob("*.md")))


# --------------------------------------------------------------------------- scanners

# python -I -B <path>: the path must start at the skill folder, never be a bare scripts/ path.
SCRIPT_COMMAND = re.compile(r"python3? -I -B (\S+)")


def bad_script_paths(text):
    """Script paths in `python -I -B ...` commands that do not start with <skill-dir>/scripts/."""
    return [path for path in SCRIPT_COMMAND.findall(text)
            if "scripts/" in path and not path.startswith("<skill-dir>/scripts/")]


def flag_problems(text):
    """Text that is shown as guidance may use run_qa.py's spelling (--barcode-expect, --barcode-region) or
    qa_frames.py's (--expect, --region), but a bare --region or --expect must come with the run_qa.py spelling."""
    problems = []
    for bare, full in ((r"--region(?![-\w])", "--barcode-region"), (r"--expect(?![-\w])", "--barcode-expect")):
        if re.search(bare, text) and full not in text:
            problems.append("%s is used without %s" % (bare.split("(")[0], full))
    return problems


STALE = (
    (r"nearest documented key", "the unknown-key hint is a suggestion that can be wrong"),
    (r"the audio clips", "no_clipping is a ceiling check, not a clip detector"),
    (r"at or above full scale, the audio", "no_clipping is a ceiling check, not a clip detector"),
    (r"Only keys present in the spec are checked", "the always-on checks exist"),
    (r"drop that check from the spec", "an agent must never drop a requested check by itself"),
    (r"SKIP, never PASS", "a SKIP that applies is NOT MEASURED, so the overall is at best WARN"),
    (r"no environment variables\.", "PATH is read to find ffmpeg and ffprobe"),
    (r"works? with any editor", "only three scripted programs were tested"),
    (r"\b628\b", "the 1.0 test count"),
    (r"\b673\b", "the 1.1 test count"),
    (r"1\.1\.0", "the previous version"),
    (r"Not measured \(SKIP, so no claim", "a by-design SKIP is not NOT MEASURED"),
    (r"3 were not measured", "a by-design SKIP is not NOT MEASURED"),
    (r"peaking at full scale", "no_clipping is a ceiling check, named for what it measures"),
    (r"when it prints help text", "Python's standard library reads terminal and locale settings on any run"),
    (r"SKIP means not measured", "SKIP means no claim is made; NOT MEASURED is a narrower class"),
    (r"Not measured \(SKIP, proves nothing", "the table line says Skipped, no claim made"),
    (r"an unedited report", "the sample report has shortened hashes and a header note"),
    (r"The recipe \(any editor\)", "only three scripted programs were tested"),
    (r"every other line is verbatim", "the request and the answer in the example are written to illustrate the format"),
    (r"[Aa] raw stream has no timestamps", "only a raw ELEMENTARY stream (and picture stacks) has none"),
    (r"mp4 and mov[.)]", "the 0.02 s default suits mp4 and mov written WITH an edit list"),
    (r"the report says nothing about frames", "the always-on rows measure decoded frames; it is the source frame per output frame that is not said"),
    (r"nothing in the report speaks about frames", "the always-on rows measure decoded frames"),
    # (portability round) wording that was measured on one ffmpeg build and written as if it held on all of them;
    # these stay ABOVE the last seven entries, which test_the_proposal_and_the_listing_source... slices off the end
    (r"[Pp]lain `?-pix_fmt yuv420p`?( on a full-range source)? leaves (a full-range source|it)", "what a plain -pix_fmt does to a full-range source differs between ffmpeg builds"),
    (r"a plain -pix_fmt yuv420p is not enough", "a plain -pix_fmt yuv420p works on some builds (it rescales the range there); the advice is not to rely on it"),
    (r"ffprobe assumes a rate \(25 fps\) instead of measuring one", "only ffmpeg 6.1.1 and newer print an assumed 25/1 for a raw stream"),
    (r"ffprobe assumes 25/1 for every raw stream", "only ffmpeg 6.1.1 and newer print an assumed 25/1 for a raw stream"),
    (r"25/1 for every one of them", "only ffmpeg 6.1.1 and newer print 25/1 for every raw format"),
    (r"made up from an assumed frame rate", "a raw stream's rate is assumed or read from a header inside the stream, depending on the format and the build"),
    (r"it reported 25/1 for a healthy 24 fps GIF", "ffprobe reports 25/1 for it on ffmpeg 6.1.1 and newer and 24000/1001 on 4.4.2 and 5.1"),
    (r"one documented exception", "a requested key that cannot be measured is never a PASS, with no exception"),
    (r"One requested key is also a plain SKIP", "the audio start offset on a file without audio or video is NOT MEASURED"),
    (r"The one exception is `max_av_start_offset_s`", "the audio start offset on a file without audio or video is NOT MEASURED"),
    (r"offset requested for a file that lacks audio or video is a plain SKIP", "that offset is NOT MEASURED"),
    (r"max_av_start_offset_s`? on a file that lacks the audio or the video stream[^.\n]{0,40}plain SKIP",
     "that offset is NOT MEASURED, a plain SKIP only when the spec says audio.present is false"),
    (r"channel layout", "audio.channels checks a channel count; nothing checks the layout"),
    (r"up to 0\.083 s past the picture", "0.083 s is not a ceiling: 4.094 s was measured on a 4.000 s clip"),
)


def stale_phrases(text):
    return [label for pattern, label in STALE if re.search(pattern, text)]


def pipe_free(cell):
    return "|" not in cell


# --------------------------------------------------------------------------- the documents (no media needed)

class CommandsInTheDocsTests(unittest.TestCase):
    def test_the_scanner_flags_a_bare_scripts_path_and_accepts_the_skill_dir_form(self):
        self.assertEqual(bad_script_paths("python -I -B scripts/make_test_clip.py x.mp4"), ["scripts/make_test_clip.py"])
        self.assertEqual(bad_script_paths("python3 -I -B ../scripts/run_qa.py x.mp4"), ["../scripts/run_qa.py"])
        self.assertEqual(bad_script_paths("python -I -B <skill-dir>/scripts/run_qa.py x.mp4"), [])
        self.assertEqual(bad_script_paths("python -I -B -m unittest discover -s tests/video-export-qa"), [])

    def test_every_script_command_in_the_docs_starts_at_the_skill_folder(self):
        checked = 0
        for path in doc_files():
            text = read(path)
            checked += len(SCRIPT_COMMAND.findall(text))
            with self.subTest(document=path.name):
                self.assertEqual(bad_script_paths(text), [])
        self.assertGreater(checked, 12)

    def test_every_row_of_the_single_script_table_runs_as_written(self):
        skill = read(PACKAGE / "SKILL.md")
        rows = re.findall(r"^\| [^|\n]+ \| (`python -I -B <skill-dir>/scripts/(qa_\w+)\.py [^`]*`)", skill, re.M)
        self.assertEqual([name for _cell, name in rows], ["qa_probe", "qa_audio", "qa_frames", "qa_gif", "qa_compare"])
        # negative control: a bare script name is not a runnable command
        self.assertEqual(re.findall(r"^\| [^|\n]+ \| (`python -I -B <skill-dir>/scripts/(qa_\w+)\.py [^`]*`)",
                                    "| Q | `qa_probe.py export.mp4` |", re.M), [])

    def test_the_gif_script_row_uses_the_flags_the_script_has(self):
        skill = read(PACKAGE / "SKILL.md")
        help_text = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "qa_gif.py"), "--help"],
                                   capture_output=True).stdout.decode("utf-8", "replace")
        row = [line for line in skill.splitlines() if "qa_gif.py anim.gif" in line][0]
        for flag in re.findall(r"--[a-z-]+", row):
            self.assertIn(flag, help_text, "qa_gif.py has no %s" % flag)
        self.assertNotIn("--spec", row)

    def test_the_flag_scanner_has_a_negative_control(self):
        self.assertEqual(flag_problems("pass the new area with --region"), ["--region is used without --barcode-region"])
        self.assertEqual(flag_problems("state it in --expect"), ["--expect is used without --barcode-expect"])
        self.assertEqual(flag_problems("--barcode-region with run_qa.py, --region with qa_frames.py"), [])
        self.assertEqual(flag_problems("compare with --expect-duration"), [])

    def test_the_guidance_that_run_qa_prints_names_the_flag_run_qa_accepts(self):
        guide = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))
        mentioned = 0
        for check_id, row in guide.items():
            for part in ("meaning", "causes", "action"):
                if re.search(r"--(region|expect)(?![-\w])", row[part]):
                    mentioned += 1
                with self.subTest(check=check_id, part=part):
                    self.assertEqual(flag_problems(row[part]), [])
        self.assertGreater(mentioned, 4)

    def test_run_qa_takes_the_barcode_flags_and_qa_frames_the_short_ones(self):
        def options(script):
            out = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / script), "--help"],
                                 capture_output=True).stdout.decode("utf-8", "replace")
            return set(re.findall(r"--[a-z-]+", out))
        run_options, frame_options = options("run_qa.py"), options("qa_frames.py")
        self.assertLessEqual({"--barcode-expect", "--barcode-region"}, run_options)
        self.assertFalse({"--expect", "--region"} & run_options)
        self.assertLessEqual({"--expect", "--region"}, frame_options)
        skill = read(PACKAGE / "SKILL.md")
        self.assertIn("`run_qa.py` has no `--expect` or `--region`", skill)
        self.assertIn("--barcode-region", read(PACKAGE / "README.md") + skill)


class WhatTheDocsClaimTests(unittest.TestCase):
    def test_the_stale_phrase_scanner_has_a_negative_control(self):
        self.assertTrue(stale_phrases("so the audio clips"))
        self.assertTrue(stale_phrases("A file that records none is SKIP, never PASS."))
        self.assertTrue(stale_phrases("It works with any editor."))
        self.assertTrue(stale_phrases("Only keys present in the spec are checked."))
        self.assertEqual(stale_phrases("a ceiling check; NOT MEASURED; works from the exported file"), [])
        # (portability round) the old one-build wording is found, the corrected wording is not
        self.assertTrue(stale_phrases("(plain -pix_fmt yuv420p leaves a full-range source yuvj420p)"))
        self.assertTrue(stale_phrases("Plain `-pix_fmt yuv420p` on a full-range source leaves it `yuvj420p`."))
        self.assertTrue(stale_phrases("so ffprobe assumes a rate (25 fps) instead of measuring one"))
        self.assertTrue(stale_phrases("ffprobe reported an average frame rate of 25/1 for every one of them"))
        self.assertTrue(stale_phrases("timestamps ffprobe made up from an assumed frame rate"))
        self.assertTrue(stale_phrases("it reported 25/1 for a healthy 24 fps GIF whose delays are 4 and 5 centiseconds"))
        self.assertTrue(stale_phrases("Plain -pix_fmt yuv420p leaves a full-range source yuvj420p"))
        self.assertTrue(stale_phrases("(a plain -pix_fmt yuv420p is not enough: on a full-range source it leaves yuvj420p)"))
        self.assertEqual(stale_phrases("(do not rely on a plain -pix_fmt yuv420p: ffmpeg 7.1 keeps it yuvj420p, range pc)"), [])
        self.assertEqual(stale_phrases("it left the file yuvj420p on ffmpeg 7.1 and newer and converted it on 4.4.2; "
                                       "25/1 for every format they could write (an assumed rate)"), [])

    def test_no_stale_phrase_survives_in_the_package_documents(self):
        for path in doc_files():
            with self.subTest(document=path.name):
                self.assertEqual(stale_phrases(read(path)), [])

    def test_the_always_on_checks_are_named_where_the_spec_is_explained(self):
        skill = read(PACKAGE / "SKILL.md")
        keys = skill.split("## Spec keys")[1].split("| Key |")[0]
        for check_id in ALWAYS_ON:
            with self.subTest(check=check_id):
                self.assertIn("`%s`" % check_id, keys)
        guide = read(REFERENCES / "interpreting-results.md")
        always = [line for line in guide.splitlines() if line.startswith("Always on:")][0]
        for check_id in ALWAYS_ON:
            self.assertIn(check_id, always)
        specs = read(REFERENCES / "delivery-specs.md").split("\n")[2]
        self.assertIn("always-on", specs)
        self.assertNotIn("not checked, and the scripts never guess", specs)

    def test_every_spec_key_the_scripts_read_is_in_the_spec_keys_table(self):
        import qa_audio
        import qa_probe
        table = read(PACKAGE / "SKILL.md").split("## Spec keys")[1].split("## Reading the result")[0]
        table = table.split("| Key |")[1]

        def documented(name, section=None):
            return ("`%s`" % name in table or "`%s." % name in table
                    or (section is not None and "`%s.%s`" % (section, name) in table))

        top = set(qa_probe.TOP_SPEC_KEYS) | set(qa_probe.SIBLING_SPEC_KEYS) | set(qa_audio.SPEC_KEYS)
        for key in sorted(top):
            with self.subTest(key=key):
                self.assertTrue(documented(key), "SKILL.md does not list the spec key %s" % key)
        for section, inner_keys in qa_probe.NESTED_SPEC_KEYS:
            for key in inner_keys:
                with self.subTest(key="%s.%s" % (section, key)):
                    self.assertTrue(documented(key, section), "SKILL.md does not list %s.%s" % (section, key))
        for key in run_qa.BARCODE_SPEC_KEYS + run_qa.GIF_SPEC_KEYS:
            with self.subTest(key=key):
                self.assertIn('"%s"' % key, table)
        # negative control: a key the code does not read is not found, and the check can fail
        self.assertFalse(documented("no_such_spec_key"))

    def test_the_audio_only_route_and_its_limit_are_written_down(self):
        skill = read(PACKAGE / "SKILL.md")
        self.assertIn("audio-only", skill)
        self.assertIn("`video_stream`", skill)
        self.assertIn("qa_audio.py", skill)
        self.assertIn("does not read `audio.sample_rate`, `audio.channels` or `audio.codec`", skill)

    def test_the_limits_say_what_the_checks_do_not_measure(self):
        skill = read(PACKAGE / "SKILL.md")
        limits = skill.split("## Limits")[1].split("## If something does not work")[0]
        for needle in ("lip-sync", "clap or flash test", "-0.1 dBTP", "flat tops below full scale", "one decimal",
                       "one silent channel", "-20.7 LUFS", "decoded picture frames, not timing and not audio",
                       "profile and level, bit rate, field order", "500 runs"):
            with self.subTest(limit=needle):
                self.assertIn(needle, limits)

    def test_the_tone_level_the_docs_give_is_the_level_the_clip_has(self):
        for name in ("SKILL.md", "README.md"):
            self.assertIn("-20.7 LUFS", read(PACKAGE / name))
        self.assertIn("-19.7 dBTP", read(REFERENCES / "delivery-specs.md"))
        # the true peak of the AAC audio depends on the ffmpeg that encoded it (-19.7 dBTP; -20.0 dBTP from 4.4.2), and
        # every place that gives the first figure gives the second
        for path in (PACKAGE / "SKILL.md", PACKAGE / "README.md", REFERENCES / "editor-recipes.md", REFERENCES / "delivery-specs.md"):
            text = read(path)
            with self.subTest(document=path.name):
                self.assertGreater(text.count("-19.7 dBTP"), 0)
                self.assertEqual(text.count("-20.0 dBTP if ffmpeg 4.4.2 made the clip"), text.count("-19.7 dBTP"))
        # negative control: a mention of -19.7 dBTP without the 4.4.2 value is found
        bare = "The tone measures about -20.7 LUFS and -19.7 dBTP, so loudness rows"
        self.assertNotEqual(bare.count("-20.0 dBTP if ffmpeg 4.4.2 made the clip"), bare.count("-19.7 dBTP"))

    def test_the_barcode_run_spec_is_the_full_spec_without_the_four_loudness_keys(self):
        full = json.loads(read(EXAMPLES / "spec_h264_1080p24.json"))
        barcode = json.loads(read(EXAMPLES / "spec_barcode_run.json"))
        self.assertLessEqual(LOUDNESS_KEYS, set(full))
        self.assertFalse(LOUDNESS_KEYS & set(barcode))
        self.assertEqual({k: v for k, v in full.items() if k not in LOUDNESS_KEYS}, barcode)
        self.assertIn("audio", barcode)

    def test_the_new_example_documents_follow_the_new_report_order(self):
        for name in ("sample_report.md", "example_session.md"):
            text = read(EXAMPLES / name)
            with self.subTest(example=name):
                self.assertIn("run_qa.py %s on Python" % common.VERSION, text)
                self.assertLess(text.index("## Next steps"), text.index("## What was checked"))
                self.assertLess(text.index("## Next steps"), text.index("## Checks"))
        session = read(EXAMPLES / "example_session.md")
        self.assertIn("0 NOT MEASURED, 3 skipped by design", session)
        self.assertIn("Skipped by design (SKIP, so no claim is made)", session)
        self.assertIn("ffprobe -version", session)

    def test_the_guidance_has_the_ids_and_notes_the_documents_rely_on(self):
        guide = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))
        self.assertIn("yuvj420p", guide["pix_fmt"]["meaning"])
        self.assertIn("audio-only", guide["video_stream"]["meaning"] + guide["video_stream"]["causes"])
        self.assertIn("qa_audio.py", guide["video_stream"]["action"])
        self.assertIn("NOT MEASURED", guide["cfr"]["meaning"])
        self.assertIn("NOT MEASURED", guide["frame_count_consistent"]["meaning"])
        self.assertIn("NOT MEASURED", guide["fps"]["meaning"])
        self.assertIn("ceiling", guide["no_clipping"]["meaning"])
        self.assertIn("-0.15", guide["no_clipping"]["meaning"])
        self.assertIn("no audio stream", guide["lufs_target"]["meaning"])
        self.assertIn("a suggestion can be wrong", read(PACKAGE / "SKILL.md"))
        self.assertIn("can be wrong", guide["unknown_spec_key"]["action"])
        self.assertIn("gif", guide["spec_key_not_applied"]["meaning"])
        self.assertIn("Never remove a check", read(PACKAGE / "SKILL.md"))

    def test_the_silent_audio_rule_is_the_same_in_every_document(self):
        pitfalls = read(REFERENCES / "pitfalls.md")
        guide = read(REFERENCES / "interpreting-results.md")
        for text in (pitfalls, guide):
            self.assertIn("-90 dB or lower", text)
            self.assertIn("-70 LUFS", text)
            self.assertIn("-60 dB or lower", text)
            self.assertIn("0.4 s", text)
        self.assertNotIn("and the integrated loudness is -70 LUFS or lower", pitfalls)

    def test_spec_files_may_be_utf8_with_a_bom_or_utf16_and_the_docs_say_so(self):
        skill = read(PACKAGE / "SKILL.md")
        self.assertIn("a byte order mark is accepted", skill)
        self.assertIn("UTF-16", skill)
        self.assertIn("UTF-8", read(REFERENCES / "delivery-specs.md"))

    def test_the_description_stays_within_the_listing_budget_and_names_the_gif_parser(self):
        line = read(PACKAGE / "SKILL.md").split("\n")[2]
        self.assertTrue(line.startswith("description: "))
        self.assertLessEqual(len(line) - len("description: "), 300)
        self.assertIn("built-in GIF parser", line)


class TestCountsTests(unittest.TestCase):
    def real_count(self):
        suite = unittest.defaultTestLoader.discover(str(TESTS), pattern="test*.py", top_level_dir=str(TESTS))
        return suite.countTestCases()

    def test_the_documents_state_the_real_number_of_tests(self):
        real = self.real_count()
        self.assertGreater(real, 700)
        found = {
            "SKILL.md": re.findall(r"The (\d+)-test suite", read(PACKAGE / "SKILL.md")),
            "README.md": re.findall(r"The (\d+)-test suite", read(PACKAGE / "README.md")),
            "tests/README.md": re.findall(r"The suite has (\d+) tests", read(TESTS / "README.md")),
        }
        for name, numbers in found.items():
            with self.subTest(document=name):
                self.assertEqual(numbers, [str(real)], "%s must state %d tests" % (name, real))

    def test_the_listing_source_has_no_stale_claim_and_the_real_count(self):
        if not LISTING.is_dir():
            self.skipTest(LISTING_ABSENT)
        real = self.real_count()
        for name in ("description.md", "listing_fields.md"):
            text = (LISTING / name).read_bytes().decode("utf-8")
            with self.subTest(file=name):
                self.assertEqual(stale_phrases(text), [])
                self.assertNotIn("5 of 5", text)
                self.assertNotIn("0 of 5", text)
                self.assertNotIn("ffmpeg 8.1.1 only", text)
                self.assertNotIn("a warning is raised", text)
        combined = (LISTING / "description.md").read_bytes().decode("utf-8") + (LISTING / "listing_fields.md").read_bytes().decode("utf-8")
        self.assertIn("%d automated tests" % real, combined)
        self.assertIn("https://github.com/sawizzle/video-export-qa", combined)


# --------------------------------------------------------------------------- the documented controls, run for real

class DocumentedControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (FFMPEG and FFPROBE):
            raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
        cls.work = Path(tempfile.mkdtemp(prefix="vexqadoc_")).resolve()
        cls.addClassCleanup(shutil.rmtree, str(cls.work), True)
        code, _out, err = cls.cli("make_test_clip.py", "barcode_hd.mp4", "--frames", "24", "--fps", "24", "--size", "1920x1080")
        if code != 0:
            raise RuntimeError("make_test_clip.py failed: " + err)
        code, _out, err = cls.cli("make_test_clip.py", "barcode_small.mp4", "--frames", "24", "--fps", "24")
        if code != 0:
            raise RuntimeError("make_test_clip.py failed: " + err)

    @classmethod
    def cli(cls, script, *args):
        proc = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args],
                              capture_output=True, cwd=str(cls.work))
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")

    @classmethod
    def ffmpeg(cls, *args):
        proc = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args],
                              capture_output=True, cwd=str(cls.work))
        if proc.returncode != 0:
            raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (args, proc.stderr.decode("utf-8", "replace")))

    def qa(self, media, spec_path, *extra):
        code, out, err = self.cli("run_qa.py", media, "--spec", spec_path, "--format", "json", *extra)
        self.assertTrue(out.strip(), "run_qa.py printed nothing, exit %d: %s" % (code, err))
        return code, json.loads(out)

    def probe(self, media, spec):
        spec_file = self.work / "probe_spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        code, out, err = self.cli("qa_probe.py", media, "--spec", spec_file.name, "--json")
        self.assertTrue(out.strip(), err)
        return {check["id"]: check for check in json.loads(out)["checks"]}

    @staticmethod
    def is_a_documented_tone_peak(value):
        return value is not None and any(abs(value - documented) <= 0.15 for documented in (-19.7, -20.0))

    def test_the_documented_barcode_run_passes_an_unedited_clip_and_the_full_spec_fails_only_on_loudness(self):
        code, data = self.qa("barcode_hd.mp4", EXAMPLES / "spec_barcode_run.json", "--barcode-expect", "0-23")
        self.assertEqual((code, data["overall"]), (0, "PASS"), [c["id"] for c in data["checks"] if c["status"] == "FAIL"])
        code, data = self.qa("barcode_hd.mp4", EXAMPLES / "spec_h264_1080p24.json", "--barcode-expect", "0-23")
        self.assertEqual(code, 1)
        self.assertEqual([c["id"] for c in data["checks"] if c["status"] == "FAIL"], ["lufs_target"])
        audio = data["info"]["qa_audio"]
        self.assertAlmostEqual(audio["integrated_lufs"], -20.7, delta=0.15)
        # The loudness filters give the same readings on every build; the true peak of the tone depends on the AAC
        # encoder that wrote the clip: -19.7 dBTP, or -20.0 dBTP from ffmpeg 4.4.2 (a PCM tone reads -20.0 everywhere).
        # Both values are in the documents (see test_the_tone_level_the_docs_give_...); any other value is not.
        self.assertTrue(self.is_a_documented_tone_peak(audio["true_peak_dbtp"]), audio["true_peak_dbtp"])
        self.assertAlmostEqual(audio["mean_volume_db"], -23.0, delta=0.15)
        # negative control: a peak the documents do not give is not accepted
        for other in (-19.4, -20.3, -18.0, None):
            self.assertFalse(self.is_a_documented_tone_peak(other), other)

    def test_the_region_formula_in_the_pitfalls_reads_a_clip_with_a_bar_on_top(self):
        self.assertIn("y0 = bar_px / total_h", read(REFERENCES / "pitfalls.md"))
        bar_px, clip_h = 60, 360
        total_h = bar_px + clip_h
        region = "0,%.4f,1,%.4f" % (bar_px / float(total_h), (bar_px + 0.0833 * clip_h) / total_h)
        self.ffmpeg("-i", "barcode_small.mp4", "-an", "-vf", "pad=640:%d:0:%d:black" % (total_h, bar_px),
                    "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "padded.mp4")
        spec = self.work / "empty.json"
        spec.write_text("{}", encoding="utf-8")
        code, data = self.qa("padded.mp4", spec, "--barcode-expect", "0-23", "--barcode-region", region)
        rows = {c["id"]: c["status"] for c in data["checks"]}
        self.assertEqual((rows["all_frames_decodable"], rows["expect"]), ("PASS", "PASS"), region)
        # control: without the region the strip is not where the default looks, and the run fails
        code, data = self.qa("padded.mp4", spec, "--barcode-expect", "0-23")
        self.assertEqual(code, 1)
        self.assertEqual({c["id"]: c["status"] for c in data["checks"]}["all_frames_decodable"], "FAIL")

    def test_the_audio_only_route_works_as_documented(self):
        self.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:a", "pcm_s16le", "tone.wav")
        spec = self.work / "wav_spec.json"
        spec.write_text(json.dumps({"lufs_target": -21.0, "audio": {"sample_rate": 44100, "channels": 1}}), encoding="utf-8")
        code, data = self.qa("tone.wav", spec)
        self.assertEqual(code, 1)
        video = [c for c in data["checks"] if c["id"] == "video_stream"][0]
        self.assertEqual(video["status"], "FAIL")
        self.assertIn("(for an audio-only file use qa_audio.py)", video["detail"])
        code, out, _err = self.cli("qa_audio.py", "tone.wav", "--spec", spec.name, "--json")
        self.assertEqual(code, 0, "qa_audio.py does not read audio.sample_rate or audio.channels, so it passes")
        self.assertEqual({c["id"] for c in json.loads(out)["checks"]}, {"not_silent", "lufs_target"})
        # the format of an audio-only file is read from qa_probe.py --json (its info), as SKILL.md says
        code, out, _err = self.cli("qa_probe.py", "tone.wav", "--json")
        audio = json.loads(out)["info"]["audio"][0]
        self.assertEqual((audio["sample_rate"], audio["channels"], audio["codec"]), (48000, 1, "pcm_s16le"))

    def test_the_tagging_recipe_in_the_guidance_writes_the_tags_without_touching_the_pixels(self):
        action = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))["color_tags"]["action"]
        bsf = re.search(r"ffmpeg -i in\.mp4 -c copy -bsf:v (\S+) out\.mp4", action).group(1)
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1", "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-vf",
                    "setparams=colorspace=unknown:color_primaries=unknown:color_trc=unknown:range=unknown", "untagged.mp4")
        self.assertEqual(self.probe("untagged.mp4", {})["color_tags"]["status"], "WARN", "the control file is untagged")
        self.ffmpeg("-i", "untagged.mp4", "-c", "copy", "-bsf:v", bsf, "tagged.mp4")
        row = self.probe("tagged.mp4", {"color": {"space": "bt709", "transfer": "bt709", "primaries": "bt709", "range": "tv"}})
        self.assertEqual(row["color_tags"]["status"], "PASS")

    def test_the_range_conversion_in_the_guidance_turns_yuvj420p_into_yuv420p(self):
        action = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))["pix_fmt"]["action"]
        vf = re.search(r"-vf (scale=\S+setparams=range=tv)", action).group(1)
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-vf", "scale=out_range=pc,format=yuv420p,setparams=range=pc:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
                    "full.mp4")
        names = self.probe("full.mp4", {"video": {"pix_fmt": "yuv420p"}, "color": {"range": "pc"}})
        if names["pix_fmt"]["actual"] != "yuvj420p":
            self.skipTest("this ffprobe build does not name full-range 4:2:0 yuvj420p")
        self.assertEqual((names["pix_fmt"]["status"], names["color_tags"]["status"]), ("FAIL", "PASS"))
        self.assertEqual(self.probe("full.mp4", {"video": {"pix_fmt": "yuvj420p"}, "color": {"range": "pc"}})["pix_fmt"]["status"], "PASS")
        self.ffmpeg("-i", "full.mp4", "-vf", vf, "-c:v", "libx264", "limited.mp4")
        names = self.probe("limited.mp4", {"video": {"pix_fmt": "yuv420p"}, "color": {"range": "tv"}})
        self.assertEqual((names["pix_fmt"]["status"], names["color_tags"]["status"]), ("PASS", "PASS"))
        # control: what a plain -pix_fmt yuv420p does to a full-range source depends on the build, as the guidance says:
        # ffmpeg 7.1 and newer keep the range (yuvj420p, pc: the pix_fmt row FAILs and so does a tv spec), ffmpeg 4.4.2,
        # 5.1 and 6.1.1 convert it by themselves (yuv420p, tv: both rows PASS). Either way the two answers belong
        # together; a name and a range tag that disagree would be a new behaviour the guidance does not describe.
        self.ffmpeg("-i", "full.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "plain.mp4")
        plain = self.probe("plain.mp4", {"video": {"pix_fmt": "yuv420p"}, "color": {"range": "tv"}})
        self.assertIn((plain["pix_fmt"]["actual"], plain["pix_fmt"]["status"], plain["color_tags"]["status"]),
                      (("yuvj420p", "FAIL", "FAIL"), ("yuv420p", "PASS", "PASS")), plain)
        self.assertIn("do not rely on a plain -pix_fmt yuv420p: on a full-range source ffmpeg 7.1, 8.0.1, 8.1.1 and 9.0.1 keep it yuvj420p, range pc, "
                      "while ffmpeg 4.4.2, 5.1 and 6.1.1 rescale it to yuv420p, range tv, by themselves", action)
        pitfalls = read(REFERENCES / "pitfalls.md")
        self.assertIn("left the file `yuvj420p`, range `pc`, with the pixel values unchanged on ffmpeg 7.1, 8.0.1, 8.1.1 and 9.0.1", pitfalls)
        self.assertIn("rescaled the file to `yuv420p`, range `tv`, by itself on 4.4.2, 5.1, 5.1.9 and 6.1.1", pitfalls)
        # the example session prints this guidance cell as part of its verbatim report
        self.assertIn(action, read(EXAMPLES / "example_session.md"))

    def test_the_hdr_colour_strings_in_the_delivery_notes_match_what_ffprobe_prints(self):
        text = read(REFERENCES / "delivery-specs.md")
        spec = json.loads(re.search(r'`(\{"space": "bt2020nc"[^`]*\})`', text).group(1))
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-x264-params", "colormatrix=bt2020nc:colorprim=bt2020:transfer=smpte2084:fullrange=off", "hdr_tags.mp4")
        self.assertEqual(self.probe("hdr_tags.mp4", {"color": spec})["color_tags"]["status"], "PASS")
        wrong = dict(spec, space="bt2020")  # the natural but wrong string
        self.assertEqual(self.probe("hdr_tags.mp4", {"color": wrong})["color_tags"]["status"], "FAIL")

    def test_the_mkv_duration_detail_is_bare_while_mpegts_names_the_audio_padding(self):
        for extension in ("mkv", "ts"):
            self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=2", "-f", "lavfi",
                        "-i", "sine=frequency=1000:sample_rate=48000:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", "d." + extension)
        mkv = self.probe("d.mkv", {"duration_s": 2.0})["duration_s"]
        ts = self.probe("d.ts", {"duration_s": 2.0})["duration_s"]
        self.assertEqual((mkv["status"], ts["status"]), ("FAIL", "FAIL"))
        self.assertNotIn("audio padding", mkv["detail"])
        self.assertIn("audio padding", ts["detail"])
        for extension in ("mkv", "ts"):
            self.assertEqual(self.probe("d." + extension, {"duration_s": 2.0, "duration_tol_s": 0.05})["duration_s"]["status"], "PASS")


    # ---- re-audit round: numbers and commands the documents quote are the ones the tools give

    @classmethod
    def has_encoder(cls, name):
        proc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True)
        return re.search(r"^ [VAS]\S*\s+%s\s" % re.escape(name), proc.stdout.decode("utf-8", "replace"), re.M) is not None

    def test_the_no_clipping_commands_in_the_pitfalls_make_the_files_the_text_describes(self):
        text = read(REFERENCES / "pitfalls.md")
        commands = re.findall(r'`(ffmpeg -f lavfi -i "sine=[^`]+)`', text)
        self.assertEqual(len(commands), 2, commands)
        for command in commands:
            self.ffmpeg(*shlex.split(command)[1:])
        spec = self.work / "nc.json"
        spec.write_text(json.dumps({"no_clipping": True}), encoding="utf-8")
        results = {}
        for name in ("full.wav", "clipped_half.wav"):
            code, out, _err = self.cli("qa_audio.py", name, "--spec", spec.name, "--json")
            data = json.loads(out)
            results[name] = (code, {c["id"]: c for c in data["checks"]}["no_clipping"], data["info"]["true_peak_dbtp"])
        self.assertEqual((results["full.wav"][0], results["full.wav"][1]["status"]), (1, "FAIL"))
        self.assertEqual((results["clipped_half.wav"][0], results["clipped_half.wav"][1]["status"]), (0, "PASS"))
        self.assertAlmostEqual(results["clipped_half.wav"][2], -5.8, delta=0.2)

        def flat_factor(name):
            proc = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(self.work / name), "-af",
                                   "astats=metadata=0", "-f", "null", "-"], capture_output=True)
            return float(re.search(r"Flat factor: ([0-9.]+)", proc.stderr.decode("utf-8", "replace")).group(1))

        self.assertEqual(flat_factor("full.wav"), 0.0)
        self.assertTrue(23.0 < flat_factor("clipped_half.wav") < 25.5, flat_factor("clipped_half.wav"))
        self.assertIn("Flat factor about 24, true peak -5.8 dBTP", text)

    def test_the_vp9_and_av1_full_range_names_are_what_the_documents_say(self):
        source = ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1", "-an", "-vf",
                  "scale=out_range=pc,format=yuv420p,setparams=range=pc:colorspace=bt709:color_primaries=bt709:color_trc=bt709"]
        ran = []
        for encoder, name, expected in (("libx264", "fr_x264.mp4", "yuvj420p"), ("libvpx-vp9", "fr_vp9.webm", "yuv420p"),
                                        ("libsvtav1", "fr_av1.mp4", "yuv420p")):
            if not self.has_encoder(encoder):
                continue
            self.ffmpeg(*source, "-c:v", encoder, name)
            names = self.probe(name, {"video": {"pix_fmt": "yuv420p"}, "color": {"range": "pc"}})
            if encoder == "libx264" and names["pix_fmt"]["actual"] != "yuvj420p":
                continue  # a build whose ffprobe does not use the yuvj names (the documents describe the usual case)
            with self.subTest(encoder=encoder):
                self.assertEqual(names["pix_fmt"]["actual"], expected)
                self.assertEqual(names["color_tags"]["status"], "PASS", "the range is tagged pc in every case")
                self.assertEqual(names["pix_fmt"]["status"], "FAIL" if expected == "yuvj420p" else "PASS")
            ran.append(encoder)
        if not {"libvpx-vp9", "libsvtav1"} & set(ran):
            self.skipTest("this ffmpeg has neither libvpx-vp9 nor libsvtav1, so the VP9/AV1 note cannot be checked")

    def test_the_mp4_flavours_in_the_documents_read_the_durations_the_documents_give(self):
        inputs = ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=4", "-f", "lavfi", "-i",
                  "sine=frequency=1000:sample_rate=48000:duration=4"]
        video = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        self.ffmpeg(*inputs, *video, "d_normal.mp4")
        self.ffmpeg(*inputs, *video, "-bf", "0", "-use_editlist", "0", "d_noedit_nob.mp4")
        self.ffmpeg(*inputs, *video, "-use_editlist", "0", "d_noedit_b.mp4")
        self.ffmpeg(*inputs, *video, "-movflags", "+frag_keyframe+empty_moov", "d_frag.mp4")

        def duration(name, tolerance=None):
            spec = {"duration_s": 4.0}
            if tolerance:
                spec["duration_tol_s"] = tolerance
            return self.probe(name, spec)["duration_s"]

        self.assertEqual(duration("d_normal.mp4")["status"], "PASS")
        for name in ("d_noedit_nob.mp4", "d_noedit_b.mp4", "d_frag.mp4"):
            with self.subTest(file=name):
                self.assertEqual(duration(name)["status"], "FAIL", "the 0.02 s default does not suit this flavour")
        self.assertTrue(4.02 < duration("d_noedit_nob.mp4")["actual"] < 4.05)
        self.assertEqual(duration("d_noedit_nob.mp4", 0.05)["status"], "PASS")
        for name in ("d_noedit_b.mp4", "d_frag.mp4"):
            with self.subTest(file=name):
                self.assertGreater(duration(name)["actual"], 4.05, "more than one AAC frame past the picture")
                self.assertEqual(duration(name, 0.05)["status"], "FAIL", "0.05 does not rescue it")
                self.assertIn("audio runs", duration(name, 0.05)["detail"])
        self.assertIn("4.021 to 4.083", read(REFERENCES / "delivery-specs.md"))

    def test_an_mp4_without_an_edit_list_whose_audio_was_reencoded_reads_further_past_the_picture_than_0_083_s(self):
        # the documents once read "up to 0.083 s past the picture"; 4.094 s was measured on a 4.000 s clip
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=4", "-f", "lavfi", "-i",
                    "sine=frequency=1000:sample_rate=48000:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "src_aac.mp4")
        again = ["-i", "src_aac.mp4", "-c:v", "copy", "-c:a", "aac"]
        self.ffmpeg(*again, "-use_editlist", "0", "d_reencoded.mp4")
        self.ffmpeg(*again, "-use_editlist", "0", "-movflags", "+frag_keyframe+empty_moov", "d_reencoded_frag.mp4")
        self.ffmpeg(*again, "d_reencoded_edit.mp4")
        for name in ("d_reencoded.mp4", "d_reencoded_frag.mp4"):
            with self.subTest(file=name):
                check = self.probe(name, {"duration_s": 4.0, "duration_tol_s": 0.05})["duration_s"]
                self.assertEqual(check["status"], "FAIL")
                self.assertGreater(check["actual"], 4.083, "more than the 0.083 s that the documents once gave as the largest overrun")
                self.assertIn("audio runs", check["detail"])
        # control: the same re-encoded audio in an mp4 written WITH an edit list stays inside the default tolerance
        self.assertEqual(self.probe("d_reencoded_edit.mp4", {"duration_s": 4.0})["duration_s"]["status"], "PASS")

    def test_a_barcode_clip_made_without_a_tone_fails_the_audio_keys_of_the_example_spec(self):
        code, _out, err = self.cli("make_test_clip.py", "barcode_notone.mp4", "--frames", "24", "--fps", "24", "--size",
                                   "1920x1080", "--tone", "0")
        self.assertEqual(code, 0, err)
        code, data = self.qa("barcode_notone.mp4", EXAMPLES / "spec_barcode_run.json", "--barcode-expect", "0-23")
        self.assertEqual(code, 1)
        self.assertEqual(sorted(c["id"] for c in data["checks"] if c["status"] == "FAIL"),
                         ["audio.channels", "audio.codec", "audio.present", "audio.sample_rate"])
        # deleting only the audio block is not enough: a file without sound cannot be compared for a start offset,
        # so that limit is NOT MEASURED and the result cannot be a PASS
        spec = json.loads(read(EXAMPLES / "spec_barcode_run.json"))
        del spec["audio"]
        (self.work / "spec_noaudio_block.json").write_text(json.dumps(spec), encoding="utf-8")
        code, data = self.qa("barcode_notone.mp4", self.work / "spec_noaudio_block.json", "--barcode-expect", "0-23")
        self.assertEqual((code, data["overall"]), (0, "WARN"))
        self.assertEqual([c["id"] for c in data["checks"] if c.get("unmeasured")], ["max_av_start_offset_s"])
        # the documented fix: delete the audio block AND the start-offset limit of the spec
        del spec["max_av_start_offset_s"]
        (self.work / "spec_noaudio.json").write_text(json.dumps(spec), encoding="utf-8")
        code, data = self.qa("barcode_notone.mp4", self.work / "spec_noaudio.json", "--barcode-expect", "0-23")
        self.assertEqual((code, data["overall"]), (0, "PASS"))

    def test_the_readme_quick_start_clip_is_smaller_than_the_size_the_example_spec_asks_for(self):
        code, data = self.qa("barcode_small.mp4", EXAMPLES / "spec_barcode_run.json", "--barcode-expect", "0-23")
        self.assertEqual(sorted(c["id"] for c in data["checks"] if c["status"] == "FAIL"), ["height", "width"])


# --------------------------------------------------------------------------- re-audit round: what the documents say

LISTING_NAMES_NOT_TO_USE = [prefix + "Craft" for prefix in ("Art", "Film", "Photo", "Vector", "Light", "Pdf", "Effect", "Design")]


def listing_texts():
    if not LISTING.is_dir():
        raise unittest.SkipTest(LISTING_ABSENT)
    return {path.name: path.read_bytes().decode("utf-8") for path in sorted(LISTING.glob("*.md"))}


class ReAuditDocumentTests(unittest.TestCase):
    def skill(self):
        return read(PACKAGE / "SKILL.md")

    def test_step_four_is_in_order_and_the_stop_comes_before_the_steps_that_need_the_export(self):
        step4 = self.skill().split("4. Only for")[1].split("5. Run everything")[0]
        positions = [step4.index(marker) for marker in ("4a.", "4b.", "STOP", "4c.", "4d.")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Do 4c, 4d and step 5 only when you have it", step4)
        self.assertNotIn("Continue at step 5 only when you have it.\n   - Write down", step4)

    def test_the_one_place_loudness_keys_are_left_out_is_named_and_the_never_remove_rule_names_it(self):
        skill = self.skill()
        self.assertIn("This is the only place where those keys are left out, and only in this copy", skill)
        rule = [line for line in skill.splitlines() if "Never remove a check from the user's spec yourself" in line][0]
        self.assertIn("step 4d", rule)
        self.assertIn("the one exception", rule)

    def test_the_output_format_asks_for_not_measured_advice_in_one_place_only(self):
        fmt = self.skill().split("## Output format")[1].split("`examples/example_session.md`")[0]
        items = dict(re.findall(r"^(\d)\. (.+)$", fmt, re.M))
        self.assertEqual(sorted(items), ["1", "2", "3", "4", "5"])
        self.assertIn("they are in item 3", items["2"])
        self.assertIn('NOT MEASURED row give the report\'s "What to do"', items["3"])
        self.assertNotIn('"What to do"', items["4"])
        self.assertNotIn("NOT MEASURED check give", items["4"])

    def test_the_example_answer_follows_the_output_format_it_illustrates(self):
        answer = read(EXAMPLES / "example_session.md").split("## What the agent tells the user")[1]
        markers = ["**Verdict:", "| Check | Status |", "\nNext steps:\n", "\nNotes:\n", "\nFull report:"]
        positions = [answer.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(answer, r"\d+ of \d+ checks failed, \d+ warned, \d+ NOT MEASURED, \d+ skipped by design")
        between = answer[answer.index("| `expect` |"):answer.index("\nNext steps:\n")]
        self.assertNotIn("Reading", between, "an explanation between the table and Next steps is not in the format")
        notes = answer.split("\nNotes:\n")[1].split("\nFull report:")[0]
        self.assertIn("Reading the `expect` row", notes)
        self.assertIn("Skipped by design", notes)
        # negative control: a reading paragraph placed before Next steps would be found between the table and it
        self.assertIn("Reading", "| `expect` | row |\n\nReading the row\n\nNext steps:")

    def test_every_next_step_in_the_example_has_a_do_line(self):
        answer = read(EXAMPLES / "example_session.md").split("\nNext steps:\n")[1].split("\nNotes:\n")[0]
        entries = re.split(r"\n(?=\d+\. )", answer.strip())
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertIn("\n   Do: ", entry)

    def test_the_example_says_which_parts_are_verbatim(self):
        text = read(EXAMPLES / "example_session.md")
        intro = text.split("## The request")[0]
        self.assertIn("is verbatim output", intro)
        self.assertIn("are written to illustrate", intro)
        self.assertIn("show only the first line of each tool's output", intro)
        self.assertIn("copied from the skill's `examples/` folder next to `input.mp4`", text)

    def test_the_sample_report_header_says_what_was_changed(self):
        text = read(EXAMPLES / "sample_report.md")
        self.assertIn("shortened to 12 characters", text)
        self.assertIn("copied next to `input.mp4`", text)
        self.assertIn("only its SHA-256 values are shortened", self.skill())

    def test_the_universal_sentence_about_a_requested_key_has_no_exception(self):
        pattern = re.compile(r"(could not|cannot|can't) be measured[^\n]*never (a )?PASS|never (a )?PASS[^\n]*(could not|cannot) be measured")

        def scan(texts):
            """Check every line that says it, and return how many lines there were."""
            found = 0
            for name, text in texts.items():
                for line in text.splitlines():
                    if pattern.search(line) and "NOT MEASURED row" not in line:
                        found += 1
                        with self.subTest(document=name, line=line[:60]):
                            self.assertNotIn("exception", line, "the sentence has no exception to name")
                            if "max_av_start_offset_s" in line or "audio start offset" in line:
                                self.assertIn("NOT MEASURED", line, "the start offset is NOT MEASURED, not a quiet SKIP")
            return found

        # The package alone says it twice (SKILL.md and delivery-specs.md). The count covers only what is available:
        # the listing source says it too, but it is not in every checkout.
        self.assertGreaterEqual(scan({path.name: read(path) for path in doc_files()}), 2)
        # negative control: the scanner finds an unqualified sentence, and the exception wording is what it rejects
        self.assertTrue(pattern.search("A spec key that was asked for and cannot be measured is never a PASS."))
        self.assertIn("exception", "is never a PASS, with one documented exception (a plain SKIP)")
        with self.subTest(part="listing source"):  # last: a skipped subtest ends the test under failfast
            if not LISTING.is_dir():
                self.skipTest(LISTING_ABSENT)
            listing = {"listing/" + name: text for name, text in listing_texts().items()
                       if name in ("description.md", "listing_fields.md", "listing_update_v1.2_PROPOSAL.md")}
            self.assertGreaterEqual(scan(listing), 3, "description.md (once) and the proposal (twice) carry the sentence")

    def test_the_audio_start_offset_rule_is_written_where_the_skip_is_defined(self):
        # without both streams the limit is NOT MEASURED; only audio.present false makes it vacuous (a plain SKIP)
        for name, needle in (("SKILL.md", "so it is NOT MEASURED (the only exception: on a file without audio, a spec that also says `audio.present` is false"),
                             ("interpreting-results.md", "SKIP and NOT MEASURED when the file lacks the audio or the video stream"),
                             ("delivery-specs.md", "That includes `max_av_start_offset_s` on a file that lacks the audio or the video stream")):
            path = PACKAGE / name if name == "SKILL.md" else REFERENCES / name
            with self.subTest(document=name):
                self.assertIn(needle, read(path))
                self.assertIn("audio.present", read(path))
        source = (SCRIPTS / "common.py").read_bytes().decode("ascii")
        self.assertIn("That includes\n        max_av_start_offset_s on a file that lacks the audio or the video stream, which is unmeasured", source)
        self.assertIn("when the spec itself says audio.present is false", source)
        self.assertNotIn("The one\n        exception", source)

    def test_the_proposal_and_the_listing_source_say_the_offset_is_not_measured_without_both_streams(self):
        texts = listing_texts()
        proposal = texts["listing_update_v1.2_PROPOSAL.md"]
        changelog = proposal.split("## 7. Changelog")[1].split("\n## 8.")[0]
        new_blocks = "\n".join(re.findall(r"^New:\n((?:>.*\n?)+)", proposal, re.M))
        for label, text in (("proposal changelog", changelog), ("proposal New blocks", new_blocks),
                            ("description.md", texts["description.md"]), ("listing_fields.md", texts["listing_fields.md"])):
            with self.subTest(text=label):
                self.assertEqual([why for pattern, why in STALE[-7:] if re.search(pattern, text)], [])
                self.assertIn("NOT MEASURED", text)
        # negative control: the last seven stale phrases do catch the old exception wording
        self.assertTrue([why for pattern, why in STALE[-7:] if re.search(pattern, "never a PASS, with one documented exception")])
        self.assertIn("an audio start offset requested for a file that lacks audio or video is NOT MEASURED", changelog)
        self.assertIn("audio.present is false", changelog)
        # the behaviour-change list carries the new behaviour (items 7 to 10) next to the older ones
        changes = changelog.split("Behaviour changes you may notice when you upgrade from 1.1:")[1]
        for needle in ("spec_key_not_applied", "effective_duration", "differs from gif.fps", "max_av_start_offset_s on a file with no audio"):
            with self.subTest(change=needle):
                self.assertIn(needle, changes)
        self.assertIn(".h264, .hevc, .m4v, .m2v, .mjpeg or .obu file no longer FAILs", changes)
        self.assertIn("video.fps 24 FAILed a 24 fps raw .h264 or .hevc", changes)
        # that FAIL happens only where ffprobe prints the assumed 25/1 (ffmpeg 6.1.1 and newer); the changelog says so
        self.assertIn("So in 1.1 on ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1, video.fps 24 FAILed a 24 fps raw .h264 or .hevc", changes)
        self.assertIn("on 4.4.2, 5.1 and 5.1.9 the 1.1 answer depended on the format", changes)
        self.assertNotIn("ffprobe assumes 25/1 for every raw stream", changes)

    def test_every_document_that_lists_the_raw_extensions_says_an_apple_style_m4v_is_an_mp4(self):
        docs = (PACKAGE / "SKILL.md", REFERENCES / "delivery-specs.md", REFERENCES / "interpreting-results.md",
                REFERENCES / "pitfalls.md")
        for path in docs:
            text = read(path).replace("`", "")
            with self.subTest(document=path.name):
                self.assertRegex(text, r"[Aa]n Apple-style \.m4v is an MP4 container")
                self.assertRegex(text, r"only a raw MPEG-4 elementary stream")
        # negative control: a list of the extensions without the note is not accepted
        self.assertIsNone(re.search(r"Apple-style \.m4v is an MP4 container", "a bare .h264, .hevc, .m4v, .m2v, .mjpeg or .obu file"))
        with self.subTest(part="listing source"):  # last: a skipped subtest ends the test under failfast
            if not LISTING.is_dir():
                self.skipTest(LISTING_ABSENT)
            texts = listing_texts()
            for name in ("listing_fields.md", "listing_update_v1.2_PROPOSAL.md"):
                with self.subTest(document=name):
                    self.assertRegex(texts[name], r"Apple-style \.m4v is an MP4 container")

    def test_the_documents_do_not_call_0_083_s_a_ceiling_and_give_the_measured_overrun(self):
        specs = read(REFERENCES / "delivery-specs.md")
        self.assertIn("0.083 s or more", specs)
        self.assertNotIn("up to 0.083 s", specs)
        self.assertIn("4.094", specs)
        self.assertIn("4.094", read(REFERENCES / "pitfalls.md"))
        self.assertIn("4.094", read(REFERENCES / "interpreting-results.md"))
        skill = self.skill()
        self.assertIn("0.083 s is not a ceiling", skill)
        self.assertIn("0.021 s up to 0.094 s", skill)
        self.assertNotIn("(measured 0.021 to 0.083 s)", skill)

    def test_no_document_claims_that_a_channel_layout_was_checked(self):
        for name, text in {path.name: read(path) for path in doc_files()}.items():
            with self.subTest(document=name):
                self.assertNotRegex(text, r"(?i)channel[ -]layout")
        # audio.channels compares a number, and the guide says so
        guide = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))["audio.channels"]
        self.assertIn("channel count", guide["meaning"])
        self.assertNotIn("layout", guide["causes"] + guide["action"])
        self.assertIn("channel count all matched the spec", read(EXAMPLES / "example_session.md"))
        # negative control: the scan can fail
        self.assertRegex("Size and channel layout all matched", r"(?i)channel[ -]layout")
        with self.subTest(part="listing source"):  # last: a skipped subtest ends the test under failfast
            if not LISTING.is_dir():
                self.skipTest(LISTING_ABSENT)
            for name, text in listing_texts().items():
                with self.subTest(document="listing/" + name):
                    self.assertNotRegex(text, r"(?i)channel[ -]layout")

    def test_the_tests_readme_does_not_promise_that_overlapping_runs_cannot_collide(self):
        text = read(TESTS / "README.md")
        self.assertNotRegex(text, r"cannot overwrite|cannot collide|never collide|therefore cannot")
        self.assertIn("private_work_folder", text)   # what is true is still said: each module has a folder of its own
        # negative control: the removed sentence would be found
        self.assertRegex("two runs therefore cannot overwrite each other's clips", r"cannot overwrite|therefore cannot")

    def test_the_readme_quick_start_says_the_spec_example_needs_a_1920x1080_clip(self):
        readme = read(PACKAGE / "README.md")
        quick = readme.split("## Quick start")[1].split("## What is in the folder")[0]
        self.assertIn("make_test_clip.py barcode_clip.mp4\n", quick)       # the 640x360 default clip
        self.assertIn("--size 1920x1080", quick.split("examples/spec_barcode_run.json")[1])
        self.assertIn("640x360", quick)

    def test_the_recipe_says_to_copy_the_spec_next_to_the_export_and_what_tone_zero_does(self):
        recipes = read(REFERENCES / "editor-recipes.md")
        self.assertIn("copy it next to the export", recipes)
        self.assertIn("then also delete the `audio` block and `max_av_start_offset_s`", recipes)
        self.assertIn("also delete the `audio` block and `max_av_start_offset_s`", read(PACKAGE / "SKILL.md"))
        self.assertIn("<skill-dir>/examples/spec_h264_1080p24.json", recipes)
        self.assertIn("`--barcode-expect` in `run_qa.py`, `--expect` in `qa_frames.py`", recipes)
        self.assertIn("editor-neutral, tested in three programs", recipes)

    def test_the_colour_recipe_runs_as_one_line_in_any_shell(self):
        pitfalls = read(REFERENCES / "pitfalls.md")
        block = pitfalls.split("```\nffmpeg -framerate 24 -i frames/f%04d.png")[1].split("```")[0]
        self.assertNotIn("\\", block, "a trailing backslash is bash only; PowerShell and cmd do not continue the line")
        self.assertEqual(len([line for line in block.splitlines() if line.strip()]), 1)

    def test_the_readme_licence_names_both_licences(self):
        licence = read(PACKAGE / "README.md").split("## Licence")[1]
        self.assertIn("is under the licence of this listing (personal use)", licence)
        self.assertIn("MIT OR Apache-2.0", licence)

    def test_the_raw_stream_documents_name_the_formats_they_talk_about(self):
        for path in (PACKAGE / "SKILL.md", REFERENCES / "delivery-specs.md", REFERENCES / "interpreting-results.md"):
            with self.subTest(document=path.name):
                text = read(path).replace("`", "")
                self.assertRegex(text, r"raw elementary stream \(a bare \.h264, \.hevc, \.m4v, \.m2v, \.mjpeg or \.obu file\)")
        pitfalls = read(REFERENCES / "pitfalls.md")
        self.assertIn("`.m4v` (MPEG-4), `.m2v` (MPEG-2), `.mjpeg` and `.obu` (AV1)", pitfalls)
        # the frame rate ffprobe prints for a raw stream is not a measurement, and what it prints depends on the build:
        # the document gives the value of each group of builds it was measured on (the guard test of
        # EveryRawElementaryStreamIsNotMeasuredTests checks the running build against the same two values for .m4v)
        self.assertIn("ffmpeg 6.1.1, 7.1, 8.0.1, 8.1.1 and 9.0.1 reported an average frame rate of 25/1 for every format they could write", pitfalls)
        self.assertIn("ffmpeg 4.4.2, 5.1 and 5.1.9 reported 24/1 for .h264, .hevc and .m2v", pitfalls)
        self.assertIn("0/0 (no average rate) for .m4v", pitfalls)

    def test_the_full_range_names_are_scoped_to_the_codecs_they_hold_for(self):
        for path in (PACKAGE / "SKILL.md", REFERENCES / "delivery-specs.md", REFERENCES / "pitfalls.md",
                     REFERENCES / "interpreting-results.md"):
            with self.subTest(document=path.name):
                text = read(path)
                self.assertIn("VP9", text)
                self.assertIn("AV1", text)
        self.assertIn("(H.264, HEVC and MJPEG)", read(REFERENCES / "pitfalls.md").split("## Full-range 4:2:0")[1].split("\n")[0])
        # each place that names yuvj420p says which codecs it holds for
        skill = [line for line in self.skill().splitlines() if line.startswith("| `video.codec`")][0]
        specs = read(REFERENCES / "delivery-specs.md").splitlines()
        row = [line for line in specs if line.startswith("| 8-bit 4:2:0")][0]
        full_range = [line for line in specs if line.startswith("- Full range")][0]
        meaning = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))["pix_fmt"]["meaning"]
        for label, text in (("SKILL.md key table", skill), ("delivery-specs row", row), ("delivery-specs full range", full_range),
                            ("interpreting-results pix_fmt", meaning)):
            with self.subTest(place=label):
                self.assertIn("VP9", text)
                self.assertIn("yuvj420p", text)

    def test_the_default_duration_tolerance_is_scoped_to_mp4_with_an_edit_list(self):
        for path in (PACKAGE / "SKILL.md", REFERENCES / "delivery-specs.md", REFERENCES / "interpreting-results.md"):
            with self.subTest(document=path.name):
                self.assertIn("edit list", read(path))
        self.assertIn("4.021 to 4.083", read(REFERENCES / "delivery-specs.md"))

    def test_the_env_wording_says_any_run(self):
        skill = self.skill()
        self.assertIn("on any run", skill)
        self.assertIn("COLUMNS, NO_COLOR and LANG", skill)

    def test_the_tool_lookup_text_says_the_current_folder_is_skipped_even_on_path(self):
        self.assertIn("even when it is listed on PATH", self.skill())
        self.assertIn("even when it is listed there", read(REFERENCES / "pitfalls.md"))

    def test_the_interpreting_guide_says_which_rows_use_its_cells(self):
        guide = read(REFERENCES / "interpreting-results.md")
        self.assertIn("of each FAIL or WARN row into the \"Next steps\"", guide)
        self.assertIn("A NOT MEASURED row does not use these cells", guide)
        self.assertIn("a note on stderr", guide)

    def test_the_no_audio_rows_of_the_guide_name_the_missing_track(self):
        guide = run_qa.load_guidance(str(REFERENCES / "interpreting-results.md"))
        for check_id in ("max_leading_silence_s", "max_trailing_silence_s", "max_silence_s", "no_clipping"):
            with self.subTest(check=check_id):
                text = guide[check_id]["causes"] + " " + guide[check_id]["action"]
                self.assertIn("no audio", text)

    def test_the_version_1_1_listing_files_are_marked_superseded(self):
        texts = listing_texts()
        for name in ("listing_update_v1.1_PROPOSAL.md", "launch_post_drafts_v2.md", "launch_walkthrough.md",
                     "telegram_post_replacement.md", "launch_post_drafts.md"):
            with self.subTest(file=name):
                head = "\n".join(texts[name].splitlines()[:4])
                self.assertIn("SUPERSEDED", head)
                self.assertIn("do not apply", head.lower().replace("do not post", "do not apply"))

    def test_no_listing_text_names_a_forbidden_product(self):
        for name, text in listing_texts().items():
            with self.subTest(file=name):
                self.assertEqual([word for word in LISTING_NAMES_NOT_TO_USE if word.lower() in text.lower()], [])
        # negative control: the scan can fail
        self.assertEqual([w for w in LISTING_NAMES_NOT_TO_USE if w.lower() in ("x " + LISTING_NAMES_NOT_TO_USE[0]).lower()],
                         [LISTING_NAMES_NOT_TO_USE[0]])

    def test_the_listing_description_names_the_ceiling_and_the_start_offset_rule(self):
        description = listing_texts()["description.md"]
        self.assertIn("reaching the -0.1 dBTP true-peak ceiling", description)
        self.assertNotIn("peaking at full scale", description)
        honest = description.split("**Honest by design**")[1].split("**What is inside**")[0]
        self.assertIn("audio start offset", honest)
        self.assertIn("NOT MEASURED", honest.split("audio start offset")[1])
        self.assertNotIn("exception", honest)

    def test_the_proposal_new_text_is_exactly_the_text_in_the_listing_source(self):
        # The proposal in force is the newest one (PROPOSAL_IN_FORCE); the 1.2 proposal stays as the record of 1.2. Every
        # field the proposal in force changes must carry exactly the text of the listing source, and it must change the
        # four fields below. A field it does not list (the summary did not change in 1.2.1) is not compared.
        texts = listing_texts()
        proposal = texts[PROPOSAL_IN_FORCE]

        def quoted_blocks(section_title):
            body = proposal.split("## " + section_title)[1].split("\n## ")[0]
            blocks = re.findall(r"^New:\n((?:>.*\n?)+)", body, re.M)
            return ["\n".join(line[2:] if line.startswith("> ") else line[1:] for line in block.rstrip("\n").split("\n")) for block in blocks]

        description = texts["description.md"].strip("\n")
        fields = texts["listing_fields.md"]

        def field(title):
            return re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(title), fields, re.M | re.S).group(1).strip("\n")

        compared = {
            "1. Full Description (the whole field)": description,
            "2. Summary (one line)": field("Summary (one line)"),
            "3. Compatibility Note": field("Compatibility note"),
            "4. Extra Details > Known limitations": field("Known limitations"),
            "5. Permissions (tools, file scopes and notes)": field("Permissions"),
        }
        listed = [title for title in compared if "\n## " + title + "\n" in proposal]
        for title in ("1. Full Description (the whole field)", "3. Compatibility Note",
                      "4. Extra Details > Known limitations", "5. Permissions (tools, file scopes and notes)"):
            self.assertIn(title, listed, "the proposal in force changes this field")
        for title in listed:
            with self.subTest(field=title):
                self.assertEqual(quoted_blocks(title), [compared[title]])
        # negative control: a text that differs from the source is not equal to it
        self.assertNotEqual(compared["3. Compatibility Note"] + " x", compared["3. Compatibility Note"])

    def test_the_proposal_has_a_release_checklist_that_names_the_public_repository(self):
        proposal = listing_texts()["listing_update_v1.2_PROPOSAL.md"]
        self.assertIn("Before anything goes out", proposal)
        self.assertIn("https://github.com/sawizzle/video-export-qa", proposal)
        self.assertIn("push", proposal.split("Before anything goes out")[1].split("\n## ")[0].lower())
        self.assertIn("Behaviour changes", proposal)

    def test_every_new_faq_answer_in_the_proposal_is_an_answer_in_the_listing_source(self):
        texts = listing_texts()
        faq = re.search(r"^## FAQ\n(.*?)(?=^## |\Z)", texts["listing_fields.md"], re.M | re.S).group(1)
        answers = [block.split("\nA: ", 1)[1].strip("\n") for block in faq.strip("\n").split("\n\n") if "\nA: " in block]
        section = texts[PROPOSAL_IN_FORCE].split("## 6. FAQ")[1].split("\n## ")[0]
        new_blocks = re.findall(r"^New:\n((?:>.*\n?)+)", section, re.M)
        self.assertGreater(len(new_blocks), 2)
        for block in new_blocks:
            text = "\n".join(line[2:] if line.startswith("> ") else line[1:] for line in block.rstrip("\n").split("\n"))
            self.assertIn(text, answers)
        # negative control: an answer that is not in the listing source is not found
        self.assertNotIn("an answer nobody wrote", answers)


if __name__ == "__main__":
    unittest.main()
