"""Sprint 2A-2 Shadow, end to end with the existing fakes: period attribution by the time the bid was placed
(current / all / a specific id, 422 on an unknown id, a marker created mid-run), the per-event-day fields and the
reliability note, and the SHADOW_ARB switch (off by default; on: request/time caps, GET only, no credentials, a
skip is recorded). KXDEMO* tickers only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow as sh
from control_api.services import shadow_model as sm
from control_api.tests.test_shadow_arb import CFG, ArbMD, ArbSpy
from control_api.tests.test_shadow_cycle import T1, FakeStrategy, use_strategy, yes_quote
from control_api.tests.test_shadow_guard import BASE

UTC = timezone.utc
pytestmark = pytest.mark.asyncio
ARB_CFG = {"arb": {"arb_series": ["KXDEMOA"], "arb_min_profit": 0.1}}


def _ep(i, join, *, result="yes", fill=5.0, price=0.9, settle=None):
    close = settle or join + timedelta(hours=10)
    return {"methodology": 2,
            "ep_id": f"ie{i}", "env": "prod", "ticker": f"KXDEMOA-I{i}", "series": "KXDEMOA", "side": "yes",
            "book_side": "bid", "price": price, "order_price": price, "count": 10.0, "cost": 10 * price,
            "cell": ["a"], "close_time": close, "join_ts": join, "last_seen_ts": join,
            "end_ts": join + timedelta(hours=1), "end_reason": "cancel", "trades_read": True,
            "fill": {"lo": fill, "mid": fill, "hi": fill}, "result": result, "settled_ts": close, "pnl": None,
            "standin": False, "ts": join}


async def _summary(env, q=""):
    r = await env.client.get(f"/api/shadow/summary{q}", headers=env.auth)
    assert r.status_code == 200, r.text
    return r.json()


def _arm_runner(env, monkeypatch, md, strategy=None):
    """The env app's own runner as a shadow leader reading `md` (read only, no trading factory use)."""
    r = env.runner
    env.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.shadow_factory = lambda: md
    use_strategy(monkeypatch, r, strategy or FakeStrategy([yes_quote()]))
    r.config = {**r.config, **ARB_CFG}
    return r


# ================================================================ period attribution
async def test_marker_created_mid_run_moves_later_episodes_to_the_new_period(env, monkeypatch):
    """Episode 1 is quoted before the marker, the bid is re-quoted after it: each belongs to the period in which its
    bid was placed, even though both settle after the marker."""
    from datetime import datetime as dt
    md = ArbMD(close=dt.now(UTC) + timedelta(hours=6))
    strat = FakeStrategy([yes_quote(price=0.4)])
    r = _arm_runner(env, monkeypatch, md, strat)
    assert (await r.shadow_cycle())["ran"] is True
    mk = await env.client.post("/api/periods", json={"name": "Period Two"}, headers=await env.h2fa())
    assert mk.status_code == 200
    md.books[T1] = {"yes_dollars": [["0.4100", "7"]]}
    strat.quotes = [yes_quote(price=0.41)]
    assert (await r.shadow_cycle())["ran"] is True
    rows = list(await env.store.shadow_open_episodes()) + list(await env.store.shadow_recent(50, "ended"))
    assert len(rows) == 2
    by_price = {round(e["order_price"], 2): e for e in rows}
    done = []
    for e in rows:                                               # settle both, as if the market had resolved
        e = dict(e, end_ts=e.get("end_ts") or e["join_ts"], end_reason=e.get("end_reason") or "cancel",
                 trades_read=True, fill={"lo": 5.0, "mid": 5.0, "hi": 5.0}, result="yes", settled_ts=e["close_time"])
        done.append(e)
    await env.store.shadow_upsert_episodes(done)
    await env.store.set_shadow_summary(sm.summarize(done, datetime.now(UTC)))    # the runner's periodic refresh
    p1 = await _summary(env, "?period=p1")
    cur = await _summary(env, "?period=current")
    all_ = await _summary(env, "?period=all")
    assert p1["status"]["settled_episodes"] == 1 and cur["status"]["settled_episodes"] == 1
    assert all_["status"]["settled_episodes"] == 2
    ids = {k: [e["ep_id"] for e in (await env.client.get(f"/api/shadow/episodes?period={k}&state=settled",
                                                         headers=env.auth)).json()["episodes"]] for k in ("p1", "current")}
    assert ids["p1"] == [by_price[0.4]["ep_id"]] and ids["current"] == [by_price[0.41]["ep_id"]]
    assert p1["period"]["ended_at"] is not None and cur["period"]["current"] is True
    await r.shutdown()


