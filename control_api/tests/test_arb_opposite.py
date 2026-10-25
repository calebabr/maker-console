"""The opposite-set gate: a demo-execute or live arbitrage set is skipped when its event already holds an EXECUTED
set of the opposite kind (Kalshi nets opposite YES/NO positions into $1 pairs). Always on. State per execution kind,
rebuilt from the store's executed opportunities after a restart or leadership change. No network: fakes only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as rn
from control_api.tests.test_arb_demo_execute import DemoTrader, TwoEventPublic

pytestmark = pytest.mark.asyncio

EV = "KXDEMOA-26OCT20"
NO_BOOK = {"yes_dollars": [["0.50", "100"]], "no_dollars": []}        # YES bids sum 1.5: a NO set
YES_BOOK = {"yes_dollars": [], "no_dollars": [["0.70", "100"]]}       # YES asks sum 0.9: a YES set


class Public(TwoEventPublic):
    def __init__(self):
        super().__init__(events=1)
        self.book = NO_BOOK

    def get_orderbook(self, ticker, depth=None):
        return {"orderbook_fp": self.book}


@pytest.fixture
def oenv(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "not-a-real-key")
    env.public = Public()
    env.runner.public_factory = lambda e: env.public
    env.trader = DemoTrader()
    env.runner.kalshi_factory = lambda mode: env.trader
    env.runner.config = {**env.runner.config, "arb": {"arb_series": ["KXDEMOA"], "arb_min_profit": 0.01,
                                                      "arb_max_contracts": 2}}
    return env


async def _on(env):
    r = await env.client.put("/api/arb", json={"enabled": True, "arb_demo_execute": True}, headers=await env.h2fa())
    assert r.status_code == 200, r.text


async def _seed(env, kind, executed="FILLED {}", event=EV, mode="demo-execute", ago=5):
    await env.store.add_arb_opportunity({"event": event, "kind": kind, "contracts": 1, "profit": 0.01,
                                         "cost": 0.5, "legs": [], "sum_best": 1.0, "executed": executed,
                                         "mode": mode, "ts": datetime.now(timezone.utc) - timedelta(seconds=ago)})


async def _last(env):
    return (await env.store.list_arb_opportunities(limit=1))[0]["executed"]


async def test_opposite_set_is_blocked(oenv):
    await _seed(oenv, "YES")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert await _last(oenv) == "skipped: gate opposite set held on event" and oenv.trader.orders == []


async def test_a_partial_opposite_set_also_blocks(oenv):
    await _seed(oenv, "YES", executed="PARTIAL {}")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert await _last(oenv) == "skipped: gate opposite set held on event"


async def test_same_kind_is_allowed(oenv):
    await _seed(oenv, "NO")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert (await _last(oenv)).startswith("FILLED") and len(oenv.trader.orders) == 3


async def test_another_event_is_allowed(oenv):
    await _seed(oenv, "YES", event="KXDEMOA-26OCT19")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert (await _last(oenv)).startswith("FILLED")


@pytest.mark.parametrize("executed", ["skipped: gate day cap 45.00 + 3.12 > 45.00", "log-only",
                                      "skipped: gate opposite set held on event"])
async def test_a_skipped_or_log_only_set_never_blocks(oenv, executed):
    await _seed(oenv, "YES", executed=executed)
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert (await _last(oenv)).startswith("FILLED")


async def test_another_execution_kind_does_not_block(oenv):
    await _seed(oenv, "YES", mode="live")                                # the production account, not demo
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert (await _last(oenv)).startswith("FILLED")


async def test_in_memory_then_rebuilt_after_a_restart(oenv):
    await _on(oenv)
    r = oenv.runner
    await r.arb_scan()                                                   # a NO set fills (memory + store)
    assert (await _last(oenv)).startswith("FILLED")
    oenv.public.book = YES_BOOK                                          # now a YES set on the same event
    await r.arb_scan()
    assert await _last(oenv) == "skipped: gate opposite set held on event"
    r._arb_held.clear()                                                  # what a restart leaves in memory
    r._arb_skip_seen.clear()
    await r.arb_scan()
    assert await _last(oenv) == "skipped: gate opposite set held on event"
    assert len(oenv.trader.orders) == 3


async def test_leadership_change_rebuild_keeps_memory(oenv):
    await _on(oenv)
    r = oenv.runner
    st = await r._arb_held_state("demo-execute")
    st.setdefault(EV, set()).add("YES")                                  # memory knows a set the store lacks
    r.lease.transitions += 1
    r._ready_mark = (r.lease.generation, r.lease.transitions)
    assert (await r._arb_held_state("demo-execute"))[EV] == {"YES"}


async def test_store_down_fails_closed(oenv, monkeypatch):
    await _on(oenv)

    async def boom(*a, **kw):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(oenv.store, "list_arb_opportunities", boom)
    await oenv.runner.arb_scan()
    assert oenv.runner.arb_recent[0]["executed"] == "skipped: gate held sets unknown"
    assert oenv.trader.orders == []


async def test_truncated_read_fails_closed(oenv, monkeypatch):
    monkeypatch.setattr(rn, "ARB_HELD_READ_MAX", 2)
    for i in range(2):
        await _seed(oenv, "NO", event=f"KXDEMOA-OLD{i}")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert oenv.runner.arb_recent[0]["executed"] == "skipped: gate held sets unknown"


async def test_an_errored_set_blocks_the_opposite(oenv):
    await _seed(oenv, "YES", executed="ERROR RuntimeError")
    await _on(oenv)
    await oenv.runner.arb_scan()
    assert await _last(oenv) == "skipped: gate opposite set held on event"


async def test_an_error_mid_set_is_recorded_as_held(oenv, monkeypatch):
    await _on(oenv)
    r = oenv.runner

    def boom(**kw):
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(oenv.trader, "create_order", boom)
    await r.arb_scan()
    assert (await _last(oenv)).startswith("ERROR") and r._arb_held["demo-execute"]["events"][EV] == {"NO"}
