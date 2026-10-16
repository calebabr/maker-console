#!/usr/bin/env python3
"""Release notes from Conventional Commits (contract C5), used by .github/workflows/release.yml.

    changelog.py --to <ref> [--from <ref>] [--version vYYYY.MM.DD-n] [--max-chars N]

Writes Markdown to stdout: commits between --from (exclusive; default: the newest earlier v* tag, else the whole
history) and --to, grouped by type. Breaking changes (`!` or a BREAKING CHANGE: line) are listed first. Commits whose
subject is not conventional go under "Other". Merge commits are skipped. Reads git only; never touches the network.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

TYPES = [  # (type, heading) in display order
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("security", "Security"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("test", "Tests"),
    ("docs", "Documentation"),
    ("ci", "CI and release"),
    ("chore", "Chores"),
]
SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(\((?P<area>[a-z-]+)\))?(?P<bang>!)?: (?P<text>.+)$")
REF_RE = re.compile(r"^[A-Za-z0-9._/@^~-]{1,100}$")
SEP_REC, SEP_FLD = "\x1e", "\x1f"


def parse_commits(raw: str) -> list[dict]:
    """Parse `git log --format=%H<US>%s<US>%b<RS>` output into dicts."""
    out = []
    for rec in raw.split(SEP_REC):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split(SEP_FLD)
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), SEP_FLD.join(parts[2:])
        m = SUBJECT_RE.match(subject)
        breaking = bool(re.search(r"(?m)^BREAKING[ -]CHANGE:", body))
        if m and m["type"] in dict(TYPES):
            out.append({"sha": sha, "type": m["type"], "area": m["area"], "text": m["text"],
                        "breaking": breaking or bool(m["bang"])})
        else:
            out.append({"sha": sha, "type": "other", "area": None, "text": subject, "breaking": breaking})
    return out


def render(commits: list[dict], version: str | None = None, max_chars: int | None = None) -> str:
    lines: list[str] = []
    if version:
        lines += [f"# {version}", ""]

    def item(c: dict) -> str:
        area = f"**{c['area']}**: " if c["area"] else ""
        return f"- {area}{c['text']} ({c['sha'][:7]})"

    breaking = [c for c in commits if c["breaking"]]
    if breaking:
        lines += ["## Breaking changes", *map(item, breaking), ""]
    for typ, heading in [*TYPES, ("other", "Other")]:
        group = [c for c in commits if c["type"] == typ]
        if group:
            lines += [f"## {heading}", *map(item, group), ""]
    if not commits:
        lines += ["No changes since the previous release.", ""]
    text = "\n".join(lines).rstrip() + "\n"
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 60].rstrip() + "\n\n... (truncated; full list on GitHub)\n"
    return text


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def previous_tag(to_ref: str) -> str | None:
    try:
        return _git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{to_ref}^").strip() or None
    except subprocess.CalledProcessError:
        return None


def collect(to_ref: str, from_ref: str | None) -> list[dict]:
    spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    raw = _git("log", "--no-merges", f"--format=%H{SEP_FLD}%s{SEP_FLD}%b{SEP_REC}", spec)
    return parse_commits(raw)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", dest="to_ref", default="HEAD")
    ap.add_argument("--from", dest="from_ref")
    ap.add_argument("--version")
    ap.add_argument("--max-chars", type=int)
    a = ap.parse_args(argv)
    for r in (a.to_ref, a.from_ref):
        if r is not None and (not REF_RE.match(r) or r.startswith("-")):
            print(f"changelog: refusing odd ref {r!r}", file=sys.stderr)
            return 2
    from_ref = a.from_ref or previous_tag(a.to_ref)
    try:
        commits = collect(a.to_ref, from_ref)
    except subprocess.CalledProcessError as exc:
        print(f"changelog: git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 1
    sys.stdout.write(render(commits, a.version, a.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
