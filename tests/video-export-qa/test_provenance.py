# Tests that every script records how its result was made (provenance) and that the recorded
# sha256 of the input is the real sha256 of the file. Real ffmpeg, files generated here.
# Every comparison is made against a hash computed independently with hashlib, and each
# positive test has a negative control (a changed file must change the recorded hash).
# Run:  python -B -m unittest discover -s tests/video-export-qa
import datetime
import functools
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "packages" / "video-export-qa" / "scripts"
from ffmpeg_fixtures import private_work_folder  # noqa: E402
WORK = Path(private_work_folder(ROOT, "integrator", "tests-provenance"))
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

PROVENANCE_KEYS = {"script", "version", "python", "created_utc", "ffmpeg_version", "ffprobe_version",
                   "input", "spec_sha256", "commands"}
INPUT_KEYS = {"path", "size_bytes", "mtime_utc", "sha256"}
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# script, arguments (file names are relative to the fixture folder), the input file the hash is about,
# the tools whose commands the script runs, the tools whose version it records, whether it reads a spec.
BOTH = {"ffmpeg", "ffprobe"}
RUNS = [
    ("qa_probe.py", ["clip.mp4", "--spec", "spec.json", "--json"], "clip.mp4", {"ffprobe"}, BOTH, True),
    ("qa_audio.py", ["clip.mp4", "--spec", "spec.json", "--json"], "clip.mp4", BOTH, BOTH, True),
    ("qa_frames.py", ["clip.mp4", "--expect", "0-23", "--json"], "clip.mp4", {"ffmpeg"}, BOTH, False),
    ("qa_gif.py", ["anim.gif", "--fps", "15", "--json"], "anim.gif", set(), set(), False),
    ("qa_compare.py", ["clip.mp4", "other.mp4", "--json"], "clip.mp4", {"ffmpeg"}, {"ffmpeg"}, False),
    ("run_qa.py", ["clip.mp4", "--spec", "spec.json", "--barcode-expect", "0-23", "--json"], "clip.mp4", BOTH, BOTH, True),
]


def sha256(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def first_line(command):
    proc = subprocess.run([command, "-version"], capture_output=True)
    return proc.stdout.decode("utf-8", "replace").splitlines()[0].strip()


def cli(script, *args):
    cmd = [sys.executable, "-I", "-B", str(SCRIPTS / script)] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


@functools.lru_cache(maxsize=None)
def run_json(script, args):
    code, out, err = cli(script, *args)
    if not out.strip():
        raise AssertionError("%s printed nothing (exit %d): %s" % (script, code, err))
    return code, json.loads(out)


def ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, cwd=str(WORK))
    if proc.returncode != 0:
        raise RuntimeError("fixture ffmpeg failed: %s\n%s" % (cmd, proc.stderr.decode("utf-8", "replace")))


def setUpModule():
    if not (FFMPEG and FFPROBE):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to run these tests")
    WORK.mkdir(parents=True, exist_ok=True)
    for name, frames in (("clip.mp4", "24"), ("other.mp4", "25")):
        code, _out, err = cli("make_test_clip.py", name, "--frames", frames, "--overwrite")
        if code != 0:
            raise RuntimeError("make_test_clip.py failed: " + err)
    ffmpeg("-i", "clip.mp4", "-t", "1", "-an", "-vf", "fps=15,scale=160:90", "anim.gif")
    with open(str(WORK / "spec.json"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"video": {"width": 640}, "no_clipping": True}))


