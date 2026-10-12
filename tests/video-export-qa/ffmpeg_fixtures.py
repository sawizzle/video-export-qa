"""Fixture-only ffmpeg compatibility; never adapt commands executed by the product."""
import atexit
import functools
import os
import shutil
import subprocess
import unittest
import uuid


def private_work_folder(root, *parts):
    """The scratch folder of ONE run of the suite, as text: <root>/_work/video-export-qa/<parts>-<pid>-<random>.
    A fixed folder is shared by every run, so two runs that overlap (a second terminal, a review running the suite
    while another run is going) overwrite each other's clips and fail with errors such as "moov atom not found".
    The module that asks for the folder creates it; it is removed when the run ends."""
    folder = os.path.join(str(root), "_work", "video-export-qa", *parts[:-1],
                          "%s-%d-%s" % (parts[-1], os.getpid(), uuid.uuid4().hex[:8]))
    atexit.register(shutil.rmtree, folder, True)
    return folder


@functools.lru_cache(maxsize=None)
def supports_option(ffmpeg, option):
    """Probe once per executable/option with a real, one-frame lavfi encode."""
    source = ["-f", "lavfi", "-i", "testsrc2=size=16x16:rate=1"]
    if option == "-display_rotation":
        args = [option, "90"] + source
    elif option == "-fps_mode":
        args = source + [option, "passthrough"]
    else:
        raise ValueError("no capability probe for " + option)
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
        + args + ["-frames:v", "1", "-f", "null", "-"], capture_output=True)
    if proc.returncode == 0:
        return True
    error = proc.stderr.decode("utf-8", "replace")
    if "Unrecognized option" in error and option.lstrip("-") in error:
        return False
    raise AssertionError("ffmpeg capability probe failed unexpectedly: " + error)


def fixture_command(cmd):
    """Use legacy frame-sync spelling only for test fixture/reference commands."""
    cmd = [str(arg) for arg in cmd]
    if "-fps_mode" in cmd and not supports_option(cmd[0], "-fps_mode"):
        values = {"passthrough": "0", "vfr": "2", "cfr": "1"}
        for i in range(len(cmd) - 1):
            if cmd[i] == "-fps_mode":
                cmd[i:i + 2] = ["-vsync", values[cmd[i + 1]]]
    return cmd


class UnavailableFixture:
    """An unavailable optional fixture, resolved only by its dependent test."""
    def __init__(self, reason):
        self.reason = reason

    def require(self):
        raise unittest.SkipTest(self.reason)


class FailedFixture(UnavailableFixture):
    """A broken fixture is an assertion failure, never a capability skip."""
    def require(self):
        raise AssertionError(self.reason)


def guarded_fixture(build, context):
    """Defer one fixture's failure to dependents, allowing setup to continue."""
    try:
        return build()
    except Exception as error:
        return FailedFixture("%s: %s: %s" % (context, type(error).__name__, error))


class FixturePaths(dict):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        if isinstance(value, UnavailableFixture):
            value.require()
        return value


def optional_fixture(ffmpeg, option, build):
    """Leave unrelated fixtures/tests runnable when this input option is missing."""
    def attempt():
        if not supports_option(ffmpeg, option):
            return UnavailableFixture("ffmpeg build lacks " + option)
        return build()
    return guarded_fixture(attempt, "fixture requiring " + option)


def require_fixture(value):
    if isinstance(value, UnavailableFixture):
        value.require()
    return value
