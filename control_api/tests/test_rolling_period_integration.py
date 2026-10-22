"""Sprint 2A-2 rolling P&L, end to end through the API: windows clipped to the period, window label, default callers
unchanged, /api/since, and a period marker created mid-run moving attribution by the FILL-date rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import periods as P
from control_api.tests.test_analytics import env  # noqa: F401  (env fixture)
from control_api.tests.test_periods import _fill, _settle

pytestmark = pytest.mark.asyncio


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


async def _seed(store, now):
    """Period 2 started 10 days ago. Winning maker fills (NO at 0.4/0.5/0.6, 10 contracts => +6/+5/+4):
    F1 3 days ago (P2), F3 8 days ago (P2), F2 20 days ago (P1). All settle an hour ago."""
    p2 = now - timedelta(days=10)
    await store.add_period({"name": "Demo Period 2", "started_at": p2, "created_by": "owner"})
    await store.upsert_fills([_fill("F1", "KXDEMOB-1", ts=now - timedelta(days=3), price=0.4),
                              _fill("F2", "KXDEMOB-2", ts=now - timedelta(days=20), price=0.5),
                              _fill("F3", "KXDEMOB-3", ts=now - timedelta(days=8), price=0.6)])
    await store.upsert_settlements([_settle(f"KXDEMOB-{i}", "no", now - timedelta(hours=1)) for i in (1, 2, 3)])
    return p2


async def _pnl(env, period=None):
    params = {"period": period} if period else None
    return (await env.client.get("/api/status", params=params, headers=env.auth)).json()["pnl"]


async def test_windows_are_clipped_to_the_period_and_labelled(env):
    now = _now()
    p2 = await _seed(env.store, now)
    cur = await _pnl(env, "current")
    assert (cur["d7"], cur["d30"], cur["settled_fills"]) == (6.0, 10.0, 2)    # F2 (Period 1) is clipped out
    w7, w30 = cur["window"]["d7"], cur["window"]["d30"]
    assert w7["clipped"] is False and w7["label"] == "Last 7 days within Demo Period 2"
    assert w30["clipped"] is True and w30["label"].startswith("Demo Period 2 so far")
    assert P.utc(w30["from"]) == p2 and w30["period_id"] == w7["period_id"] != "all"
    allp = await _pnl(env, "all")
    assert (allp["d7"], allp["d30"]) == (6.0, 15.0) and allp["window"]["d30"]["label"] == "Last 30 days (all periods)"
    p1 = await _pnl(env, "p1")
    assert p1["d30"] == 5.0 and p1["window"]["d7"]["period_id"] == "p1"       # only the Period 1 fill (F2)


async def test_default_callers_are_unchanged(env):
    now = _now()
    await _seed(env.store, now)
    body = (await env.client.get("/api/status", headers=env.auth)).json()
    assert set(body["pnl"]) == {"d7", "d30", "settled_fills"}                 # old shape, no window or basis
    assert body["pnl"]["d7"] == 15.0                                          # by settlement time, all periods
    since = (await env.client.get("/api/since", params={"ts": (now - timedelta(days=30)).isoformat()},
                                  headers=env.auth)).json()
    assert "window" not in since and "clipped" not in since and since["pnl_change"] == 15.0


async def test_since_is_clipped_to_the_period_start(env):
    now = _now()
    p2 = await _seed(env.store, now)
    ts = (now - timedelta(days=30)).isoformat()
    cur = (await env.client.get("/api/since", params={"ts": ts, "period": "current"}, headers=env.auth)).json()
    assert cur["clipped"] is True and cur["since"] == p2.isoformat()
    assert cur["pnl_change"] == 10.0                                          # F1 + F3; F2 belongs to Period 1
    assert "Demo Period 2" in cur["window"]["label"] and cur["window"]["period_id"] != "all"
    allp = (await env.client.get("/api/since", params={"ts": ts, "period": "all"}, headers=env.auth)).json()
    assert allp["pnl_change"] == 15.0


async def test_unknown_period_is_422_on_both_routes(env):
    assert (await env.client.get("/api/status", params={"period": "nope"}, headers=env.auth)).status_code == 422
    r = await env.client.get("/api/since", params={"ts": _now().isoformat(), "period": "nope"}, headers=env.auth)
    assert r.status_code == 422


async def test_marker_created_mid_run_keeps_old_fills_in_period_1_even_if_they_settle_later(env):
    """A fill placed before the marker and settling after it stays in Period 1 (fill-date rule); a fill after the
    marker belongs to Period 2."""
    now = _now()
    await env.store.upsert_fills([_fill("A", "KXDEMOB-A", ts=now - timedelta(hours=2), price=0.4)])
    r = await env.client.post("/api/periods", json={"name": "Period Two"}, headers=await env.h2fa())
    assert r.status_code == 200
    marker = [x for x in (await env.client.get("/api/periods", headers=env.auth)).json()["periods"]
              if x["name"] == "Period Two"][0]
    started = P.utc(marker["started_at"])
    await env.store.upsert_fills([_fill("B", "KXDEMOB-B", ts=started, price=0.5)])      # placed at/after the marker
    await env.store.upsert_settlements([_settle("KXDEMOB-A", "no", started), _settle("KXDEMOB-B", "no", started)])
    cur = await _pnl(env, "current")
    assert cur["settled_fills"] == 1 and cur["d30"] == 5.0                    # only B (+5)
    p1 = await _pnl(env, "p1")
    assert p1["settled_fills"] == 1 and p1["d30"] == 6.0                      # only A (+6), though it settled later
