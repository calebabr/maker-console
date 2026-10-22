"""Period-aware rolling P&L (Sprint 2A-2 S4): /api/status pnl, /api/since and the period summary's equity-change
label. Nothing passed = the old behaviour; a period = windows clipped to the period, attributed by FILL date."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import periods as P
from control_api.tests.test_analytics import env  # noqa: F401  (env fixture)
from control_api.tests.test_periods import NOW, P2_START, _fill, _seed, _settle



async def _seed2(store):
    """test_periods._seed plus an old period-1 fill (20 days before the marker) that won."""
    await _seed(store)
    await store.upsert_fills([_fill("C1", "KXDEMOB-OLD", ts=P2_START - timedelta(days=20), price=0.5)])
    await store.upsert_settlements([_settle("KXDEMOB-OLD", "no", P2_START - timedelta(days=19))])


# ---------------------------------------------------------------- pure windows
def test_rolling_window_labels_and_clipping():
    tl = P.timeline([{"id": "x2", "name": "Period 2", "started_at": NOW - timedelta(days=3)}])
    cur, p1 = tl[-1], tl[0]
    w = P.rolling_window(cur, 7, NOW)
    assert w["period_id"] == "x2" and w["clipped"] is True and w["from"] == P.iso(cur["started_at"])
    assert w["label"].startswith("Period 2 so far") and w["to"] == P.iso(NOW)
    w30 = P.rolling_window(cur, 30, NOW)
    assert w30["clipped"] is True and w30["from"] == w["from"]
    old = P.timeline([{"id": "x2", "name": "Period 2", "started_at": NOW - timedelta(days=40)}])[-1]
    w = P.rolling_window(old, 7, NOW)
    assert w["clipped"] is False and w["label"] == "Last 7 days within Period 2"
    assert P.utc(w["from"]) == NOW - timedelta(days=7)
    # an ended period counts back from ITS end, not from today
    w = P.rolling_window(p1, 7, NOW)
    assert w["to"] == P.iso(cur["started_at"]) and P.utc(w["from"]) == cur["started_at"] - timedelta(days=7)
    assert w["label"] == "Last 7 days within Demo Period 1" and w["period_id"] == "p1"
    allw = P.rolling_window(None, 30, NOW)
    assert allw["period_id"] == "all" and allw["label"] == "Last 30 days (all periods)" and not allw["clipped"]


def test_equity_change_is_labelled_and_summary_declares_its_basis():
    tl = P.timeline([{"id": "x2", "name": "Period 2", "started_at": P2_START}])
    s = P.summarize([], [], tl[-1], env="demo", now=NOW)
    cf = s["cash_flows"]
    assert cf["equity_change_includes_earlier_settlements"] is True
    assert "EARLIER period" in cf["equity_change_label"] and s["pnl_basis"] == "fill_date"


# ---------------------------------------------------------------- /api/status
@pytest.mark.asyncio
async def test_status_pnl_default_is_unchanged(env):
    await _seed2(env.store)
    body = (await env.client.get("/api/status", headers=env.auth)).json()
    assert set(body["pnl"]) == {"d7", "d30", "settled_fills"}        # no window, no basis: old shape
    assert body["pnl"]["d7"] == 10.0                                  # by settlement time: A1 +6, B1 +4


@pytest.mark.asyncio
async def test_status_pnl_current_period_is_clipped_and_by_fill_date(env):
    await _seed2(env.store)
    body = (await env.client.get("/api/status", params={"period": "current"}, headers=env.auth)).json()
    pnl = body["pnl"]
    assert pnl["basis"] == "fill_date" and pnl["period"]["name"] == "Demo Period 2"
    assert (pnl["d7"], pnl["d30"], pnl["settled_fills"]) == (4.0, 4.0, 1)     # only B1; A1 settled now but P1's
    assert set(pnl["window"]) == {"d7", "d30"}
    w = pnl["window"]["d7"]
    assert set(w) >= {"label", "from", "to", "period_id"} and w["clipped"] is True
    assert P.utc(w["from"]) == P2_START and "Demo Period 2" in w["label"]


@pytest.mark.asyncio
async def test_status_pnl_past_period_and_all(env):
    await _seed2(env.store)
    p1 = (await env.client.get("/api/status", params={"period": "p1"}, headers=env.auth)).json()["pnl"]
    # window counts back from the period end: A1 (-10d) and A2 (-9d) are inside the last 7 days, C1 (-23d) is only
    # inside the 30-day window. A1 +6, A2 -5, C1 +5.
    assert (p1["d7"], p1["d30"], p1["settled_fills"]) == (1.0, 6.0, 3)
    assert p1["window"]["d7"]["period_id"] == "p1" and p1["window"]["d7"]["to"] == P.iso(P2_START)
    al = (await env.client.get("/api/status", params={"period": "all"}, headers=env.auth)).json()["pnl"]
    assert al["window"]["d30"]["period_id"] == "all" and al["period"] is None
    assert (al["d7"], al["d30"], al["settled_fills"]) == (4.0, 10.0, 4)   # fills of the last 7 / 30 days
    assert al["window"]["d7"]["label"] == "Last 7 days (all periods)"


@pytest.mark.asyncio
async def test_status_pnl_without_settled_fills_keeps_the_window(env):
    await env.store.add_period({"name": "Demo Period 2", "started_at": NOW - timedelta(days=1), "created_by": "o"})
    body = (await env.client.get("/api/status", params={"period": "current"}, headers=env.auth)).json()
    assert body["pnl"]["d7"] is None and body["pnl"]["settled_fills"] == 0 and body["pnl"]["window"]["d7"]["label"]


@pytest.mark.asyncio
async def test_status_bad_period_is_422_and_store_error_gives_null(env, monkeypatch):
    assert (await env.client.get("/api/status", params={"period": "zzz"}, headers=env.auth)).status_code == 422
    await env.store.add_period({"name": "Demo Period 2", "started_at": P2_START, "created_by": "o"})

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(env.store, "list_fills", boom)
    r = await env.client.get("/api/status", params={"period": "current"}, headers=env.auth)
    assert r.status_code == 200 and r.json()["pnl"] is None


# ---------------------------------------------------------------- /api/since
@pytest.mark.asyncio
async def test_since_default_unchanged_and_period_clips_by_fill_date(env):
    await _seed2(env.store)
    ts = (NOW - timedelta(days=12)).isoformat()
    base = (await env.client.get("/api/since", params={"ts": ts}, headers=env.auth)).json()
    assert "window" not in base and "clipped" not in base and base["pnl_change"] == 5.0   # A1 +6, A2 -5, B1 +4
    cur = (await env.client.get("/api/since", params={"ts": ts, "period": "current"}, headers=env.auth)).json()
    assert cur["clipped"] is True and cur["since"] == P2_START.isoformat()
    assert cur["pnl_change"] == 4.0 and cur["fills"] >= 1           # only the P2 fill B1 (A1 settled now is P1's)
    assert cur["window"]["period_id"] != "all" and "Demo Period 2" in cur["window"]["label"]
    assert set(cur["window"]) == {"label", "from", "to", "period_id"} and cur["window"]["from"] == cur["since"]
    # period 1 keeps the P1 fills that settled since the time asked (A1 +6, A2 -5; C1 settled before)
    p1 = (await env.client.get("/api/since", params={"ts": ts, "period": "p1"}, headers=env.auth)).json()
    assert p1["clipped"] is False and p1["pnl_change"] == 1.0 and p1["window"]["period_id"] == "p1"
    allp = (await env.client.get("/api/since", params={"ts": ts, "period": "all"}, headers=env.auth)).json()
    assert allp["window"]["period_id"] == "all" and allp["clipped"] is False
    assert (await env.client.get("/api/since", params={"ts": ts, "period": "zzz"}, headers=env.auth)).status_code == 422
