"""Sprint 2A-1 integration: maker output -> runner alert, arbitrage demo-execute + risk gate + maker exposure
together, and every demo-execute refusal driven through a whole scan. Fakes only; no network.

The helpers here (`demo_env`, `TwoEventPublic`, `DemoTrader`, `HttpError`) are reused by test_failure_combinations.py
and test_stop_trio_2a1.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as rn

pytestmark = pytest.mark.asyncio

DEMO_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
PROD_URL = "https://external-api.kalshi.com/trade-api/v2"


class HttpError(Exception):
    """Looks like kalshi_client.KalshiError: status + body in the message."""

    def __init__(self, status, body="{}"):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


class TwoEventPublic:
    """Open 3-bracket events in KXDEMOA whose YES bids sum to 1.5: a $3.12 NO-arb set each."""

    def __init__(self, events=1):
        self.events = events

    def get_markets(self, series_ticker=None, status=None):
        if series_ticker != "KXDEMOA":
            return []
        out = []
        for e in range(self.events):
            ev = f"KXDEMOA-26OCT2{e}"
            out += [{"event_ticker": ev, "ticker": f"{ev}-B{i}"} for i in range(3)]
        return out

    def get_orderbook(self, ticker, depth=None):
        return {"orderbook_fp": {"yes_dollars": [["0.50", "100"]], "no_dollars": []}}


class DemoTrader:
    """Fake demo client: records orders; each read/write can be made to raise an HttpError."""

    def __init__(self, base_url=DEMO_URL, cash_cents=100000, positions=None):
        self.base_url, self.cash_cents = base_url, cash_cents
        self.positions = positions or []
        self.fail = {}            # method name -> HTTP status (raised on every call) or an exception instance
        self.fail_after = {}      # create_order only: raise from the Nth call on (1-based)
        self.fill_ratio = 1.0
        self.orders, self.cancelled = [], []
        self.resting = []         # what get_orders() returns (Stop cancels the mm- ones)
        self.on_order = None      # hook called before an order is recorded (runs in the worker thread)

    def _maybe(self, name):
        f = self.fail.get(name)
        if f is not None:
            raise f if isinstance(f, Exception) else HttpError(f)

    def get_balance(self):
        self._maybe("get_balance")
        return {"balance": self.cash_cents}

    def get_positions(self, **kw):
        self._maybe("get_positions")
        return {"market_positions": list(self.positions)}

    def get_orders(self, status=None):
        self._maybe("get_orders")
        return list(self.resting)

    def cancel_order(self, order_id, market_ticker=None):
        self._maybe("cancel_order")
        self.cancelled.append(order_id)

    def create_order(self, *, ticker, side, count, price, client_order_id, **kw):
        n = len(self.orders) + 1
        if self.on_order:
            self.on_order(ticker)
        self._maybe("create_order")
        nth = self.fail_after.get("create_order")
        if nth is not None and n >= nth:
            raise HttpError(503)
        self.orders.append((ticker, side, count, price, client_order_id))
        return {"order": {"fill_count_fp": str(float(count) * self.fill_ratio)}}


@pytest.fixture
def demo_env(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "not-a-real-key")
    env.public = TwoEventPublic()
    env.runner.public_factory = lambda e: env.public
    env.trader = DemoTrader()
    env.factory_modes = []

    def factory(mode):
        env.factory_modes.append(mode)
        return env.trader
    env.runner.kalshi_factory = factory
    env.runner.config = {**env.runner.config, "arb": {"arb_series": ["KXDEMOA"], "arb_min_profit": 0.01,
                                                      "arb_max_contracts": 2}}
    return env


async def arb_on(env, **extra):
    r = await env.client.put("/api/arb", json={"enabled": True, "arb_demo_execute": True, **extra},
                             headers=await env.h2fa())
    assert r.status_code == 200, r.text
    return r.json()


async def last_opps(env, n=1):
    return await env.store.list_arb_opportunities(limit=n)


# ============================================================ (a) maker output lines -> one grouped alert
OUT = ("series_capped: KXDEMOA exposure=21.50 cap=20.65\n"
       "write_errors: cancel=2 place=1 paused=0\n")


async def test_write_errors_through_real_passes_make_one_throttled_alert_and_passes_stay_ok(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")
    r = env.runner
    r.desired, r.mode = "running", "demo"
    r.last_monitor_utc = rn.utcnow()
    monkeypatch.setattr(r.ms, "main", lambda *a, **k: (print(OUT, end=""), 0)[1])
    for _ in range(3):                              # three passes in a row, all with order errors
        await r._pass("t")
    alerts = [a for a in r.alerter.sent if a["title"].startswith("Order errors")]
    assert len(alerts) == 1 and alerts[0]["title"] == "Order errors: 2 cancel, 1 place"
    assert alerts[0]["priority"] == "high"
    assert not any(a["title"].startswith("Trading pass error") for a in r.alerter.sent)
    hb = await env.store.last_heartbeat()
    assert hb and hb["ok"] is True and r.last_error is None
    assert r.last_write_report["series_capped"][0]["series"] == "KXDEMOA"
    # the suppressed passes are mentioned in the next alert once the 30 minutes have gone by
    await r._handle_write_report(OUT, "demo", now=rn.utcnow() + timedelta(minutes=31))
    alerts = [a for a in r.alerter.sent if a["title"].startswith("Order errors")]
    assert len(alerts) == 2 and "2 more pass(es)" in alerts[1]["message"]


async def test_alert_text_and_status_carry_no_secrets(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "FAKEPEMFAKEPEMFAKE")
    r = env.runner
    await r._handle_write_report(OUT + "place_failed order-id-77 key FAKEPEMFAKEPEMFAKE\n", "demo")
    blob = repr(r.alerter.sent) + (await env.client.get("/api/status", headers=env.auth)).text
    assert "FAKEPEMFAKEPEMFAKE" not in blob and "order-id-77" not in blob


# ============================================================ (b) gate + maker exposure together
async def test_total_ceiling_counts_maker_exposure_in_other_series(demo_env):
    # cash $100, other series hold $140: ceiling 0.5 x (100 + 140) = 120 < 140 + 3.12, though KXDEMOA is empty
    demo_env.trader.cash_cents = 10000
    demo_env.trader.positions = [{"ticker": "KXDEMOB-26OCT19-B1", "market_exposure_dollars": "70.00"},
                                 {"ticker": "KXDEMOC-26OCT19-B1", "market_exposure_dollars": "-70.00"}]
    await arb_on(demo_env)
    await demo_env.runner.arb_scan()
    assert demo_env.trader.orders == []
    assert (await last_opps(demo_env))[0]["executed"].startswith("skipped: gate")


async def test_series_cap_is_per_series_not_total(demo_env):
    # KXDEMOB holds $18 (above 35% of the ceiling) but KXDEMOA is empty, so a KXDEMOA set still goes through
    demo_env.trader.cash_cents = 10000
    demo_env.trader.positions = [{"ticker": "KXDEMOB-26OCT19-B1", "market_exposure_dollars": "18.00"}]
    await arb_on(demo_env)
    await demo_env.runner.arb_scan()
    assert len(demo_env.trader.orders) == 3
    assert (await last_opps(demo_env))[0]["executed"].startswith("FILLED")


async def test_series_cap_blocks_when_the_maker_already_holds_the_series(demo_env):
    demo_env.trader.cash_cents = 10000
    demo_env.trader.positions = [{"ticker": "KXDEMOA-26OCT19-B1", "market_exposure_dollars": "18.00"}]
    await arb_on(demo_env)
    await demo_env.runner.arb_scan()
    assert demo_env.trader.orders == []
    assert (await last_opps(demo_env))[0]["executed"].startswith("skipped: gate series cap KXDEMOA")


async def test_scan_cap_limits_one_scan_to_one_set(demo_env):
    demo_env.public.events = 2
    await arb_on(demo_env, arb_scan_cap=4)                  # one $3.12 set per scan
    await demo_env.runner.arb_scan()
    assert len(demo_env.trader.orders) == 3
    docs = await last_opps(demo_env, 2)
    assert sorted(d["executed"].split(" ")[0] for d in docs) == ["FILLED", "skipped:"]
    assert any(d["executed"].startswith("skipped: gate scan cap") for d in docs)


async def test_day_spend_survives_a_simulated_restart(demo_env):
    await arb_on(demo_env, arb_day_cap=7)
    r = demo_env.runner
    await r.arb_scan()
    await r.arb_scan()                                       # 6.24 spent today
    assert len(demo_env.trader.orders) == 6
    r._arb_day.clear()                                       # restart / leadership change: memory is gone
    await r.arb_scan()
    assert len(demo_env.trader.orders) == 6                  # rebuilt from the store: 6.24 + 3.12 > 7
    assert (await last_opps(demo_env))[0]["executed"].startswith("skipped: gate day cap")
    assert r._arb_day["demo-execute"]["amount"] == pytest.approx(6.24)


async def test_live_arb_spend_does_not_count_in_the_demo_day_total(demo_env):
    await demo_env.store.add_arb_opportunity({"event": "KXDEMOA-X", "kind": "NO", "contracts": 2, "profit": 1,
                                              "legs": [], "sum_best": 1.5, "cost": 29.0, "mode": "live",
                                              "executed": "FILLED {}", "ts": datetime.now(timezone.utc)})
    await arb_on(demo_env)
    await demo_env.runner.arb_scan()
    assert len(demo_env.trader.orders) == 3


# ============================================================ (c) refusals through a whole scan
async def test_refused_when_the_maker_is_live(demo_env):
    await arb_on(demo_env)
    demo_env.runner.mode = "live"
    await demo_env.runner.arb_scan()
    assert demo_env.trader.orders == [] and "demo" not in demo_env.factory_modes
    assert (await last_opps(demo_env))[0]["mode"] == "log-only"


async def test_refused_with_live_keys_reused_as_demo_keys(demo_env, monkeypatch):
    monkeypatch.setenv("KALSHI_PROD_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_PROD_PRIVATE_KEY", "not-a-real-key")
    await arb_on(demo_env)
    await demo_env.runner.arb_scan()
    assert demo_env.trader.orders == [] and demo_env.factory_modes == []


@pytest.mark.parametrize("url", [PROD_URL, None, "", "https://evil.example/demo.kalshi.co",
                                 "https://external-api.kalshi.com/demo"])
async def test_refused_on_a_production_or_unknown_base_url(demo_env, url):
    await arb_on(demo_env)
    demo_env.trader.base_url = url
    await demo_env.runner.arb_scan()
    assert demo_env.trader.orders == []


async def test_refused_when_not_leader(demo_env):
    await arb_on(demo_env)
    demo_env.runner._leader_ready = False
    res = await demo_env.runner.arb_scan()
    assert res["scanned"] is False and demo_env.trader.orders == []


async def test_refused_with_the_arb_kill_switch_and_arb_is_turned_off(demo_env):
    await arb_on(demo_env)
    demo_env.runner.arb_kill_path.write_text('{"reason": "partial fill / legging risk"}')
    res = await demo_env.runner.arb_scan()
    assert res["scanned"] is False and demo_env.trader.orders == []
    assert demo_env.runner.arb_desired == "stopped"


async def test_refused_when_arb_is_stopped(demo_env):
    await arb_on(demo_env)
    demo_env.runner.arb_desired = "stopped"
    res = await demo_env.runner.arb_scan()
    assert res == {"scanned": False, "reason": "stopped"} and demo_env.trader.orders == []


async def test_refused_after_the_whole_bot_is_stopped_via_the_api(demo_env):
    await arb_on(demo_env)
    await demo_env.client.post("/api/bot/stop", headers=demo_env.auth)
    res = await demo_env.runner.arb_scan()
    assert res["scanned"] is False and demo_env.trader.orders == []


async def test_legging_kill_switch_still_trips_on_a_partial_set(demo_env):
    await arb_on(demo_env)
    demo_env.trader.fill_ratio = 0.5
    await demo_env.runner.arb_scan()
    r = demo_env.runner
    assert r.arb_kill_switch() and r.arb_desired == "stopped"
    assert len(demo_env.trader.orders) == 1                  # the set stopped at the first partial leg
    assert any("legging" in a["title"] for a in r.alerter.sent)
    assert (await demo_env.store.get_state())["arb_desired"] == "stopped"
    assert (await last_opps(demo_env))[0]["executed"].startswith("PARTIAL")


async def test_legging_kill_switch_trips_on_an_order_error_mid_set(demo_env):
    await arb_on(demo_env)
    demo_env.trader.fail_after["create_order"] = 2           # the second leg errors
    await demo_env.runner.arb_scan()
    r = demo_env.runner
    assert r.arb_kill_switch() and r.arb_desired == "stopped" and len(demo_env.trader.orders) == 1
    assert (await last_opps(demo_env))[0]["executed"] == "ERROR HttpError"
