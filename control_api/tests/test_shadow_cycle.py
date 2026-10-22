"""Shadow mode: one cycle's episode lifecycle (services/shadow.run_cycle) and the runner + API path end to end.

Fixtures use KXDEMO* tickers and a fake strategy module; no strategy logic is written here.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.runner import Runner
from control_api.services import shadow as sh

NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
T1, T2 = "KXDEMOA-26OCT17-B1", "KXDEMOA-26OCT17-B2"
CLOSE = NOW + timedelta(hours=6)


@dataclass(frozen=True)
class Quote:
    ticker: str
    side: str
    price: float
    count: float
    book_side: str
    order_price: float
    cell: tuple

    @property
    def cost(self) -> float:
        return self.count * self.price


def yes_quote(ticker=T1, price=0.4, count=10.0):
    return Quote(ticker, "yes", price, count, "bid", price, (0.1, 0.2, 1, 2, "yes"))


class FakeStrategy:
    """Returns whatever the test sets; records each call's arguments."""
    def __init__(self, quotes=None, raises=False):
        self.quotes = list(quotes or [])
        self.raises = raises
        self.calls = []

    def desired_quotes(self, markets, books, now, max_order_dollars, budget):
        self.calls.append({"tickers": [m["ticker"] for m in markets], "books": dict(books), "now": now,
                           "max_order": max_order_dollars, "budget": budget})
        if self.raises:
            raise RuntimeError("strategy broke")
        tickers = {m["ticker"] for m in markets}
        return [q for q in self.quotes if q.ticker in tickers]


class FakeMD:
    """Same four methods as ReadOnlyMarketData, from in-memory data."""
    def __init__(self, close=CLOSE):
        self.mkts = {"KXDEMOA": [{"ticker": T1, "close_time": close.isoformat()},
                                 {"ticker": T2, "close_time": close.isoformat()}]}
        self.books = {T1: {"yes_dollars": [["0.4000", "120"]], "no_dollars": [["0.5500", "30"]]},
                      T2: {"yes_dollars": [["0.3000", "5"]]}}
        self.trade_rows: dict[str, list] = {}
        self.results: dict[str, dict] = {}
        self.fail = set()
        self.calls = []

    def markets(self, series, status, min_close_ts=None):
        self.calls.append(("markets", series))
        if "markets" in self.fail:
            raise ConnectionError("down")
        return list(self.mkts.get(series, [])), False

    def orderbook(self, ticker, depth=None):
        self.calls.append(("orderbook", ticker))
        return self.books.get(ticker, {})

    def market(self, ticker):
        self.calls.append(("market", ticker))
        if "market" in self.fail:
            raise ConnectionError("down")
        return self.results.get(ticker, {"ticker": ticker, "result": ""})

    def trades(self, ticker, min_ts, max_ts):
        self.calls.append(("trades", ticker, min_ts, max_ts))
        if "trades" in self.fail:
            raise ConnectionError("down")
        return list(self.trade_rows.get(ticker, [])), False


def inp(**kw):
    base = dict(series=["KXDEMOA"], run_budget=40.0, max_order=15.0, now=NOW)
    base.update(kw)
    return sh.CycleInput(**base)


def trade(ts, yes_price, count, taker="no"):
    return {"trade_id": f"t{ts.timestamp()}{yes_price}", "created_time": ts.isoformat(),
            "yes_price_dollars": f"{yes_price:.4f}", "count_fp": str(count), "taker_side": taker}


# ---------------------------------------------------------------- run_cycle
def test_new_episode_joins_behind_the_whole_level():
    md, st = FakeMD(), FakeStrategy([yes_quote()])
    res = sh.run_cycle(md, st, inp())
    assert len(res.upserts) == 1
    ep = res.upserts[0]
    want = hashlib.sha256(f"{T1}|bid|0.4000|{NOW.isoformat()}".encode()).hexdigest()
    assert ep["ep_id"] == want and ep["env"] == "prod" and ep["series"] == "KXDEMOA"
    assert ep["q0"] == 120 and ep["level_min"] == 120 and ep["n_obs"] == 1
    assert ep["end_ts"] is None and ep["trades_read"] is False and ep["fill"] is None and ep["result"] is None
    assert ep["cost"] == pytest.approx(4.0) and ep["close_time"] == CLOSE and ep["cell"] == [0.1, 0.2, 1, 2, "yes"]
    assert res.pass_doc["new_eps"] == 1 and res.pass_doc["ok"] is True and res.pass_doc["books"] == 2


