#!/usr/bin/env python3
# get_skill_sim.py - simulate a tool that gives an agent a skill as TEXT (nothing is installed, so there is no skill
# folder), in the shapes such a tool has used. Test helper; it needs no network and no ffmpeg.
# Purpose: try references/no-skill-folder.md and its program without any real service.
# Inputs:  a package folder (it holds SKILL.md) and an output folder that does not exist yet or is empty:
#            python -I get_skill_sim.py <package_dir> <out_dir> [--inline-limit N] [--display NAME]
# Outputs: in <out_dir>:
#   first_answer.txt      the first answer of the current shape: a listing-like header, the SKILL.md text inline and an
#                         index of every other file as lines "- `<package>/<path>` (<size> KB)" (KB of 1024 bytes, one
#                         decimal), ending with a sentence that says how to load one file
#   results/<name>.txt    per file, the result of asking for that one file: "## File: <package>/<path> (from <Display>)",
#                         a blank line, then the exact bytes of the file
#   results/<name>.p1.txt the same result as a host saves a long one in plain text (P1: exactly the text)
#   results/<name>.p2.json the same result as a host saves a long one in JSON (P2: a list with one {"type": "text",
#                         "text": ...} object, indented by 2, every non-ASCII character escaped)
#   old_shape_answer.txt  the older shape: one answer that carries every file as a "### File: <package>/<path>" block in
#                         plain fences, the last block with no final line end
#   fake_get_skill.py     a stand-in for the tool, with the package inside it:
#                           python fake_get_skill.py                  prints the first answer
#                           python fake_get_skill.py --file <path>    prints the result for one file (<package>/<path>
#                                                                     or <path>)
#                         Like a host, it does not print a result longer than the inline limit (default 30,000
#                         characters): it saves it under <out_dir>/tool-results/ (P2 JSON when longer than 50,000
#                         characters, P1 plain text otherwise) and prints only a short "saved to <path>" message. This
#                         saves more results than a real host would, on purpose: a shell tool cuts long output.
# Exit codes: 0 written, 2 bad arguments, no SKILL.md, an output folder inside the package or one that is not empty.
# The wording around the files is neutral filler, except for the headings and line forms that the shapes are made of.
import json
import os
import sys

FENCE = "```"
INLINE_LIMIT = 30000
JSON_OVER = 50000
HEADER = ("# {name}\n\nSimulated answer of a tool that returns a skill as text. Test helper, not a real service.\n\n---\n\n"
          "## SKILL.md Content (latest version)\n\nThe SKILL.md text follows.\n\n")
MIDDLE = "\n\n---\n\n## Other files in this skill\n\nThe other files of the skill follow, one block per file.\n\n"
FIRST_HEADER = ("# {display}\n\nSimulated first answer of a tool that returns a skill as text. Test helper, not a real "
                "service.\n\n**Price:** Free\n\n## Description\nA listing text would stand here.\n\n## Permissions\n"
                "{{\n  \"tools\": [\"terminal\", \"file_read\", \"file_write\"]\n}}\n\n---\n\n"
                "## SKILL.md Content (latest version)\n\n---\n\n")
INDEX_HEADER = "\n\n---\n\n## Files in this skill\n\nThese files belong to the skill.\n\n"
INDEX_END = "\nLoad a file with `python fake_get_skill.py --file <path>` when SKILL.md tells you to."


def package_files(package_dir):
    """Relative posix paths of every file in the package except SKILL.md, in sorted order."""
    found = []
    for folder, names, files in os.walk(package_dir):
        names[:] = sorted(n for n in names if n != "__pycache__")
        for file in files:
            if file.endswith((".pyc", ".pyo")):
                continue
            rel = os.path.relpath(os.path.join(folder, file), package_dir).replace(os.sep, "/")
            if rel != "SKILL.md":
                found.append(rel)
    return sorted(found)


def read_bytes(package_dir, rel):
    with open(os.path.join(package_dir, *rel.split("/")), "rb") as handle:
        return handle.read()


