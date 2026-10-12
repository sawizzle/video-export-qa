# Tests that ffmpeg and ffprobe are only ever told to open local files (hard rule: no network).
# A user's file can be a playlist whose lines are web addresses; without a protocol whitelist
# ffmpeg would try to fetch them. These tests (1) check the recorded command lines of every
# script that reads a user file, and (2) feed a real playlist that points at a web address and
# require that it is refused before any connection is attempted.
# Run:  python -B -m unittest discover -s tests/video-export-qa
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from ffmpeg_fixtures import supports_option

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
WORK = ROOT / "_work" / "video-export-qa" / "integrator" / "tests-hardening"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
WHITELIST = ["-protocol_whitelist", "file"]
# A documentation-only address (RFC 5737). It is never contacted: the whitelist refuses it first.
PLAYLIST = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nhttp://203.0.113.1/segment.ts\n#EXT-X-ENDLIST\n"


def cli(script, *args):
    cmd = [sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def inputs_are_whitelisted(command):
    """True when every input of an ffmpeg/ffprobe command is preceded by -protocol_whitelist file."""
    name = os.path.basename(command[0]).lower()
    if name.startswith("ffprobe"):
        index = command.index("-protocol_whitelist") if "-protocol_whitelist" in command else -1
        return index >= 0 and command[index + 1] == "file" and index < len(command) - 2
    positions = [i for i, part in enumerate(command) if part == "-i"]
    return bool(positions) and all(i >= 2 and command[i - 2:i] == WHITELIST for i in positions)


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    code, _out, err = cli("make_test_clip.py", "clip.mp4", "--frames", "24", "--overwrite")
    if code != 0:
        raise RuntimeError("make_test_clip.py failed: " + err)
    shutil.copy(str(WORK / "clip.mp4"), str(WORK / "other.mp4"))
    with open(str(WORK / "remote.m3u8"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(PLAYLIST)


class CheckerSelfTests(unittest.TestCase):
    def test_the_command_checker_accepts_whitelisted_and_rejects_bare_commands(self):
        # Negative controls for the helper used below.
        good_ffmpeg = ["ffmpeg", "-hide_banner"] + WHITELIST + ["-i", "a.mp4", "-f", "null", "-"]
        two_inputs = ["ffmpeg"] + WHITELIST + ["-i", "a.mp4"] + WHITELIST + ["-i", "b.mp4", "-f", "null", "-"]
        good_ffprobe = ["ffprobe", "-v", "error"] + WHITELIST + ["-show_format", "a.mp4"]
        self.assertTrue(inputs_are_whitelisted(good_ffmpeg))
        self.assertTrue(inputs_are_whitelisted(two_inputs))
        self.assertTrue(inputs_are_whitelisted(good_ffprobe))
        self.assertFalse(inputs_are_whitelisted(["ffmpeg", "-i", "a.mp4", "-f", "null", "-"]))
        self.assertFalse(inputs_are_whitelisted(["ffmpeg"] + WHITELIST + ["-i", "a.mp4", "-i", "b.mp4", "-f", "null", "-"]))
        self.assertFalse(inputs_are_whitelisted(["ffprobe", "-v", "error", "a.mp4"]))
        self.assertFalse(inputs_are_whitelisted(["ffprobe", "-v", "error", "a.mp4"] + WHITELIST))
        self.assertFalse(inputs_are_whitelisted(["ffmpeg", "-f", "lavfi"]))

    def test_the_shared_constant_is_the_whitelist_option(self):
        self.assertEqual(common.LOCAL_FILES_ONLY, WHITELIST)


class RecordedCommandTests(unittest.TestCase):
    CASES = (
        ("qa_probe.py", ["clip.mp4", "--json"]),
        ("qa_audio.py", ["clip.mp4", "--json"]),
        ("qa_frames.py", ["clip.mp4", "--json"]),
        ("qa_compare.py", ["clip.mp4", "other.mp4", "--min-psnr", "30", "--json"]),
        ("run_qa.py", ["clip.mp4", "--barcode-expect", "0-23", "--json"]),
    )

    def test_every_command_that_opens_a_user_file_allows_local_files_only(self):
        for script, args in self.CASES:
            with self.subTest(script=script):
                code, out, err = cli(script, *args)
                self.assertIn(code, (0, 1), err)
                commands = json.loads(out)["provenance"]["commands"]
                self.assertTrue(commands)
                for command in commands:
                    self.assertTrue(inputs_are_whitelisted(command), command)

    def test_the_compare_psnr_command_whitelists_both_of_its_inputs(self):
        _code, out, _err = cli("qa_compare.py", "clip.mp4", "other.mp4", "--min-psnr", "30", "--json")
        psnr = [c for c in json.loads(out)["provenance"]["commands"] if "-lavfi" in c]
        # ffmpeg older than 5.1 rejects -fps_mode, so the product records that attempt and its -vsync 0 retry.
        self.assertEqual(len(psnr), 1 if supports_option(FFMPEG, "-fps_mode") else 2)
        for command in psnr:
            self.assertEqual(command.count("-protocol_whitelist"), 2)


class RemotePlaylistTests(unittest.TestCase):
    def test_a_playlist_that_points_at_a_web_address_is_refused_by_every_script(self):
        cases = (
            ("qa_probe.py", ["remote.m3u8"]),
            ("qa_audio.py", ["remote.m3u8"]),
            ("qa_frames.py", ["remote.m3u8"]),
            ("qa_compare.py", ["remote.m3u8", "clip.mp4"]),
            ("run_qa.py", ["remote.m3u8"]),
        )
        for script, args in cases:
            with self.subTest(script=script):
                code, out, err = cli(script, *args)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "", "no report may be produced from a refused file")
                self.assertIn("error:", err)

    def test_the_refusal_names_the_whitelist_not_a_connection_problem(self):
        # ffprobe and ffmpeg say 'not on whitelist' when the protocol is refused before any
        # connection; a connection failure would read 'Connection refused' or 'timed out'.
        code, _out, err = cli("qa_probe.py", "remote.m3u8")
        self.assertEqual(code, 2)
        self.assertIn("not on whitelist", err)
        for word in ("Connection refused", "timed out", "Network"):
            self.assertNotIn(word, err)

    def test_ordinary_local_files_still_work(self):
        code, out, _err = cli("run_qa.py", "clip.mp4", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["overall"], "PASS")


if __name__ == "__main__":
    unittest.main()
