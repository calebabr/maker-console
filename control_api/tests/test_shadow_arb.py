"""Sprint 2A-2 S3: the arbitrage shadow scan (read only, load capped, off by default) and its daily buckets.

KXDEMO* tickers and the stand-in scanner's textbook math only; no strategy logic here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from control_api.db import MemoryStore
from control_api.services import shadow as sh
from control_api.services import shadow_model as sm
from control_api.tests.test_shadow_cycle import (NOW, T1, T2, FakeMD, FakeStrategy, inp, leader_runner,
                                                 yes_quote)
from control_api.tests.test_shadow_guard import BASE, PREFIX

EV = "KXDEMOA-26OCT17"
CFG = {"series": ["KXDEMOA"], "max_contracts": 5.0, "min_profit": 0.1, "scan_cap": 15.0}


class ArbMD(FakeMD):
    """FakeMD whose two brackets form one event with a YES set cheaper than $1 (stand-in math)."""
    def __init__(self, close=None):
        super().__init__(**({"close": close} if close else {}))
        for m in self.mkts["KXDEMOA"]:
            m["event_ticker"] = EV
        self.books = {T1: {"yes_dollars": [["0.4000", "120"]], "no_dollars": [["0.7000", "50"]]},
                      T2: {"yes_dollars": [["0.3000", "5"]], "no_dollars": [["0.7000", "50"]]}}


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(sh.SHADOW_ARB_ENV, raising=False)
    assert sh.arb_enabled() is False
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "TRUE")
    assert sh.arb_enabled() is True
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "1")
    assert sh.arb_enabled() is False


def test_scan_finds_a_set_counts_requests_and_gate():
    md = ArbMD()
    out = sh.arb_scan(md, CFG, NOW)
    assert out["ok"] is True and out["skipped"] is None and out["events_scanned"] == 1
    assert out["requests"] == 3                                  # one market list + two books
    [op] = out["opps"]
    assert op["event"] == EV and op["kind"] == "YES" and op["legs"] == 2 and op["profit"] > 0
    assert op["gate_ok"] is True
    small = sh.arb_scan(md, {**CFG, "scan_cap": 1.0}, NOW)["opps"][0]
    assert small["gate_ok"] is False
    assert [c[0] for c in md.calls] == ["markets", "orderbook", "orderbook"] * 2


def test_series_start_rotates_between_scans():
    seen = []
    for minute in range(3):
        md = ArbMD()
        sh.arb_scan(md, {**CFG, "series": ["KXDEMOA", "KXDEMOB", "KXDEMOC"]}, NOW + timedelta(minutes=minute))
        seen.append(next(c[1] for c in md.calls if c[0] == "markets"))
    assert sorted(seen) == ["KXDEMOA", "KXDEMOB", "KXDEMOC"]


def test_min_profit_filters():
    assert sh.arb_scan(ArbMD(), {**CFG, "min_profit": 50.0}, NOW)["opps"] == []


def test_request_budget_skips(monkeypatch):
    monkeypatch.setattr(sh, "SHADOW_ARB_MAX_REQUESTS", 2)
    md = ArbMD()
    out = sh.arb_scan(md, CFG, NOW)
    assert out["skipped"] == "request_budget" and out["opps"] == [] and out["requests"] == 1
    assert ("orderbook", T1) not in md.calls


def test_time_budget_and_cycle_deadline_skip_without_raising():
    import time
    md = ArbMD()
    out = sh.arb_scan(md, CFG, NOW, deadline=time.monotonic() - 1)
    assert out["skipped"] == "time_budget" and out["requests"] == 0 and md.calls == []


class SlowBooksMD(ArbMD):
    """ArbMD whose order books each take `delay` seconds (a slow exchange)."""
    def __init__(self, delay, **kw):
        super().__init__(**kw)
        self.delay = delay

    def orderbook(self, ticker, depth=None):
        import time
        time.sleep(self.delay)
        return super().orderbook(ticker, depth)


def test_slow_books_stop_mid_event_at_the_time_budget(monkeypatch):
    """L1: the time budget is checked before every book read, so a slow event stops the scan between legs."""
    import time
    monkeypatch.setattr(sh, "SHADOW_ARB_BUDGET_S", 0.15)
    md = SlowBooksMD(0.2)
    t0 = time.monotonic()
    out = sh.arb_scan(md, CFG, NOW)
    assert time.monotonic() - t0 < 0.6
    assert out["skipped"] == "time_budget" and out["opps"] == [] and out["events_scanned"] == 0
    assert [c for c in md.calls if c[0] == "orderbook"] == [("orderbook", T1)]       # the second leg never read


def test_slow_books_never_push_the_cycle_past_its_deadline(monkeypatch):
    """L1: with a deadline, no book read starts unless a worst-case read still ends before it; the cycle's maker
    episodes are returned (not discarded) and the scan records why it stopped."""
    import time
    monkeypatch.setattr(sh, "SHADOW_ARB_READ_RESERVE_S", 0.25)
    md = SlowBooksMD(0.2)
    deadline = time.monotonic() + 0.5
    res = sh.run_cycle(md, FakeStrategy([yes_quote()]), inp(arb=dict(CFG)), deadline=deadline)
    assert time.monotonic() < deadline
    assert len(res.upserts) == 1 and res.upserts[0]["ticker"] == T1        # the episode work is kept
    assert res.arb["skipped"] == "time_budget" and res.pass_doc["arb_skipped"] == "time_budget"
    assert res.pass_doc["arb_opps"] == 0


def test_read_errors_and_scanner_errors_never_raise():
    md = ArbMD()
    md.fail.add("markets")
    out = sh.arb_scan(md, CFG, NOW)
    assert out["ok"] is False and out["errors"] == ["ConnectionError"] and out["opps"] == []

    class Broken:
        def best_opportunity(self, *a):
            raise RuntimeError("scanner broke")
    out = sh.arb_scan(ArbMD(), CFG, NOW, scanner=Broken())
    assert out["skipped"] == "error" and out["errors"] == ["RuntimeError"]


def test_run_cycle_scans_only_when_asked():
    md = ArbMD()
    res = sh.run_cycle(md, FakeStrategy([yes_quote()]), inp())
    assert res.arb is None and "arb_opps" not in res.pass_doc
    res = sh.run_cycle(md, FakeStrategy([yes_quote()]), inp(arb=dict(CFG)))
    assert res.arb["opps"] and res.pass_doc["arb_opps"] == 1 and res.pass_doc["arb_skipped"] is None
    assert len(res.upserts) == 1                                 # the episode work is unchanged


def test_shadow_source_never_uses_scanner_side_effects():
    src = Path(sh.__file__).read_text(encoding="utf-8")
    for word in ("scan_once", "log_opp", ".execute(", "live_enabled", "public_factory"):
        assert word not in src, word


class ArbSpy(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.seen = []

    def send(self, request, **kw):
        self.seen.append((request.method, request.url, dict(request.headers)))
        path = urlsplit(request.url).path[len(PREFIX):]
        if path == "/markets":
            body = {"markets": [{"ticker": T1, "event_ticker": EV}, {"ticker": T2, "event_ticker": EV}],
                    "cursor": ""}
        elif path.endswith("/orderbook"):
            body = {"orderbook_fp": {"no_dollars": [["0.7000", "50"]]}}
        else:
            body = {}
        r = requests.Response()
        r.status_code, r._content, r.url, r.request = 200, json.dumps(body).encode(), request.url, request
        r.headers["Content-Type"] = "application/json"
        return r

    def close(self):
        pass


def test_real_chain_scan_is_public_gets_only():
    spy = ArbSpy()
    out = sh.arb_scan(sh.build_market_data(adapter=spy), CFG, NOW)
    assert out["opps"] and spy.seen
    for method, url, headers in spy.seen:
        assert method == "GET" and url.startswith(BASE + "/markets") and "/portfolio" not in url
        assert not {h.lower() for h in headers} & {"authorization", "kalshi-access-key", "kalshi-access-signature"}


# ---------------------------------------------------------------- buckets (pure)
def _scan(ts, *opps, skipped=None):
    return {"ts": ts, "ok": True, "skipped": skipped, "requests": 3, "duration_s": 0.1, "events_scanned": 1,
            "opps": list(opps)}


def _op(event=EV, kind="YES", profit=0.5, cost=4.0, gate=True):
    return {"event": event, "kind": kind, "profit": profit, "cost": cost, "contracts": 5.0, "legs": 2,
            "gate_ok": gate}


def test_merge_tracks_distinct_events_first_last_and_best():
    b = sm.arb_merge(None, _scan(NOW, _op(profit=0.3)), NOW)
    b = sm.arb_merge(b, _scan(NOW + timedelta(minutes=10), _op(profit=0.6, cost=5.0, kind="NO")), NOW)
    b = sm.arb_merge(b, _scan(NOW + timedelta(minutes=20), skipped="time_budget"), NOW)
    [day] = b["days"]
    assert day["scans"] == 3 and day["skipped"] == 1 and day["requests"] == 9
    [e] = day["events"]
    assert e["seen"] == 2 and e["seen_yes"] == 1 and e["seen_no"] == 1
    assert e["profit"] == 0.6 and e["cost"] == 5.0 and e["kind"] == "NO"
    assert e["first_seen"] == NOW.isoformat() and e["last_seen"] == (NOW + timedelta(minutes=10)).isoformat()
    assert b["last_scan"]["skipped"] == "time_budget"


def test_merge_keeps_30_days_and_caps_events(monkeypatch):
    old = NOW - timedelta(days=31)
    b = sm.arb_merge(None, _scan(old, _op()), old)
    b = sm.arb_merge(b, _scan(NOW, _op()), NOW)
    assert [d["day"] for d in b["days"]] == [NOW.date().isoformat()]
    monkeypatch.setattr(sm, "ARB_MAX_EVENTS_PER_DAY", 2)
    b = sm.arb_merge(None, _scan(NOW, _op("E1"), _op("E2"), _op("E3")), NOW)
    assert len(b["days"][0]["events"]) == 2 and b["days"][0]["dropped"] == 1


def test_view_daily_and_rolling_with_period_clip():
    b = None
    for d, ev, kind, cost, gate in ((0, "E1", "YES", 4.0, True), (0, "E2", "NO", 20.0, False),
                                    (3, "E3", "YES", 6.0, True), (10, "E4", "NO", 2.0, True)):
        ts = NOW - timedelta(days=d)
        b = sm.arb_merge(b, _scan(ts, _op(ev, kind, 0.5, cost, gate)), NOW)
    b = sm.arb_merge(b, _scan(NOW + timedelta(minutes=5), _op("E1", "YES", 0.4, 4.0, True)), NOW)
    v = sm.arb_view(b, NOW + timedelta(minutes=5), enabled=True)
    assert v["enabled"] is True and len(v["days"]) == 3
    today = v["days"][-1]
    assert today["opportunities"] == 2 and today["yes"] == 1 and today["no"] == 1
    assert today["gate_pass_share"] == 0.5 and today["median_cost"] == 12.0
    assert today["median_duration_s"] == 300.0                     # E1 seen twice, 5 minutes apart
    r7, r30 = v["rolling"]["7d"], v["rolling"]["30d"]
    assert r7["opportunities"] == 3 and r30["opportunities"] == 4 and r7["clipped_to_period"] is False
    assert r30["profit"] == pytest.approx(2.0)
    # clipped to a period that started two days ago
    v = sm.arb_view(b, NOW + timedelta(minutes=5), enabled=False, start=NOW - timedelta(days=2))
    assert [d["day"] for d in v["days"]] == [NOW.date().isoformat()]
    assert v["rolling"]["7d"]["opportunities"] == 2 and v["rolling"]["7d"]["clipped_to_period"] is True
    assert v["rolling"]["30d"]["from"] == (NOW - timedelta(days=2)).isoformat()


def test_view_of_nothing_is_the_empty_shape():
    v, e = sm.arb_view(None, NOW, enabled=False), sm.empty_arb(False)
    assert set(v) == set(e) and v["days"] == [] and v["last_scan"] is None
    for k in ("7d", "30d"):
        assert set(v["rolling"][k]) == set(e["rolling"][k]) and v["rolling"][k]["opportunities"] == 0
    assert v["rolling"]["7d"]["to"] == NOW.isoformat()


# ---------------------------------------------------------------- runner: persisted inside the summary doc
@pytest.mark.asyncio
async def test_runner_persists_buckets_and_refresh_keeps_them(settings, bot_paths, real_strategy, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    store = MemoryStore()
    md = ArbMD(close=datetime.now(timezone.utc) + timedelta(hours=6))
    r = await leader_runner(settings, store, md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.config = {**r.config, "arb": {"arb_series": ["KXDEMOA"], "arb_min_profit": 0.1}}
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["arb_opps"] == 1
    doc = await store.get_shadow_summary()
    [day] = doc["arb"]["days"]
    assert day["scans"] == 1 and day["events"][0]["event"] == EV
    assert doc["status"]["last_pass_ts"] is not None             # the refresh ran after the persist and kept it
    r._shadow_summary_utc = None                                  # force another refresh
    await r.shadow_cycle()
    doc = await store.get_shadow_summary()
    assert doc["arb"]["days"][0]["scans"] == 2 and doc["arb"]["days"][0]["events"][0]["seen"] == 2
    await r.shutdown()


@pytest.mark.asyncio
async def test_runner_with_standin_never_scans(settings, bot_paths, standin, monkeypatch):
    monkeypatch.setenv(sh.SHADOW_ARB_ENV, "true")
    md = ArbMD(close=datetime.now(timezone.utc) + timedelta(hours=6))
    r = await leader_runner(settings, MemoryStore(), md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    out = await r.shadow_cycle()
    assert "arb_opps" not in out["pass"]
    await r.shutdown()


@pytest.mark.asyncio
async def test_runner_without_the_switch_never_scans(settings, bot_paths, real_strategy, monkeypatch):
    monkeypatch.delenv(sh.SHADOW_ARB_ENV, raising=False)
    store = MemoryStore()
    md = ArbMD(close=datetime.now(timezone.utc) + timedelta(hours=6))
    r = await leader_runner(settings, store, md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    out = await r.shadow_cycle()
    assert "arb_opps" not in out["pass"]
    assert (await store.get_shadow_summary())["arb"]["days"] == []
    assert [c for c in md.calls if c[0] == "orderbook"] == [("orderbook", T1), ("orderbook", T2)]
    await r.shutdown()
