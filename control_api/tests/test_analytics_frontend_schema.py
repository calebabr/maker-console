"""The real analytics API output must satisfy the exact key/type schema the frontend mock is built to.

The frontend keeps a strict contract checker (control_app/src/test/contractShape.js) and a generator
(control_app/src/services/mockAnalytics.js). This test feeds the REAL endpoint output (seeded + hand-built data,
all `days` x `env`, several trade filters) through that same checker with Node, and compares the key skeleton of the
real summary with the mock's, so backend and mock cannot drift apart silently. Skipped when Node is not installed.
No network; Node only imports two plain ESM files from the repo.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from control_api.db.seed import seed
from control_api.tests.test_analytics import env  # noqa: F401  (synthetic-cells env)
from control_api.tests.test_analytics import NOW, _ep, fill, settle
from control_api.tests.test_analytics_extra import _put

pytestmark = pytest.mark.asyncio

APP = Path(__file__).resolve().parents[2] / "control_app"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

SCRIPT = """
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const app = process.argv[2];
const data = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const { checkSummary, checkTrades } = await import(pathToFileURL(app + '/src/test/contractShape.js').href);
const { analyticsSummary } = await import(pathToFileURL(app + '/src/services/mockAnalytics.js').href);
const problems = [];
for (const s of data.summaries) {
  for (const p of checkSummary(s.body, { days: s.days, env: s.env })) problems.push(`summary days=${s.days} env=${s.env}: ${p}`);
}
for (const t of data.trades) {
  for (const p of checkTrades(t.body, t.expect)) problems.push(`trades ${t.query}: ${p}`);
}
const mock = analyticsSummary(new URLSearchParams('days=180&env=demo'), { nowMs: Date.parse(data.now) })[1];
console.log(JSON.stringify({ problems, mock }));
"""


def _skeleton(v, depth=0):
    """Recursive key/type skeleton; null is compatible with anything (lists use the union of element keys)."""
    if isinstance(v, dict):
        return {k: _skeleton(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        merged: dict = {}
        for el in v:
            sk = _skeleton(el)
            if isinstance(sk, dict):
                for k, x in sk.items():
                    if merged.get(k) in (None, "null"):
                        merged[k] = x
            else:
                return [sk]
        return [merged] if merged else []
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def _compatible(real, mock, path="$"):
    """Same keys everywhere; same scalar types wherever both sides have a non-null value."""
    bad = []
    if isinstance(real, dict) and isinstance(mock, dict):
        for k in sorted(set(real) | set(mock)):
            if k not in real:
                bad.append(f"{path}.{k}: only in mock")
            elif k not in mock:
                bad.append(f"{path}.{k}: only in real API")
            else:
                bad += _compatible(real[k], mock[k], f"{path}.{k}")
    elif isinstance(real, list) and isinstance(mock, list):
        if real and mock:
            bad += _compatible(real[0], mock[0], path + "[]")
    elif "null" not in (real, mock) and real != mock:
        bad.append(f"{path}: real {real} vs mock {mock}")
    return bad


async def _populate(env):
    await seed(env.store)
    t = NOW - timedelta(days=3)
    fills = [
        fill("fs-a", ts=t, ticker="KXDEMOB-1", price=0.43, fill_quality={"front": True}),
        fill("fs-b", ts=t, ticker="KXDEMOB-2", price=0.45, count=7),
        fill("fs-c", ts=t, ticker="KXDEMOA-3", price=0.38, taker=True, fee=0.04),
        fill("fs-d", ts=t, ticker="KXDEMOC-4", price=0.47, side="yes", fill_quality={"trade_through": True}),
        fill("fs-open", ts=NOW - timedelta(hours=2), ticker="KXDEMOB-OPEN", price=0.4),
        fill("fs-live", ts=t, ticker="KXDEMOD-5", price=0.41, env="live"),
    ]
    sts = [
        settle("KXDEMOB-1", "no", t + timedelta(hours=8), t + timedelta(hours=8)),
        settle("KXDEMOB-2", "yes", t + timedelta(hours=8), t + timedelta(hours=8)),
        settle("KXDEMOA-3", "no", t + timedelta(hours=1), t + timedelta(hours=1)),
        settle("KXDEMOC-4", "yes", t + timedelta(hours=1), t + timedelta(hours=1)),
        settle("KXDEMOD-5", "no", t + timedelta(hours=9), t + timedelta(hours=9)),
    ]
    # dense blocks so the sprint-extension fields are populated and verdicts leave "too_early" (>= 20 markets/cell)
    close_dense = NOW - timedelta(days=5)
    for i in range(30):          # cell 0.34-0.38|5-11h|no, mostly wins
        tk = f"KXDEMOB-DENSE{i}"
        fills.append(fill(f"fd-a{i}", ts=close_dense - timedelta(hours=8), ticker=tk, price=0.35))
        sts.append(settle(tk, "yes" if i % 10 == 0 else "no", close_dense, close_dense))
    for i in range(25):          # cell 0.38-0.45|5-11h|no, losing badly -> "below"
        tk = f"KXDEMOA-DENSE{i}"
        fills.append(fill(f"fd-b{i}", ts=close_dense - timedelta(hours=7), ticker=tk, price=0.43, count=3 + i % 4))
        sts.append(settle(tk, "no" if i % 3 == 0 else "yes", close_dense, close_dense))
    eps = [_ep(f"qe{i}", joined=close_dense - timedelta(hours=9), outcome="filled" if i % 2 else "expired",
               filled=5 if i % 2 else 0, first=close_dense - timedelta(hours=8) if i % 2 else None,
               closed=close_dense - timedelta(hours=8 if i % 2 else 6), price=0.35, close=close_dense,
               series="KXDEMOB" if i % 3 else "KXDEMOA") for i in range(12)]
    await _put(env.store, fills, sts, episodes=eps, arbs=[
        {"ts": t, "event": "E1", "kind": "NO", "executed": "FILLED {}", "profit": 1.0, "mode": "demo"},
        {"ts": t, "event": "E2", "kind": "NO", "executed": "", "profit": 0.5, "mode": "demo"},
    ])


TRADE_QUERIES = [
    "", "days=7", "days=180&limit=200", "days=180&limit=3&offset=2", "env=all&days=180", "env=live&days=180",
    "status=open&days=180", "status=settled&result=loss&days=180", "kind=taker&days=180", "side=yes&days=180",
    "band=0.38-0.44&days=180", "series=KXDEMOB&days=180", "days=180&offset=100000",
]


@pytest.mark.slow
@needs_node
async def test_real_api_output_passes_the_frontend_contract_checker_and_matches_mock_keys(env, tmp_path):
    await _populate(env)
    summaries, trades = [], []
    for days in (7, 30, 90, 180):
        for e in ("demo", "live", "all"):
            r = await env.client.get(f"/api/analytics/summary?days={days}&env={e}", headers=env.auth)
            assert r.status_code == 200, r.text
            summaries.append({"days": days, "env": e, "body": r.json()})
    for q in TRADE_QUERIES:
        r = await env.client.get("/api/analytics/trades" + (f"?{q}" if q else ""), headers=env.auth)
        assert r.status_code == 200, (q, r.text)
        params = dict(p.split("=") for p in q.split("&") if p)
        trades.append({"query": q, "body": r.json(),
                       "expect": {"limit": int(params.get("limit", 50)), "offset": int(params.get("offset", 0))}})

    real_180 = next(s["body"] for s in summaries if s["days"] == 180 and s["env"] == "demo")
    assert real_180["totals"]["settled_fills"] > 0, "test data must produce settled fills or the check is vacuous"
    assert any(t["body"]["trades"] for t in trades)
    # non-vacuity: the sprint-extension fields carry real values, so the checker really exercised them
    assert real_180["expectations"]["loaded"] is True
    assert any(b["n"] > 0 and b["win_rate"] is not None for b in real_180["calibration"])
    assert real_180["fill_rate"]["totals"]["quotes"] > 0 and real_180["fill_rate"]["by_series"]
    assert any(c["cluster_ci_low"] is not None and c["roi_ci_low"] is not None for c in real_180["by_cell"])
    assert {c["verdict"] for c in real_180["by_cell"]} - {"too_early"}, "no cell reached a real verdict"

    script, payload = tmp_path / "check.mjs", tmp_path / "payload.json"
    script.write_text(SCRIPT, encoding="utf8")
    payload.write_text(json.dumps({"summaries": summaries, "trades": trades,
                                   "now": NOW.isoformat().replace("+00:00", "Z")}), encoding="utf8")
    p = subprocess.run([NODE, str(script), str(APP), str(payload)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["problems"] == [], "\n".join(out["problems"][:25])

    # key skeleton: the mock (what the UI was built against) and the real API expose the same fields
    drift = _compatible(_skeleton(real_180), _skeleton(out["mock"]))
    assert drift == [], "\n".join(drift[:25])


@needs_node
async def test_empty_real_summary_passes_the_checker_too(env, tmp_path):
    summaries = []
    for days in (7, 30, 90, 180):
        r = await env.client.get(f"/api/analytics/summary?days={days}", headers=env.auth)
        summaries.append({"days": days, "env": "demo", "body": r.json()})
    r = await env.client.get("/api/analytics/trades", headers=env.auth)
    script, payload = tmp_path / "check.mjs", tmp_path / "payload.json"
    script.write_text(SCRIPT, encoding="utf8")
    payload.write_text(json.dumps({"summaries": summaries, "now": NOW.isoformat().replace("+00:00", "Z"),
                                   "trades": [{"query": "", "body": r.json(), "expect": {"limit": 50, "offset": 0}}]}),
                       encoding="utf8")
    p = subprocess.run([NODE, str(script), str(APP), str(payload)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["problems"] == []
