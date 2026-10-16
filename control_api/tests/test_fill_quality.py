"""Fill-quality telemetry: trade-through within 60 min and the 60-min maker-side move, with fakes only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.services import fillquality as fq
from control_api.tests.conftest import FAKE_BASELINE

pytestmark = pytest.mark.asyncio


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


class FakeTrades:
    """Public client: /markets/trades for each ticker from a dict, records every call."""

    def __init__(self, by_ticker, fail=False):
        self.by_ticker, self.fail, self.calls = by_ticker, fail, []

    def request(self, method, endpoint, params=None, **kw):
        self.calls.append((method, endpoint, dict(params or {})))
        if self.fail:
            raise RuntimeError("kalshi down")
        assert method == "GET" and endpoint == "/markets/trades" and not kw.get("auth")
        return {"trades": list(self.by_ticker.get(params["ticker"], [])), "cursor": None}


async def test_evaluate_no_side_trade_through_and_move():
    t0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    fill = {"ts": t0, "side": "no", "no_price_dollars": "0.45", "count_fp": "5"}
    trades = [
        {"created_time": _iso(t0), "yes_price_dollars": "0.55"},                       # our own print: ignored
        {"created_time": _iso(t0 + timedelta(minutes=5)), "yes_price_dollars": "0.56"},  # NO at 0.44 < 0.45
        {"created_time": _iso(t0 + timedelta(minutes=50)), "yes_price": 54},            # cents format; NO 0.46
        {"created_time": _iso(t0 + timedelta(minutes=70)), "yes_price_dollars": "0.50"},  # outside window
    ]
    r = fq.evaluate(fill, trades)
    assert r["trade_through"] is True and r["trades_60m"] == 2
    assert r["move_60m_c"] == pytest.approx(1.0)            # last in-window NO price 0.46 - 0.45


async def test_evaluate_yes_side_held_and_no_trades():
    t0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    fill = {"ts": t0, "side": "yes", "yes_price_dollars": "0.48"}
    r = fq.evaluate(fill, [{"created_time": _iso(t0 + timedelta(minutes=10)), "yes_price_dollars": "0.48"}])
    assert r["trade_through"] is False and r["move_60m_c"] == 0.0
    r = fq.evaluate(fill, [])
    assert r["trade_through"] is False and r["move_60m_c"] is None and r["trades_60m"] == 0
    assert fq.evaluate({"ts": t0, "side": "maybe"}, []) is None


async def test_aggregate_shape_and_baseline():
    rows = [{"fill_quality": {"trade_through": True, "move_60m_c": -2.0}},
            {"fill_quality": {"trade_through": False, "move_60m_c": 1.0}},
            {"fill_quality": {"trade_through": False, "move_60m_c": None}},
            {"fill_quality": {"unreadable": True}},
            {"is_taker": True, "fill_quality": {"trade_through": True, "move_60m_c": 9}},
            {"env": "live", "fill_quality": {"trade_through": True, "move_60m_c": 9}}]
    a = fq.aggregate(rows, env="demo")
    assert a == {"n": 3, "trade_through_share": round(1 / 3, 4), "avg_move_60m_c": -0.5,
                 "baseline": FAKE_BASELINE}
    assert fq.aggregate([])["n"] == 0 and fq.aggregate([])["trade_through_share"] is None


async def _seed(store, now, n=3):
    fills = []
    for i in range(n):
        ts = now - timedelta(minutes=90 + i)
        fills.append({"fill_id": f"f{i}", "ticker": f"KXDEMOA-26OCT13-B{i}", "side": "no",
                      "no_price_dollars": "0.40", "count_fp": "1", "is_taker": False, "env": "demo",
                      "created_time": _iso(ts)})
    fills.append({"fill_id": "fresh", "ticker": "KXDEMOA-X", "side": "no", "no_price_dollars": "0.4",
                  "is_taker": False, "created_time": _iso(now - timedelta(minutes=10))})     # window still open
    fills.append({"fill_id": "taker", "ticker": "KXDEMOA-Y", "side": "no", "no_price_dollars": "0.4",
                  "is_taker": True, "created_time": _iso(now - timedelta(minutes=90))})      # arb leg: skipped
    await store.upsert_fills(fills)
    return fills


async def test_runner_scores_fills_and_status_exposes_it(env):
    now = datetime.now(timezone.utc)
    await _seed(env.store, now)
    ts = now - timedelta(minutes=90)
    pub = FakeTrades({"KXDEMOA-26OCT13-B0": [{"created_time": _iso(ts + timedelta(minutes=3)),
                                                "yes_price_dollars": "0.62"}]})   # NO 0.38 < 0.40: through
    env.runner.public_factory = lambda e: pub
    res = await env.runner.run_fill_quality()          # evaluate + refresh the cached aggregate (S-23)
    assert res["evaluated"] == 3
    assert {c[2]["ticker"] for c in pub.calls} == {f"KXDEMOA-26OCT13-B{i}" for i in range(3)}
    by = {f["fill_id"]: f for f in await env.store.list_fills(limit=50)}
    assert by["f0"]["fill_quality"]["trade_through"] is True
    assert by["f0"]["fill_quality"]["move_60m_c"] == pytest.approx(-2.0)
    assert "fill_quality" not in by["fresh"] and "fill_quality" not in by["taker"]
    assert by["f0"]["ts"] < now                                          # ts untouched
    st = (await env.client.get("/api/status", headers=env.auth)).json()["fill_quality"]
    assert st["n"] == 3 and st["trade_through_share"] == round(1 / 3, 4)
    assert st["baseline"] == FAKE_BASELINE
    mk = (await env.client.get("/api/markets", headers=env.auth)).json()
    assert mk["fill_quality"]["n"] == 3
    lax = next(m for m in mk["markets"] if m["series"] == "KXDEMOA")
    assert lax["fill_quality"]["n"] == 3
    # already-scored fills are not fetched again
    pub.calls.clear()
    assert (await env.runner.evaluate_fill_quality())["evaluated"] == 0 and pub.calls == []


async def test_api_call_cap_per_pass(env):
    now = datetime.now(timezone.utc)
    await _seed(env.store, now, n=fq.MAX_FILLS_PER_PASS + 5)
    pub = FakeTrades({})
    env.runner.public_factory = lambda e: pub
    res = await env.runner.evaluate_fill_quality()
    assert res["evaluated"] == fq.MAX_FILLS_PER_PASS and res["pending"] == 5
    assert len(pub.calls) <= fq.MAX_FILLS_PER_PASS * fq.MAX_PAGES


async def test_failures_never_raise(env):
    now = datetime.now(timezone.utc)
    await _seed(env.store, now)
    env.runner.public_factory = lambda e: FakeTrades({}, fail=True)
    res = await env.runner.evaluate_fill_quality()
    assert res["evaluated"] == 0
    assert all("fill_quality" not in f for f in await env.store.list_fills(limit=50))   # retried next pass

    class Dead(MemoryStore):
        async def list_fills(self, **kw):
            raise RuntimeError("atlas down")
    env.runner.store = Dead()
    assert "error" in await env.runner.evaluate_fill_quality()
    await env.runner.run_fill_quality()
    assert env.runner.fill_quality() is None
    assert (await env.client.get("/api/status", headers=env.auth)).json()["fill_quality"] is None


async def test_status_fill_quality_empty(env):
    st = (await env.client.get("/api/status", headers=env.auth)).json()["fill_quality"]
    assert st["n"] == 0 and st["trade_through_share"] is None and st["avg_move_60m_c"] is None


async def test_set_fill_fields_memory_store():
    s = MemoryStore()
    await s.upsert_fills([{"fill_id": "a", "created_time": "2026-10-13T12:00:00Z"}])
    assert await s.set_fill_fields("a", {"fill_quality": {"x": 1}, "ts": "nope", "fill_id": "b"})
    assert not await s.set_fill_fields("missing", {"x": 1})
    f = (await s.list_fills(limit=5))[0]
    assert f["fill_id"] == "a" and f["fill_quality"] == {"x": 1} and isinstance(f["ts"], datetime)
    await s.upsert_fills([{"fill_id": "a", "created_time": "2026-10-13T12:00:00Z", "count": 2}])  # re-sync
    assert (await s.list_fills(limit=5))[0]["fill_quality"] == {"x": 1}                           # kept