def read_text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8")


def name_of(package_dir):
    return os.path.basename(os.path.abspath(package_dir).rstrip("\\/"))


def display_name(name):
    """video-export-qa -> Video Export QA (a display name in the form the shapes carry)."""
    return " ".join(word.upper() if len(word) <= 2 else word.capitalize() for word in name.split("-"))


# --------------------------------------------------------------------------- the older shape (every file in one answer)

def block(name, rel, text, fence=FENCE):
    return "### File: %s/%s\n\n%s\n%s\n%s" % (name, rel, fence, text, fence)


def old_answer(package_dir):
    """The older shape as one string: SKILL.md inline, then one block per other file; the last block has no line end."""
    package_dir = os.path.abspath(package_dir)
    name = name_of(package_dir)
    skill = read_text(os.path.join(package_dir, "SKILL.md"))
    blocks = [block(name, rel, read_bytes(package_dir, rel).decode("utf-8")) for rel in package_files(package_dir)]
    return HEADER.format(name=name) + skill.rstrip("\n") + MIDDLE + "\n\n".join(blocks)


build = old_answer          # the name the 1.2.1 tests used for the older shape


# --------------------------------------------------------------------------- the current shape (an index, then one file per call)

def size_kb(size):
    return "%.1f" % (size / 1024.0)


def first_answer(package_dir, display=None):
    package_dir = os.path.abspath(package_dir)
    name = name_of(package_dir)
    skill = read_text(os.path.join(package_dir, "SKILL.md"))
    index = "".join("- `%s/%s` (%s KB)\n" % (name, rel, size_kb(len(read_bytes(package_dir, rel))))
                    for rel in package_files(package_dir))
    return FIRST_HEADER.format(display=display or display_name(name)) + skill.rstrip("\n") + INDEX_HEADER + index + INDEX_END


def file_result(package_dir, rel, display=None):
    """The result for one file: the header line, a blank line, then the exact text of the file."""
    name = name_of(package_dir)
    return "## File: %s/%s (from %s)\n\n" % (name, rel, display or display_name(name)) + read_bytes(package_dir, rel).decode("utf-8")


def saved_p1(result):
    return result.encode("utf-8")


def saved_p2(result):
    return json.dumps([{"type": "text", "text": result}], indent=2).encode("ascii")


def result_file_name(rel):
    return rel.replace("/", "__")


