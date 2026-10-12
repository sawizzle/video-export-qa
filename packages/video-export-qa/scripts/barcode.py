#!/usr/bin/env python3
# barcode.py - the frame-index barcode shared by make_test_clip.py and qa_frames.py.
#
# Purpose: one definition of the barcode that proves frame identity. make_test_clip.py
#          draws it, qa_frames.py reads it back, and both import the same constants so
#          the writer and the reader can never drift apart.
# Inputs:  none. This module is imported by other scripts; it is not run directly.
# Outputs: none. Functions return bytes / lists / tuples; callers decide what to print.
# Exit codes: not applicable (library module).
#
# Rules this module follows: standard library only, no environment variable reads,
# no network, every subprocess call takes an argument list (never a shell string).
#
# BARCODE FORMAT (version 1)
#   The strip is the top max(16, round(H/12)) pixel rows of the frame, full width,
#   divided into 26 blocks. White (255) = bit 1, black (0) = bit 0.
#     blocks  0-2   marker  W B W
#     blocks  3-18  16-bit frame index, most significant bit first
#     blocks 19-22  checksum nibble = (n0+n1+n2+n3) mod 16, where n0..n3 are the four
#                   hex digits of the index, most significant first
#     blocks 23-25  marker  B B W
#   The two markers are different on purpose: a horizontally flipped frame reads as a
#   marker mismatch instead of as a plausible wrong index.
#   Below the strip the frame is one flat colour, hue = (index * 7) mod 360 degrees,
#   saturation 0.6, value 0.55.
#
# GEOMETRY NOTE (the one deliberate refinement of the written contract)
#   The contract says the last block "absorbs the remainder". The reader scales the
#   strip to 208 columns (26 blocks x 8) without knowing the original width, so every
#   block must sit at width/26 of the way across. With a remainder-absorbing last block
#   the right-hand blocks drift by up to half a block (at 640 px the reader's window for
#   block 24 straddles blocks 24 and 25). Block edges here are therefore proportional:
#   edge[i] = (i * width) // 26. Blocks still differ in width by at most one pixel and
#   the last block still ends exactly at the right edge. test_barcode.py proves the
#   reader's sample window lies inside the writer's block for many widths.

import colorsys
import subprocess
import threading

NUM_BLOCKS = 26
HEAD_MARKER = (1, 0, 1)  # blocks 0-2: W B W
TAIL_MARKER = (0, 0, 1)  # blocks 23-25: B B W
INDEX_FIRST_BLOCK = 3
INDEX_BITS = 16
CHECK_FIRST_BLOCK = 19
CHECK_BITS = 4
TAIL_FIRST_BLOCK = 23
MAX_INDEX = 65535

MIN_STRIP_PX = 16
STRIP_DIVISOR = 12
# Smallest frame the barcode is meant for: 8 px per block leaves the reader enough pixels.
MIN_FRAME_WIDTH = NUM_BLOCKS * 8
MIN_FRAME_HEIGHT = 48

# Reader geometry: the barcode region is scaled to 208 x 4 grey pixels, 8 columns per block.
COLUMNS_PER_BLOCK = 8
DECODE_WIDTH = NUM_BLOCKS * COLUMNS_PER_BLOCK
DECODE_HEIGHT = 4
DECODE_FRAME_BYTES = DECODE_WIDTH * DECODE_HEIGHT
# Only the middle 4 of each block's 8 columns are averaged: block edges are where
# compression ringing and resampling blur live, block centres stay clean.
SAMPLE_FIRST_COLUMN = 2
SAMPLE_COLUMNS = 4
THRESHOLD = 128  # mean luma above this reads as white

# Default reading region as fractions of the frame: x0, y0, x1, y1.
DEFAULT_REGION = (0.0, 0.0, 1.0, 0.0833)

HUE_STEP_DEGREES = 7
COLOUR_SATURATION = 0.6
COLOUR_VALUE = 0.55

OK = "ok"
MARKER_MISMATCH = "marker mismatch"
CHECKSUM_MISMATCH = "checksum mismatch"

