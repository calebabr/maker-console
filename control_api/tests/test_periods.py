"""Periods + cash-flow events + the period filter (Sprint 2A-1 section 1): timeline, validation, fresh-2FA writes,
audit, attribution by FILL date, return on money spent, cash-flow display, and the `period=` filter on analytics,
activity, history and by-source."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import periods as P
from control_api.tests.test_analytics import env  # noqa: F401  (env fixture with synthetic cells)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
P2_START = NOW - timedelta(days=3)


def _fill(fid, ticker, *, ts, side="no", price=0.4, count=10, src="bot", strat="maker", fee=0.0, e="demo"):
    d = {"fill_id": fid, "order_id": f"o-{fid}", "ticker": ticker, "side": side, "count_fp": str(count), "ts": ts,
         "source": src, "env": e, "fee_cost": str(fee),
         f"{side}_price_dollars": str(price), "is_taker": strat == "arb"}
    if src == "bot":
        d["strategy"] = strat
    return d


def _settle(ticker, result, ts):
    return {"ticker": ticker, "market_result": result, "ts": ts}


async def _seed(store):
    """Period 1 = before NOW-3d, period 2 = from NOW-3d. A1 (win) and A2 (loss) are filled in period 1 and settle
    in period 2 / period 1. B1 (win) settled, B2 (open, arb), Y1 (manual, loss) are filled in period 2."""
    await store.add_period({"name": "Demo Period 2", "started_at": P2_START, "created_by": "owner"})
    await store.upsert_fills([
        _fill("A1", "KXDEMOB-A", ts=NOW - timedelta(days=10), price=0.4),
        _fill("A2", "KXDEMOB-A2", ts=NOW - timedelta(days=9), price=0.5),
        _fill("B1", "KXDEMOB-B", ts=NOW - timedelta(days=2), price=0.6),
        _fill("B2", "KXDEMOB-C", ts=NOW - timedelta(days=1), price=0.3, strat="arb"),
        _fill("Y1", "FED-1", ts=NOW - timedelta(days=2), side="yes", price=0.4, count=5, src="you")])
    await store.upsert_settlements([
        _settle("KXDEMOB-A", "no", NOW - timedelta(days=1)),          # P1 fill settling in period 2
        _settle("KXDEMOB-A2", "yes", NOW - timedelta(days=8)),
        _settle("KXDEMOB-B", "no", NOW - timedelta(hours=20)),
        _settle("FED-1", "no", NOW - timedelta(hours=19))])


# ---------------------------------------------------------------- pure service
def test_timeline_has_implicit_first_period_and_one_current():
    tl = P.timeline([])
    assert len(tl) == 1 and tl[0]["id"] == "p1" and tl[0]["started_at"] is None and tl[0]["current"] is True
    m = [{"id": "x2", "name": "Two", "started_at": P2_START}, {"id": "x3", "name": "Three", "started_at": NOW}]
    tl = P.timeline(list(reversed(m)))                                   # order of input does not matter
    assert [p["id"] for p in tl] == ["p1", "x2", "x3"]
    assert tl[0]["ended_at"] == P2_START and tl[1]["ended_at"] == NOW and tl[2]["ended_at"] is None
    assert [p["current"] for p in tl] == [False, False, True]
    assert P.resolve(tl, "current")["id"] == "x3" and P.resolve(tl, "all") is None and P.resolve(tl, None) is None
    with pytest.raises(ValueError):
        P.resolve(tl, "nope")


def test_marker_validation_and_typo_guard():
    ok = P.validate_marker({"name": "  Demo   Period 2 ", "note": "n​ote"}, [], NOW)
    assert ok["name"] == "Demo Period 2" and ok["note"] == "n ote" and ok["started_at"] == NOW
    for bad in ({}, {"name": ""}, {"name": "x" * 61}, {"name": 5}, {"name": "ok", "note": "y" * 201},
                {"name": "ok", "extra": 1}, "str"):
        with pytest.raises(ValueError) as e:
            P.validate_marker(bad, [], NOW)
        assert isinstance(e.value.args[0], list)
    with pytest.raises(ValueError, match="too_soon"):
        P.validate_marker({"name": "b"}, [{"started_at": NOW - timedelta(minutes=4)}], NOW)
    P.validate_marker({"name": "b"}, [{"started_at": NOW - timedelta(minutes=6)}], NOW)


def test_cash_flow_validation():
    ok = P.validate_cash_flow({"kind": "deposit", "amount": 12.345}, NOW)
    assert ok["amount"] == 12.35 and ok["ts"] == NOW and ok["note"] is None
    ok = P.validate_cash_flow({"kind": "withdrawal", "amount": 5, "ts": (NOW - timedelta(days=2)).isoformat(),
                               "note": "rent"}, NOW)
    assert ok["ts"] == NOW - timedelta(days=2) and ok["note"] == "rent"
    for bad in ({"kind": "gift", "amount": 5}, {"kind": "deposit", "amount": 0}, {"kind": "deposit", "amount": -1},
                {"kind": "deposit", "amount": True}, {"kind": "deposit", "amount": "5"},
                {"kind": "deposit", "amount": 100001}, {"kind": "deposit", "amount": float("nan")},
                {"kind": "deposit", "amount": 0.001}, {"kind": "deposit", "amount": 5, "ts": "garbage"},
                {"kind": "deposit", "amount": 5, "ts": (NOW + timedelta(hours=1)).isoformat()},
                {"kind": "deposit", "amount": 5, "ts": "2001-01-01T00:00:00+00:00"},
                {"kind": "deposit", "amount": 5, "note": "z" * 201}, {"kind": "deposit", "amount": 5, "x": 1}, []):
        with pytest.raises(ValueError) as e:
            P.validate_cash_flow(bad, NOW)
        assert isinstance(e.value.args[0], list)


def test_cover_days_picks_the_smallest_window_that_reaches_the_start():
    allowed = (7, 30, 90, 180)
    assert P.cover_days(NOW - timedelta(days=3), NOW, allowed) == 7
    assert P.cover_days(NOW - timedelta(days=8), NOW, allowed) == 30
    assert P.cover_days(None, NOW, allowed) == 180


def test_summarize_attribution_and_returns():
    fills = [_fill("A1", "T-A", ts=NOW - timedelta(days=10), price=0.4), _fill("A2", "T-A2", ts=NOW - timedelta(days=9), price=0.5),
             _fill("B1", "T-B", ts=NOW - timedelta(days=2), price=0.6),
             _fill("B2", "T-C", ts=NOW - timedelta(days=1), price=0.3, strat="arb"),
             _fill("Y1", "T-Y", ts=NOW - timedelta(days=2), side="yes", price=0.4, count=5, src="you")]
    setts = [_settle("T-A", "no", NOW - timedelta(days=1)), _settle("T-A2", "yes", NOW - timedelta(days=8)),
             _settle("T-B", "no", NOW), _settle("T-Y", "no", NOW)]
    tl = P.timeline([{"id": "m2", "name": "Two", "started_at": P2_START}])
    p1 = P.summarize(fills, setts, tl[0], env="demo", now=NOW)
    assert (p1["fills"], p1["settled"], p1["wins"], p1["losses"], p1["pnl"]) == (2, 2, 1, 1, 1.0)
    assert p1["money_spent"] == 9.0 and p1["settled_cost"] == 9.0 and p1["open_at_risk"] == 0.0
    assert p1["return_on_settled_cost"] == pytest.approx(0.1111, abs=1e-4) and p1["return_on_all_cost"] == p1["return_on_settled_cost"]
    p2 = P.summarize(fills, setts, tl[1], env="demo", now=NOW)                  # the A1 settlement does NOT count here
    assert (p2["fills"], p2["settled"], p2["wins"], p2["pnl"]) == (2, 1, 1, 4.0)
    assert p2["money_spent"] == 9.0 and p2["settled_cost"] == 6.0 and p2["open_fills"] == 1 and p2["open_at_risk"] == 3.0
    assert p2["return_on_settled_cost"] == pytest.approx(0.6667, abs=1e-4)
    assert p2["return_on_all_cost"] == pytest.approx(0.4444, abs=1e-4)
    assert p2["by_source"]["arb"]["open_at_risk"] == 3.0 and p2["by_source"]["you"]["pnl"] == -2.0
    assert p2["total"]["fills"] == 3 and p2["total"]["pnl"] == 2.0
    you = P.summarize(fills, setts, tl[1], env="demo", source="you", now=NOW)
    assert you["fills"] == 1 and you["pnl"] == -2.0
    allsrc = P.summarize(fills, setts, tl[1], env="demo", source="all", now=NOW)
    assert allsrc["fills"] == 3 and allsrc["pnl"] == 2.0
    assert P.summarize(fills, setts, tl[1], env="live", now=NOW)["fills"] == 0           # env filter
    empty = P.summarize([], [], tl[1], env="demo", now=NOW)
    assert empty["pnl"] is None and empty["return_on_settled_cost"] is None and empty["fills"] == 0


def test_cash_flows_are_shown_beside_pnl_never_netted():
    tl = P.timeline([{"id": "m2", "name": "Two", "started_at": P2_START}])
    flows = [{"id": "f1", "ts": NOW - timedelta(days=5), "kind": "deposit", "amount": 100.0},
             {"id": "f2", "ts": NOW - timedelta(days=1), "kind": "deposit", "amount": 50.0, "note": "top up"},
             {"id": "f3", "ts": NOW - timedelta(hours=2), "kind": "withdrawal", "amount": 20.0}]
    snaps = [{"ts": NOW - timedelta(days=4), "cash": 90.0, "exposure": 10.0},
             {"ts": NOW - timedelta(minutes=5), "cash": 150.0, "exposure": 12.0}]
    fills = [_fill("B1", "T-B", ts=NOW - timedelta(days=2), price=0.6)]
    s = P.summarize(fills, [_settle("T-B", "no", NOW)], tl[1], env="demo", flows=flows, snaps=snaps, now=NOW)
    cf = s["cash_flows"]
    assert s["pnl"] == 4.0                                         # untouched by the deposits / withdrawals
    assert (cf["deposits"], cf["withdrawals"], cf["net"]) == (50.0, 20.0, 30.0)
    assert [i["id"] for i in cf["items"]] == ["f2", "f3"]
    assert (cf["start_equity"], cf["end_equity"]) == (100.0, 162.0)
    assert cf["capital_base"] == 150.0 and cf["return_on_capital"] == pytest.approx(0.0267, abs=1e-4)
    assert cf["equity_change_ex_flows"] == 32.0                    # 162 - 100 - (50 - 20)
    none = P.summarize(fills, [], tl[1], env="demo", flows=flows, snaps=[], now=NOW)["cash_flows"]
    assert none["start_equity"] is None and none["return_on_capital"] is None and none["deposits"] == 50.0


# ---------------------------------------------------------------- API: list + markers
@pytest.mark.asyncio
async def test_periods_require_a_login(env):
    for method, path in (("get", "/api/periods"), ("post", "/api/periods"), ("post", "/api/cash-flows"),
                         ("delete", "/api/cash-flows/x"), ("get", "/api/periods/p1/summary"),
                         ("get", "/api/pnl/by-source")):
        assert (await getattr(env.client, method)(path)).status_code == 401


@pytest.mark.asyncio
async def test_list_starts_with_the_implicit_first_period(env):
    r = await env.client.get("/api/periods", headers=env.auth)
    assert r.status_code == 200
    assert r.json() == {"periods": [{"id": "p1", "name": "Demo Period 1", "started_at": None, "ended_at": None,
                                     "note": None, "current": True, "implicit": True}], "cash_flows": []}


@pytest.mark.asyncio
async def test_writes_need_fresh_2fa_and_nothing_is_stored_without_it(env):
    body = {"name": "Demo Period 2"}
    r = await env.client.post("/api/periods", json=body, headers=env.auth)
    assert r.status_code == 403 and r.json()["detail"] == "2fa_required"
    r = await env.client.post("/api/cash-flows", json={"kind": "deposit", "amount": 5}, headers=env.auth)
    assert r.status_code == 403
    cf = await env.store.add_cash_flow({"kind": "deposit", "amount": 5, "created_by": "owner"})
    r = await env.client.delete(f"/api/cash-flows/{cf['id']}", headers=env.auth)
    assert r.status_code == 403
    assert len(await env.store.list_cash_flows()) == 1 and await env.store.list_periods() == []


@pytest.mark.asyncio
async def test_create_period_marks_now_audits_and_guards_typos(env):
    r = await env.client.post("/api/periods", json={"name": "Demo Period 2", "note": "after the fix"},
                              headers=await env.h2fa())
    assert r.status_code == 200, r.text
    body = r.json()
    ps = body["periods"]
    assert [p["name"] for p in ps] == ["Demo Period 1", "Demo Period 2"] and body["created"] == ps[1]["id"]
    assert ps[0]["current"] is False and ps[1]["current"] is True and ps[0]["ended_at"] == ps[1]["started_at"]
    assert ps[1]["note"] == "after the fix" and ps[1]["implicit"] is False
    started = datetime.fromisoformat(ps[1]["started_at"])
    assert abs((datetime.now(timezone.utc) - started).total_seconds()) < 30
    stored = await env.store.list_periods()
    assert len(stored) == 1 and stored[0]["created_by"] == "owner"
    audit = [a for a in await env.store.list_audit(limit=50) if a["action"] == "period.create"]
    assert len(audit) == 1 and audit[0]["ok"] is True and audit[0]["details"]["id"] == stored[0]["id"]
    # a second marker within 5 minutes is a typo guard refusal
    r = await env.client.post("/api/periods", json={"name": "oops"}, headers=await env.h2fa())
    assert r.status_code == 409 and r.json()["detail"] == "period_too_soon"
    assert len(await env.store.list_periods()) == 1
    assert [a["ok"] for a in await env.store.list_audit(limit=50) if a["action"] == "period.create"] == [False, True]


@pytest.mark.asyncio
async def test_create_period_after_five_minutes_and_bad_input(env):
    await env.store.add_period({"name": "Old", "started_at": datetime.now(timezone.utc) - timedelta(minutes=10)})
    r = await env.client.post("/api/periods", json={"name": "Next"}, headers=await env.h2fa())
    assert r.status_code == 200 and len(r.json()["periods"]) == 3
    for bad in ({}, {"name": ""}, {"name": "x" * 61}, {"name": "ok", "note": "n" * 201}, {"name": "ok", "id": "p9"}):
        r = await env.client.post("/api/periods", json=bad, headers=await env.h2fa())
        assert r.status_code == 400, bad
    r = await env.client.post("/api/periods", content="not json", headers=await env.h2fa())
    assert r.status_code == 400
    assert len(await env.store.list_periods()) == 2


@pytest.mark.asyncio
async def test_create_period_store_down_is_503(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "add_period", boom)
    r = await env.client.post("/api/periods", json={"name": "Two"}, headers=await env.h2fa())
    assert r.status_code == 503


# ---------------------------------------------------------------- API: cash flows
@pytest.mark.asyncio
async def test_cash_flow_add_list_delete_and_audit(env):
    ts = (NOW - timedelta(days=2)).isoformat()
    r = await env.client.post("/api/cash-flows", json={"kind": "deposit", "amount": 100, "ts": ts, "note": "start"},
                              headers=await env.h2fa())
    assert r.status_code == 200, r.text
    fid = r.json()["created"]
    r2 = await env.client.post("/api/cash-flows", json={"kind": "withdrawal", "amount": 25.5},
                               headers=await env.h2fa())
    assert [f["kind"] for f in r2.json()["cash_flows"]] == ["deposit", "withdrawal"]      # oldest first
    f0 = r2.json()["cash_flows"][0]
    assert set(f0) == {"id", "ts", "kind", "amount", "note", "created_by"}
    assert (f0["id"], f0["amount"], f0["note"], f0["created_by"]) == (fid, 100.0, "start", "owner")
    got = (await env.client.get("/api/periods", headers=env.auth)).json()
    assert len(got["cash_flows"]) == 2
    d = await env.client.delete(f"/api/cash-flows/{fid}", headers=await env.h2fa())
    assert d.status_code == 200 and d.json()["deleted"] == fid and len(d.json()["cash_flows"]) == 1
    d = await env.client.delete(f"/api/cash-flows/{fid}", headers=await env.h2fa())
    assert d.status_code == 404
    acts = [(a["action"], a["ok"]) for a in await env.store.list_audit(limit=50) if a["action"].startswith("cash_flow")]
    assert sorted(acts) == [("cash_flow.create", True), ("cash_flow.create", True), ("cash_flow.delete", False),
                            ("cash_flow.delete", True)]


@pytest.mark.asyncio
async def test_cash_flow_bad_input_is_400_and_audited(env):
    for bad in ({}, {"kind": "deposit"}, {"kind": "deposit", "amount": 0}, {"kind": "deposit", "amount": 1e9},
                {"kind": "x", "amount": 5}, {"kind": "deposit", "amount": 5, "ts": "nope"}):
        r = await env.client.post("/api/cash-flows", json=bad, headers=await env.h2fa())
        assert r.status_code == 400 and isinstance(r.json()["detail"], list), bad
    assert await env.store.list_cash_flows() == []
    assert (await env.client.delete("/api/cash-flows/bad%20id", headers=await env.h2fa())).status_code in (400, 404)
    assert all(not a["ok"] for a in await env.store.list_audit(limit=50) if a["action"].startswith("cash_flow"))


@pytest.mark.asyncio
async def test_delete_cash_flow_store_down_is_503_and_stop_is_untouched(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(env.store, "delete_cash_flow", boom)
    r = await env.client.delete("/api/cash-flows/abc", headers=await env.h2fa())
    assert r.status_code == 503
    assert (await env.client.post("/api/bot/stop", headers=env.auth)).status_code == 200      # Stop never needs periods


# ---------------------------------------------------------------- API: period summary + filters
@pytest.mark.asyncio
async def test_period_summary_api(env):
    await _seed(env.store)
    await env.store.add_cash_flow({"kind": "deposit", "amount": 40, "ts": NOW - timedelta(days=1), "created_by": "o"})
    r1 = await env.client.get("/api/periods/p1/summary", headers=env.auth)
    assert r1.status_code == 200, r1.text
    s1 = r1.json()
    assert s1["period"]["id"] == "p1" and s1["period"]["ended_at"] is not None
    assert (s1["fills"], s1["settled"], s1["wins"], s1["pnl"]) == (2, 2, 1, 1.0)
    assert s1["cash_flows"]["deposits"] == 0.0 and s1["source"] == "bot" and s1["env"] == "demo"
    expected = {"period", "env", "source", "fills", "contracts", "settled", "wins", "losses", "win_rate", "pnl",
                "money_spent", "settled_cost", "fees", "return_on_settled_cost", "return_on_all_cost", "open_fills",
                "open_at_risk", "by_source", "total", "cash_flows", "data_from", "data_to", "generated_utc",
                "truncated"}
    assert set(s1) == expected
    s2 = (await env.client.get("/api/periods/current/summary", headers=env.auth)).json()
    assert s2["period"]["current"] is True and s2["period"]["name"] == "Demo Period 2"
    assert (s2["fills"], s2["settled"], s2["pnl"], s2["open_at_risk"]) == (2, 1, 4.0, 3.0)
    assert s2["return_on_settled_cost"] == pytest.approx(0.6667, abs=1e-4)
    assert s2["return_on_all_cost"] == pytest.approx(0.4444, abs=1e-4)
    assert s2["cash_flows"]["deposits"] == 40.0 and s2["pnl"] == 4.0
    pid = s2["period"]["id"]
    assert (await env.client.get(f"/api/periods/{pid}/summary?source=you", headers=env.auth)).json()["pnl"] == -2.0
    assert (await env.client.get(f"/api/periods/{pid}/summary?env=live", headers=env.auth)).json()["fills"] == 0


@pytest.mark.asyncio
async def test_period_summary_errors(env):
    assert (await env.client.get("/api/periods/zzz/summary", headers=env.auth)).status_code == 404
    assert (await env.client.get("/api/periods/p1/summary?source=x", headers=env.auth)).status_code == 422
    assert (await env.client.get("/api/periods/p1/summary?env=x", headers=env.auth)).status_code == 422


@pytest.mark.asyncio
async def test_by_source_filters_by_fill_period(env):
    await _seed(env.store)
    all_ = (await env.client.get("/api/pnl/by-source", headers=env.auth)).json()
    assert all_["period"] is None and all_["by_source"]["total"] == 3.0
    p1 = (await env.client.get("/api/pnl/by-source?period=p1", headers=env.auth)).json()
    assert p1["period"]["id"] == "p1" and p1["by_source"]["maker"] == 1.0 and p1["by_source"]["you"] is None
    cur = (await env.client.get("/api/pnl/by-source?period=current", headers=env.auth)).json()["by_source"]
    assert cur["maker"] == 4.0 and cur["you"] == -2.0 and cur["bot"] == 4.0 and cur["total"] == 2.0
    assert (await env.client.get("/api/pnl/by-source?period=nope", headers=env.auth)).status_code == 422


@pytest.mark.asyncio
async def test_analytics_period_filter_default_unchanged(env):
    await _seed(env.store)
    base = (await env.client.get("/api/analytics/summary?days=30", headers=env.auth)).json()
    assert "period" not in base                                     # default (all) shape is unchanged
    assert base["totals"]["settled_fills"] == 3                      # settlement-date window: A1, A2, B1
    same = (await env.client.get("/api/analytics/summary?days=30&period=all", headers=env.auth)).json()
    assert same["totals"] == base["totals"]


@pytest.mark.asyncio
async def test_analytics_summary_attributes_by_fill_period(env):
    await _seed(env.store)
    s1 = (await env.client.get("/api/analytics/summary?period=p1", headers=env.auth)).json()
    assert s1["period"]["id"] == "p1" and s1["period"]["ended_at"] is not None
    assert s1["totals"]["settled_fills"] == 2 and s1["totals"]["pnl"] == 1.0 and s1["totals"]["cost"] == 9.0
    s2 = (await env.client.get("/api/analytics/summary?period=current", headers=env.auth)).json()
    assert s2["period"]["name"] == "Demo Period 2"
    assert s2["totals"]["settled_fills"] == 1 and s2["totals"]["pnl"] == 4.0      # A1's settlement is not here
    assert s2["totals"]["open_fills"] == 1
    assert s2["days"] == 7 and s1["days"] == 180                                     # smallest window reaching the start
    assert (await env.client.get("/api/analytics/summary?period=nope", headers=env.auth)).status_code == 422


@pytest.mark.asyncio
async def test_analytics_trades_and_my_trades_follow_the_period(env):
    await _seed(env.store)
    t1 = (await env.client.get("/api/analytics/trades?period=p1&env=demo", headers=env.auth)).json()
    assert sorted(t["fill_id"] for t in t1["trades"]) == ["A1", "A2"]
    t2 = (await env.client.get("/api/analytics/trades?period=current&source=all", headers=env.auth)).json()
    assert sorted(t["fill_id"] for t in t2["trades"]) == ["B1", "B2", "Y1"]
    csv = await env.client.get("/api/analytics/trades.csv?period=p1", headers=env.auth)
    assert csv.status_code == 200 and "A1" in csv.text and "B1" not in csv.text
    mine = (await env.client.get("/api/analytics/my-trades?period=current", headers=env.auth)).json()
    assert mine["totals"]["pnl"] == -2.0 and [t["fill_id"] for t in mine["trades"]] == ["Y1"]
    assert (await env.client.get("/api/analytics/my-trades?period=p1", headers=env.auth)).json()["totals"]["settled_fills"] == 0


@pytest.mark.asyncio
async def test_activity_period_keeps_settlements_with_their_fill_period(env):
    await _seed(env.store)
    a1 = (await env.client.get("/api/activity?period=p1&limit=100", headers=env.auth)).json()
    assert sorted(f["fill_id"] for f in a1["fills"]) == ["A1", "A2"]
    assert sorted(s["ticker"] for s in a1["settlements"]) == ["KXDEMOB-A", "KXDEMOB-A2"] and a1["period"]["id"] == "p1"
    a2 = (await env.client.get("/api/activity?period=current&limit=100", headers=env.auth)).json()
    assert sorted(f["fill_id"] for f in a2["fills"]) == ["B1", "B2", "Y1"]
    assert sorted(s["ticker"] for s in a2["settlements"]) == ["FED-1", "KXDEMOB-B"]      # not A, though it settled now
    plain = (await env.client.get("/api/activity?limit=100", headers=env.auth)).json()
    assert "period" not in plain and len(plain["fills"]) == 5


@pytest.mark.asyncio
async def test_history_period_window(env):
    await _seed(env.store)
    for d, cash in ((5, 100.0), (2, 120.0), (1, 130.0)):
        await env.store.add_account_snapshot({"ts": NOW - timedelta(days=d), "cash": cash, "exposure": 0.0, "headroom": 1})
    allh = (await env.client.get("/api/history?days=30", headers=env.auth)).json()["snapshots"]
    p1 = (await env.client.get("/api/history?period=p1", headers=env.auth)).json()["snapshots"]
    p2 = (await env.client.get("/api/history?period=current", headers=env.auth)).json()["snapshots"]
    assert [s["cash"] for s in allh] == [100.0, 120.0, 130.0]
    assert [s["cash"] for s in p1] == [100.0] and [s["cash"] for s in p2] == [120.0, 130.0]