def test_budget_split_matches_the_public_settings():
    st = FakeStrategy()
    sh.run_cycle(FakeMD(), st, inp(series=["KXDEMOA", "KXDEMOB"], run_budget=40.0, max_order=15.0))
    assert st.calls[0]["budget"] == 20.0 and st.calls[0]["max_order"] == 15.0
    st2 = FakeStrategy()
    sh.run_cycle(FakeMD(), st2, inp(series=["KXDEMOA", "KXDEMOB"], run_budget=20.0, max_order=15.0))
    assert st2.calls[0]["max_order"] == 10.0                    # the per-series share is smaller


def test_unchanged_key_is_the_same_episode_and_tracks_the_level():
    md, st = FakeMD(), FakeStrategy([yes_quote()])
    ep = sh.run_cycle(md, st, inp()).upserts[0]
    md.books[T1] = {"yes_dollars": [["0.4000", "80"]]}
    later = NOW + timedelta(minutes=10)
    res = sh.run_cycle(md, st, inp(open_eps=[ep], now=later))
    [u] = res.upserts
    assert u["ep_id"] == ep["ep_id"] and u["level_min"] == 80 and u["level_last"] == 80 and u["n_obs"] == 2
    assert u["last_seen_ts"] == later and u["end_ts"] is None and res.pass_doc["new_eps"] == 0


def test_requote_ends_the_old_episode_and_starts_a_new_one():
    md, st = FakeMD(), FakeStrategy([yes_quote()])
    ep = sh.run_cycle(md, st, inp()).upserts[0]
    md.books[T1] = {"yes_dollars": [["0.4100", "7"]]}
    st.quotes = [yes_quote(price=0.41)]
    later = NOW + timedelta(minutes=10)
    res = sh.run_cycle(md, st, inp(open_eps=[ep], now=later))
    by = {u["ep_id"]: u for u in res.upserts}
    assert by[ep["ep_id"]]["end_reason"] == "cancel" and by[ep["ep_id"]]["end_ts"] == later
    [new] = [u for k, u in by.items() if k != ep["ep_id"]]
    assert new["order_price"] == 0.41 and new["q0"] == 7 and new["join_ts"] == later


def test_closed_market_ends_closed_and_failed_series_is_left_alone():
    md, st = FakeMD(), FakeStrategy([yes_quote()])
    ep = sh.run_cycle(md, st, inp()).upserts[0]
    later = NOW + timedelta(minutes=10)
    md.fail.add("markets")                                      # listing failed: never "cancel" on missing data
    res = sh.run_cycle(md, st, inp(open_eps=[ep], now=later))
    assert res.upserts == [] and res.pass_doc["ok"] is False and res.pass_doc["errors"] == ["ConnectionError"]
    md.fail.clear()
    md.mkts["KXDEMOA"] = [m for m in md.mkts["KXDEMOA"] if m["ticker"] != T1]
    [u] = sh.run_cycle(md, st, inp(open_eps=[ep], now=later)).upserts
    assert u["end_reason"] == "closed"


def test_strategy_error_leaves_episodes_open():
    md = FakeMD()
    ep = sh.run_cycle(md, FakeStrategy([yes_quote()]), inp()).upserts[0]
    res = sh.run_cycle(md, FakeStrategy(raises=True), inp(open_eps=[ep], now=NOW + timedelta(minutes=5)))
    assert res.upserts == [] and res.pass_doc["errors"] == ["RuntimeError"]


def test_stale_and_unconfigured_series_end_stale():
    md, st = FakeMD(), FakeStrategy([yes_quote()])
    ep = sh.run_cycle(md, st, inp()).upserts[0]
    res = sh.run_cycle(md, st, inp(open_eps=[ep], now=NOW + timedelta(hours=2)))
    old = [u for u in res.upserts if u["ep_id"] == ep["ep_id"]][0]
    assert old["end_reason"] == "stale" and old["end_ts"] == NOW          # ended where it was last seen
    res = sh.run_cycle(md, st, inp(series=["KXDEMOB"], open_eps=[ep], now=NOW + timedelta(minutes=5)))
    assert res.upserts[0]["end_reason"] == "stale"


