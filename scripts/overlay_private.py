"""Overlay the private strategy on the public stand-in kalshi_bot.

    python scripts/overlay_private.py --private PATH [--target DIR] [--with-tests] [--allow-git-worktree] [--dry-run]

PATH is a checkout of the private repo. The files listed in scripts/overlay_manifest.txt are copied from PATH into
DIR (default: this repo), each written atomically. Then kalshi_bot/_overlay.py is written with IS_STANDIN = False,
which is what lets the control API allow LIVE mode. Nothing is marked installed if any step fails.

Overlay contract (see kalshi_bot/README.md):
  * the real kalshi_bot/maker_strategy.py must not set IS_STANDIN = True (the stand-in does); checked here;
  * it must expose desired_quotes (5 parameters) and Quote (7 fields) for shadow mode (exit 3 otherwise);
  * the stand-in kalshi_bot/__init__.py and botfiles.is_standin() read kalshi_bot/_overlay.py; both must say False;
  * only paths under kalshi_bot/ and control_api/data/ can be overlaid; secrets never come from the private repo.

Run it in a BUILD or TEMP copy only. In a git working tree of this public repo the overlaid files would show as
modified and could be committed by mistake, so a git work tree is refused unless --allow-git-worktree is given
(for a CI/deploy build directory that is a fresh clone and is never pushed back).
Exit codes: 0 ok, 2 bad arguments/manifest, 3 private file missing or contract broken.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
DEFAULT_TARGET = HERE.parent
MANIFEST = HERE / "overlay_manifest.txt"
ALLOWED_PREFIXES = ("kalshi_bot/", "control_api/data/")
KINDS = ("required", "optional", "tests", "skiptest")
SKIP_NAMES = {"__pycache__", ".pytest_cache", "logs", "state", "cache", ".venv", "venv"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log", ".pem", ".key", ".env")
MARKER = "kalshi_bot/_overlay.py"
STANDIN_TRUE = re.compile(r"^\s*IS_STANDIN\s*(?::[^=]*)?=\s*True\b", re.M)


class OverlayError(Exception):
    def __init__(self, msg: str, code: int = 3):
        super().__init__(msg)
        self.code = code


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """[(kind, posix relative path)]; raises OverlayError(code 2) on any unsafe or malformed line."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in KINDS:
            raise OverlayError(f"manifest line {n}: expected '<{'|'.join(KINDS)}> <path>'", 2)
        kind, rel = parts
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts or "\\" in rel or ":" in rel:
            raise OverlayError(f"manifest line {n}: unsafe path {rel!r}", 2)
        norm = p.as_posix() + ("/" if rel.endswith("/") else "")
        if not norm.startswith(ALLOWED_PREFIXES) or norm.rstrip("/") == MARKER:
            raise OverlayError(f"manifest line {n}: {rel!r} is outside {ALLOWED_PREFIXES}", 2)
        out.append((kind, norm))
    if not any(k == "required" for k, _ in out):
        raise OverlayError("manifest has no required files", 2)
    return out


def _skip(rel: PurePosixPath) -> bool:
    return any(part in SKIP_NAMES for part in rel.parts) or rel.name.endswith(SKIP_SUFFIXES) \
        or rel.name in ("bot.env", ".env", "_overlay.py", "MAKER_KILL_SWITCH", "ARB_KILL_SWITCH")


