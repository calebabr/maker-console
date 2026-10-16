#!/usr/bin/env python3
"""Compare a semgrep JSON report with the reviewed baseline (contract C3).

    semgrep_triage.py --report semgrep.json [--baseline .github/semgrep-baseline.json] [--root .]
    semgrep_triage.py --report semgrep.json --print-baseline      # draft entries for NEW findings (then add a real reason)

A finding is "triaged" when the baseline has an entry with the same rule id, the same path and the same fingerprint
(sha256 of the flagged source text with whitespace collapsed, so moving the line does not invalidate the entry but
changing it does). Anything not triaged fails the check (exit 1), at every severity. A baseline entry needs a human
`reason`. Entries that no longer match anything are reported as stale (a warning, not a failure) so the file shrinks
as the code is fixed. Semgrep itself runs with --metrics=off; this script only reads local files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

BASELINE_VERSION = 1


def fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:12]


def source_text(root: Path, rel: str, start: int, end: int) -> str | None:
    """The flagged lines, read from the working tree (the report's own `lines` field is withheld for logged-out runs)."""
    try:
        p = (root / rel).resolve()
        p.relative_to(root.resolve())
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None
    if start < 1 or end < start or end > len(lines):
        return None
    return "\n".join(lines[start - 1:end])


def finding_key(result: dict, root: Path) -> tuple[str, str, str]:
    start, end = result["start"]["line"], result["end"]["line"]
    text = source_text(root, result["path"], start, end)
    return result["check_id"], result["path"].replace("\\", "/"), fingerprint(text) if text is not None else "unreadable"


def load_baseline(path: Path) -> tuple[dict[tuple[str, str, str], dict], list[str]]:
    problems: list[str] = []
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"baseline unreadable: {exc}"]
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION or not isinstance(data.get("findings"), list):
        return {}, ["baseline has the wrong shape (expected {version: 1, findings: [...]})"]
    out: dict[tuple[str, str, str], dict] = {}
    for i, e in enumerate(data["findings"]):
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k].strip() for k in ("rule", "path", "fingerprint", "reason"))):
            problems.append(f"baseline entry #{i} needs non-empty rule, path, fingerprint and reason")
            continue
        out[(e["rule"], e["path"], e["fingerprint"])] = e
    return out, problems


def triage(report: dict, baseline: dict, root: Path) -> tuple[list[dict], list[tuple[str, str, str]], set]:
    """-> (untriaged findings, stale baseline keys, matched keys)"""
    new, matched = [], set()
    for r in report.get("results", []):
        key = finding_key(r, root)
        if key in baseline and key[2] != "unreadable":
            matched.add(key)
        else:
            new.append({"rule": key[0], "path": key[1], "line": r["start"]["line"], "fingerprint": key[2],
                        "severity": r.get("extra", {}).get("severity", "?"), "message": r.get("extra", {}).get("message", "")})
    stale = [k for k in baseline if k not in matched]
    return new, stale, matched


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True)
    ap.add_argument("--baseline", default=".github/semgrep-baseline.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--print-baseline", action="store_true")
    a = ap.parse_args(argv)
    try:
        report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"semgrep_triage: cannot read the report ({exc}); failing closed", file=sys.stderr)
        return 1
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        print("semgrep_triage: the report has no 'results' list; failing closed", file=sys.stderr)
        return 1
    root = Path(a.root)
    baseline, problems = load_baseline(Path(a.baseline))
    new, stale, matched = triage(report, baseline, root)
    for e in report.get("errors", []):
        if isinstance(e, dict) and e.get("level") == "error":
            print(f"semgrep error (not a finding): {str(e.get('message') or e.get('type'))[:200]}", file=sys.stderr)
    if a.print_baseline:
        print(json.dumps({"version": BASELINE_VERSION, "findings": [
            {"rule": n["rule"], "path": n["path"], "fingerprint": n["fingerprint"], "reason": "TODO: why this is not a problem"}
            for n in new]}, indent=2))
        return 0
    for p in problems:
        print(f"BASELINE PROBLEM: {p}", file=sys.stderr)
    for k in stale:
        print(f"warning: stale baseline entry (fixed or changed): {k[0]} {k[1]}", file=sys.stderr)
    for n in new:
        print(f"UNTRIAGED [{n['severity']}] {n['path']}:{n['line']} {n['rule']}\n    {n['message'][:200]}", file=sys.stderr)
    total = len(report["results"])
    print(f"semgrep: {total} finding(s), {len(matched)} triaged in the baseline, {len(new)} new, {len(stale)} stale entr(ies)")
    if new:
        print("Fix the code, or add a reviewed entry with a reason to .github/semgrep-baseline.json "
              "(draft entries: semgrep_triage.py --print-baseline).", file=sys.stderr)
    return 1 if (new or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
