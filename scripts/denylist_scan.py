"""Scan staged changes, pushed commits or a whole tree against a private denylist; never print what matched.

    denylist_scan.py staged                 added lines of the staged diff + staged file names (pre-commit)
    denylist_scan.py push                   stdin = git pre-push lines; scans every new commit (pre-push)
    denylist_scan.py guard                  refuse staged private-overlay files (no denylist needed)
    denylist_scan.py tree [DIR]             every file the repo would publish (tracked or untracked, not ignored)

The denylist is a text file, one Python regex per line ('#' comments, blank lines ignored). It is NOT in this repo:
the path comes from env MAKER_DENYLIST, else <repo>/../tools/.denylist.txt. No usable denylist = FAIL CLOSED.
Output names the file and line (and commit) only, plus the pattern NUMBER; the matched text is never printed.
Exit: 0 clean, 1 match found, 3 no usable denylist / cannot scan (fail closed).
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
HELP = """\
denylist_scan: no usable denylist, refusing (fail closed).
  Generate it in the PRIVATE repo:   python tools/make_denylist.py     (writes tools/.denylist.txt)
  then either keep the private repo next to this one (default ../tools/.denylist.txt) or set
  MAKER_DENYLIST=/path/to/.denylist.txt"""


def die(msg: str, code: int = 3) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if check and p.returncode != 0:
        die(f"denylist_scan: git {args[0]} failed (cannot scan; refusing)")
    return p.stdout.decode("utf-8", errors="replace")


def toplevel(allow_none: bool = False) -> Path | None:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
    if out.returncode != 0:
        if allow_none:
            return None
        die("denylist_scan: not inside a git repository")
    return Path(out.stdout.decode().strip())


def load_denylist(top: Path) -> list[re.Pattern]:
    path = Path(os.environ.get("MAKER_DENYLIST") or top.parent / "tools" / ".denylist.txt")
    if not path.is_file():
        die(HELP)
    pats: list[re.Pattern] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pats.append(re.compile(line))
        except re.error:
            die(f"denylist_scan: denylist line {n} is not a valid regex (refusing)")
    if not pats:
        die(HELP)
    return pats


class Scanner:
    def __init__(self, pats: list[re.Pattern]):
        self.pats = pats
        self.hits = 0

    def match(self, text: str) -> int | None:
        for i, p in enumerate(self.pats, 1):
            if p.search(text):
                return i
        return None

    def name(self, where: str, path: str) -> None:
        i = self.match(path)
        if i:
            self.hits += 1
            print(f"{where}{path}: file name matches denylist pattern #{i}")

    def line(self, where: str, path: str, no: int, text: str) -> None:
        i = self.match(text)
        if i:
            self.hits += 1
            print(f"{where}{path}:{no}: matches denylist pattern #{i}")

    def diff(self, where: str, patch: str) -> None:
        """Parse a zero-context unified diff; check added lines and new file names."""
        path, no = "", 0
        for raw in patch.splitlines():
            if raw.startswith("+++ "):
                path = raw[4:].strip()
                path = path[2:] if path.startswith("b/") else path
                if path != "/dev/null":
                    self.name(where, path)
                else:
                    path = ""
            elif raw.startswith("@@"):
                m = re.search(r"\+(\d+)", raw)
                no = int(m.group(1)) if m else 0
            elif raw.startswith("+") and not raw.startswith("+++") and path:
                self.line(where, path, no, raw[1:])
                no += 1
            elif raw.startswith(" "):
                no += 1


def run_staged(sc: Scanner) -> None:
    patch = git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR", "--src-prefix=a/",
                "--dst-prefix=b/")
    sc.diff("", patch)


def run_push(sc: Scanner) -> None:
    seen: set[str] = set()
    for raw in sys.stdin.read().splitlines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        _lref, lsha, _rref, rsha = parts[:4]
        if lsha == ZERO:
            continue                                         # deleting a ref
        if rsha != ZERO:
            revs = git("rev-list", f"{rsha}..{lsha}").split()
        else:
            revs = git("rev-list", lsha, "--not", "--remotes").split()
        for sha in revs:
            if sha in seen:
                continue
            seen.add(sha)
            where = f"commit {sha[:7]} "
            msg = git("log", "-1", "--format=%B", sha)
            for no, text in enumerate(msg.splitlines(), 1):
                sc.line(where, "(commit message)", no, text)
            patch = git("diff-tree", "--root", "-r", "-p", "-m", "-U0", "--no-color", "--no-ext-diff", "--no-commit-id",
                        "--src-prefix=a/", "--dst-prefix=b/", sha)
            sc.diff(where, patch)


def run_tree(sc: Scanner, root: Path) -> None:
    if (root / ".git").exists():                             # a repo: exactly what git would publish
        listed = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=root,
                                capture_output=True)
        if listed.returncode != 0:
            die("denylist_scan: git ls-files failed (cannot scan; refusing)")
        rels = [p for p in listed.stdout.decode("utf-8", errors="replace").split("\0") if p]
    else:                                                    # a plain directory (e.g. a temp copy): walk it
        rels = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__")]
            rels += [Path(dp, f).relative_to(root).as_posix() for f in fns]
    for rel in sorted(set(rels)):
        sc.name("", rel)
        f = root / rel
        if f.is_symlink() or not f.is_file():
            continue
        try:
            data = f.read_bytes()
        except OSError:
            die(f"denylist_scan: cannot read {rel} (refusing)")
        if b"\0" in data[:8192]:
            continue                                         # binary: file name checked above
        for no, text in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            sc.line("", rel, no, text)


# Public stand-in files that legitimately share a name with an overlay manifest entry.
STANDIN_NAMES = {"config.py", "kalshi_client.py", "maker_strategy.py", "maker_monitor.py", "arb_scanner.py",
                 "maker_config.json", "__init__.py", "conftest.py", "test_client.py", "test_standin.py"}
# Fallback for content ast cannot parse: anchored to a line start, so docstrings/comments that mention the marker
# mid-sentence never match.
MARKER_TRUE = re.compile(r"^[ \t]*IS_STANDIN[ \t]*(?::[^=\n]*)?=[ \t]*True\b", re.M)
MARKER_FALSE = re.compile(r"^[ \t]*IS_STANDIN[ \t]*(?::[^=\n]*)?=[ \t]*False\b", re.M)


def standin_values(body: str) -> set[bool]:
    """Constants really assigned to IS_STANDIN in this Python source (docstrings, comments and strings never
    count). Uses ast; falls back to the anchored line regexes when the content does not parse."""
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        vals = set()
        if MARKER_TRUE.search(body):
            vals.add(True)
        if MARKER_FALSE.search(body):
            vals.add(False)
        return vals
    vals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "IS_STANDIN" for t in targets) \
                and isinstance(value, ast.Constant) and isinstance(value.value, bool):
            vals.add(value.value)
    return vals


def run_guard() -> int:
    """Refuse a commit that stages overlaid private files (S-42): the overlay marker, the private benchmark file,
    manifest-listed modules that the stand-in does not have, or any file that sets IS_STANDIN = False."""
    top = toplevel()
    manifest = top / "scripts" / "overlay_manifest.txt"
    if not manifest.is_file():
        die("guard: scripts/overlay_manifest.txt missing (refusing)")
    listed = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) == 2:
            listed.append(parts[1])
    staged = [p for p in git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split("\0") if p]
    bad = 0
    for path in staged:
        name = path.rsplit("/", 1)[-1]
        why = None
        if path == "kalshi_bot/_overlay.py" or name == "_overlay.py":
            why = "overlay marker file"
        elif any((path == m) or (m.endswith("/") and path.startswith(m)) for m in listed)                 and name not in STANDIN_NAMES:
            why = "private file named in the overlay manifest"
        else:
            body = git("show", f":{path}", check=False) if path.startswith("kalshi_bot/") and path.endswith(".py") else ""
            vals = standin_values(body) if body else set()
            if "/tests/" not in path and False in vals:
                why = "sets IS_STANDIN = False"
            elif path == "kalshi_bot/maker_strategy.py" and True not in vals:
                why = "kalshi_bot/maker_strategy.py is not the stand-in (no IS_STANDIN = True)"
        if why:
            bad += 1
            print(f"{path}: refused, {why}")
    if bad:
        print("guard: overlaid private files must never be committed. Restore with: git checkout -- kalshi_bot control_api",
              file=sys.stderr)
        return 1
    print("guard: no overlaid private files staged")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("staged", "push", "tree", "guard"):
        die(__doc__, 2)
    mode = argv[0]
    if mode == "guard":
        return run_guard()
    root = Path(argv[1]).resolve() if mode == "tree" and len(argv) > 1 else None
    if root is not None:
        os.chdir(root)
    top = toplevel(allow_none=mode == "tree")
    base = top or root or Path.cwd()
    sc = Scanner(load_denylist(base))
    {"staged": lambda: run_staged(sc), "push": lambda: run_push(sc), "tree": lambda: run_tree(sc, base)}[mode]()
    if sc.hits:
        print(f"denylist_scan: {sc.hits} match(es) in {mode} scan. Refusing. Remove or generalise the flagged "
              "content (the matched text is deliberately not printed).", file=sys.stderr)
        return 1
    print(f"denylist_scan: {mode} scan clean ({len(sc.pats)} patterns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