class ProvenanceShapeTests(unittest.TestCase):
    def test_every_script_writes_a_provenance_object_with_the_documented_fields(self):
        for script, args, _file, _runs, _versions, _spec in RUNS:
            with self.subTest(script=script):
                _code, data = run_json(script, tuple(args))
                prov = data["provenance"]
                self.assertEqual(set(prov), PROVENANCE_KEYS)
                self.assertEqual(prov["script"], script)
                self.assertEqual(prov["version"], common.VERSION)
                self.assertRegex(prov["version"], r"^\d+\.\d+\.\d+$")
                self.assertEqual(prov["python"], platform.python_version())
                self.assertEqual(data["tool"], script)

    def test_created_time_is_a_real_recent_utc_time(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for script, args, _file, _runs, _versions, _spec in RUNS:
            with self.subTest(script=script):
                _code, data = run_json(script, tuple(args))
                made = datetime.datetime.strptime(data["provenance"]["created_utc"], UTC_FORMAT).replace(tzinfo=datetime.timezone.utc)
                self.assertLess(abs((now - made).total_seconds()), 600)

    def test_tool_versions_are_the_real_first_lines_of_the_tools(self):
        ffmpeg_line, ffprobe_line = first_line(FFMPEG), first_line(FFPROBE)
        self.assertTrue(ffmpeg_line.startswith("ffmpeg version"))
        self.assertTrue(ffprobe_line.startswith("ffprobe version"))
        for script, args, _file, _runs, versions, _spec in RUNS:
            with self.subTest(script=script):
                prov = run_json(script, tuple(args))[1]["provenance"]
                self.assertEqual(prov["ffmpeg_version"], ffmpeg_line if "ffmpeg" in versions else None)
                self.assertEqual(prov["ffprobe_version"], ffprobe_line if "ffprobe" in versions else None)

    def test_commands_are_argument_lists_that_start_with_the_tool_that_was_run(self):
        for script, args, _file, runs, _versions, _spec in RUNS:
            with self.subTest(script=script):
                commands = run_json(script, tuple(args))[1]["provenance"]["commands"]
                if not runs:
                    self.assertEqual(commands, [])  # qa_gif.py reads the file itself and starts no tool
                    continue
                self.assertTrue(commands)
                names = set()
                for command in commands:
                    self.assertIsInstance(command, list)
                    self.assertTrue(all(isinstance(part, str) for part in command))
                    names.add(re.sub(r"\.exe$", "", os.path.basename(command[0]).lower()))
                self.assertEqual(names, runs)


class InputHashTests(unittest.TestCase):
    def test_recorded_input_matches_the_file_on_disk(self):
        for script, args, name, _runs, _versions, _spec in RUNS:
            with self.subTest(script=script):
                prov = run_json(script, tuple(args))[1]["provenance"]
                info = prov["input"]
                self.assertEqual(set(info), INPUT_KEYS)
                self.assertEqual(info["path"], name)
                self.assertEqual(info["size_bytes"], os.path.getsize(str(WORK / name)))
                self.assertEqual(info["sha256"], sha256(WORK / name))
                self.assertRegex(info["sha256"], r"^[0-9a-f]{64}$")
                stamp = datetime.datetime.fromtimestamp(os.stat(str(WORK / name)).st_mtime, datetime.timezone.utc)
                self.assertEqual(info["mtime_utc"], stamp.strftime(UTC_FORMAT))

    def test_a_changed_file_gives_a_different_recorded_hash_that_matches_the_new_bytes(self):
        # Negative control: if the hash were a constant or came from somewhere else, this would fail.
        data = bytearray((WORK / "clip.mp4").read_bytes())
        data[-5] ^= 0x01
        (WORK / "clip_changed.mp4").write_bytes(bytes(data))
        original = run_json("qa_probe.py", ("clip.mp4", "--json"))[1]["provenance"]["input"]["sha256"]
        _code, out, _err = cli("qa_probe.py", "clip_changed.mp4", "--json")
        changed = json.loads(out)["provenance"]["input"]["sha256"]
        self.assertNotEqual(original, changed)
        self.assertEqual(changed, sha256(WORK / "clip_changed.mp4"))
        self.assertEqual(original, sha256(WORK / "clip.mp4"))

    def test_the_hash_is_not_the_hash_of_the_name_or_of_nothing(self):
        recorded = run_json("qa_probe.py", ("clip.mp4", "--json"))[1]["provenance"]["input"]["sha256"]
        self.assertNotEqual(recorded, hashlib.sha256(b"clip.mp4").hexdigest())
        self.assertNotEqual(recorded, hashlib.sha256(b"").hexdigest())

    def test_qa_compare_records_the_second_file_hash_too(self):
        _code, data = run_json("qa_compare.py", ("clip.mp4", "other.mp4", "--json"))
        self.assertEqual(data["provenance"]["input"]["sha256"], sha256(WORK / "clip.mp4"))
        self.assertEqual(data["info"]["b"]["sha256"], sha256(WORK / "other.mp4"))
        self.assertNotEqual(data["info"]["b"]["sha256"], data["provenance"]["input"]["sha256"])


class SpecHashTests(unittest.TestCase):
    def test_spec_hash_is_the_hash_of_the_spec_file_bytes(self):
        for script, args, _file, _runs, _versions, reads_spec in RUNS:
            with self.subTest(script=script):
                prov = run_json(script, tuple(args))[1]["provenance"]
                if reads_spec:
                    self.assertEqual(prov["spec_sha256"], sha256(WORK / "spec.json"))
                else:
                    self.assertIsNone(prov["spec_sha256"])

    def test_a_different_spec_gives_a_different_hash(self):
        with open(str(WORK / "spec2.json"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"video": {"width": 641}, "no_clipping": True}))
        _code, out, _err = cli("qa_audio.py", "clip.mp4", "--spec", "spec2.json", "--json")
        other = json.loads(out)["provenance"]["spec_sha256"]
        self.assertEqual(other, sha256(WORK / "spec2.json"))
        self.assertNotEqual(other, sha256(WORK / "spec.json"))


