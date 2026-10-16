"""Commit-message check: the regex cases, the real commit-msg hook, and range mode used by CI."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import commit_msg_check as cm  # noqa: E402

GOOD = [
    "feat(app): add sticky page title",
    "fix(api): handle empty body",
    "security(auth)!: rotate the signing key",
    "docs: tidy the readme",
    "chore(deploy): pin the actions",
    "ci(deploy): add the release workflow",
    "feat(db): " + "x" * 72,                      # exactly 72 characters of summary
    "Merge pull request #3 from calebabr/feature-x",
    "feat(app): add thing\n\nWhy it changed.\nTests: green",
]
BAD = [
    "Update README",
    "added stuff",
    "feat(ui): wrong area",
    "feature(app): wrong type",
    "feat(app):no space",
    "feat(app): ",
    "feat(app): " + "x" * 73,                      # 73 characters
    "FEAT(app): upper case",
    "",
    "   \n",
    "# only a comment",
]


@pytest.mark.parametrize("msg", GOOD)
def test_good_messages(msg):
    assert cm.check_message(msg) == []


@pytest.mark.parametrize("msg", BAD)
def test_bad_messages(msg):
    assert cm.check_message(msg)


def test_comment_lines_are_ignored_like_git_does():
    assert cm.check_message("# comment first\nfix(api): real subject") == []


@pytest.mark.parametrize("trailer", [
    "Co-Authored-By: Claude Sonnet <noreply@anthropic.com>",
    "co-authored-by: Claude <x@y.z>",
    "Generated with Claude Code",
])
def test_agent_attribution_is_refused(trailer):
    assert cm.check_message(f"fix(api): ok\n\nbody\n\n{trailer}")


def test_human_co_author_is_allowed():
    assert cm.check_message("fix(api): ok\n\nCo-Authored-By: Pat Smith <pat@example.invalid>") == []


# ---- the real hook and range mode, in a throwaway repo -------------------------------------------------------------
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="needs bash and git")


def sh(cmd, cwd, env=None):
    e = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "w"
    (r / "scripts").mkdir(parents=True)
    (r / ".githooks").mkdir()
    shutil.copy(REPO / "scripts" / "commit_msg_check.py", r / "scripts")
    shutil.copy(REPO / ".githooks" / "commit-msg", r / ".githooks")
    (r / ".githooks" / "commit-msg").chmod(0o755)
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"],
              ["config", "commit.gpgsign", "false"], ["config", "core.hooksPath", ".githooks"]):
        sh(["git", *c], r)
    return r


def commit(repo, msg, name=None):
    (repo / "f.txt").write_text(msg + "x", encoding="utf-8")
    sh(["git", "add", "-A"], repo)
    cmd = ["git"] + (["-c", f"user.name={name}"] if name else []) + ["commit", "-q", "-m", msg]
    return sh(cmd, repo)


@needs_bash
def test_hook_accepts_and_rejects(repo):
    ok = commit(repo, "feat(app): good one")
    assert ok.returncode == 0, ok.stderr
    bad = commit(repo, "Update stuff")
    assert bad.returncode != 0 and "commit-msg: rejected" in bad.stderr
    log = sh(["git", "log", "--format=%s"], repo).stdout.split("\n")
    assert "Update stuff" not in log


@needs_bash
def test_range_mode_flags_bad_commits_and_skips_dependabot(repo):
    sh(["git", "config", "core.hooksPath", "/nonexistent"], repo)       # let bad commits in, as a rebase/web edit could
    commit(repo, "feat(app): base")
    base = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    commit(repo, "fix(api): fine")
    commit(repo, "sloppy message")
    commit(repo, "Bump something from 1 to 2 in /control_api " + "x" * 40, name="dependabot[bot]")
    head = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    r = sh([sys.executable, "scripts/commit_msg_check.py", "range", f"{base}..{head}"], repo)
    assert r.returncode == 1
    assert "sloppy message" in r.stderr
    assert r.stdout.count("1 rejected") == 1 and "3 commit(s)" in r.stdout
    one = sh([sys.executable, "scripts/commit_msg_check.py", "commit", base], repo)
    assert one.returncode == 0


def test_range_argument_is_validated():
    assert cm.main(["range", "--output=x"]) == 1
    assert cm.main(["bogus", "x"]) == 2
