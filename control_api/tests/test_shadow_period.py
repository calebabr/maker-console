"""Sprint 2A-2 S4: period-aware shadow summary and episodes (period=<id|current|all>, default current).

An episode belongs to the period in which its bid was placed (join_ts in [started_at, ended_at))."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow_model as sm

UTC = timezone.utc


def _ep(i, join, *, result="yes", fill=5.0, price=0.9):
    close = join + timedelta(hours=10)
    return {"ep_id": f"pe{i}", "env": "prod", "ticker": f"KXDEMOA-P{i}", "series": "KXDEMOA", "side": "yes",
            "book_side": "bid", "price": price, "order_price": price, "count": 10.0, "cost": 10 * price,
            "cell": ["a"], "close_time": close, "join_ts": join, "last_seen_ts": join, "end_ts": join + timedelta(hours=1),
            "end_reason": "cancel", "trades_read": True, "fill": {"lo": fill, "mid": fill, "hi": fill},
            "result": result, "settled_ts": close, "pnl": None, "standin": False, "ts": join}


async def _seed(env):
    """Three settled episodes 6, 5 and 1 days ago; a period marker 3 days ago; the cached summary over all."""
    now = datetime.now(UTC)
    eps = [_ep(1, now - timedelta(days=6)), _ep(2, now - timedelta(days=5), result="no"),
           _ep(3, now - timedelta(days=1), fill=8.0)]
    await env.store.shadow_upsert_episodes(eps)
    await env.store.set_shadow_summary(sm.summarize(eps, now))
    return now, eps


async def _get(env, q=""):
    r = await env.client.get(f"/api/shadow/summary{q}", headers=env.auth)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_without_markers_current_is_the_cached_summary(env):
    await _seed(env)
    cur, all_ = await _get(env), await _get(env, "?period=all")
    assert cur["headline"] == all_["headline"] and cur["status"]["settled_episodes"] == 3
    assert cur["period"]["id"] == "p1" and cur["period"]["current"] is True and cur["period"]["spec"] == "current"
    assert all_["period"]["id"] == "all" and all_["period_ok"] is True and all_["period_rule"] == "join_ts"


@pytest.mark.asyncio
async def test_period_filters_by_the_time_the_bid_was_placed(env):
    now, eps = await _seed(env)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    all_ = await _get(env, "?period=all")
    cur = await _get(env)                                         # default: current
    p1 = await _get(env, "?period=p1")
    by_id = await _get(env, "?period=per2")
    assert all_["status"]["settled_episodes"] == 3
    assert cur["status"]["settled_episodes"] == 1 and cur["period"]["id"] == "per2"
    drop = ("arb", "period")                                       # arb windows end at each request's "now"
    assert {k: v for k, v in cur.items() if k not in drop} == {k: v for k, v in by_id.items() if k not in drop}
    assert cur["headline"]["pnl"]["mid"] == pytest.approx(sm.score_episode(eps[2])["maker"]["mid"], abs=1e-4)
    assert p1["status"]["settled_episodes"] == 2 and p1["period"]["ended_at"] is not None
    assert len(p1["by_event_day"]) == 2 and len(cur["by_event_day"]) == 1
    # the existing headline (all) is exactly the runner's cached doc
    assert all_["headline"] == (await env.store.get_shadow_summary())["headline"]


@pytest.mark.asyncio
async def test_open_episodes_count_toward_peak_capital_in_their_period(env):
    now, _ = await _seed(env)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    open_ep = dict(_ep(9, now - timedelta(hours=2)), end_ts=None, result=None, settled_ts=None, fill=None)
    await env.store.shadow_upsert_episodes([open_ep])
    cur = await _get(env, "?period=per2")
    assert cur["headline"]["peak_capital"] >= 9.0
    p1 = await _get(env, "?period=p1")
    assert p1["headline"]["peak_capital"] < 18.0                  # the open bid is not in Period 1


@pytest.mark.asyncio
async def test_bad_and_unknown_period_are_422(env):
    for q in ("?period=nope", "?period=bad%20id", "?period=" + "x" * 65):
        r = await env.client.get(f"/api/shadow/summary{q}", headers=env.auth)
        assert r.status_code == 422, q
    assert (await env.client.get("/api/shadow/summary?period=p1")).status_code == 401


@pytest.mark.asyncio
async def test_store_down_still_answers_the_zeroed_shape(env, monkeypatch):
    await _seed(env)

    async def boom(*a, **k):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "list_periods", boom)
    s = await _get(env)
    assert s["period_ok"] is False and s["headline"]["pnl"]["mid"] == 0 and s["period"]["id"] is None
    monkeypatch.undo()
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": datetime.now(UTC) - timedelta(days=3)})
    monkeypatch.setattr(env.store, "shadow_settled", boom)
    s = await _get(env, "?period=per2")
    assert s["period_ok"] is True and s["status"]["settled_episodes"] == 0


@pytest.mark.asyncio
async def test_period_summary_is_cached_per_period(env, monkeypatch):
    now, _ = await _seed(env)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    calls = []
    real = env.store.shadow_settled

    async def spy(*a, **k):
        calls.append(1)
        return await real(*a, **k)
    monkeypatch.setattr(env.store, "shadow_settled", spy)
    await _get(env, "?period=per2")
    await _get(env, "?period=per2")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_arb_section_is_clipped_to_the_period(env):
    now, _ = await _seed(env)
    block = None
    for d, ev in ((5, "E-old"), (0, "E-new")):
        ts = now - timedelta(days=d)
        block = sm.arb_merge(block, {"ts": ts, "ok": True, "skipped": None, "requests": 2, "events_scanned": 1,
                                     "opps": [{"event": ev, "kind": "NO", "profit": 0.5, "cost": 3.0,
                                               "contracts": 5, "legs": 3, "gate_ok": True}]}, now)
    doc = await env.store.get_shadow_summary()
    await env.store.set_shadow_summary({**doc, "arb": block})
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    all_ = await _get(env, "?period=all")
    cur = await _get(env)
    assert all_["arb"]["rolling"]["30d"]["opportunities"] == 2 and all_["arb"]["enabled"] is False
    assert cur["arb"]["rolling"]["30d"]["opportunities"] == 1 and cur["arb"]["rolling"]["30d"]["clipped_to_period"]
    assert [d["day"] for d in cur["arb"]["days"]] == [now.date().isoformat()]


@pytest.mark.asyncio
async def test_episodes_filter_by_period(env):
    now, _ = await _seed(env)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    r = (await env.client.get("/api/shadow/episodes?period=current", headers=env.auth)).json()
    assert [e["ep_id"] for e in r["episodes"]] == ["pe3"] and r["period"]["id"] == "per2"
    r = (await env.client.get("/api/shadow/episodes?period=p1&state=settled", headers=env.auth)).json()
    assert sorted(e["ep_id"] for e in r["episodes"]) == ["pe1", "pe2"]
    r = (await env.client.get("/api/shadow/episodes", headers=env.auth)).json()     # default all: unchanged
    assert len(r["episodes"]) == 3 and r["period"]["id"] == "all"
    assert (await env.client.get("/api/shadow/episodes?period=zzz", headers=env.auth)).status_code == 422
