#!/usr/bin/env python3
# test_barcode.py - tests for barcode.py and make_test_clip.py (real ffmpeg, no mocks).
#
# Purpose: prove the barcode writer and reader agree, that the reader survives realistic
#          re-encoding, and that it refuses damaged or mirrored barcodes (negative controls).
# Inputs:  none. Needs ffmpeg and ffprobe on PATH. Media is generated, never copied.
# Outputs: test results on stderr; generated media under a per-run folder in _work/video-export-qa/barcode-frames/.
# Run:     python -I -B -m unittest discover -s tests/video-export-qa -p "test_barcode.py"
# Exit codes: 0 = all tests passed, 1 = a test failed.

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPTS = os.path.join(ROOT, "packages", "video-export-qa", "scripts")
from ffmpeg_fixtures import private_work_folder  # noqa: E402
WORK = private_work_folder(ROOT, "barcode-frames", "test_barcode")
sys.path.insert(0, SCRIPTS)

import barcode  # noqa: E402
import common  # noqa: E402
import make_test_clip  # noqa: E402
import qa_frames  # noqa: E402

FFMPEG = None
FFPROBE = None
SIZE = (640, 360)
FRAMES = 96
IDENTITY = list(range(FRAMES))


def setUpModule():
    global FFMPEG, FFPROBE
    FFMPEG = common.find_tool("ffmpeg")  # a missing tool is a loud error, not a silent skip
    FFPROBE = common.find_tool("ffprobe")
    os.makedirs(WORK, exist_ok=True)


# ---------------------------------------------------------------- helpers

def path_in_work(name):
    return os.path.join(WORK, name)


def run_ffmpeg(*args):
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"] + list(args)
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise AssertionError("ffmpeg failed: %s" % proc.stderr.decode("utf-8", errors="replace"))


def run_script(name, *args):
    cmd = [sys.executable, "-I", "-B", os.path.join(SCRIPTS, name)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(path):
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True,
    )
    return json.loads(proc.stdout)


def has_encoder(name):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return re.search(r"\s%s\s" % re.escape(name), proc.stdout) is not None


def decode(path):
    """Decode every frame of a file with the real reader. Returns a qa_frames.Decoded."""
    return qa_frames.decode_video(FFMPEG, path, barcode.DEFAULT_REGION, [])


def write_clip(name, frame_source, count, size=SIZE):
    path = path_in_work(name)
    make_test_clip.generate_clip(FFMPEG, path, frame_source, count, "24", size, tone=0, crf=14, overwrite=True)
    return path


def mirror_rgb(frame, width):
    """Horizontally flip one rgb24 frame (pure Python, used on single frames only)."""
    row_bytes = width * 3
    out = bytearray()
    for start in range(0, len(frame), row_bytes):
        row = frame[start:start + row_bytes]
        pixels = [row[i:i + 3] for i in range(0, row_bytes, 3)]
        out += b"".join(reversed(pixels))
    return bytes(out)


def invert_block(frame, width, height, block):
    """Invert the colours of one barcode block (all strip rows) in an rgb24 frame."""
    edges = barcode.block_edges(width)
    data = bytearray(frame)
    for row in range(barcode.strip_height(height)):
        start = (row * width + edges[block]) * 3
        stop = (row * width + edges[block + 1]) * 3
        data[start:stop] = bytes(255 - b for b in data[start:stop])
    return bytes(data)


def gray_image(bits, white=255, black=0):
    """A synthetic 208x4 grey image for the given 26 bits."""
    row = b"".join(bytes([white if bit else black]) * barcode.COLUMNS_PER_BLOCK for bit in bits)
    return row * barcode.DECODE_HEIGHT


# ---------------------------------------------------------------- bits, checksum, markers (pure)