def test_trades_read_once_with_fill_scenarios_and_through():
    md = FakeMD()
    ep = sh.run_cycle(md, FakeStrategy([yes_quote(count=10)]), inp()).upserts[0]
    ep = dict(ep, end_ts=NOW + timedelta(minutes=20), end_reason="cancel", level_min=100)
    md.trade_rows[T1] = [trade(NOW + timedelta(minutes=1), 0.40, 115),
                         trade(NOW + timedelta(minutes=2), 0.40, 2, taker="yes"),      # wrong side: ignored
                         trade(NOW - timedelta(minutes=1), 0.40, 500)]                  # before join: ignored
    [u] = sh.run_cycle(md, FakeStrategy(), inp(ended_unread=[ep], now=NOW + timedelta(minutes=30))).upserts
    assert u["trades_read"] is True and u["traded_vol"] == 115 and u["through"] is False
    assert u["fill"] == {"lo": 0.0, "mid": 5.0, "hi": 10.0}                # q0 120, a_min 100, a_mid 110
    md.trade_rows[T1] = [trade(NOW + timedelta(minutes=3), 0.38, 1)]
    [u] = sh.run_cycle(md, FakeStrategy(), inp(ended_unread=[ep], now=NOW + timedelta(minutes=30))).upserts
    # 2A-3 F4: lo = max(0, at-price 0 + through 1 - q0 120) = 0, hi = the full count, mid halfway
    assert u["through"] is True and u["fill"] == {"lo": 0.0, "mid": 5.0, "hi": 10.0}
    assert u["through_ts"] == NOW + timedelta(minutes=3) and u["through_vol"] == 1


def test_settlement_scores_with_the_model():
    md = FakeMD()
    ep = sh.run_cycle(md, FakeStrategy([yes_quote(count=10)]), inp()).upserts[0]
    ep = dict(ep, end_ts=NOW + timedelta(minutes=20), end_reason="cancel", trades_read=True,
              fill={"lo": 0.0, "mid": 5.0, "hi": 10.0})
    md.results[T1] = {"ticker": T1, "result": "yes"}
    after = CLOSE + timedelta(hours=1)
    [u] = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=after)).upserts
    assert u["result"] == "yes" and u["settled_ts"] == CLOSE
    assert u["pnl"]["zero"]["mid"] == pytest.approx(5 * 0.6)
    assert u["pnl"]["maker"]["mid"] < u["pnl"]["zero"]["mid"]
    md.results[T1] = {"ticker": T1, "result": "", "status": "closed"}      # unresolved: only the check stamp
    [u] = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=after)).upserts
    assert u["result"] is None and u["settle_checked_ts"] == after


def test_daily_cap_and_read_caps_truncate(monkeypatch):
    md, st = FakeMD(), FakeStrategy([yes_quote(), yes_quote(T2, price=0.3)])
    res = sh.run_cycle(md, st, inp(new_today=sh.SHADOW_DAILY_NEW_CAP - 1))
    assert res.pass_doc["new_eps"] == 1 and res.pass_doc["truncated"] is True
    monkeypatch.setattr(sh, "SHADOW_MAX_BOOKS", 1)
    res = sh.run_cycle(FakeMD(), FakeStrategy(), inp())
    assert res.pass_doc["books"] == 1 and res.pass_doc["truncated"] is True


def test_deadline_raises_so_nothing_partial_is_returned():
    with pytest.raises(sh.ShadowDeadline):
        sh.run_cycle(FakeMD(), FakeStrategy([yes_quote()]), inp(), deadline=0.0)


def test_installed_standin_records_nothing():
    from control_api.services import botfiles
    if not botfiles.is_standin():
        pytest.skip("private strategy overlaid")
    res = sh.run_cycle(FakeMD(), botfiles.strategy(), inp(standin=True))
    assert res.upserts == [] and res.pass_doc["quotes"] == 0


