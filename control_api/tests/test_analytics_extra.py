"""Extra analytics coverage: contract shape/types, CI math, cell edge cases, filters+pagination, 503/timeout,
truncation, env defaults, auth, Stop independence, store parity, no secrets."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from control_api.db import MemoryStore
from control_api.db.schemas import FORBIDDEN_KEYS
from control_api.routes import analytics as route
from control_api.services import analytics as an
from control_api.tests.test_analytics import env  # noqa: F401  (synthetic-cells env)
from control_api.tests.test_analytics import (CELLS, GROUP_KEYS, NOW, SUMMARY_KEYS, TOTAL_KEYS, TRADE_KEYS, _put,
                                              _z, fill, settle)

pytestmark = pytest.mark.asyncio
NUM = (int, float)


def _num_or_none(v):
    return v is None or (isinstance(v, NUM) and not isinstance(v, bool))


def check_group(g):
    assert set(g) >= GROUP_KEYS
    assert isinstance(g["key"], str) and isinstance(g["n"], int) and isinstance(g["wins"], int)
    for k in GROUP_KEYS - {"key", "n", "wins"}:
        assert _num_or_none(g[k]), (k, g[k])
    if g["n"]:
        assert 0 <= g["win_ci_low"] <= g["win_rate"] <= g["win_ci_high"] <= 1
        assert g["edge_ci_low"] <= g["edge"] <= g["edge_ci_high"]


def check_summary(s):
    assert set(s) == SUMMARY_KEYS
    assert s["env"] in ("demo", "live", "all") and isinstance(s["days"], int) and isinstance(s["truncated"], bool)
    datetime.fromisoformat(s["generated_utc"].replace("Z", "+00:00"))
    assert set(s["totals"]) == TOTAL_KEYS
    for k, v in s["totals"].items():
        assert _num_or_none(v), k
    for p in s["pnl_curve"]:
        assert {"date", "pnl", "cum_pnl", "drawdown", "maker_pnl", "taker_pnl"} <= set(p)
        assert p["drawdown"] <= 0
    for name in ("by_cell", "by_series", "by_band", "by_ttc"):
        for g in s[name]:
            check_group(g)
    for g in s["by_cell"]:
        assert {"cell", "in_config"} <= set(g) and isinstance(g["in_config"], bool)
    for w in s["fill_quality"]:
        assert set(w) == {"week", "fills", "front", "trade_through", "front_rate", "avg_queue_ahead", "requotes"}
    assert set(s["arb"]) == {"opportunities", "executed", "pnl", "daily"}


def check_trade(t):
    assert set(t) == TRADE_KEYS
    assert t["side"] in ("yes", "no") and t["kind"] in ("maker", "taker") and t["status"] in ("settled", "open")
    assert t["result"] in (None, "win", "loss") and t["fill_quality"] in (None, "front", "trade_through")
    assert t["band"] in ("0.34-0.37", "0.38-0.44", "0.45-0.49", "other")
    assert isinstance(t["price"], float) and isinstance(t["count"], NUM) and isinstance(t["fee"], NUM)
    datetime.fromisoformat(t["ts"])
    if t["status"] == "open":
        assert t["pnl"] is None and t["result"] is None and t["settled_utc"] is None
    else:
        assert isinstance(t["pnl"], NUM) and t["result"] in ("win", "loss")


# ------------------------------------------------------------ contract shape
async def test_shape_conformance_empty_and_populated(env):
    s = (await env.client.get("/api/analytics/summary", headers=env.auth)).json()
    check_summary(s)
    t = NOW - timedelta(days=2)
    fills = [fill("a", ts=t, ticker="KXDEMOB-1", fill_quality={"trade_through": True}),
             fill("b", ts=t, ticker="KXDEMOB-2", price=0.45), fill("c", ts=t, ticker="KXDEMOA-3", taker=True),
             fill("d", ts=NOW - timedelta(hours=1), ticker="KXDEMOB-4")]
    sts = [settle("KXDEMOB-1", "no", t + timedelta(hours=8), t + timedelta(hours=8)),
           settle("KXDEMOB-2", "yes", t + timedelta(hours=8), t + timedelta(hours=8)),
           settle("KXDEMOA-3", "no", t + timedelta(hours=1), t + timedelta(hours=1))]
    await _put(env.store, fills, sts, arbs=[{"ts": t, "event": "E1", "kind": "NO", "executed": "FILLED {}", "profit": 1.0, "mode": "demo"}])
    route.clear_cache(env.app)                                                # summary is cached for 60 s
    for days in (7, 30, 90, 180):
        s = (await env.client.get(f"/api/analytics/summary?days={days}", headers=env.auth)).json()
        check_summary(s)
        assert s["days"] == days and len(s["pnl_curve"]) == days + 1
    assert s["totals"]["settled_fills"] == 3 and s["totals"]["open_fills"] == 1
    body = (await env.client.get("/api/analytics/trades", headers=env.auth)).json()
    assert set(body) == {"total", "offset", "limit", "trades"} and body["total"] == 4
    for tr in body["trades"]:
        check_trade(tr)


# ------------------------------------------------------------ CI math
async def test_wilson_more_known_values():
    lo, hi = an.wilson(50, 100)
    assert (round(lo, 4), round(hi, 4)) == (0.4038, 0.5962)
    lo, hi = an.wilson(1, 1)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.2065, abs=1e-4)
    lo, hi = an.wilson(0, 1)
    assert lo == pytest.approx(0.0) and hi == pytest.approx(0.7935, abs=1e-4)
    assert an.wilson(3, -1) == (None, None)
    for n in (1, 2, 7, 50, 500):
        for w in range(n + 1):
            lo, hi = an.wilson(w, n)
            assert -1e-12 <= lo <= w / n + 1e-12 and w / n - 1e-12 <= hi <= 1 + 1e-12


def _row(price, win, count=1):
    cost = price * count
    pnl = count * (1 - price) if win else -cost
    return {"result": "win" if win else "loss", "count": count, "_cost": cost, "_pnl": pnl, "price": price}


async def test_edge_ci_is_wilson_minus_avg_price_and_roi():
    rows = [_row(0.40, True)] * 8 + [_row(0.40, False)] * 2
    g = an.group_stats("k", rows)
    lo, hi = an.wilson(8, 10)
    assert g["win_rate"] == 0.8 and g["avg_price"] == 0.4 and g["edge"] == 0.4
    assert g["edge_ci_low"] == round(lo - 0.4, 4) and g["edge_ci_high"] == round(hi - 0.4, 4)
    assert g["roi"] == round((8 * 0.6 - 2 * 0.4) / 4.0, 4)
    assert g["pnl"] == pytest.approx(4.0) and g["contracts"] == 10
    z = an.group_stats("z", [])
    assert z["n"] == 0 and z["roi"] is None and z["edge"] is None and z["edge_ci_low"] is None and z["cost"] == 0


async def test_avg_price_is_unweighted_by_contracts():
    g = an.group_stats("k", [_row(0.34, True, 100), _row(0.44, True, 1)])
    assert g["avg_price"] == 0.39


async def test_drawdown_peak_at_zero_and_curve():
    assert an.drawdown([0, 0, 0]) == [0, 0, 0]
    assert an.drawdown([0, -1, 0, -0.5]) == [0, -1, 0, -0.5]
    assert an.drawdown([2, 1, 3, 2.5, 0]) == [0, -1, 0, -0.5, -3]
    assert all(x <= 0 for x in an.drawdown([5, -5, 5, -100]))
    t = NOW - timedelta(days=3)
    rows = an.trade_rows([fill("l", ts=t - timedelta(hours=8), ticker="T-1", price=0.4, count=10)],
                         [settle("T-1", "yes", t, t)], [], cells=CELLS, series_list=[], env="demo")
    s = an.summary(rows, [], [], days=7, env="demo", now=NOW, cells=CELLS)
    assert s["totals"]["max_drawdown"] == -4.0 and s["pnl_curve"][0]["drawdown"] == 0.0


# ------------------------------------------------------------ cell matching edge cases
async def test_cell_edges():
    cells = an.parse_cells([[0.34, 0.38, 6, 12, "no"], [0.38, 0.45, 6, 12, "no"], [0.45, 0.49, 6, 12, "no"]])
    assert an.match_cell(0.49, 8, "no", cells) == cells[2]          # hi-inclusive quirk (matches in_cell)
    assert an.match_cell(0.4901, 8, "no", cells) is None
    assert an.match_cell(0.3399, 8, "no", cells) is None and an.match_cell(0.34, 8, "no", cells) == cells[0]
    assert an.match_cell(0.45, 8, "no", cells) == cells[1]          # first match wins at a shared edge
    assert an.match_cell(0.35, 6, "no", cells) == cells[0]          # h_lo inclusive
    assert an.match_cell(0.35, 5.999, "no", cells) is None and an.match_cell(0.35, 12, "no", cells) is None
    assert an.match_cell(0.35, 8, "yes", cells) is None and an.match_cell(0.35, None, "no", cells) is None
    assert an.match_cell(0.35, 8, None, cells) is None and an.match_cell(0.35, 8, "no", []) is None
    assert an.parse_cells(None) == [] and an.parse_cells("x") == [] and an.parse_cells([[1, 2]]) == []


async def test_not_in_config_cell_group():
    t = NOW - timedelta(days=2)
    rows = an.trade_rows([fill("a", ts=t - timedelta(hours=8), ticker="T-1", price=0.5)],
                         [settle("T-1", "no", t, t)], [], cells=CELLS, series_list=[], env="demo")
    s = an.summary(rows, [], [], days=7, env="demo", now=NOW, cells=CELLS)
    assert [(g["key"], g["cell"], g["in_config"]) for g in s["by_cell"]] == [("none", None, False)]
    assert an.trade_rows([fill("a", ts=t)], [], [], cells=None, series_list=[], env="demo")[0]["cell"] is None


async def test_journal_fallback_rules():
    t = NOW - timedelta(days=2)
    jr = [{"event": "quote", "note": "order_id=o-a", "cell": json.dumps(CELLS[0])}]
    f = fill("a", ts=t, ticker="T-1", close_time=_z(t + timedelta(hours=1)))
    r = an.trade_rows([f], [], [], cells=CELLS, series_list=[], env="demo", journal=jr)[0]
    assert r["ttc_hours"] == 1.0 and r["cell"] == "0.34-0.38|5-11h|no"      # maker: journal cell used
    ft = fill("a", ts=t, ticker="T-1", taker=True)
    assert an.trade_rows([ft], [settle("T-1", "no", t + timedelta(hours=20))], [], cells=CELLS, series_list=[],
                         env="demo", journal=jr)[0]["cell"] is None       # taker never uses the journal
    bad = [{"event": "quote", "note": "order_id=o-a", "cell": "not json"}, {"event": "fill"}, "x",
           {"event": "quote", "note": "no id", "cell": json.dumps(CELLS[0])}]
    assert an.journal_cells(bad) == {}


async def test_side_yes_cell_and_price_for_yes_fill():
    t = NOW - timedelta(days=1)
    f = fill("y", ts=t, ticker="T-1", side="yes", price=0.49, close_time=_z(t + timedelta(hours=1)))
    r = an.trade_rows([f], [], [], cells=CELLS, series_list=[], env="demo")[0]
    assert r["cell"] == "0.45-0.49|0-3h|yes" and r["band"] == "0.45-0.49" and r["price"] == 0.49


# ------------------------------------------------------------ filters combined + pagination
async def _many(env):
    t = NOW - timedelta(days=1)
    fills, sts = [], []
    for i in range(30):
        tk = f"KXDEMOB-26OCT12-B{i}" if i % 2 == 0 else f"KXDEMOA-26OCT12-B{i}"
        fills.append(fill(f"f{i:02d}", ts=t - timedelta(minutes=i), ticker=tk, side="no" if i % 3 else "yes",
                          price=0.35 if i % 4 else 0.43))
        if i < 20:
            sts.append(settle(tk, "no" if i % 5 else "yes", t + timedelta(hours=8), t + timedelta(hours=8)))
    await _put(env.store, fills, sts)


async def test_combined_filters_and_pagination_totals(env):
    await _many(env)
    get = lambda q: env.client.get(f"/api/analytics/trades?{q}", headers=env.auth)  # noqa: E731
    everything = (await get("limit=200")).json()["trades"]
    assert len(everything) == 30
    want = [x for x in everything if x["series"] == "KXDEMOB" and x["side"] == "no" and x["status"] == "settled"]
    body = (await get("series=KXDEMOB&side=no&status=settled&limit=200")).json()
    assert body["total"] == len(want) > 0 and [x["fill_id"] for x in body["trades"]] == [x["fill_id"] for x in want]
    want = [x for x in want if x["result"] == "win" and x["band"] == "0.34-0.37"]
    body = (await get("series=KXDEMOB&side=no&status=settled&result=win&band=0.34-0.37&limit=200")).json()
    assert body["total"] == len(want)
    seen = []
    for off in range(0, 30, 7):
        p = (await get(f"limit=7&offset={off}")).json()
        assert p["total"] == 30 and p["offset"] == off and p["limit"] == 7
        seen += [x["fill_id"] for x in p["trades"]]
    assert seen == [x["fill_id"] for x in everything] and len(set(seen)) == 30
    assert (await get("limit=200&offset=1000000")).json()["trades"] == []
    assert (await get("kind=taker&status=settled")).json() == {"total": 0, "offset": 0, "limit": 50, "trades": []}
    await env.store.upsert_fills([fill("old", ts=NOW - timedelta(days=20), ticker="KXDEMOB-OLD")])
    route.clear_cache(env.app)                                                # trade rows are cached for 60 s
    assert (await get("days=7&limit=200")).json()["total"] == 30
    assert (await get("days=30&limit=200")).json()["total"] == 31


@pytest.mark.parametrize("q", ["days=0", "days=-7", "days=7.5", "limit=abc", "limit=-1", "offset=abc",
                               "offset=1000001", "env=", "side=", "kind=MAKER", "cell=", "series=", "series=KX HIGH",
                               "series=../etc", "status=pending", "band=0.34-0.38"])
async def test_more_422s(env, q):
    assert (await env.client.get(f"/api/analytics/trades?{q}", headers=env.auth)).status_code == 422


async def test_422_body_is_a_list_with_loc(env):
    j = (await env.client.get("/api/analytics/summary?days=14", headers=env.auth)).json()
    assert isinstance(j["detail"], list) and j["detail"][0]["loc"] == ["query", "days"]


async def test_cell_none_filter(env):
    t = NOW - timedelta(days=1)
    await _put(env.store, [fill("a", ts=t, ticker="KXDEMOB-1", price=0.5)])
    assert (await env.client.get("/api/analytics/trades?cell=none", headers=env.auth)).json()["total"] == 1


# ------------------------------------------------------------ store failure / timeout / truncation
class _BadStore:
    def __init__(self, real, which):
        self._real, self._which = real, which

    def __getattr__(self, name):
        if name == self._which:
            async def boom(*a, **k):
                raise RuntimeError("secret-uri mongodb://u:p@h boom")
            return boom
        return getattr(self._real, name)


@pytest.mark.parametrize("which", ["list_fills", "list_settlements", "list_quote_episodes", "list_journal",
                                   "list_arb_opportunities"])
@pytest.mark.parametrize("path", ["summary", "trades"])
async def test_any_read_failing_is_503_without_leak(env, which, path):
    if path == "trades" and which == "list_arb_opportunities":
        pytest.skip("trades does not read arb")
    env.app.state.store = _BadStore(env.store, which)
    r = await env.client.get(f"/api/analytics/{path}", headers=env.auth)
    assert r.status_code == 503 and "mongodb" not in r.text and "boom" not in r.text


async def test_store_timeout_is_503(env, monkeypatch):
    monkeypatch.setattr(route, "STORE_TIMEOUT_S", 0.05)

    async def slow(**k):
        await asyncio.sleep(1)
        return []
    monkeypatch.setattr(env.store, "list_fills", slow)
    t0 = time.monotonic()
    r = await env.client.get("/api/analytics/summary", headers=env.auth)
    assert r.status_code == 503 and time.monotonic() - t0 < 0.9


async def test_truncated_flag_at_and_over_cap(env, monkeypatch):
    monkeypatch.setattr(an, "MAX_FILLS", 3)
    await _put(env.store, [fill(f"x{i}", ts=NOW - timedelta(hours=i + 1), ticker=f"T-{i}") for i in range(3)])
    s = (await env.client.get("/api/analytics/summary", headers=env.auth)).json()
    assert s["truncated"] is False and s["totals"]["open_fills"] == 3        # exactly at the cap: not truncated
    await env.store.upsert_fills([fill("x9", ts=NOW - timedelta(hours=9), ticker="T-9")])
    route.clear_cache(env.app)                                                # summary is cached for 60 s
    s = (await env.client.get("/api/analytics/summary", headers=env.auth)).json()
    assert s["truncated"] is True and s["totals"]["open_fills"] == 3         # oldest dropped
    ids = {t["fill_id"] for t in (await env.client.get("/api/analytics/trades", headers=env.auth)).json()["trades"]}
    assert "x9" not in ids


@pytest.mark.parametrize("cap", ["MAX_SETTLEMENTS", "MAX_EPISODES"])
async def test_truncated_for_other_reads(env, monkeypatch, cap):
    monkeypatch.setattr(an, cap, 1)
    t = NOW - timedelta(hours=2)
    if cap == "MAX_SETTLEMENTS":
        await env.store.upsert_settlements([settle("A", "no", t), settle("B", "no", t - timedelta(hours=1))])
    else:
        await env.store.upsert_quote_episodes([{"order_id": f"e{i}", "ts": t - timedelta(minutes=i), "env": "demo"}
                                               for i in range(2)])
    r = await env.client.get("/api/analytics/summary", headers=env.auth)
    assert r.status_code == 200 and r.json()["truncated"] is True


# ------------------------------------------------------------ env defaults
async def test_env_default_follows_bot_mode_and_all(env):
    t = NOW - timedelta(days=1)
    await _put(env.store, [fill("d", ts=t, ticker="T-D", env="demo"), fill("l", ts=t, ticker="T-L", env="live")])
    g = lambda q="": env.client.get(f"/api/analytics/trades?{q}", headers=env.auth)  # noqa: E731
    ids = lambda r: sorted(x["fill_id"] for x in r.json()["trades"])  # noqa: E731
    assert ids(await g()) == ["d"]
    env.runner.mode = "live"
    assert ids(await g()) == ["l"]
    assert (await env.client.get("/api/analytics/summary", headers=env.auth)).json()["env"] == "live"
    assert ids(await g("env=all")) == ["d", "l"]
    assert ids(await g("env=demo")) == ["d"]
    assert (await env.client.get("/api/analytics/summary?env=all", headers=env.auth)).json()["env"] == "all"
    env.runner.mode = "weird"
    assert (await env.client.get("/api/analytics/summary", headers=env.auth)).json()["env"] == "demo"


# ------------------------------------------------------------ auth
async def test_auth_wrong_typ_expired_and_scheme(env):
    from control_api.auth import security as sec
    for mk in (lambda: sec.create_token(env.settings, "owner", sec.TYP_MFA, 3600)[0],
               lambda: sec.create_token(env.settings, "owner", sec.TYP_ACCESS, -10)[0]):
        h = {"Authorization": f"Bearer {mk()}"}
        for p in ("summary", "trades"):
            assert (await env.client.get(f"/api/analytics/{p}", headers=h)).status_code == 401
    assert (await env.client.get("/api/analytics/summary", headers={"Authorization": "Basic abc"})).status_code == 401
    assert (await env.client.get("/api/analytics/summary", headers=env.auth)).status_code == 200


async def test_garbage_2fa_header_ignored_and_other_methods_rejected(env):
    h = {**env.auth, "X-2FA-Code": "000000"}
    assert (await env.client.get("/api/analytics/summary", headers=h)).status_code == 200
    for m in (env.client.post, env.client.put, env.client.delete):
        assert (await m("/api/analytics/summary", headers=env.auth)).status_code == 405


async def test_analytics_never_writes(env):
    await _put(env.store, [fill("a", ts=NOW - timedelta(days=1))])
    before = (env.runner.desired, env.runner.mode, env.runner.kill_path.exists())
    audit_before = len(await env.store.list_audit(limit=1000))
    await env.client.get("/api/analytics/summary", headers=env.auth)
    await env.client.get("/api/analytics/trades", headers=env.auth)
    assert (env.runner.desired, env.runner.mode, env.runner.kill_path.exists()) == before
    assert env.kalshi.cancelled == []
    assert len(await env.store.list_audit(limit=1000)) == audit_before


# ------------------------------------------------------------ Stop independent of analytics
async def test_stop_works_while_analytics_aggregation_runs(env, monkeypatch):
    """Aggregation runs in a worker thread: a blocking sleep there must not stall the event loop / Stop."""
    def slow_rows(*a, **k):
        time.sleep(0.8)
        return []
    monkeypatch.setattr(an, "trade_rows", slow_rows)
    env.runner.desired = "running"
    ana = asyncio.create_task(env.client.get("/api/analytics/summary", headers=env.auth))
    await asyncio.sleep(0.15)
    t0 = time.monotonic()
    r = await env.client.post("/api/bot/stop", headers=env.auth)
    took = time.monotonic() - t0
    assert r.status_code == 200 and env.runner.kill_path.exists() and env.runner.desired == "stopped"
    assert took < 0.6, f"Stop was blocked by analytics for {took:.2f}s"
    assert (await ana).status_code == 200


async def test_stop_works_when_analytics_store_read_hangs(env, monkeypatch):
    async def hang(**k):
        await asyncio.sleep(30)
    monkeypatch.setattr(env.store, "list_fills", hang)
    ana = asyncio.create_task(env.client.get("/api/analytics/summary", headers=env.auth))
    await asyncio.sleep(0.1)
    r = await asyncio.wait_for(env.client.post("/api/bot/stop", headers=env.auth), 3)
    assert r.status_code == 200 and env.runner.kill_path.exists()
    ana.cancel()
    await asyncio.gather(ana, return_exceptions=True)


# ------------------------------------------------------------ no secrets in responses
async def test_responses_never_contain_forbidden_keys_or_secret_values(env):
    t = NOW - timedelta(days=1)
    leaky = fill("s1", ts=t, ticker="T-1", password="pw-secret-1", totp_secret="TOTPSECRET", api_key="AK-123",
                 private_key="-----BEGIN PRIVATE KEY-----", jwt_secret="jj", mongodb_uri="mongodb://u:p@h",
                 tick_token="tt", cronjob_api_key="ck")
    try:
        await env.store.upsert_fills([leaky])
    except Exception:  # noqa: BLE001  (store may reject forbidden keys; then plain fill)
        await env.store.upsert_fills([fill("s1", ts=t, ticker="T-1")])
    await env.store.upsert_settlements([settle("T-1", "no", t + timedelta(hours=8), t + timedelta(hours=8))])
    for p in ("summary?days=180&env=all", "trades?env=all&limit=200"):
        r = await env.client.get(f"/api/analytics/{p}", headers=env.auth)
        assert r.status_code == 200
        low = r.text.lower()
        for k in FORBIDDEN_KEYS:
            assert k not in low, k
        for v in ("pw-secret-1", "totpsecret", "ak-123", "private key", "mongodb://", "owner", env.token.lower(),
                  env.settings.tick_token.lower(), "cron-key"):
            assert v not in low, v


# ------------------------------------------------------------ store parity: list_monitor_summaries
_params = ["memory"] + (["mongo"] if os.environ.get("TEST_MONGODB_URI") else [])


@pytest_asyncio.fixture(params=_params)
async def pstore(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from control_api.db.mongo import MongoStore
    s = MongoStore(os.environ["TEST_MONGODB_URI"], "kalshi_test_" + uuid.uuid4().hex[:8])
    await s.ensure_indexes()
    yield s
    await s.client.drop_database(s.db.name) if hasattr(s, "client") else None
    await s.close()


async def test_list_monitor_summaries_contract(pstore):
    t = datetime.now(timezone.utc)
    assert await pstore.list_monitor_summaries() == []
    for i in range(5):
        await pstore.add_monitor_summary({"ts": t - timedelta(days=i), "n": i, "summary": {"x": i}})
    allr = await pstore.list_monitor_summaries()
    assert [d["n"] for d in allr] == [0, 1, 2, 3, 4]                       # newest first
    assert all("_id" not in d for d in allr)
    assert [d["n"] for d in await pstore.list_monitor_summaries(limit=2)] == [0, 1]
    assert await pstore.list_monitor_summaries(limit=0) == []
    got = await pstore.list_monitor_summaries(since=t - timedelta(days=2, minutes=1))
    assert [d["n"] for d in got] == [0, 1, 2]
    assert await pstore.list_monitor_summaries(since=t + timedelta(days=1)) == []
    assert (await pstore.list_monitor_summaries(limit=1))[0] == await pstore.last_monitor_summary()
    naive = (t - timedelta(days=1, minutes=1)).replace(tzinfo=None)
    assert [d["n"] for d in await pstore.list_monitor_summaries(since=naive)] == [0, 1]


async def test_summarize_matches_endpoint_shape():
    t = NOW - timedelta(days=2)
    out = an.summarize(fills=[fill("a", ts=t, ticker="KXDEMOB-1")],
                       settlements=[settle("KXDEMOB-1", "no", t + timedelta(hours=8), t + timedelta(hours=8))],
                       config={"cells": CELLS, "series": ["KXDEMOB"]}, now=NOW, days=30, env="demo")
    check_summary(out)
    check_summary(an.summarize(fills=[], settlements=[], now=NOW, days=7, env="all"))