BLACK_PIXEL = b"\x00\x00\x00"
WHITE_PIXEL = b"\xff\xff\xff"


# ---------------------------------------------------------------- geometry

def strip_height(frame_height):
    """Height in pixel rows of the barcode strip for a frame of this height."""
    return max(MIN_STRIP_PX, int(round(frame_height / float(STRIP_DIVISOR))))


def block_edges(frame_width):
    """27 x-positions: block i spans edges[i] .. edges[i+1]. Proportional, ends at the width."""
    return [(i * frame_width) // NUM_BLOCKS for i in range(NUM_BLOCKS + 1)]


# ---------------------------------------------------------------- bits <-> index

def _bits_to_int(bits):
    value = 0
    for bit in bits:
        value = (value << 1) | (1 if bit else 0)
    return value


def _int_to_bits(value, width):
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def checksum_nibble(index):
    """(n0+n1+n2+n3) mod 16 over the four hex digits of the 16-bit index."""
    nibbles = [(index >> shift) & 0xF for shift in (12, 8, 4, 0)]
    return sum(nibbles) % 16


def encode_bits(index):
    """The 26 block bits for a frame index (0..65535)."""
    if not isinstance(index, int) or index < 0 or index > MAX_INDEX:
        raise ValueError("frame index must be an integer from 0 to %d, got %r" % (MAX_INDEX, index))
    return (
        list(HEAD_MARKER)
        + _int_to_bits(index, INDEX_BITS)
        + _int_to_bits(checksum_nibble(index), CHECK_BITS)
        + list(TAIL_MARKER)
    )


def decode_bits(bits):
    """Turn 26 bits back into (index, reason). index is None when the barcode is not valid.

    reason is one of OK, MARKER_MISMATCH (wrong or flipped markers: no barcode here, or the
    frame is mirrored, cropped or scaled out of the reading region) or CHECKSUM_MISMATCH
    (markers fine but the index bits were damaged).
    """
    if len(bits) != NUM_BLOCKS:
        raise ValueError("expected %d bits, got %d" % (NUM_BLOCKS, len(bits)))
    if tuple(bits[0:3]) != HEAD_MARKER or tuple(bits[TAIL_FIRST_BLOCK:NUM_BLOCKS]) != TAIL_MARKER:
        return None, MARKER_MISMATCH
    index = _bits_to_int(bits[INDEX_FIRST_BLOCK:INDEX_FIRST_BLOCK + INDEX_BITS])
    stored = _bits_to_int(bits[CHECK_FIRST_BLOCK:CHECK_FIRST_BLOCK + CHECK_BITS])
    if stored != checksum_nibble(index):
        return None, CHECKSUM_MISMATCH
    return index, OK


# ---------------------------------------------------------------- drawing (writer side)

def hue_rgb(index):
    """The flat colour under the strip for this index, as an (r, g, b) tuple of 0..255."""
    hue = ((index * HUE_STEP_DEGREES) % 360) / 360.0
    red, green, blue = colorsys.hsv_to_rgb(hue, COLOUR_SATURATION, COLOUR_VALUE)
    return (int(red * 255 + 0.5), int(green * 255 + 0.5), int(blue * 255 + 0.5))


def render_frame_rgb(index, width, height):
    """One rgb24 frame (width*height*3 bytes) carrying the barcode for `index`.

    Rows are built with bytes multiplication, never a per-pixel loop: a 640x360 frame costs
    a few dozen small operations instead of 230,400.
    """
    if width < MIN_FRAME_WIDTH or height < MIN_FRAME_HEIGHT:
        raise ValueError(
            "frame size %dx%d is too small for the barcode; use at least %dx%d"
            % (width, height, MIN_FRAME_WIDTH, MIN_FRAME_HEIGHT)
        )
    bits = encode_bits(index)
    edges = block_edges(width)
    strip_row = b"".join(
        (WHITE_PIXEL if bit else BLACK_PIXEL) * (edges[i + 1] - edges[i]) for i, bit in enumerate(bits)
    )
    strip_rows = strip_height(height)
    colour_row = bytes(hue_rgb(index)) * width
    return strip_row * strip_rows + colour_row * (height - strip_rows)


# ---------------------------------------------------------------- reading (reader side)

def block_levels(gray):
    """Mean grey level (0..255) of each of the 26 blocks, from a 208x4 grey image (832 bytes)."""
    if len(gray) != DECODE_FRAME_BYTES:
        raise ValueError("expected %d grey bytes, got %d" % (DECODE_FRAME_BYTES, len(gray)))
    levels = []
    for block in range(NUM_BLOCKS):
        total = 0
        for row in range(DECODE_HEIGHT):
            start = row * DECODE_WIDTH + block * COLUMNS_PER_BLOCK + SAMPLE_FIRST_COLUMN
            total += sum(gray[start:start + SAMPLE_COLUMNS])
        levels.append(total / float(DECODE_HEIGHT * SAMPLE_COLUMNS))
    return levels


def bits_from_levels(levels, threshold=THRESHOLD):
    return [1 if level > threshold else 0 for level in levels]


def decode_gray(gray, threshold=THRESHOLD):
    """Decode one 208x4 grey image. Returns (index_or_None, reason, margin).

    margin is how far the least certain block sat from the threshold (0 = on the knife
    edge, 127 = pure white 255 or better against 128). It is measured data about robustness.
    """
    levels = block_levels(gray)
    index, reason = decode_bits(bits_from_levels(levels, threshold))
    margin = min(abs(level - threshold) for level in levels)
    return index, reason, margin


def decode_rgb_frame(rgb, width, height):
    """Exact reader for an uncompressed rgb24 frame: samples the middle of every block.

    Not used on video files (qa_frames.py reads those through ffmpeg); it exists so the
    writer and reader can be tested against each other with no codec in between.
    """
    if len(rgb) != width * height * 3:
        raise ValueError("frame has %d bytes, expected %d" % (len(rgb), width * height * 3))
    edges = block_edges(width)
    middle_row = strip_height(height) // 2
    bits = []
    for block in range(NUM_BLOCKS):
        x = (edges[block] + edges[block + 1]) // 2
        offset = (middle_row * width + x) * 3
        bits.append(1 if sum(rgb[offset:offset + 3]) / 3.0 > THRESHOLD else 0)
    return decode_bits(bits)


# ---------------------------------------------------------------- streaming ffmpeg helper

STDERR_LIMIT = 64 * 1024


class StreamingProcess:
    """A child process with piped stdin/stdout whose stderr is drained on a thread.

    Reading stdout while the child fills an unread stderr pipe can deadlock; a small
    background reader on stderr removes that risk. Used by both the clip writer (frames in)
    and the barcode reader (frames out). The command must be an argument list.
    """

    def __init__(self, cmd, stdin=False, stdout=False, record=None):
        if not isinstance(cmd, (list, tuple)):
            raise TypeError("commands must be argument lists, never shell strings")
        if record is not None:
            record.append([str(c) for c in cmd])
        self.proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._stderr = bytearray()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        while True:
            chunk = self.proc.stderr.read1(4096)
            if not chunk:
                return
            room = STDERR_LIMIT - len(self._stderr)
            if room > 0:
                self._stderr.extend(chunk[:room])

    @property
    def stdin(self):
        return self.proc.stdin

    @property
    def stdout(self):
        return self.proc.stdout

    def finish(self):
        """Close stdin, wait for the child, return (returncode, stderr_text)."""
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass  # the child already went away; its exit code tells the story
        returncode = self.proc.wait()
        self._thread.join(timeout=10)
        for pipe in (self.proc.stdout, self.proc.stderr):
            if pipe is not None:
                pipe.close()
        return returncode, self._stderr.decode("utf-8", errors="replace")

    def abort(self):
        """Stop the child if it is still running (used when the caller gives up early)."""
        if self.proc.poll() is None:
            self.proc.terminate()
        self.finish()
