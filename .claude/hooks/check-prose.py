"""Register, constructions and recorded measurements, in prose only.

Comment lines in code and body text in markdown are checked. Code, strings and
fixture data never are: the judgement half of the register list is ordinary
English and would fire on an identifier or a test name.

Usage: check-prose.py <file> <register.txt> [<register.local.txt>]

Prints one line per problem and exits 0 either way. The caller decides.
"""

import re
import sys

# Scripts and config formats comment with `#`. Everything else here uses `//`
# or a `/* */` block.
HASH_COMMENTS = (".sh", ".bash", ".zsh", ".ps1", ".py", ".rb", ".yml", ".yaml", ".toml", ".tf")


# Data formats have no comment syntax, so every line in one is content.
DATA_FORMATS = (".json", ".lock", ".svg", ".csv", ".snap")


def prose_lines(path: str, text: str) -> list[tuple[int, str]]:
    """The lines a human wrote as prose, numbered from one."""
    if path.endswith(DATA_FORMATS):
        return []
    out: list[tuple[int, str]] = []
    fenced = False
    in_block = False
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if path.endswith(".md"):
            if stripped.startswith("```"):
                fenced = not fenced
                continue
            if not fenced and stripped and not line.startswith("    "):
                out.append((number, line))
            continue
        if path.endswith(HASH_COMMENTS):
            # A docstring is prose written for a person, so it counts as much as a `#`
            # comment does. Tracked by counting fences rather than parsing.
            fences = stripped.count('"""') + stripped.count("'''")
            if in_block or fences:
                out.append((number, line))
                if fences % 2:
                    in_block = not in_block
            elif stripped.startswith("#"):
                out.append((number, line))
            continue
        opens = "/*" in stripped
        if opens:
            in_block = True
        keep = in_block or stripped.startswith("//") or stripped.startswith("*")
        if "*/" in stripped:
            in_block = False
        if keep:
            out.append((number, line))
    return out


# A number somebody measured, as against a limit the outside world imposes. `~6 ms` and
# `measured at 40 MB` go; `510000`, `48 kHz` and `-100 dB` stay.
#
# Keyed on the number's immediate neighbour rather than on a measuring verb anywhere in
# the sentence. The wider form fires on documentation where a measured constraint on a
# named dependency is the whole point of the entry, and a hit here is meant to be a
# violation rather than a suggestion.
UNITS = r"ms|s\b|MB|MiB|GB|kB|kbps|Hz|dB|fps"
RECORDED = re.compile(
    rf"~\s*[\d.]+\s*({UNITS})"
    rf"|\bmeasured\s+(at|around|about)?\s*~?\s*[\d.]+\s*({UNITS})"
    rf"|\b(costs?|takes?|spends?)\s+(about|around|roughly|~)?\s*[\d.]+\s*({UNITS})"
    rf"|\b(roughly|about|around)\s+[\d.]+\s*({UNITS})",
    re.IGNORECASE,
)

# Structural tells. `voice/constructions.md` explains each one and says what to write
# instead. A name here can be switched off per repository with a `-name` line in the
# local register file.
BUILTINS: list[tuple[str, re.Pattern[str], str]] = [
    (
        # A dash alone in a table cell is a placeholder for "none", not a joint, so a
        # dash touching a cell boundary does not count.
        "emdash",
        re.compile(r"(?<!\|)(?<!\|\s)—(?!\s*\|)|(?<=\s)–(?=\s)"),
        "uses a dash as a joint. Comma for an aside, colon before a definition, "
        "full stop when it is its own thought.",
    ),
    (
        "curly",
        re.compile(r"[‘’“”]"),
        "uses curly quotes. Straight quotes only.",
    ),
    (
        "emoji",
        re.compile(r"[\U0001f300-\U0001faff✅❌⚠✨❗❤]"),
        "has an emoji in technical prose.",
    ),
    (
        "measurement",
        RECORDED,
        "reads as a recorded measurement. Keep the constraint, drop the number.",
    ),
]


def as_phrase(phrase: str) -> re.Pattern[str]:
    """A literal, anchored at both ends so it cannot match inside a longer word.

    Without the trailing anchor `in the quest` fires on `in the question`. Inflections
    are therefore not covered: a phrase that needs them belongs on a `~` line.
    """
    body = re.escape(phrase)
    lead = r"\b" if phrase[:1].isalnum() else ""
    tail = r"\b" if phrase[-1:].isalnum() else ""
    return re.compile(lead + body + tail, re.IGNORECASE)


def load_register(paths: list[str]) -> tuple[list[tuple[str, re.Pattern[str]]], list[re.Pattern[str]], set[str]]:
    """Named literal phrases, compiled patterns, and the names of disabled builtins."""
    literals: list[tuple[str, re.Pattern[str]]] = []
    patterns: list[re.Pattern[str]] = []
    disabled: set[str] = set()
    for path in paths:
        try:
            lines = open(path, encoding="utf-8").read().split("\n")
        except OSError:
            continue
        for line in lines:
            body = line.strip()
            if not body:
                continue
            if body.startswith("!"):
                phrase = body[1:].strip().lower()
                if phrase:
                    literals.append((phrase, as_phrase(phrase)))
            elif body.startswith("~"):
                try:
                    patterns.append(re.compile(body[1:].strip(), re.IGNORECASE))
                except re.error:
                    continue
            elif body.startswith("-"):
                disabled.add(body[1:].strip())
    return literals, patterns, disabled


def main() -> int:
    path, listings = sys.argv[1], sys.argv[2:]
    raw = open(path, "rb").read()
    # A NUL byte means this is not something a person wrote prose in.
    if b"\x00" in raw[:8192]:
        return 0
    text = raw.decode("utf-8", errors="replace")
    literals, patterns, disabled = load_register(listings)
    builtins = [check for check in BUILTINS if check[0] not in disabled]

    problems: list[str] = []
    for number, line in prose_lines(path, text):
        hit = next((phrase for phrase, pattern in literals if pattern.search(line)), None)
        if hit:
            problems.append(f'line {number}: "{hit}" reads as an assistant. See the register list.')
            continue
        found = next((pattern for pattern in patterns if pattern.search(line)), None)
        if found:
            problems.append(f"line {number}: reads as an assistant construction. See voice/constructions.md.")
            continue
        for name, pattern, message in builtins:
            if pattern.search(line):
                problems.append(f"line {number}: {message}")
                break

    for problem in problems[:8]:
        print(problem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