class BitsTests(unittest.TestCase):
    def test_round_trip_every_index(self):
        for index in range(barcode.MAX_INDEX + 1):
            self.assertEqual(barcode.decode_bits(barcode.encode_bits(index)), (index, barcode.OK))

    def test_known_vector_for_index_37(self):
        # 37 = 0000 0000 0010 0101, hex digits 0,0,2,5, sum 7 = 0111
        expected = "101" + "0000000000100101" + "0111" + "001"
        self.assertEqual("".join(str(b) for b in barcode.encode_bits(37)), expected)

    def test_checksum_values(self):
        self.assertEqual(barcode.checksum_nibble(0), 0)
        self.assertEqual(barcode.checksum_nibble(0x1234), 10)
        self.assertEqual(barcode.checksum_nibble(0xFFFF), 60 % 16)

    def test_edge_indices_named_in_the_contract(self):
        for index in (0, 1, 255, 256, 65535):
            bits = barcode.encode_bits(index)
            self.assertEqual(len(bits), barcode.NUM_BLOCKS)
            self.assertEqual(barcode.decode_bits(bits)[0], index)

    def test_out_of_range_index_is_refused(self):
        for bad in (-1, 65536, "7", 3.5, None):
            with self.assertRaises(ValueError):
                barcode.encode_bits(bad)

    def test_wrong_number_of_bits_is_refused(self):
        with self.assertRaises(ValueError):
            barcode.decode_bits([1, 0, 1])

    # negative controls -------------------------------------------------

    def test_every_single_bit_damage_in_index_or_checksum_is_rejected(self):
        for index in (0, 1, 255, 256, 4660, 43981, 65535):
            good = barcode.encode_bits(index)
            for block in range(barcode.INDEX_FIRST_BLOCK, barcode.TAIL_FIRST_BLOCK):
                damaged = list(good)
                damaged[block] ^= 1
                value, reason = barcode.decode_bits(damaged)
                self.assertIsNone(value, "index %d block %d damage went unnoticed" % (index, block))
                self.assertEqual(reason, barcode.CHECKSUM_MISMATCH)

    def test_damage_to_any_marker_block_is_a_marker_mismatch(self):
        good = barcode.encode_bits(1234)
        for block in (0, 1, 2, 23, 24, 25):
            damaged = list(good)
            damaged[block] ^= 1
            self.assertEqual(barcode.decode_bits(damaged), (None, barcode.MARKER_MISMATCH), "block %d" % block)

    def test_a_mirrored_barcode_is_a_marker_mismatch_for_every_index(self):
        for index in range(barcode.MAX_INDEX + 1):
            mirrored = list(reversed(barcode.encode_bits(index)))
            self.assertEqual(barcode.decode_bits(mirrored), (None, barcode.MARKER_MISMATCH), index)

    def test_all_black_and_all_white_are_not_barcodes(self):
        self.assertEqual(barcode.decode_bits([0] * 26), (None, barcode.MARKER_MISMATCH))
        self.assertEqual(barcode.decode_bits([1] * 26), (None, barcode.MARKER_MISMATCH))


# ---------------------------------------------------------------- reading a grey image (pure)

class GrayDecodeTests(unittest.TestCase):
    def test_clean_image_decodes_with_full_margin(self):
        index, reason, margin = barcode.decode_gray(gray_image(barcode.encode_bits(777)))
        self.assertEqual((index, reason), (777, barcode.OK))
        self.assertAlmostEqual(margin, 127.0)  # white 255 is 127 above 128; black 0 is 128 below

    def test_threshold_is_strictly_greater_than_128(self):
        bits = barcode.encode_bits(5)
        self.assertEqual(barcode.decode_gray(gray_image(bits, white=129))[0], 5)
        # white at exactly 128 reads as black, so the markers fail: the boundary is real
        self.assertIsNone(barcode.decode_gray(gray_image(bits, white=128))[0])

    def test_grey_levels_far_from_pure_black_and_white_still_decode(self):
        bits = barcode.encode_bits(40000)
        self.assertEqual(barcode.decode_gray(gray_image(bits, white=190, black=70))[0], 40000)

    def test_block_edge_columns_are_ignored(self):
        bits = barcode.encode_bits(321)
        row = bytearray()
        for bit in bits:
            clean = 255 if bit else 0
            wrong = 0 if bit else 255
            cols = [wrong, wrong] + [clean] * 4 + [wrong, wrong]  # outer 2 columns are garbage
            row += bytes(cols)
        self.assertEqual(barcode.decode_gray(bytes(row) * 4)[0], 321)

    def test_garbage_in_the_centre_columns_does_break_it(self):
        bits = barcode.encode_bits(321)
        row = bytearray()
        for bit in bits:
            wrong = 0 if bit else 255
            row += bytes([wrong] * 8)  # whole block inverted
        self.assertIsNone(barcode.decode_gray(bytes(row) * 4)[0])

    def test_wrong_size_image_is_refused(self):
        with self.assertRaises(ValueError):
            barcode.decode_gray(b"\x00" * 10)


