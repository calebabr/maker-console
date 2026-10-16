#!/usr/bin/env python3
r"""Conventional-commit subject check, shared by .githooks/commit-msg and CI (contract C7).

    commit_msg_check.py file <path-to-message-file>     # the commit-msg hook
    commit_msg_check.py range <base>..<head>            # CI: every non-merge commit in the range
    commit_msg_check.py commit <sha>                    # CI: a single commit (new branch, no usable base)

Subject regex (docs/RELEASE_CONTRACT.md section 4):
    ^(feat|fix|perf|refactor|security|test|docs|ci|chore)(\((app|api|bot|db|auth|deploy|tasks|research)\))?!?: .{1,72}$
Also refused: agent attribution trailers (project rule: commits carry the owner's name only).
Git-generated "Merge ..." subjects are accepted. Commits authored by dependabot[bot] are skipped in range mode
(their titles are generated and often longer than 72 characters; the PR title is what the owner squash-merges).
Exit 0 = ok, 1 = rejected, 2 = usage error.
"""
from __future__ import annotations

import re
import subprocess
import sys

SUBJECT_RE = re.compile(
    r"^(feat|fix|perf|refactor|security|test|docs|ci|chore)"
    r"(\((app|api|bot|db|auth|deploy|tasks|research)\))?!?: .{1,72}$"
)
# Attribution trailers an agent might add; the repo's commits are the owner's only.
FORBIDDEN_BODY = [
    re.compile(r"(?im)^co-authored-by:.*(claude|anthropic|noreply@anthropic\.com)"),
    re.compile(r"(?im)generated with .*claude"),
]
SKIP_AUTHORS = {"dependabot[bot]"}
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
RANGE_RE = re.compile(r"^[0-9a-fA-F]{7,40}\.\.[0-9a-fA-F]{7,40}$")


def check_message(message: str) -> list[str]:
    """Return a list of problems (empty = ok). `message` is the full commit message."""
    lines = [ln for ln in message.splitlines() if not ln.startswith("#")]   # git strips comment lines
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty commit message"]
    subject = lines[0].rstrip("\r")
    problems: list[str] = []
    if not (SUBJECT_RE.match(subject) or subject.startswith("Merge ")):
        problems.append(
            f"subject does not match '<type>(<area>): <summary up to 72 chars>': {subject[:100]!r}"
        )
    body = "\n".join(lines[1:])
    for pat in FORBIDDEN_BODY:
        if pat.search(body):
            problems.append("agent attribution trailer found; commits carry the owner's name only")
            break
    return problems


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _commits(spec: str) -> list[str]:
    if SHA_RE.match(spec):
        return [spec]
    if not RANGE_RE.match(spec):
        raise ValueError(f"not a sha or <sha>..<sha> range: {spec!r}")
    return _git("rev-list", "--no-merges", spec).split()


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("file", "range", "commit"):
        print(__doc__, file=sys.stderr)
        return 2
    mode, arg = argv
    if mode == "file":
        try:
            with open(arg, encoding="utf-8", errors="replace") as fh:
                problems = check_message(fh.read())
        except OSError as exc:
            print(f"commit-msg: cannot read the message file: {exc}", file=sys.stderr)
            return 1
        if problems:
            print("commit-msg: rejected.", *[f"  - {p}" for p in problems], sep="\n", file=sys.stderr)
            print("  Format: <type>(<area>): <imperative summary, max 72 chars>   (see CONTRIBUTING.md)", file=sys.stderr)
            return 1
        return 0
    try:
        shas = _commits(arg)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"commit-msg check: {exc}", file=sys.stderr)
        return 1
    bad = 0
    for sha in shas:
        author, msg = _git("log", "-1", "--format=%an%x00%B", sha).split("\x00", 1)
        if author.strip() in SKIP_AUTHORS:
            continue
        problems = check_message(msg)
        if problems:
            bad += 1
            print(f"{sha[:10]}: " + "; ".join(problems), file=sys.stderr)
    print(f"commit message check: {len(shas)} commit(s), {bad} rejected")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