# ---------------------------------------------------------------- runner + API
class Alerter:
    async def send(self, *a, **k):
        return True


def use_strategy(monkeypatch, r, strategy):
    """The installed strategy module, with only its pure desired_quotes swapped for the fake (tests only)."""
    from control_api.services import botfiles
    monkeypatch.setattr(botfiles.strategy(), "desired_quotes", strategy.desired_quotes)
    r.config = {**r.config, "series": ["KXDEMOA"], "run_budget": 40, "max_order": 15}


def live_md():
    """FakeMD whose markets close in the future of the real clock (the runner uses the real time)."""
    return FakeMD(close=datetime.now(timezone.utc) + timedelta(hours=6))


async def leader_runner(settings, store, md, monkeypatch, *, strategy=None):
    settings.shadow_enabled = True
    r = Runner(settings, store, kalshi_factory=lambda m: (_ for _ in ()).throw(AssertionError("trading factory")),
               alerter=Alerter(), shadow_factory=lambda: md)
    await r.rehydrate()
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    if strategy is not None:
        use_strategy(monkeypatch, r, strategy)
    return r


@pytest.mark.asyncio
async def test_runner_cycle_writes_episodes_pass_and_summary(settings, bot_paths, monkeypatch):
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["new_eps"] == 1
    assert len(await store.shadow_open_episodes()) == 1
    [p] = await store.shadow_recent_passes(5)
    assert p["ok"] is True and p["new_eps"] == 1
    summary = await store.get_shadow_summary()
    assert summary["status"]["last_pass_ts"] is not None and summary["headline"]["peak_capital"] == pytest.approx(4)
    await r.shutdown()


@pytest.mark.asyncio
async def test_runner_turning_off_ends_open_episodes(settings, bot_paths, monkeypatch):
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    await r.shadow_cycle()
    r.cloud["shadow_enabled"] = False
    assert (await r.shadow_cycle())["reason"] == "disabled"
    assert await store.shadow_open_episodes() == []
    [ep] = await store.shadow_recent(5, "ended")
    assert ep["end_reason"] == "shadow_off"
    await r.shutdown()


@pytest.mark.asyncio
async def test_api_summary_and_episodes(env, monkeypatch):
    r = env.runner
    st = FakeStrategy([yes_quote()])
    r.shadow_factory = live_md
    r.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    use_strategy(monkeypatch, r, st)
    assert (await env.client.get("/api/shadow/summary")).status_code == 401
    await r.shadow_cycle()
    s = (await env.client.get("/api/shadow/summary", headers=env.auth)).json()
    assert s["enabled"] is True and "headline_zero_fee" in s and s["fee_mode"] == "maker"
    assert s["status"]["last_pass_ts"] is not None
    e = (await env.client.get("/api/shadow/episodes?state=open&limit=10", headers=env.auth)).json()
    assert len(e["episodes"]) == 1 and "_id" not in e["episodes"][0]
    assert e["episodes"][0]["ticker"] == T1 and isinstance(e["episodes"][0]["join_ts"], str)
    bad = await env.client.get("/api/shadow/episodes?state=nope", headers=env.auth)
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_api_summary_off_or_store_down_never_errors(env, monkeypatch):
    s = (await env.client.get("/api/shadow/summary", headers=env.auth)).json()
    assert s["enabled"] is False and s["status"]["too_early"] is True and s["by_series"] == []

    async def boom():
        raise RuntimeError("atlas down")
    monkeypatch.setattr(env.store, "get_shadow_summary", boom)
    resp = await env.client.get("/api/shadow/summary", headers=env.auth)
    assert resp.status_code == 200 and resp.json()["headline"]["pnl"]["mid"] == 0


# ---------------------------------------------------------------- security review SH-1 / SH-3
def _ended(md, **kw):
    ep = sh.run_cycle(md, FakeStrategy([yes_quote(count=10)]), inp()).upserts[0]
    return dict(ep, end_ts=NOW + timedelta(minutes=20), end_reason="cancel", **kw)


