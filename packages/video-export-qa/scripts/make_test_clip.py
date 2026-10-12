#!/usr/bin/env python3
# make_test_clip.py - write a short test clip in which every frame carries its own index.
#
# Purpose: produce a known-good reference clip for the frame checks. Each frame shows a
#          barcode of its own frame index (see barcode.py) over a flat colour whose hue
#          follows the index, plus an optional sine tone at -20 dBFS peak. Edit, export or
#          re-encode the clip in your tool, then run qa_frames.py on the result: the barcode
#          says exactly which source frame sits on every output frame.
# Inputs:  OUT.mp4 (path to write; its file name may not contain a colon), --frames N (default 96,
#          max 65536), --fps F (default 24; "24", "24000/1001"; the decimal NTSC spellings 23.976,
#          29.97, 47.952, 59.94 and 119.88 are taken to mean the exact fractions 24000/1001,
#          30000/1001, 48000/1001, 60000/1001 and 120000/1001), --size WxH (default 640x360), --tone HZ (default
#          440, 0 = no audio; the tone peaks at 0.1 = -20 dBFS on each of the two channels),
#          --crf N (default 14), --overwrite, --json, --ffmpeg PATH.
# Outputs: the clip (H.264 yuv420p, GOP 12, tagged BT.709 limited range; AAC 48 kHz stereo
#          when a tone is requested). Prints the path and a frames/fps/size summary, or one
#          JSON object with --json (including a provenance object whose "output" entry holds
#          the clip's size and sha256; "input" is null because this script reads no file).
#          The file carries metadata comment="video-export-qa
#          barcode clip v1 frames=N fps=F size=WxH". Output is deterministic: the same
#          arguments and ffmpeg build give the same bytes.
# Exit codes: 0 = clip written, 2 = could not run (missing ffmpeg, bad arguments, ffmpeg
#          failed, output exists without --overwrite). This script has no FAIL state.
#
# Rules: standard library only, no environment reads except PATH, no network, argument-list
# subprocess only. Frames are piped to ffmpeg as raw rgb24 and never written to disk.
# ffmpeg reads an output name as a URL ("tee:file.mkv" would write through the tee protocol and
# skip the overwrite check), so it is always given the absolute path of a file and is told to
# open local files only.

import argparse
import json
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # needed under python -I

import barcode  # noqa: E402
import common  # noqa: E402
from common import QAError, UsageError  # noqa: E402

SCRIPT = "make_test_clip.py"
CLIP_FORMAT_VERSION = 1
GOP_FRAMES = 12
# A decimal NTSC rate is shorthand for an exact fraction: 23.976 read as a decimal is 2997/125, which
# is not the 24000/1001 that cameras, editors and delivery specs mean.
NTSC_RATES = {
    "23.976": "24000/1001", "29.97": "30000/1001", "47.952": "48000/1001",
    "59.94": "60000/1001", "119.88": "120000/1001",
}
TONE_SAMPLE_RATE = 48000
# lavfi "sine" has a fixed peak of 0.125 (-18.06 dBFS); volume 0.8 makes it 0.1 = -20 dBFS.
# The pan filter copies the mono tone to both channels at unity gain: a plain "-ac 2" upmix
# would lower each channel by 3 dB (measured: -22.2 dB peak instead of -20).
TONE_VOLUME = "0.8"
TONE_STEREO = "pan=stereo|c0=c0|c1=c0"
# Convert RGB to BT.709 limited-range YUV, then tag the stream so players do not guess.
COLOUR_FILTER = (
    "scale=out_color_matrix=bt709:out_range=tv,"
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,"
    "format=yuv420p"
)


def parse_size(text):
    try:
        left, right = text.lower().split("x")
        width, height = int(left), int(right)
    except ValueError:
        raise UsageError("--size must look like 640x360, got %r" % text)
    if width % 2 or height % 2:
        raise UsageError("--size %dx%d: yuv420p needs even width and height" % (width, height))
    if width < barcode.MIN_FRAME_WIDTH or height < barcode.MIN_FRAME_HEIGHT:
        raise UsageError(
            "--size %dx%d is too small for the barcode (minimum %dx%d)"
            % (width, height, barcode.MIN_FRAME_WIDTH, barcode.MIN_FRAME_HEIGHT)
        )
    return width, height


