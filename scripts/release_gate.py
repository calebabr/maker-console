#!/usr/bin/env python3
"""Pre-deploy safety gate (docs/RELEASE_PROCESS.md section 6, contract R5). Pure decision logic plus a small CLI.

The release workflow refuses to deploy unless ALL of these hold (every refusal has a stable reason code):

  CI_NOT_GREEN               CI is not green on the exact commit
  STATUS_UNAVAILABLE         the running server's /api/deploy/status could not be read (down, first deploy that has
                             the status endpoint, wrong key). Passes ONLY if the owner attests the bot is stopped
                             (workflow input bot_stopped_confirmed)
  STATUS_MALFORMED           the answer is not exactly the agreed shape; same rule as STATUS_UNAVAILABLE
  ARB_EXECUTING              an arbitrage set is executing (never bypassable)
  NOT_HANDOVER_CAPABLE       the running version cannot hand over and the bot is not stopped
  UNHEALTHY_BOT_RUNNING      the running server reports unhealthy and the bot is not stopped
  SCHEMA_NEWER_THAN_RELEASE  the running data schema is newer than the release's: deploying older code over newer
                             data is refused (never bypassable)

"Stopped" means the persisted `stopped` flag is true. Fail closed: anything unexpected refuses.

CLI (exit 0 = deploy allowed, 1 = refused, 2 = usage):
    release_gate.py ci   --checks-file checks.json [--required a,b,c]
    release_gate.py gate --status-file status.json --ci-green true|false [--bot-stopped-confirmed]
                         [--release-schema N] [--min-handover-version V]
`--status-file` missing, empty or not JSON counts as "unavailable". No secrets are read or printed here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

STATUS_FIELDS = {
    "version": str, "schema_version": int, "lease_holder": (str, type(None)), "generation": int, "role": str,
    "running": bool, "stopped": bool, "mode": str, "arb_executing": bool, "healthy": bool,
}
ROLES = {"leader", "standby"}
DEFAULT_REQUIRED_CHECKS = ("python", "frontend", "e2e", "gitleaks", "semgrep", "commit-messages")

CI_NOT_GREEN = "CI_NOT_GREEN"
STATUS_UNAVAILABLE = "STATUS_UNAVAILABLE"
STATUS_MALFORMED = "STATUS_MALFORMED"
ARB_EXECUTING = "ARB_EXECUTING"
NOT_HANDOVER_CAPABLE = "NOT_HANDOVER_CAPABLE"
UNHEALTHY_BOT_RUNNING = "UNHEALTHY_BOT_RUNNING"
SCHEMA_NEWER = "SCHEMA_NEWER_THAN_RELEASE"
ALL_REASONS = (CI_NOT_GREEN, STATUS_UNAVAILABLE, STATUS_MALFORMED, ARB_EXECUTING, NOT_HANDOVER_CAPABLE,
               UNHEALTHY_BOT_RUNNING, SCHEMA_NEWER)

EXPLAIN = {
    CI_NOT_GREEN: "CI is not green on this exact commit",
    STATUS_UNAVAILABLE: "cannot read the running server's deploy status; stop the bot in the app, then re-run with bot_stopped_confirmed",
    STATUS_MALFORMED: "the deploy status answer is not the agreed shape; stop the bot, then re-run with bot_stopped_confirmed",
    ARB_EXECUTING: "an arbitrage set is executing right now; try again in a minute",
    NOT_HANDOVER_CAPABLE: "the running version cannot hand over the bot lease; stop the bot first",
    UNHEALTHY_BOT_RUNNING: "the running server is unhealthy while the bot is running; stop the bot first",
    SCHEMA_NEWER: "the stored data schema is newer than this release; deploying it would be a rollback over newer data",
}


@dataclass
class Decision:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def version_tuple(v: object) -> tuple[int, ...] | None:
    """'2026.10.16-3' / 'v2026.10.16-3' -> (2026, 10, 16, 3); anything without digits or a dev marker -> None."""
    if not isinstance(v, str) or "dev" in v:
        return None
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums) if nums else None


def validate_status(status: object) -> bool:
    """Exactly the agreed fields with the right types (bool is not an int here)."""
    if not isinstance(status, dict) or set(status) != set(STATUS_FIELDS):
        return False
    for key, typ in STATUS_FIELDS.items():
        val = status[key]
        if not isinstance(val, typ):
            return False
        if typ is int and isinstance(val, bool):
            return False
    return status["role"] in ROLES and status["generation"] >= 0 and status["schema_version"] >= 0


def evaluate(status: object, *, ci_green: bool, bot_stopped_confirmed: bool = False,
             release_schema_version: int | None = None, min_handover_version: str | None = None) -> Decision:
    reasons: list[str] = []
    notes: list[str] = []
    if ci_green is not True:
        reasons.append(CI_NOT_GREEN)

    if status is None or status == {}:
        if bot_stopped_confirmed is True:
            notes.append("deploy status unavailable; proceeding on the owner's attestation that the bot is stopped")
        else:
            reasons.append(STATUS_UNAVAILABLE)
    elif not validate_status(status):
        if bot_stopped_confirmed is True:
            notes.append("deploy status malformed; proceeding on the owner's attestation that the bot is stopped")
        else:
            reasons.append(STATUS_MALFORMED)
    else:
        stopped = status["stopped"] is True
        if status["arb_executing"]:
            reasons.append(ARB_EXECUTING)
        if min_handover_version is not None:
            need, have = version_tuple(min_handover_version), version_tuple(status["version"])
            capable = have is not None and need is not None and have >= need
        else:
            capable = True          # a server that answers the status endpoint was built with the lease code
        if not capable and not stopped:
            reasons.append(NOT_HANDOVER_CAPABLE)
        if not status["healthy"] and not stopped:
            reasons.append(UNHEALTHY_BOT_RUNNING)
        if release_schema_version is not None and status["schema_version"] > release_schema_version:
            reasons.append(SCHEMA_NEWER)
        if stopped:
            notes.append("bot is stopped; it stays stopped across the deploy")

    return Decision(ok=not reasons, reasons=reasons, notes=notes)


def ci_green_from_check_runs(payload: object, required: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS) -> tuple[bool, list[str]]:
    """GitHub 'list check runs for a ref' JSON -> (green, problems). Every required check must be present, completed
    and successful. Skipped / neutral / cancelled / missing / unreadable all count as NOT green (fail closed)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
        return False, ["unreadable check-runs answer"]
    total = payload.get("total_count")
    if isinstance(total, int) and total > len(payload["check_runs"]):
        return False, ["more check runs than were fetched (paginate)"]
    by_name: dict[str, list[dict]] = {}
    for run in payload["check_runs"]:
        if isinstance(run, dict) and isinstance(run.get("name"), str):
            by_name.setdefault(run["name"], []).append(run)
    problems = []
    for name in required:
        runs = by_name.get(name)
        if not runs:
            problems.append(f"{name}: missing")
            continue
        newest = max(runs, key=lambda r: str(r.get("started_at") or ""))
        if newest.get("status") != "completed" or newest.get("conclusion") != "success":
            problems.append(f"{name}: {newest.get('status')}/{newest.get('conclusion')}")
    return not problems, problems