# ---------------------------------------------------------------- geometry and drawing (pure)

class GeometryTests(unittest.TestCase):
    def test_strip_height(self):
        self.assertEqual(barcode.strip_height(360), 30)
        self.assertEqual(barcode.strip_height(720), 60)
        self.assertEqual(barcode.strip_height(1080), 90)
        self.assertEqual(barcode.strip_height(180), 16)  # the 16 px floor
        self.assertEqual(barcode.strip_height(48), 16)

    def test_block_edges_cover_the_width_with_near_equal_blocks(self):
        for width in list(range(208, 700, 7)) + [1280, 1920, 3840]:
            edges = barcode.block_edges(width)
            self.assertEqual(len(edges), 27)
            self.assertEqual(edges[0], 0)
            self.assertEqual(edges[-1], width)
            widths = [edges[i + 1] - edges[i] for i in range(26)]
            self.assertLessEqual(max(widths) - min(widths), 1, "width %d" % width)
            self.assertGreaterEqual(min(widths), 8)

    def test_reader_sample_window_lies_inside_the_writer_block(self):
        for width in list(range(208, 1200, 5)) + [1280, 1920, 2048, 3840]:
            edges = barcode.block_edges(width)
            scale = width / float(barcode.DECODE_WIDTH)
            for block in range(26):
                first = (block * 8 + barcode.SAMPLE_FIRST_COLUMN) * scale
                last = (block * 8 + barcode.SAMPLE_FIRST_COLUMN + barcode.SAMPLE_COLUMNS) * scale
                self.assertGreaterEqual(first, edges[block] - 1e-9, "width %d block %d" % (width, block))
                self.assertLessEqual(last, edges[block + 1] + 1e-9, "width %d block %d" % (width, block))

    def test_negative_control_remainder_absorbing_geometry_misaligns_at_640(self):
        # The contract text (equal floor-width blocks, last block absorbs the remainder) fails
        # the same test at the default width: the proof that proportional edges are needed.
        width = 640
        block_width = width // 26
        literal = [i * block_width for i in range(26)] + [width]
        scale = width / float(barcode.DECODE_WIDTH)
        outside = 0
        for block in range(26):
            first = (block * 8 + barcode.SAMPLE_FIRST_COLUMN) * scale
            last = (block * 8 + barcode.SAMPLE_FIRST_COLUMN + barcode.SAMPLE_COLUMNS) * scale
            if first < literal[block] - 1e-9 or last > literal[block + 1] + 1e-9:
                outside += 1
        self.assertGreater(outside, 0)

    def test_hue_colours(self):
        self.assertEqual(barcode.hue_rgb(0), (140, 56, 56))  # hue 0, s 0.6, v 0.55
        self.assertEqual(barcode.hue_rgb(60), (140, 140, 56))  # 60 * 7 = 420 = 60 degrees
        self.assertEqual(barcode.hue_rgb(360), barcode.hue_rgb(0))  # 360 * 7 = 2520 = 7 full turns
        self.assertNotEqual(barcode.hue_rgb(1), barcode.hue_rgb(2))

    def test_rendered_frame_layout(self):
        width, height = SIZE
        frame = barcode.render_frame_rgb(37, width, height)
        self.assertEqual(len(frame), width * height * 3)
        strip = barcode.strip_height(height)
        colour = bytes(barcode.hue_rgb(37))
        for row in (0, strip - 1):
            pixels = {frame[(row * width + x) * 3:(row * width + x) * 3 + 3] for x in range(width)}
            self.assertEqual(pixels, {barcode.BLACK_PIXEL, barcode.WHITE_PIXEL})
        for row in (strip, height - 1):
            pixels = {frame[(row * width + x) * 3:(row * width + x) * 3 + 3] for x in range(width)}
            self.assertEqual(pixels, {colour})

    def test_exact_round_trip_without_a_codec(self):
        width, height = SIZE
        for index in (0, 1, 255, 256, 4096, 65534, 65535):
            frame = barcode.render_frame_rgb(index, width, height)
            self.assertEqual(barcode.decode_rgb_frame(frame, width, height), (index, barcode.OK))

    def test_exact_round_trip_at_several_sizes(self):
        for width, height in ((208, 48), (320, 180), (640, 360), (1280, 720), (1920, 1080)):
            frame = barcode.render_frame_rgb(43981, width, height)
            self.assertEqual(barcode.decode_rgb_frame(frame, width, height)[0], 43981, (width, height))

    def test_mirrored_frame_is_a_marker_mismatch(self):
        width, height = SIZE
        frame = mirror_rgb(barcode.render_frame_rgb(1234, width, height), width)
        self.assertEqual(barcode.decode_rgb_frame(frame, width, height), (None, barcode.MARKER_MISMATCH))

    def test_checksum_corruption_is_detected_in_pixels(self):
        width, height = SIZE
        frame = invert_block(barcode.render_frame_rgb(1234, width, height), width, height, 20)
        self.assertEqual(barcode.decode_rgb_frame(frame, width, height), (None, barcode.CHECKSUM_MISMATCH))

    def test_too_small_frame_is_refused(self):
        with self.assertRaises(ValueError):
            barcode.render_frame_rgb(1, 100, 100)

    def test_rendering_is_fast_enough_for_pure_python(self):
        width, height = SIZE
        started = time.perf_counter()
        for index in range(FRAMES):
            barcode.render_frame_rgb(index, width, height)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0, "96 frames took %.2fs" % elapsed)


