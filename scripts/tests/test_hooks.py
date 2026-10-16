"""Tests for the denylist gate (scripts/denylist_scan.py, scripts/scan_tree.sh, .githooks/*).

Each test builds a throwaway git repo in tmp_path with copies of the hooks and a FAKE denylist (made-up tokens,
no real strategy value anywhere). Run: python -m pytest -q scripts/tests
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="needs bash and git")

NL = chr(10)
SECRET = "zq7-plum-ferret"          # fake token the fake denylist forbids
ROW = "[0.11, 0.22, 3, 4, 'x']"     # fake "cell row"
DENY = [r"zq7-plum-ferret", r"\b0\.11\D{1,8}0\.22\D{1,16}3\D{1,8}4\b", r"(?i)forbidden[_ ]name"]


def sh(cmd: list[str], cwd: Path, env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    e = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    return subprocess.run(cmd, cwd=cwd, env=e, input=stdin, capture_output=True, text=True, timeout=60)


def git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return sh(["git", *args], repo, env)


@pytest.fixture
def deny(tmp_path) -> Path:
    f = tmp_path / "deny.txt"
    f.write_text("# fake denylist for tests\n" + "\n".join(DENY) + "\n", encoding="utf-8")
    return f


@pytest.fixture
def repo(tmp_path, deny):
    r = tmp_path / "work"
    (r / "scripts").mkdir(parents=True)
    shutil.copytree(REPO / ".githooks", r / ".githooks")
    shutil.copy(REPO / "scripts" / "denylist_scan.py", r / "scripts")
    shutil.copy(REPO / "scripts" / "scan_tree.sh", r / "scripts")
    shutil.copy(REPO / "scripts" / "overlay_manifest.txt", r / "scripts")
    for p in list((r / ".githooks").iterdir()) + [r / "scripts" / "scan_tree.sh"]:
        p.chmod(0o755)
    git(r, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("core.hooksPath", ".githooks"),
                 ("commit.gpgsign", "false")):
        git(r, "config", k, v)
    (r / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (r / "ok.txt").write_text("nothing to see\n", encoding="utf-8")
    return r


def env(repo: Path) -> dict:
    return {"MAKER_DENYLIST": str(repo.parent / "deny.txt"), "PATH": os.environ["PATH"], "SKIP_GITLEAKS_LOCAL": "1"}


def commit(repo: Path, msg: str = "work") -> subprocess.CompletedProcess:
    git(repo, "add", "-A")
    return git(repo, "commit", "-q", "-m", msg, env=env(repo))


def test_commit_clean_passes(repo):
    r = commit(repo, "add files")
    assert r.returncode == 0, r.stderr


def test_commit_blocked_reports_file_and_line_not_the_secret(repo):
    (repo / "a.py").write_text(f"x = 1\ny = '{SECRET}'\n", encoding="utf-8")
    r = commit(repo)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "a.py:2:" in out and "pattern #" in out
    assert SECRET not in out
    assert git(repo, "log", "--oneline").returncode != 0 or git(repo, "log", "--oneline").stdout.strip() == ""


def test_commit_blocked_for_a_cell_row_in_other_formatting(repo):
    (repo / "t.js").write_text(f"const c = {ROW}\n", encoding="utf-8")
    assert commit(repo).returncode != 0
    (repo / "t.js").write_text("const c = (0.11,0.22,3,4)\n", encoding="utf-8")
    r = commit(repo)
    assert r.returncode != 0 and "t.js:1:" in r.stdout + r.stderr


def test_commit_blocked_for_a_file_name(repo):
    (repo / "forbidden_name.csv").write_text("a,b\n", encoding="utf-8")
    r = commit(repo)
    assert r.returncode != 0 and "file name matches" in r.stdout + r.stderr


def test_only_added_lines_count(repo):
    (repo / "b.txt").write_text("fine\n", encoding="utf-8")
    assert commit(repo).returncode == 0
    (repo / "b.txt").write_text("fine\nmore fine\n", encoding="utf-8")
    assert commit(repo, "second").returncode == 0


def test_fail_closed_without_a_denylist(repo, tmp_path):
    e = {"MAKER_DENYLIST": str(tmp_path / "does-not-exist.txt"), "PATH": os.environ["PATH"]}
    git(repo, "add", "-A")
    r = git(repo, "commit", "-q", "-m", "x", env=e)
    assert r.returncode != 0
    assert "make_denylist.py" in r.stdout + r.stderr and "MAKER_DENYLIST" in r.stdout + r.stderr


def test_fail_closed_default_path_missing(repo):
    e = {"PATH": os.environ["PATH"], "MAKER_DENYLIST": ""}      # empty = use ../tools/.denylist.txt, absent here
    git(repo, "add", "-A")
    r = git(repo, "commit", "-q", "-m", "x", env=e)
    assert r.returncode != 0 and "no usable denylist" in r.stdout + r.stderr


def test_fail_closed_on_empty_or_bad_denylist(repo, tmp_path):
    git(repo, "add", "-A")
    empty = tmp_path / "empty.txt"
    empty.write_text("# only comments\n\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("(unclosed\n", encoding="utf-8")
    for f in (empty, bad):
        r = git(repo, "commit", "-q", "-m", "x", env={"MAKER_DENYLIST": str(f), "PATH": os.environ["PATH"]})
        assert r.returncode != 0, f


@pytest.fixture
def remote(tmp_path, repo):
    bare = tmp_path / "remote.git"
    sh(["git", "init", "-q", "--bare", "-b", "main", str(bare)], tmp_path)
    git(repo, "remote", "add", "origin", str(bare))
    return bare


def test_push_clean_passes(repo, remote):
    assert commit(repo, "clean").returncode == 0
    r = git(repo, "push", "-q", "origin", "main", env=env(repo))
    assert r.returncode == 0, r.stderr


def test_push_blocked_for_a_bad_commit_hidden_in_history(repo, remote):
    """Commit-time hook bypassed (--no-verify): the pre-push scan still catches it, and names the commit."""
    assert commit(repo, "clean").returncode == 0
    (repo / "h.py").write_text(f"s = '{SECRET}'\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert git(repo, "commit", "-q", "--no-verify", "-m", "sneaky").returncode == 0
    (repo / "later.txt").write_text("fine\n", encoding="utf-8")
    assert commit(repo, "later").returncode == 0
    r = git(repo, "push", "origin", "main", env=env(repo))
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "h.py:1:" in out and "commit " in out
    assert SECRET not in out
    assert sh(["git", "--git-dir", str(remote), "rev-parse", "--verify", "-q", "main"], repo).returncode != 0


def test_push_blocked_for_a_commit_message(repo, remote):
    (repo / "m.txt").write_text("fine\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert git(repo, "commit", "-q", "-m", f"note about {SECRET}", env=env(repo)).returncode == 0
    r = git(repo, "push", "origin", "main", env=env(repo))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "(commit message)" in out and SECRET not in out


def test_push_fail_closed_without_denylist(repo, remote, tmp_path):
    assert commit(repo, "clean").returncode == 0
    e = {"MAKER_DENYLIST": str(tmp_path / "nope.txt"), "PATH": os.environ["PATH"], "SKIP_GITLEAKS_LOCAL": "1"}
    r = git(repo, "push", "origin", "main", env=e)
    assert r.returncode != 0


def scan_tree(repo: Path, e: dict) -> subprocess.CompletedProcess:
    return sh([BASH, "scripts/scan_tree.sh"], repo, e)


def test_scan_tree_clean_then_flags_untracked_and_skips_ignored(repo):
    assert scan_tree(repo, env(repo)).returncode == 0
    (repo / "x.pyc").write_text(SECRET, encoding="utf-8")                    # ignored by .gitignore: not published
    assert scan_tree(repo, env(repo)).returncode == 0
    (repo / "sub").mkdir()
    (repo / "sub" / "n.md").write_text(f"a\nb\n{ROW}\n", encoding="utf-8")   # untracked, would be published
    r = scan_tree(repo, env(repo))
    assert r.returncode == 1 and "sub/n.md:3:" in r.stdout and SECRET not in r.stdout + r.stderr


def test_scan_tree_non_git_directory_walk(tmp_path, deny):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "f.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    r = sh([sys.executable, str(REPO / "scripts" / "denylist_scan.py"), "tree", str(d)], tmp_path,
           {"MAKER_DENYLIST": str(deny)})
    assert r.returncode == 1 and "f.txt:1:" in r.stdout


def test_hook_scripts_contain_no_strategy_values():
    """The scripts stay generic: no price-like literals at all (the private denylist file holds every value)."""
    import re
    price_like = re.compile(r"(?<![\w.])0?\.[89]\d")
    for p in [*(REPO / ".githooks").iterdir(), REPO / "scripts" / "denylist_scan.py", REPO / "scripts" / "scan_tree.sh"]:
        assert not price_like.search(p.read_text(encoding="utf-8")), p.name


# ---- S-42: never commit overlaid private files ----
def stage(repo: Path, rel: str, text: str) -> subprocess.CompletedProcess:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return commit(repo, "x")


@pytest.mark.parametrize("rel,text", [
    ("kalshi_bot/_overlay.py", "IS_STANDIN = False\n"),
    ("kalshi_bot/markets.py", "x = 1\n"),                           # private-only module named in the manifest
    ("control_api/data/cell_expectations.json", "{}\n"),
    ("kalshi_bot/maker_strategy.py", "IS_STANDIN = False\n"),
    ("kalshi_bot/other.py", "IS_STANDIN: bool = False\n"),
    ("kalshi_bot/maker_strategy.py", "x = 1\n"),                    # not the stand-in (no IS_STANDIN = True)
    ("kalshi_bot/tests/test_private.py", "x = 1\n"),                # private tests directory entry
])
def test_guard_refuses_overlaid_private_files(repo, rel, text):
    r = stage(repo, rel, text)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "refused" in out, out


def test_guard_allows_the_standin_files(repo):
    assert stage(repo, "kalshi_bot/maker_strategy.py", "IS_STANDIN = True\n").returncode == 0
    assert stage(repo, "kalshi_bot/tests/test_client.py", "x = 1\n").returncode == 0
    assert stage(repo, "scripts/doc.py", 'MARK = "IS_STANDIN = False"\n').returncode == 0   # only kalshi_bot/*.py count


def test_guard_accepts_the_real_standin_files(repo):
    """S-52: the docstrings in the real stand-in mention 'IS_STANDIN = False'; they must not trip the guard."""
    for rel in ("kalshi_bot/__init__.py", "kalshi_bot/maker_strategy.py"):
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dst)
    for p in sorted((REPO / "kalshi_bot").glob("*.py")) + [REPO / "kalshi_bot" / "maker_config.json"]:
        shutil.copy(p, repo / "kalshi_bot" / p.name)
    r = commit(repo, "standin")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("rel,text", [
    ("kalshi_bot/maker_strategy.py", '"""doc mentions IS_STANDIN = False."""' + NL + "IS_STANDIN = True" + NL + "IS_STANDIN = False" + NL),
    ("kalshi_bot/other.py", "x = 1" + NL + "IS_STANDIN = False  # real" + NL),
    ("kalshi_bot/other.py", "if x:" + NL + "    IS_STANDIN: bool = False" + NL),
    ("kalshi_bot/other.py", "def broken(:" + NL + "IS_STANDIN = False" + NL),     # unparsable: anchored regex fallback
    ("kalshi_bot/_overlay.py", '"""x"""' + NL + "IS_STANDIN=False" + NL),
])
def test_guard_still_refuses_a_real_false_assignment(repo, rel, text):
    r = stage(repo, rel, text)
    assert r.returncode != 0 and "refused" in r.stdout + r.stderr, r.stdout + r.stderr


def test_guard_ignores_comment_and_string_mentions(repo):
    text = "# IS_STANDIN = False is written by the overlay" + NL + 'NOTE = "IS_STANDIN = False"' + NL + "IS_STANDIN = True" + NL
    assert stage(repo, "kalshi_bot/maker_strategy.py", text).returncode == 0


def test_guard_standin_true_only_in_docstring_is_not_the_standin(repo):
    r = stage(repo, "kalshi_bot/maker_strategy.py", '"""' + NL + "IS_STANDIN = True" + NL + '"""' + NL + "x = 1" + NL)
    assert r.returncode != 0 and "refused" in r.stdout + r.stderr


def test_install_hooks_sets_core_hooks_path(repo):
    shutil.copy(REPO / "scripts" / "install_hooks.sh", repo / "scripts")
    git(repo, "config", "--unset", "core.hooksPath")
    assert git(repo, "config", "--get", "core.hooksPath").stdout.strip() == ""
    r = sh([BASH, "scripts/install_hooks.sh"], repo)
    assert r.returncode == 0, r.stderr
    assert git(repo, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"


# ---- S-43: gitleaks in the hooks ----
def fake_gitleaks(tmp_path: Path, code: int) -> str:
    d = tmp_path / f"bin{code}"
    d.mkdir()
    g = d / "gitleaks"
    g.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    g.chmod(0o755)
    return str(d) + os.pathsep + os.environ["PATH"]


def push_env(repo: Path, **kw) -> dict:
    e = env(repo)
    e.pop("SKIP_GITLEAKS_LOCAL", None)
    e.update(kw)
    return e


@pytest.mark.skipif(shutil.which("gitleaks") is not None, reason="gitleaks is installed here")
def test_push_fails_closed_without_gitleaks_and_explains(repo, remote):
    assert commit(repo, "clean").returncode == 0
    r = git(repo, "push", "origin", "main", env=push_env(repo))
    assert r.returncode != 0 and "gitleaks is not installed" in r.stderr and "SKIP_GITLEAKS_LOCAL=1" in r.stderr
    r = git(repo, "push", "origin", "main", env=push_env(repo, SKIP_GITLEAKS_LOCAL="1"))
    assert r.returncode == 0, r.stderr


def test_push_runs_gitleaks_and_obeys_its_verdict(repo, remote, tmp_path):
    assert commit(repo, "clean").returncode == 0
    r = git(repo, "push", "origin", "main", env=push_env(repo, PATH=fake_gitleaks(tmp_path, 1)))
    assert r.returncode != 0 and "gitleaks found a secret" in r.stderr
    r = git(repo, "push", "origin", "main", env=push_env(repo, PATH=fake_gitleaks(tmp_path, 0)))
    assert r.returncode == 0, r.stderr


def test_commit_runs_gitleaks_when_installed_and_skips_when_not(repo, tmp_path):
    (repo / "g.txt").write_text("fine\n", encoding="utf-8")
    git(repo, "add", "-A")
    r = git(repo, "commit", "-q", "-m", "x", env=push_env(repo, PATH=fake_gitleaks(tmp_path, 1)))
    assert r.returncode != 0
    if shutil.which("gitleaks") is None:
        r = git(repo, "commit", "-q", "-m", "x", env=push_env(repo))
        assert r.returncode == 0 and "gitleaks not installed" in r.stderr