FAKE_TOOL = '''#!/usr/bin/env python3
# fake_get_skill.py - a stand-in for a tool that gives a skill as text, made by get_skill_sim.py (test helper, not a real
# service). It holds the package inside itself.
#   python fake_get_skill.py                  the first answer: listing, SKILL.md, the index of files
#   python fake_get_skill.py --file <path>    the result for one file (<package>/<path> or <path>)
# A result longer than the inline limit is saved under tool-results/ next to this file (JSON when longer than %(json_over)d
# characters, plain text otherwise) and only a short message with its path is printed, as a host does.
import json
import os
import sys
import time

INLINE_LIMIT = %(limit)d
JSON_OVER = %(json_over)d
DATA = json.loads(%(data)s)


def emit(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv):
    if argv == []:
        result = DATA["first"]
    elif len(argv) == 2 and argv[0] == "--file":
        path = argv[1]
        if path.startswith(DATA["name"] + "/"):
            path = path[len(DATA["name"]) + 1:]
        if path not in DATA["files"]:
            emit("The file %%s is not in this skill. The file index lists the files.\\n" %% argv[1])
            return 1
        result = "## File: %%s/%%s (from %%s)\\n\\n" %% (DATA["name"], path, DATA["display"]) + DATA["files"][path]
    else:
        sys.stderr.write("usage: python fake_get_skill.py [--file <path>]\\n")
        return 2
    if len(result) <= INLINE_LIMIT:
        emit(result)
        return 0
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool-results")
    os.makedirs(folder, exist_ok=True)
    stamp = "get_skill-%%d" %% time.time_ns()
    if len(result) > JSON_OVER:
        target = os.path.join(folder, stamp + ".json")
        data = json.dumps([{"type": "text", "text": result}], indent=2).encode("ascii")
        with open(target, "xb") as handle:
            handle.write(data)
        emit("Output too large (%%.1fKB), full output saved to %%s\\n" %% (len(data) / 1024.0, target))
    else:
        target = os.path.join(folder, stamp + ".txt")
        with open(target, "xb") as handle:
            handle.write(result.encode("utf-8"))
        emit("result (%%s characters across %%s lines) exceeds maximum allowed tokens, output saved to %%s\\nFormat: Plain text\\n"
             %% ("{:,}".format(len(result)), "{:,}".format(len(result.splitlines())), target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def fake_tool(package_dir, display=None, limit=INLINE_LIMIT, json_over=JSON_OVER):
    package_dir = os.path.abspath(package_dir)
    name = name_of(package_dir)
    data = {"name": name, "display": display or display_name(name), "first": first_answer(package_dir, display),
            "files": {rel: read_bytes(package_dir, rel).decode("utf-8") for rel in package_files(package_dir)}}
    return FAKE_TOOL % {"limit": limit, "json_over": json_over, "data": repr(json.dumps(data))}


def write_simulation(package_dir, out_dir, limit=INLINE_LIMIT, display=None):
    """Write every shape for a package into out_dir (see the header). Returns the number of files of the package."""
    rels = package_files(package_dir)
    results = os.path.join(out_dir, "results")
    os.makedirs(results, exist_ok=True)
    outputs = {"first_answer.txt": first_answer(package_dir, display).encode("utf-8"),
               "old_shape_answer.txt": old_answer(package_dir).encode("utf-8"),
               "fake_get_skill.py": fake_tool(package_dir, display, limit).encode("utf-8")}
    for rel in rels:
        result = file_result(package_dir, rel, display)
        base = os.path.join("results", result_file_name(rel))
        outputs[base + ".txt"] = result.encode("utf-8")
        outputs[base + ".p1.txt"] = saved_p1(result)
        outputs[base + ".p2.json"] = saved_p2(result)
    for rel_out, data in sorted(outputs.items()):
        with open(os.path.join(out_dir, rel_out), "xb") as handle:
            handle.write(data)
    return len(rels)


def main(argv):
    args, limit, display = [], INLINE_LIMIT, None
    it = iter(argv)
    for arg in it:
        if arg == "--inline-limit":
            try:
                limit = int(next(it))
            except (StopIteration, ValueError):
                args = []
                break
        elif arg == "--display":
            display = next(it, None)
        else:
            args.append(arg)
    if len(args) != 2 or (display is not None and not display.strip()):
        sys.stderr.write("usage: python -I get_skill_sim.py <package_dir> <out_dir> [--inline-limit N] [--display NAME]\n")
        return 2
    package_dir, out_dir = args
    if not os.path.isfile(os.path.join(package_dir, "SKILL.md")):
        sys.stderr.write("error: %s has no SKILL.md\n" % package_dir)
        return 2
    try:
        inside = os.path.commonpath([os.path.abspath(package_dir), os.path.abspath(out_dir)]) == os.path.abspath(package_dir)
    except ValueError:  # another drive on Windows
        inside = False
    if inside:
        sys.stderr.write("error: the output folder must not be inside the package folder\n")
        return 2
    if os.path.exists(out_dir) and (not os.path.isdir(out_dir) or os.listdir(out_dir)):
        sys.stderr.write("error: the output folder exists and is not empty\n")
        return 2
    os.makedirs(out_dir, exist_ok=True)
    count = write_simulation(package_dir, out_dir, limit, display)
    print("wrote %s: the first answer, %d file results in three forms, the older shape and fake_get_skill.py"
          % (out_dir, count))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
