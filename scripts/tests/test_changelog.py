"""changelog.py grouping and the git plumbing (throwaway repo)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import changelog as cl  # noqa: E402

SCRIPT = Path(cl.__file__)
RS, US = cl.SEP_REC, cl.SEP_FLD


def rec(sha, subject, body=""):
    return f"{sha}{US}{subject}{US}{body}{RS}"


def test_groups_by_type_in_fixed_order_and_skips_empty_groups():
    raw = "".join([
        rec("a" * 40, "chore(deploy): bump pins"),
        rec("b" * 40, "feat(app): add release card"),
        rec("c" * 40, "fix(api): handle empty body"),
        rec("d" * 40, "fix: no area given"),
    ])
    out = cl.render(cl.parse_commits(raw), "v2026.10.16-1")
    assert out.startswith("# v2026.10.16-1")
    assert out.index("## Features") < out.index("## Fixes") < out.index("## Chores")
    assert "## Security" not in out and "## Other" not in out
    assert "- **app**: add release card (bbbbbbb)" in out
    assert "- no area given (ddddddd)" in out


def test_breaking_changes_listed_first_via_bang_or_body():
    raw = rec("a" * 40, "feat(api)!: drop old field") + rec("b" * 40, "fix(db): rename", "why\n\nBREAKING CHANGE: x")
    out = cl.render(cl.parse_commits(raw))
    assert out.startswith("## Breaking changes")
    assert out.count("drop old field") == 2 and out.count("rename") == 2     # in Breaking and in its own group


def test_non_conventional_subject_goes_to_other_and_unknown_type_too():
    raw = rec("a" * 40, "Update README") + rec("b" * 40, "wip(app): thing")
    out = cl.render(cl.parse_commits(raw))
    assert "## Other" in out and "Update README" in out and "wip(app): thing" in out


def test_empty_range_and_truncation():
    assert "No changes" in cl.render([])
    big = cl.parse_commits("".join(rec(f"{i:040x}", f"fix(api): {'x' * 50} {i}") for i in range(200)))
    out = cl.render(big, max_chars=500)
    assert len(out) <= 500 and "truncated" in out


@pytest.fixture
def repo(tmp_path):
    def git(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    git("init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false"),
                 ("core.hooksPath", "")):
        git("config", k, v)

    def commit(msg):
        (tmp_path / "f.txt").write_text(msg, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", msg)
    commit("feat(app): first")
    git("tag", "v2026.10.01-1")
    commit("fix(api): second")
    commit("docs(tasks): third")
    return tmp_path, git


def run(cwd, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def test_cli_uses_previous_tag_by_default(repo):
    path, _ = repo
    r = run(path, "--to", "HEAD", "--version", "v2026.10.16-1")
    assert r.returncode == 0, r.stderr
    assert "second" in r.stdout and "third" in r.stdout and "first" not in r.stdout


def test_cli_without_any_tag_lists_everything_and_explicit_from_wins(repo):
    path, git = repo
    git("tag", "-d", "v2026.10.01-1")
    assert "first" in run(path, "--to", "HEAD").stdout
    r = run(path, "--to", "HEAD", "--from", "HEAD~2")
    assert "second" in r.stdout and "third" in r.stdout and "first" not in r.stdout


def test_cli_refuses_option_like_refs(repo):
    path, _ = repo
    r = run(path, "--to", "--output=/tmp/x")
    assert r.returncode == 2
