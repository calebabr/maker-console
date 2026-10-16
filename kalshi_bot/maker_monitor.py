"""DEMO PLACEHOLDER health check (public stand-in). Same interface as the private monitor, no decision rules.

It reads the journal for the window, writes logs/monitor_summary.json and logs/monitor_report.txt in the shape the
control API and the app read, and NEVER changes maker_config.json, never trades and never calls Kalshi.
The private overlay replaces this file.

Usage:  python maker_monitor.py [--days 30] [--apply] [--no-api]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
LOGS = HERE / "logs"
JOURNAL = LOGS / "maker_journal.csv"
CONFIG = HERE / "maker_config.json"
KILL = HERE / "MAKER_KILL_SWITCH"

EXPECTED_ROI = None          # no benchmark in the stand-in


def series_of(ticker: str, series_list: list[str]) -> str | None:
    for s in series_list:
        if ticker.startswith(s + "-"):
            return s
    return None


def roi_ci(rows: list[dict]) -> dict:
    """Ratio ROI = sum(pnl)/sum(cost) with an event-clustered 95% CI (delta method). Generic statistics."""
    cost = sum(r["cost"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    out = {"fills": len(rows), "cost": round(cost, 2), "pnl": round(pnl, 2), "events": len({r["event"] for r in rows})}
    if cost <= 0:
        return {**out, "roi": None, "ci_low": None, "ci_high": None}
    roi = pnl / cost
    by_ev = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        by_ev[r["event"]][0] += r["pnl"]
        by_ev[r["event"]][1] += r["cost"]
    n = len(by_ev)
    if n < 2:
        return {**out, "roi": roi, "ci_low": None, "ci_high": None}
    resid = [p - roi * c for p, c in by_ev.values()]
    se = math.sqrt(n / (n - 1) * sum(e * e for e in resid)) / cost
    return {**out, "roi": roi, "ci_low": roi - 1.96 * se, "ci_high": roi + 1.96 * se}


def read_journal(since: datetime) -> tuple[float, list[dict]]:
    """$ quoted and account snapshots since `since` (journal only)."""
    quoted, accounts = 0.0, []
    if not JOURNAL.exists():
        return quoted, accounts
    with JOURNAL.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(row["utc"])
            except (ValueError, KeyError, TypeError):
                continue
            if t < since:
                continue
            if row.get("event") == "quote":
                try:
                    quoted += float(row.get("cost") or 0)
                except ValueError:
                    pass
            elif row.get("event") == "account":
                try:
                    accounts.append({"utc": row["utc"], **json.loads(row.get("note") or "{}")})
                except ValueError:
                    pass
    return quoted, accounts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30, help="look-back window in days (default 30)")
    ap.add_argument("--apply", action="store_true", help="accepted for compatibility; the stand-in never applies")
    ap.add_argument("--no-api", action="store_true", help="accepted for compatibility; the stand-in never calls Kalshi")
    a = ap.parse_args(argv)

    from maker_strategy import load_config
    series_list = load_config()["series"]
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    quoted, accounts = read_journal(since)
    per_series = {s: roi_ci([]) for s in series_list}
    overall = roi_ci([])
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(), "window_days": a.days,
        "expected_roi": EXPECTED_ROI, "quoted_dollars": round(quoted, 2), "filled_dollars": 0.0, "fill_rate": None,
        "overall": overall, "per_series": per_series,
        "actions": {"pause": [], "keep": list(series_list), "need_more": {}, "collateral_return": "not applicable"},
        "last_account": accounts[-1] if accounts else None,
        "note": "stand-in monitor (demo placeholder): no fill statistics and no decisions",
    }
    LOGS.mkdir(exist_ok=True)
    (LOGS / "monitor_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    lines = [f"Monitor (stand-in)  {summary['generated_utc'][:16]} UTC   window: last {a.days} days",
             f"Quoted ${quoted:.2f}", "No decisions: the demo placeholder has no decision rules."]
    (LOGS / "monitor_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