def test_finalized_market_without_result_is_void_and_unscored():
    md = FakeMD()
    ep = _ended(md, trades_read=True, fill={"lo": 0.0, "mid": 5.0, "hi": 10.0})
    md.results[T1] = {"ticker": T1, "result": "", "status": "finalized"}
    res = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=CLOSE + timedelta(hours=1)))
    [u] = res.upserts
    assert u["result"] == "void" and u["pnl"] is None and u["settled_ts"] == CLOSE
    assert res.pass_doc["voided"] == 1 and res.pass_doc["settled"] == 0
    from control_api.services import shadow_model as sm
    assert sm.summarize([u], CLOSE + timedelta(hours=1))["status"]["settled_episodes"] == 0


def test_long_unresolved_market_becomes_void_after_a_week():
    md = FakeMD()
    ep = _ended(md)
    md.results[T1] = {"ticker": T1, "result": "", "status": "closed"}
    [u] = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=CLOSE + timedelta(days=6))).upserts
    assert u["result"] is None
    [u] = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=CLOSE + timedelta(days=8))).upserts
    assert u["result"] == "void" and u["pnl"] is None


def test_failed_settle_read_still_rotates_the_queue():
    md = FakeMD()
    ep = _ended(md)
    md.fail.add("market")
    after = CLOSE + timedelta(hours=1)
    res = sh.run_cycle(md, FakeStrategy(), inp(unsettled=[ep], now=after))
    [u] = res.upserts
    assert u["settle_checked_ts"] == after and u["result"] is None and res.pass_doc["errors"] == ["ConnectionError"]


def test_trade_reads_give_up_after_three_failures():
    md = FakeMD()
    ep = _ended(md)
    md.fail.add("trades")
    later = NOW + timedelta(minutes=30)
    for attempt in (1, 2):
        [ep] = sh.run_cycle(md, FakeStrategy(), inp(ended_unread=[ep], now=later)).upserts
        assert ep["trade_attempts"] == attempt and ep["trades_read"] is False
    [ep] = sh.run_cycle(md, FakeStrategy(), inp(ended_unread=[ep], now=later)).upserts
    assert ep["trade_attempts"] == 3 and ep["trades_read"] is True and ep["fill"] is None
    assert ep["truncated"] is True and ep["trade_failed"] is True


# ---------------------------------------------------------------- security review SH-2 / SH-4 (runner)
@pytest.mark.asyncio
async def test_summary_uses_a_projection_and_refreshes_at_most_every_30_min(settings, bot_paths, monkeypatch):
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    seen = []
    real = store.shadow_settled

    async def spy(since, **kw):
        seen.append(kw)
        return await real(since, **kw)
    monkeypatch.setattr(store, "shadow_settled", spy)
    await r.shadow_cycle()
    assert seen == [{"fields": sh.SUMMARY_FIELDS, "limit": sh.SHADOW_SUMMARY_MAX_ROWS, "scored_only": True}]
    first = r._shadow_summary_utc
    await r.shadow_cycle()                                  # nothing settled, < 30 min: no refresh
    assert len(seen) == 1 and r._shadow_summary_utc == first
    r._shadow_summary_utc = first - sh.SHADOW_SUMMARY_EVERY
    await r.shadow_cycle()
    assert len(seen) == 2
    assert r._shadow_summary_due(first, {"settled": 1})     # something settled: refresh at once
    await r.shutdown()


@pytest.mark.asyncio
async def test_lease_lost_mid_cycle_writes_nothing(settings, bot_paths, monkeypatch):
    store = MemoryStore()
    role = {"v": "leader"}

    class Demoting(FakeMD):
        def markets(self, *a, **k):
            role["v"] = "standby"                           # the lease is lost while the worker reads
            return super().markets(*a, **k)
    md = Demoting(close=datetime.now(timezone.utc) + timedelta(hours=6))
    r = await leader_runner(settings, store, md, monkeypatch, strategy=FakeStrategy([yes_quote()]))
    monkeypatch.setattr(r, "role", lambda: role["v"])
    out = await r.shadow_cycle()
    assert out == {"ran": False, "reason": "standby"}
    assert await store.shadow_recent(10) == [] and await store.shadow_recent_passes(5) == []
    assert await store.get_shadow_summary() is None
    await r.shutdown()