class ClipGeneratorTests(unittest.TestCase):
    def test_make_test_clip_json_describes_the_clip_it_wrote(self):
        code, out, _err = cli("make_test_clip.py", "gen.mp4", "--frames", "12", "--overwrite", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        prov = data["provenance"]
        self.assertEqual(set(prov), PROVENANCE_KEYS | {"output"})
        self.assertEqual(prov["script"], "make_test_clip.py")
        self.assertIsNone(prov["input"])  # it reads no input file
        self.assertEqual(prov["ffmpeg_version"], first_line(FFMPEG))
        self.assertEqual(prov["output"]["sha256"], sha256(WORK / "gen.mp4"))
        self.assertEqual(data["sha256"], prov["output"]["sha256"])
        self.assertEqual(prov["output"]["size_bytes"], os.path.getsize(str(WORK / "gen.mp4")))
        self.assertEqual(prov["commands"], data["commands"])

    def test_the_clip_file_carries_its_own_provenance_in_the_container_comment(self):
        proc = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format_tags=comment", "-of", "default=nw=1:nk=1",
                               str(WORK / "clip.mp4")], capture_output=True)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "video-export-qa barcode clip v1 frames=24 fps=24 size=640x360")


class LargeInputHashTests(unittest.TestCase):
    """The hash covers the WHOLE file, not just its first chunk. The fixtures above are small; these
    are larger than the 1 MiB read size, and differ from each other only after it."""

    @classmethod
    def setUpClass(cls):
        cls.data = b"".join(hashlib.sha256(str(i).encode("ascii")).digest() for i in range(81920))  # 2.5 MiB
        cls.big = WORK / "big_input.bin"
        cls.big.write_bytes(cls.data)
        cls.last_byte_changed = WORK / "big_input_changed.bin"
        cls.last_byte_changed.write_bytes(cls.data[:-1] + bytes([cls.data[-1] ^ 0xFF]))

    def test_the_fixture_is_larger_than_the_read_size_and_differs_only_after_the_first_chunk(self):
        self.assertGreater(len(self.data), 2 * (1 << 20))
        self.assertEqual(self.data[:1 << 20], self.last_byte_changed.read_bytes()[:1 << 20])
        self.assertNotEqual(self.data, self.last_byte_changed.read_bytes())

    def test_the_hash_of_a_file_larger_than_one_chunk_is_the_hash_of_all_of_it(self):
        self.assertEqual(common.sha256_file(str(self.big)), hashlib.sha256(self.data).hexdigest())
        self.assertNotEqual(common.sha256_file(str(self.big)), hashlib.sha256(self.data[:1 << 20]).hexdigest())

    def test_a_change_after_the_first_megabyte_changes_the_hash(self):
        self.assertNotEqual(common.sha256_file(str(self.big)), common.sha256_file(str(self.last_byte_changed)))

    def test_the_chunk_size_does_not_change_the_answer(self):
        expected = hashlib.sha256(self.data).hexdigest()
        for chunk in (1000, 1 << 20, 1 << 22):
            self.assertEqual(common.sha256_file(str(self.big), chunk=chunk), expected, chunk)

    def test_provenance_records_the_whole_file_hash_and_size(self):
        report = common.Report("x.py", file=str(self.big), tools={})
        recorded = report.provenance()["input"]
        self.assertEqual(recorded["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(recorded["size_bytes"], len(self.data))


class NoResultNoProvenanceTests(unittest.TestCase):
    def test_a_run_that_cannot_happen_prints_no_report_at_all(self):
        # Negative control: provenance is only ever attached to a result that was really measured.
        for script in ("qa_probe.py", "qa_audio.py", "qa_frames.py", "qa_gif.py", "run_qa.py"):
            with self.subTest(script=script):
                code, out, err = cli(script, "no_such_file.mp4", "--json")
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