def check_arguments(frames, fps_text, tone, crf):
    if frames < 1 or frames > barcode.MAX_INDEX + 1:
        raise UsageError("--frames must be between 1 and %d, got %d" % (barcode.MAX_INDEX + 1, frames))
    fps = common.parse_fraction(fps_text)
    if fps <= 0 or fps > 240:
        raise UsageError("--fps must be above 0 and at most 240, got %s" % fps_text)
    if tone < 0 or tone > TONE_SAMPLE_RATE / 2 - 1:
        raise UsageError("--tone must be 0 (no audio) or a frequency below %d Hz" % (TONE_SAMPLE_RATE // 2))
    if crf < 0 or crf > 51:
        raise UsageError("--crf must be between 0 and 51, got %d" % crf)
    return fps


def canonical_fps(text):
    """The frame rate text ffmpeg is given: NTSC decimals become exact fractions, anything else is kept."""
    value = common.parse_fraction(text)
    for decimal, exact in NTSC_RATES.items():
        if value == Fraction(decimal):
            return exact
    return str(text).strip()


def safe_output_path(out_path):
    """Absolute path to give ffmpeg. A relative name such as "tee:keep.mkv" would be read as a URL, not a
    file; an absolute path is always a file. A colon in the file name, or in the first part of a relative
    path ("tee:C:/folder/keep.mkv"), is refused: it is a protocol prefix, and on Windows it would also
    name an alternate data stream. A drive letter ("C:") is not a colon in this sense."""
    parts = os.path.splitdrive(out_path)[1].replace("\\", "/").split("/")
    if ":" in parts[-1] or (not os.path.isabs(out_path) and ":" in parts[0]):
        raise UsageError("the output name %r contains a colon; ffmpeg would read that as a protocol such as "
                         "tee: or pipe:. Choose a plain file name." % out_path)
    return os.path.abspath(out_path)


def duration_text(frames, fps):
    seconds = Fraction(frames) / fps
    return "%.6f" % float(seconds)


def build_command(ffmpeg, out_path, size, fps_text, frames, fps, tone, crf, overwrite):
    """The ffmpeg argument list: raw rgb frames on stdin, optional lavfi sine, H.264 + AAC out."""
    width, height = size
    seconds = duration_text(frames, fps)
    comment = "video-export-qa barcode clip v%d frames=%d fps=%s size=%dx%d" % (
        CLIP_FORMAT_VERSION, frames, fps_text, width, height,
    )
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y" if overwrite else "-n",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", "%dx%d" % (width, height),
        "-framerate", fps_text, "-i", "pipe:0",
    ]
    if tone:
        cmd += [
            "-f", "lavfi", "-i",
            "sine=frequency=%s:sample_rate=%d:duration=%s,volume=%s,%s"
            % (tone, TONE_SAMPLE_RATE, seconds, TONE_VOLUME, TONE_STEREO),
        ]
    cmd += ["-map", "0:v:0"]
    if tone:
        cmd += ["-map", "1:a:0"]
    cmd += [
        "-vf", COLOUR_FILTER,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-g", str(GOP_FRAMES), "-pix_fmt", "yuv420p",
    ]
    if tone:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", str(TONE_SAMPLE_RATE), "-ac", "2"]
    cmd += [
        "-t", seconds,
        "-metadata", "comment=" + comment,
        # bitexact keeps encoder version strings out of the file so the bytes are reproducible.
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    ]
    return cmd + common.LOCAL_FILES_ONLY + [out_path]


def generate_clip(ffmpeg, out_path, frame_source, frames, fps_text, size, tone=440, crf=14,
                  overwrite=False, commands=None):
    """Encode `frames` rgb24 frames from the iterable `frame_source` into out_path.

    The CLI feeds barcode.render_frame_rgb(i, ...) for i in range(frames); the tests feed
    custom or deliberately damaged frames through the same function. Raises QAError if
    ffmpeg fails or the source yields the wrong number of frames.
    """
    fps_text = canonical_fps(fps_text)
    fps = check_arguments(frames, fps_text, tone, crf)
    absolute = safe_output_path(out_path)
    common.refuse_overwrite(out_path, overwrite)  # same file as `absolute`; the message shows the name as typed
    if not os.path.isdir(os.path.dirname(absolute)):
        raise UsageError("the folder for %s does not exist" % out_path)
    cmd = build_command(ffmpeg, absolute, size, fps_text, frames, fps, tone, crf, overwrite)
    process = barcode.StreamingProcess(cmd, stdin=True, record=commands)
    written = 0
    try:
        for frame in frame_source:
            process.stdin.write(frame)
            written += 1
    except (BrokenPipeError, OSError):
        pass  # ffmpeg stopped reading; its exit code and message explain why
    except BaseException:
        process.abort()
        raise
    returncode, stderr = process.finish()
    if returncode != 0:
        tail = stderr.strip().splitlines()[-5:]
        raise QAError(
            "ffmpeg failed with exit code %d while writing %s (the file may be incomplete, do not use it): %s"
            % (returncode, out_path, " | ".join(tail) or "no message")
        )
    if written != frames:
        raise QAError("expected %d frames from the frame source but wrote %d" % (frames, written))
    return cmd


def index_frames(frames, size):
    """Generator of barcode frames for indices 0..frames-1."""
    width, height = size
    for index in range(frames):
        yield barcode.render_frame_rgb(index, width, height)


def clip_provenance(ffmpeg, out_path, commands):
    """The provenance object the checking scripts write, for --json. This script has no input
    file, so 'input' is null and the clip it wrote is described under 'output' (size, sha256)."""
    report = common.Report(SCRIPT, tools={"ffmpeg": ffmpeg})
    report.commands = commands
    provenance = report.provenance()
    provenance["output"] = {
        "path": out_path,
        "size_bytes": os.path.getsize(out_path),
        "sha256": common.sha256_file(out_path),
    }
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Write a test clip where every frame carries a barcode of its own frame index.",
    )
    parser.add_argument("out", metavar="OUT.mp4", help="clip to write")
    parser.add_argument("--frames", type=int, default=96, help="number of frames (default 96)")
    parser.add_argument("--fps", default="24",
                        help='frame rate: "24" or an exact fraction such as "24000/1001"; the NTSC decimals '
                             '23.976, 29.97 and 59.94 are mapped to 24000/1001, 30000/1001 and 60000/1001 (default 24)')
    parser.add_argument("--size", default="640x360", help="width x height (default 640x360)")
    parser.add_argument("--tone", type=int, default=440, help="sine tone in Hz at -20 dBFS peak; 0 = no audio")
    parser.add_argument("--crf", type=int, default=14, help="x264 quality, lower is better (default 14)")
    parser.add_argument("--overwrite", action="store_true", help="replace OUT if it already exists")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    size = parse_size(args.size)
    args.fps = canonical_fps(args.fps)
    check_arguments(args.frames, args.fps, args.tone, args.crf)
    safe_output_path(args.out)  # refuse a bad name before any frame is made
    ffmpeg = common.find_tool("ffmpeg", args.ffmpeg)

    commands = []
    generate_clip(
        ffmpeg, args.out, index_frames(args.frames, size), args.frames, args.fps, size,
        tone=args.tone, crf=args.crf, overwrite=args.overwrite, commands=commands,
    )

    summary = {
        "tool": SCRIPT,
        "output": args.out,
        "frames": args.frames,
        "fps": args.fps,
        "size": "%dx%d" % size,
        "tone_hz": args.tone,
        "crf": args.crf,
        "sha256": common.sha256_file(args.out),
        "commands": commands,
    }
    if args.json:
        summary["provenance"] = clip_provenance(ffmpeg, args.out, commands)
        print(json.dumps(summary, indent=2))
    else:
        print("wrote %s" % args.out)
        print("frames=%d fps=%s size=%s tone=%s crf=%d" % (
            args.frames, args.fps, summary["size"], "%d Hz" % args.tone if args.tone else "none", args.crf,
        ))
    return 0


if __name__ == "__main__":
    sys.exit(common.run_main(main))