async def test_current_all_specific_and_unknown_period(env):
    now = datetime.now(UTC)
    eps = [_ep(1, now - timedelta(days=6)), _ep(2, now - timedelta(days=1), fill=8.0)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.set_shadow_summary(sm.summarize(eps, now))
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    cur, all_ = await _summary(env), await _summary(env, "?period=all")
    p1, per2 = await _summary(env, "?period=p1"), await _summary(env, "?period=per2")
    assert cur["period"]["id"] == "per2" and cur["period"]["spec"] == "current" and cur["period_rule"] == "join_ts"
    assert all_["period"]["id"] == "all" and all_["status"]["settled_episodes"] == 2
    assert p1["status"]["settled_episodes"] == 1 and per2["status"]["settled_episodes"] == 1
    assert cur["headline"]["pnl"]["mid"] == per2["headline"]["pnl"]["mid"] != p1["headline"]["pnl"]["mid"]
    for bad in ("nope", "p9", "bad%20id"):
        assert (await env.client.get(f"/api/shadow/summary?period={bad}", headers=env.auth)).status_code == 422
        assert (await env.client.get(f"/api/shadow/episodes?period={bad}", headers=env.auth)).status_code == 422


async def test_episode_on_the_marker_boundary_belongs_to_the_new_period(env):
    now = datetime.now(UTC)
    start = now - timedelta(days=2)
    eps = [_ep(1, start - timedelta(seconds=1)), _ep(2, start)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": start})
    assert (await _summary(env, "?period=p1"))["status"]["settled_episodes"] == 1
    assert (await _summary(env, "?period=per2"))["status"]["settled_episodes"] == 1


# ================================================================ per-event-day fields + reliability note
async def test_event_day_fields_and_the_reliability_note(env):
    now = datetime.now(UTC)
    eps = [_ep(1, now - timedelta(days=3), fill=5.0), _ep(2, now - timedelta(days=3, hours=1), fill=5.0, result="no"),
           _ep(3, now - timedelta(days=2), fill=8.0), _ep(4, now - timedelta(days=1), fill=0.0)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.set_shadow_summary(sm.summarize(eps, now))
    s = await _summary(env, "?period=all")
    days = s["by_event_day"]
    assert len(days) == len({d["day"] for d in days}) >= 3
    assert sum(d["episodes"] for d in days) == 4 and sum(d["filled_episodes"] for d in days) == 3
    for d in days:
        assert d["pnl"]["lo"] <= d["pnl"]["mid"] <= d["pnl"]["hi"]
        if d["filled_episodes"] == 0:
            assert d["return"] == {"lo": None, "mid": None, "hi": None}
        else:
            assert d["return"]["mid"] == pytest.approx(d["pnl"]["mid"] / d["cost"]["mid"], abs=1e-3)
    ds = s["day_stats"]
    assert ds["event_days"] == len(days) and ds["filled_episodes"] == 3
    assert ds["worst_day"]["return_mid"] <= ds["best_day"]["return_mid"]
    assert s["reliability_note"].startswith("Too early to estimate") and "of 7 days" in s["reliability_note"]
    assert "of 30 filled episodes" in s["reliability_note"]
    assert s["headline"]["pnl"]["mid"] == sm.summarize(eps, now)["headline"]["pnl"]["mid"]   # headline unchanged


async def test_enough_days_and_fills_clears_the_note(env):
    now = datetime.now(UTC)
    eps = [_ep(10 * d + k, now - timedelta(days=d + 1, hours=k), fill=5.0) for d in range(8) for k in range(4)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.set_shadow_summary(sm.summarize(eps, now))
    s = await _summary(env, "?period=all")
    assert s["day_stats"]["days_with_fills"] >= 7 and s["day_stats"]["filled_episodes"] == 32
    assert s["reliability_note"] is None


async def test_event_day_fields_are_period_scoped(env):
    now = datetime.now(UTC)
    eps = [_ep(1, now - timedelta(days=6)), _ep(2, now - timedelta(days=1), fill=8.0)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    p1, cur = await _summary(env, "?period=p1"), await _summary(env, "?period=per2")
    assert [d["episodes"] for d in p1["by_event_day"]] == [1] and [d["episodes"] for d in cur["by_event_day"]] == [1]
    assert p1["by_event_day"][0]["day"] != cur["by_event_day"][0]["day"]


# ================================================================ SHADOW_ARB switch
async def test_arb_is_off_by_default_and_makes_no_scan_requests(env, monkeypatch):
    monkeypatch.delenv(sh.SHADOW_ARB_ENV, raising=False)
    md = ArbMD(close=datetime.now(UTC) + timedelta(hours=6))
    r = _arm_runner(env, monkeypatch, md)
    out = await r.shadow_cycle()
    assert out["ran"] is True and "arb_opps" not in out["pass"]
    assert len([c for c in md.calls if c[0] == "orderbook"]) == 2                  # episode path only
    s = await _summary(env, "?period=all")
    assert s["arb"]["enabled"] is False and s["arb"]["days"] == [] and s["arb"]["last_scan"] is None
    await r.shutdown()


async def test_arb_on_scans_once_per_cycle_and_the_view_says_enabled(env, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    md = ArbMD(close=datetime.now(UTC) + timedelta(hours=6))
    r = _arm_runner(env, monkeypatch, md)
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["arb_opps"] == 1 and out["pass"]["arb_skipped"] is None
    assert len([c for c in md.calls if c[0] == "markets" and c[1] == "KXDEMOA"]) == 2   # one episode read + one scan
    s = await _summary(env, "?period=all")
    assert s["arb"]["enabled"] is True and s["arb"]["last_scan"]["ok"] is True
    assert s["arb"]["rolling"]["7d"]["opportunities"] == 1 and s["arb"]["rolling"]["7d"]["yes"] == 1
    await r.shutdown()


async def test_arb_request_budget_skips_and_records_it(env, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    monkeypatch.setattr(sh, "SHADOW_ARB_MAX_REQUESTS", 2)
    md = ArbMD(close=datetime.now(UTC) + timedelta(hours=6))
    r = _arm_runner(env, monkeypatch, md)
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["arb_skipped"] == "request_budget"
    scan_orderbooks = [c for c in md.calls if c[0] == "orderbook"][2:]                 # after the episode path's two
    assert scan_orderbooks == []
    s = await _summary(env, "?period=all")
    assert s["arb"]["last_scan"]["skipped"] == "request_budget" and s["arb"]["last_scan"]["requests"] <= 2
    assert s["arb"]["days"][-1]["skipped"] == 1 and s["arb"]["rolling"]["7d"]["opportunities"] == 0
    assert out["pass"]["ok"] is True                                                   # the episode work is unharmed
    await r.shutdown()


async def test_arb_time_budget_skips_and_records_it(env, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    monkeypatch.setattr(sh, "SHADOW_ARB_BUDGET_S", -1.0)   # already spent (monotonic is coarse on Windows)
    md = ArbMD(close=datetime.now(UTC) + timedelta(hours=6))
    r = _arm_runner(env, monkeypatch, md)
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["arb_skipped"] == "time_budget"
    s = await _summary(env, "?period=all")
    assert s["arb"]["last_scan"]["skipped"] == "time_budget" and s["arb"]["days"][-1]["skipped"] == 1
    await r.shutdown()


async def test_arb_over_budget_cycle_never_raises_and_next_cycle_recovers(env, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    md = ArbMD(close=datetime.now(UTC) + timedelta(hours=6))
    r = _arm_runner(env, monkeypatch, md)
    with monkeypatch.context() as m:
        m.setattr(sh, "SHADOW_ARB_MAX_REQUESTS", 1)
        assert (await r.shadow_cycle())["pass"]["arb_skipped"] == "request_budget"
    r._shadow_summary_utc = None
    out = await r.shadow_cycle()
    assert out["pass"]["arb_skipped"] is None and out["pass"]["arb_opps"] == 1
    day = (await _summary(env, "?period=all"))["arb"]["days"][-1]
    assert day["scans"] == 2 and day["skipped"] == 1
    await r.shutdown()


async def test_arb_scan_is_get_only_with_no_credentials_through_the_runner(env, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    for k in ("KALSHI_PROD_API_KEY_ID", "KALSHI_PROD_PRIVATE_KEY", "KALSHI_DEMO_API_KEY_ID"):
        monkeypatch.setenv(k, "secret-should-never-be-sent")
    spy = ArbSpy()
    r = _arm_runner(env, monkeypatch, None)
    r.shadow_factory = lambda: sh.build_market_data(adapter=spy)
    out = await r.shadow_cycle()
    assert out["ran"] is True and spy.seen
    for method, url, headers in spy.seen:
        assert method == "GET" and url.startswith(BASE)
        assert "/portfolio" not in url and "/orders" not in url
        low = {h.lower(): v for h, v in headers.items()}
        assert not {"authorization", "kalshi-access-key", "kalshi-access-signature"} & set(low)
        assert "secret-should-never-be-sent" not in str(headers)
    await r.shutdown()


async def test_arb_figures_are_clipped_to_the_selected_period(env, monkeypatch):
    now = datetime.now(UTC)
    block = None
    for d, ev in ((5, "E-old"), (0, "E-new")):
        block = sm.arb_merge(block, {"ts": now - timedelta(days=d), "ok": True, "skipped": None, "requests": 2,
                                     "events_scanned": 1,
                                     "opps": [{"event": ev, "kind": "NO", "profit": 0.5, "cost": 3.0,
                                               "contracts": 5, "legs": 3, "gate_ok": True}]}, now)
    await env.store.set_shadow_summary({**sm.summarize([], now), "arb": block})
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    monkeypatch.delenv(sh.SHADOW_ARB_ENV, raising=False)
    cur, all_ = await _summary(env), await _summary(env, "?period=all")
    assert cur["arb"]["rolling"]["30d"]["opportunities"] == 1 and all_["arb"]["rolling"]["30d"]["opportunities"] == 2
    assert cur["arb"]["rolling"]["30d"]["clipped_to_period"] is True and cur["arb"]["enabled"] is False