# ---------------------------------------------------------------- the writer script

class MakeTestClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clip = path_in_work("cli_default.mp4")
        started = time.perf_counter()
        cls.result = run_script("make_test_clip.py", cls.clip, "--overwrite")
        cls.generate_seconds = time.perf_counter() - started

    def test_exit_code_and_summary(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertIn(self.clip, self.result.stdout)
        self.assertIn("frames=96 fps=24 size=640x360", self.result.stdout)

    def test_generation_is_within_the_time_target(self):
        self.assertLess(self.generate_seconds, 20.0, "took %.2fs" % self.generate_seconds)

    def test_container_facts(self):
        info = probe(self.clip)
        video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        audio = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual((video["codec_name"], video["pix_fmt"]), ("h264", "yuv420p"))
        self.assertEqual((video["width"], video["height"]), (640, 360))
        self.assertEqual(video["r_frame_rate"], "24/1")
        self.assertEqual(int(video["nb_read_frames"]), 96)
        self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))
        self.assertAlmostEqual(float(info["format"]["duration"]), 4.0, delta=0.05)

    def test_colour_is_tagged_bt709_limited(self):
        video = [s for s in probe(self.clip)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual(video["color_space"], "bt709")
        self.assertEqual(video["color_transfer"], "bt709")
        self.assertEqual(video["color_primaries"], "bt709")
        self.assertEqual(video["color_range"], "tv")

    def test_metadata_comment_is_exact(self):
        comment = probe(self.clip)["format"]["tags"]["comment"]
        self.assertEqual(comment, "video-export-qa barcode clip v1 frames=96 fps=24 size=640x360")

    def test_gop_is_at_most_12_frames(self):
        def longest_gap(path):
            proc = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pict_type",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True,
            )
            types = [line.strip(",") for line in proc.stdout.split()]
            keyframes = [i for i, kind in enumerate(types) if kind == "I"] + [len(types)]
            return max(b - a for a, b in zip(keyframes, keyframes[1:]))

        self.assertLessEqual(longest_gap(self.clip), 12)
        # negative control: the same measurement does see a long GOP when one is asked for
        long_gop = path_in_work("long_gop.mp4")
        run_ffmpeg("-i", self.clip, "-an", "-c:v", "libx264", "-g", "250", "-sc_threshold", "0", long_gop)
        self.assertGreater(longest_gap(long_gop), 12)

    def test_tone_is_minus_20_dbfs_peak(self):
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostdin", "-i", self.clip, "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        peak = float(re.search(r"max_volume:\s*(-?[\d.]+) dB", proc.stderr).group(1))
        self.assertAlmostEqual(peak, -20.0, delta=1.0)

    def test_output_is_deterministic(self):
        again = path_in_work("cli_default_again.mp4")
        self.assertEqual(run_script("make_test_clip.py", again, "--overwrite").returncode, 0)
        self.assertEqual(common.sha256_file(again), common.sha256_file(self.clip))

    def test_negative_control_different_arguments_give_different_bytes(self):
        other = path_in_work("cli_other_frames.mp4")
        self.assertEqual(run_script("make_test_clip.py", other, "--frames", "95", "--overwrite").returncode, 0)
        self.assertNotEqual(common.sha256_file(other), common.sha256_file(self.clip))

    def test_tone_zero_means_no_audio(self):
        silent = path_in_work("cli_no_audio.mp4")
        self.assertEqual(run_script("make_test_clip.py", silent, "--tone", "0", "--overwrite").returncode, 0)
        kinds = [s["codec_type"] for s in probe(silent)["streams"]]
        self.assertEqual(kinds, ["video"])

    def test_json_output(self):
        out = path_in_work("cli_json.mp4")
        result = run_script("make_test_clip.py", out, "--overwrite", "--json", "--frames", "24")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["tool"], data["frames"], data["size"]), ("make_test_clip.py", 24, "640x360"))
        self.assertEqual(data["sha256"], common.sha256_file(out))

    def test_custom_size_fps_and_frames(self):
        out = path_in_work("cli_custom.mp4")
        result = run_script("make_test_clip.py", out, "--overwrite", "--frames", "30", "--fps", "30000/1001",
                            "--size", "320x240", "--tone", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        video = probe(out)["streams"][0]
        self.assertEqual((video["width"], video["height"], int(video["nb_read_frames"])), (320, 240, 30))
        self.assertEqual(video["r_frame_rate"], "30000/1001")

    # refusals: exit code 2 and a message on stderr -----------------------

    def test_refuses_to_overwrite_without_the_flag(self):
        before = common.sha256_file(self.clip)
        result = run_script("make_test_clip.py", self.clip)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--overwrite", result.stderr)
        self.assertEqual(common.sha256_file(self.clip), before)  # untouched

    def test_bad_arguments_exit_2(self):
        out = path_in_work("never_written.mp4")
        for args in (["--frames", "0"], ["--frames", "70000"], ["--size", "641x360"], ["--size", "100x100"],
                     ["--size", "abc"], ["--fps", "0"], ["--fps", "x"], ["--crf", "99"], ["--tone", "-5"],
                     ["--tone", "30000"]):
            result = run_script("make_test_clip.py", out, "--overwrite", *args)
            self.assertEqual(result.returncode, 2, "%s -> %s" % (args, result.stderr))
            self.assertNotEqual(result.stderr.strip(), "")
        self.assertFalse(os.path.exists(out), "a rejected run must not create output")

    def test_missing_output_folder_exit_2(self):
        result = run_script("make_test_clip.py", path_in_work("no_such_folder/clip.mp4"))
        self.assertEqual(result.returncode, 2)

    def test_missing_ffmpeg_exit_2(self):
        result = run_script("make_test_clip.py", path_in_work("x.mp4"), "--ffmpeg", path_in_work("no_such_ffmpeg"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found", result.stderr)

    def test_frame_source_of_the_wrong_length_is_an_error(self):
        out = path_in_work("short_source.mp4")
        source = (barcode.render_frame_rgb(i, *SIZE) for i in range(10))
        with self.assertRaises(common.QAError):
            make_test_clip.generate_clip(FFMPEG, out, source, 12, "24", SIZE, tone=0, overwrite=True)


# ---------------------------------------------------------------- the reader through real codecs

class CodecSurvivalTests(unittest.TestCase):
    """The barcode must come back exactly (0..95) after the transforms an export might apply."""

    MIN_MARGIN = 50.0  # of a possible 127.5: headroom measured, not just pass/fail

    @classmethod
    def setUpClass(cls):
        cls.ref = write_clip("ref.mp4", make_test_clip.index_frames(FRAMES, SIZE), FRAMES)

    def make(self, name, *args):
        out = path_in_work(name)
        run_ffmpeg("-i", self.ref, "-an", *args, out)
        return out

    def assert_identity(self, path, label):
        decoded = decode(path)
        self.assertEqual(len(decoded.sequence), FRAMES, "%s: frame count" % label)
        self.assertEqual(decoded.sequence, IDENTITY, "%s: indices" % label)
        self.assertGreaterEqual(decoded.min_margin, self.MIN_MARGIN, "%s: margin %.1f" % (label, decoded.min_margin))

    def test_identity(self):
        self.assert_identity(self.ref, "identity")

    def test_round_trip_edge_indices_through_the_codec(self):
        indices = [0, 1, 255, 256, 4095, 4096, 43981, 65534, 65535]
        source = (barcode.render_frame_rgb(i, *SIZE) for i in indices)
        path = write_clip("edge_indices.mp4", source, len(indices))
        self.assertEqual(decode(path).sequence, indices)

    def test_survives_crf_28(self):
        self.assert_identity(self.make("crf28.mp4", "-c:v", "libx264", "-crf", "28"), "crf 28")

    def test_survives_crf_51(self):
        self.assert_identity(self.make("crf51.mp4", "-c:v", "libx264", "-crf", "51"), "crf 51")

    def test_survives_rescale_to_1280x720(self):
        path = self.make("r720.mp4", "-vf", "scale=1280:720", "-c:v", "libx264", "-crf", "18")
        self.assert_identity(path, "1280x720")

    def test_survives_rescale_to_720p_and_back(self):
        up = self.make("up720.mp4", "-vf", "scale=1280:720", "-c:v", "libx264", "-crf", "18")
        back = path_in_work("back360.mp4")
        run_ffmpeg("-i", up, "-an", "-vf", "scale=640:360", "-c:v", "libx264", "-crf", "18", back)
        self.assert_identity(back, "720p and back")

    def test_survives_rescale_to_1920x1080(self):
        path = self.make("r1080.mp4", "-vf", "scale=1920:1080", "-c:v", "libx264", "-crf", "18")
        self.assert_identity(path, "1920x1080")

    def test_survives_rescale_to_320x180(self):
        path = self.make("r180.mp4", "-vf", "scale=320:180", "-c:v", "libx264", "-crf", "18")
        self.assert_identity(path, "320x180")

    def test_survives_odd_aspect_rescale(self):
        path = self.make("r854.mp4", "-vf", "scale=854:480", "-c:v", "libx264", "-crf", "18")
        self.assert_identity(path, "854x480")

    def test_survives_mpeg4_reencode(self):
        self.assert_identity(self.make("mpeg4.mp4", "-c:v", "mpeg4"), "mpeg4 default bitrate")
        self.assert_identity(self.make("mpeg4_q15.mp4", "-c:v", "mpeg4", "-q:v", "15"), "mpeg4 q 15")

    def test_survives_mkv_remux(self):
        self.assert_identity(self.make("remux.mkv", "-c:v", "copy"), "mkv remux")

    def test_survives_full_range_pixel_format(self):
        path = self.make("fullrange.mp4", "-vf", "scale=out_range=pc", "-c:v", "libx264", "-pix_fmt", "yuvj420p",
                         "-crf", "18")
        self.assert_identity(path, "full range")

    def test_survives_prores_10_bit(self):
        if not has_encoder("prores_ks"):
            self.skipTest("this ffmpeg build has no prores_ks encoder")
        path = self.make("prores.mov", "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le")
        pix_fmt = probe(path)["streams"][0]["pix_fmt"]
        self.assertEqual(pix_fmt, "yuv422p10le")  # the transform really was 10-bit
        self.assert_identity(path, "prores 10-bit")

    def test_survives_moderate_brightness_and_contrast_changes(self):
        for name, filt in (("bright.mp4", "eq=brightness=0.2"), ("dark.mp4", "eq=brightness=-0.2"),
                           ("flat.mp4", "eq=contrast=0.5")):
            path = self.make(name, "-vf", filt, "-c:v", "libx264", "-crf", "18")
            decoded = decode(path)
            self.assertEqual(decoded.sequence, IDENTITY, name)

    # negative controls -----------------------------------------------------

    def test_negative_control_mirrored_clip_is_unreadable_with_marker_mismatch(self):
        path = self.make("hflip.mp4", "-vf", "hflip", "-c:v", "libx264", "-crf", "14")
        decoded = decode(path)
        self.assertEqual(decoded.sequence, [None] * FRAMES)
        self.assertEqual(dict(decoded.reasons), {barcode.MARKER_MISMATCH: FRAMES})

    def test_negative_control_one_mirrored_frame_in_a_clip(self):
        width, height = SIZE

        def source():
            for i in range(FRAMES):
                frame = barcode.render_frame_rgb(i, width, height)
                yield mirror_rgb(frame, width) if i == 5 else frame

        decoded = decode(write_clip("one_flipped.mp4", source(), FRAMES))
        expected = list(IDENTITY)
        expected[5] = None
        self.assertEqual(decoded.sequence, expected)
        self.assertEqual(dict(decoded.reasons), {barcode.MARKER_MISMATCH: 1})
        self.assertEqual(decoded.undecodable_frames, [5])

    def test_negative_control_checksum_corruption_in_a_clip(self):
        width, height = SIZE

        def source():
            for i in range(FRAMES):
                frame = barcode.render_frame_rgb(i, width, height)
                yield invert_block(frame, width, height, 20) if 10 <= i < 20 else frame

        decoded = decode(write_clip("checksum_damage.mp4", source(), FRAMES))
        expected = [None if 10 <= i < 20 else i for i in IDENTITY]
        self.assertEqual(decoded.sequence, expected)
        self.assertEqual(dict(decoded.reasons), {barcode.CHECKSUM_MISMATCH: 10})

    def test_negative_control_index_bit_damage_in_a_clip(self):
        width, height = SIZE

        def source():
            for i in range(FRAMES):
                frame = barcode.render_frame_rgb(i, width, height)
                yield invert_block(frame, width, height, 10) if i == 50 else frame

        decoded = decode(write_clip("index_damage.mp4", source(), FRAMES))
        self.assertIsNone(decoded.sequence[50])
        self.assertEqual(dict(decoded.reasons), {barcode.CHECKSUM_MISMATCH: 1})

    def test_negative_control_reading_the_wrong_region_finds_nothing(self):
        decoded = qa_frames.decode_video(FFMPEG, self.ref, (0.0, 0.5, 1.0, 0.6), [])  # flat colour area
        self.assertEqual(decoded.sequence, [None] * FRAMES)

    def test_decode_time_is_within_the_target(self):
        started = time.perf_counter()
        result = run_script("qa_frames.py", self.ref, "--json")
        elapsed = time.perf_counter() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 10.0, "decode took %.2fs" % elapsed)


# ---------------------------------------------------------------- the hashing helper the other tests rely on

class SanityTests(unittest.TestCase):
    def test_sha256_helper_matches_hashlib(self):
        path = path_in_work("sanity.bin")
        with open(path, "wb") as fh:
            fh.write(b"abc" * 1000)
        self.assertEqual(common.sha256_file(path), hashlib.sha256(b"abc" * 1000).hexdigest())


if __name__ == "__main__":
    unittest.main()