def _read_json(path: str | None) -> object:
    if not path or not Path(path).is_file():
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return json.loads(text) if text else None
    except (OSError, ValueError):
        return "unreadable"          # not None and not a dict: counts as malformed


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ci = sub.add_parser("ci")
    ci.add_argument("--checks-file", required=True)
    ci.add_argument("--required", default=",".join(DEFAULT_REQUIRED_CHECKS))
    g = sub.add_parser("gate")
    g.add_argument("--status-file")
    g.add_argument("--ci-green", required=True, choices=["true", "false"])
    g.add_argument("--bot-stopped-confirmed", action="store_true")
    g.add_argument("--release-schema", type=int)
    g.add_argument("--min-handover-version")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0

    if a.cmd == "ci":
        required = tuple(x.strip() for x in a.required.split(",") if x.strip())
        if not required:
            print("release_gate: no required checks given", file=sys.stderr)
            return 2
        ok, problems = ci_green_from_check_runs(_read_json(a.checks_file), required)
        print("CI: green on this commit" if ok else "CI: NOT green: " + "; ".join(problems))
        return 0 if ok else 1

    status = _read_json(a.status_file)
    d = evaluate(status, ci_green=a.ci_green == "true", bot_stopped_confirmed=a.bot_stopped_confirmed,
                 release_schema_version=a.release_schema, min_handover_version=a.min_handover_version)
    for n in d.notes:
        print(f"GATE note: {n}")
    if d.ok:
        print("GATE: deploy allowed")
        return 0
    for r in d.reasons:
        print(f"GATE REFUSED [{r}]: {EXPLAIN[r]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