def _inside(base: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def plan(private: Path, entries: list[tuple[str, str]], with_tests: bool) -> list[tuple[Path, str]]:
    """[(source file, posix relative target path)] in manifest order; raises OverlayError for a missing
    required file, a symlink, or anything resolving outside the private checkout."""
    files: list[tuple[Path, str]] = []
    skipped = {rel for kind, rel in entries if kind == "skiptest"}
    for kind, rel in entries:
        if kind == "skiptest" or (kind == "tests" and not with_tests):
            continue
        src = private / rel.rstrip("/")
        if src.is_symlink():
            raise OverlayError(f"{rel}: symlinks are not copied")
        if not src.exists():
            if kind == "required":
                raise OverlayError(f"required private file missing: {rel}")
            continue
        if not _inside(private, src):
            raise OverlayError(f"{rel}: resolves outside the private checkout")
        if rel.endswith("/"):
            if not src.is_dir():
                raise OverlayError(f"{rel}: expected a directory")
            for f in sorted(src.rglob("*")):
                r = PurePosixPath(rel.rstrip("/")) / PurePosixPath(f.relative_to(src).as_posix())
                if f.is_symlink():
                    raise OverlayError(f"{r}: symlinks are not copied")
                if f.is_file() and not _skip(r) and r.as_posix() not in skipped:
                    files.append((f, r.as_posix()))
        else:
            if not src.is_file():
                raise OverlayError(f"{rel}: expected a file")
            files.append((src, rel))
    return files


def check_contract(private: Path) -> None:
    ms = private / "kalshi_bot" / "maker_strategy.py"
    if STANDIN_TRUE.search(ms.read_text(encoding="utf-8", errors="replace")):
        raise OverlayError("kalshi_bot/maker_strategy.py sets IS_STANDIN = True: that is the public stand-in, not "
                           "the private strategy (check --private)")
    check_shadow_interface(ms)


SHADOW_PARAMS = ["markets", "books", "now", "max_order_dollars", "budget"]
QUOTE_FIELDS = ["ticker", "side", "price", "count", "book_side", "order_price", "cell"]


def check_shadow_interface(ms: Path) -> None:
    """Shadow-mode contract (docs/SHADOW_CONTRACT.md): desired_quotes with exactly 5 parameters and a Quote class
    with the 7 fields. Checked on the syntax tree, so the private module is never imported or run here."""
    try:
        tree = ast.parse(ms.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        raise OverlayError(f"{ms.name}: cannot parse ({e.msg})")
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    fn = fns.get("desired_quotes")
    if fn is None:
        raise OverlayError("maker_strategy.py has no desired_quotes(markets, books, now, max_order_dollars, budget)")
    a = fn.args
    params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    if params != SHADOW_PARAMS or a.vararg or a.kwarg:
        raise OverlayError(f"desired_quotes parameters are {params}, expected exactly {SHADOW_PARAMS}")
    quote = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Quote"), None)
    if quote is None:
        raise OverlayError("maker_strategy.py has no Quote class")
    fields = {n.target.id for n in quote.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    missing = [f for f in QUOTE_FIELDS if f not in fields]
    if missing:
        raise OverlayError(f"Quote is missing fields: {missing}")


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=dst.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def marker_text(copied: list[tuple[str, str]]) -> str:
    lines = ['"""Written by scripts/overlay_private.py: the private strategy is installed. Do not commit."""',
             "IS_STANDIN = False",
             f"OVERLAID_UTC = {datetime.now(timezone.utc).isoformat()!r}",
             "FILES = {"]
    lines += [f"    {rel!r}: {digest!r}," for rel, digest in copied]
    lines.append("}")
    return "\n".join(lines) + "\n"


def overlay(private: Path, target: Path, *, with_tests: bool = False, allow_git_worktree: bool = False,
            dry_run: bool = False, manifest: Path = MANIFEST) -> list[str]:
    private, target = private.resolve(), target.resolve()
    if not (private / "kalshi_bot").is_dir():
        raise OverlayError(f"{private} is not a checkout of the private repo (no kalshi_bot/)", 2)
    if private == target:
        raise OverlayError("target and private checkout are the same directory", 2)
    if not (target / "kalshi_bot").is_dir() or not (target / "control_api").is_dir():
        raise OverlayError(f"{target} is not a project tree (no kalshi_bot/ or control_api/)", 2)
    if (target / ".git").exists() and not allow_git_worktree:
        raise OverlayError(f"{target} is a git work tree: overlay a build/temp copy, or pass --allow-git-worktree "
                           "for a throwaway deploy clone", 2)
    entries = parse_manifest(manifest.read_text(encoding="utf-8"))
    files = plan(private, entries, with_tests)
    check_contract(private)
    rels = [rel for _, rel in files]
    if dry_run:
        return rels
    marker = target / MARKER
    if marker.exists():
        marker.unlink()                                 # not installed while files are being replaced
    copied = []
    for src, rel in files:
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or not _inside(target, dst.parent):     # a symlinked dir must not redirect the write
            raise OverlayError(f"{rel}: resolves outside the target")
        _atomic_copy(src, dst)
        copied.append((rel, _sha256(dst)))
    check_contract(target)                              # what landed is the private strategy, not the stand-in
    fd, tmp = tempfile.mkstemp(dir=str(marker.parent), prefix="_overlay.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(marker_text(copied))
    os.replace(tmp, marker)
    return rels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--private", required=True, type=Path, help="path to a checkout of the private repo")
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="project tree to overlay (default: this repo)")
    ap.add_argument("--with-tests", action="store_true", help="also copy the private kalshi_bot tests")
    ap.add_argument("--allow-git-worktree", action="store_true", help="allow a target that is a git work tree")
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied; change nothing")
    a = ap.parse_args(argv)
    try:
        rels = overlay(a.private, a.target, with_tests=a.with_tests, allow_git_worktree=a.allow_git_worktree,
                       dry_run=a.dry_run)
    except OverlayError as e:
        print(f"overlay failed: {e}", file=sys.stderr)
        return e.code
    print(f"{'would copy' if a.dry_run else 'copied'} {len(rels)} private files"
          + ("" if a.dry_run else "; kalshi_bot/_overlay.py written (IS_STANDIN = False)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
