"""H2: the optional per-market held cap in the Shadow estimate (services/shadow.py run_cycle).

Synthetic episodes only (KXDEMO* tickers); the limit is a TEST value passed in (the real one is a private data
file, key `market_share_limit`, absent in the public repo = no market cap). A market's held cost (filled mid x
price of ended, unsettled episodes, both sides together) plus its resting bids may not exceed
limit x (max_open_frac x capital); a bid that does not fit is sized down in 0.01-contract steps (the strategy's floor), and one that cannot
fit a single contract is stored `capped` with cap_reason "market_cap" and never scored.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import timedelta

import pytest

from control_api.services import shadow as sh
from control_api.tests.test_shadow_reliability import NOW, BudgetStrategy, Quote, SimMD, book, trade

T1 = "KXDEMOA-26OCT19-T1"
T2 = "KXDEMOA-26OCT19-T2"
T3 = "KXDEMOB-26OCT19-T3"
CAPITAL, FRAC = 150.0, 0.5            # ceiling 75
LIMIT = 0.12                          # a test value only: cap 9.0 dollars per market
CAP = LIMIT * FRAC * CAPITAL


@pytest.fixture(autouse=True)
def _no_private_files(tmp_path, monkeypatch):
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee_schedule.json"))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(tmp_path / "absent_shadow_limits.json"))


class Sim:
    def __init__(self, md, strategy, *, series, market_limit="file", series_limit=None, run_budget=40.0,
                 max_order=15.0):
        self.md, self.st, self.series = md, strategy, series
        self.market_limit, self.series_limit = market_limit, series_limit
        self.run_budget, self.max_order = run_budget, max_order
        self.eps: dict[str, dict] = {}
        self.passes: list[dict] = []

    def cycle(self, now):
        eps = list(self.eps.values())
        inp = sh.CycleInput(
            series=self.series, run_budget=self.run_budget, max_order=self.max_order, now=now,
            open_eps=[e for e in eps if e.get("end_ts") is None],
            ended_unread=sorted([e for e in eps if e.get("end_ts") is not None and e.get("trades_read") is not True],
                                key=lambda e: e["end_ts"]),
            unsettled=[e for e in eps if e.get("result") is None and e.get("close_time") and e["close_time"] <= now],
            held=[e for e in eps if e.get("end_ts") is not None and e.get("result") is None],
            capital=CAPITAL, max_open_frac=FRAC, fee_schedule=None, series_share_limit=self.series_limit,
            market_share_limit=self.market_limit)
        res = sh.run_cycle(self.md, self.st, inp)
        for u in res.upserts:
            self.eps[u["ep_id"]] = {**self.eps.get(u["ep_id"], {}), **u}
        self.passes.append(res.pass_doc)
        return res

    def held(self, ticker=None) -> float:
        return sum(sh.held_cost(e) for e in self.eps.values() if ticker in (None, e["ticker"]))

    def resting(self, ticker=None) -> float:
        return sum(e["cost"] for e in self.eps.values()
                   if e.get("end_ts") is None and not e.get("capped") and ticker in (None, e["ticker"]))

    def live(self, ticker=None):
        return [e for e in self.eps.values() if e.get("end_ts") is None and not e.get("capped")
                and ticker in (None, e["ticker"])]


@dataclass
class FixedStrategy:
    """Wants exactly the quotes it is given (a list of (ticker, side, book_side, price, count)), whatever the
    budget: the market cap alone is what sizes them."""
    wants: list

    def desired_quotes(self, markets, books, now, max_order_dollars, budget):
        have = {m["ticker"] for m in markets}
        return [Quote(t, side, p, float(n), bs, p, ("c", 1)) for t, side, bs, p, n in self.wants if t in have]


def _md(*tickers_by_series):
    md = SimMD({s: ts for s, ts in tickers_by_series})
    for _s, ts in tickers_by_series:
        for t in ts:
            md.books[t] = book(0.5, 10)
    return md


def _held_ep(ticker, cost_dollars, *, price=0.5, side="yes", eid=None):
    n = cost_dollars / price
    return {"ep_id": eid or f"h-{ticker}-{side}", "ticker": ticker, "series": ticker.split("-")[0], "side": side,
            "price": price, "count": n, "end_ts": NOW, "result": None, "trades_read": True,
            "fill": {"lo": n, "mid": n, "hi": n}}


def _cycle(md, st, *, held=(), market_limit=LIMIT, series=("KXDEMOA",), now=NOW):
    return sh.run_cycle(md, st, sh.CycleInput(
        series=list(series), run_budget=40.0, max_order=15.0, now=now, held=list(held), capital=CAPITAL,
        max_open_frac=FRAC, fee_schedule=None, series_share_limit=None, market_share_limit=market_limit))


# ================================================================ the limit and its file
def test_absent_key_means_no_market_cap_and_identical_behaviour(tmp_path, monkeypatch):
    assert sh.load_market_limit() is None                               # no private file in the public repo
    f = tmp_path / "limits.json"
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(f))
    f.write_text(json.dumps({"series_share_limit": 0.4}))
    assert sh.load_market_limit() is None                               # the series key alone is no market cap
    f.write_text(json.dumps({"series_share_limit": 0.4, "market_share_limit": 0.12}))
    assert sh.load_market_limit() == 0.12 and sh.load_series_limit() == 0.4
    for bad in (json.dumps({"market_share_limit": 0}), json.dumps({"market_share_limit": 1.5}),
                json.dumps({"market_share_limit": True}), json.dumps({"market_share_limit": "0.12"}),
                json.dumps({"market_share_limit": -0.12}), json.dumps([0.12]), "not json", "[" * 5000 + "]" * 5000):
        f.write_text(bad)
        assert sh.load_market_limit() is None
    f.write_text(json.dumps({"market_share_limit": 0.12, "pad": "x" * sh.SHADOW_LIMITS_MAX_BYTES}))
    assert sh.load_market_limit() is None                               # too large

    # a heavy single-market ask: with the key absent the result equals an explicit "no cap", and no market fields
    f.write_text(json.dumps({"series_share_limit": 0.4}))
    st = FixedStrategy([(T1, "yes", "bid", 0.5, 60)])
    a = _cycle(_md(("KXDEMOA", [T1])), st, market_limit="file")
    b = _cycle(_md(("KXDEMOA", [T1])), st, market_limit=None)
    assert [(u["count"], u["capped"], u["cap_reason"]) for u in a.upserts] == \
        [(u["count"], u["capped"], u["cap_reason"]) for u in b.upserts] == [(60.0, False, None)]
    assert "market_capped" not in a.pass_doc and "market_capped" not in b.pass_doc


def test_run_cycle_reads_the_file_when_the_input_says_file(tmp_path, monkeypatch):
    f = tmp_path / "limits.json"
    f.write_text(json.dumps({"market_share_limit": LIMIT}))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(f))
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]), market_limit="file")
    assert res.upserts[0]["count"] == 18.0 and res.pass_doc["market_capped"] == [T1]


# ================================================================ sizing
def test_a_bid_over_the_cap_is_sized_down_in_hundredths():
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]))
    (ep,) = res.upserts
    assert ep["count"] == 18.0 and ep["cost"] == pytest.approx(9.0)    # 9.0 / 0.5
    assert ep["capped"] is False and ep["cap_reason"] is None
    assert res.pass_doc["market_capped"] == [T1]
    # a price that does not divide the room: floored to 0.01, never rounded up (9.0 / 0.7 = 12.857 -> 12.85)
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.7, 40)]))
    assert res.upserts[0]["count"] == pytest.approx(12.85) and res.upserts[0]["cost"] <= 9.0
    # exactly one contract of room is a quote; just under one is refused
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 40)]), held=[_held_ep(T1, 8.5)])
    assert res.upserts[0]["count"] == pytest.approx(1.0) and res.upserts[0]["capped"] is False
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 40)]), held=[_held_ep(T1, 8.51)])
    assert res.upserts[0]["capped"] is True and res.upserts[0]["cap_reason"] == "market_cap"
    # the pure rule
    assert sh.sized_to_room(40, 0.7, 9.0) == pytest.approx(12.85)
    assert sh.sized_to_room(40, 0.5, 0.49) == 0.0 and sh.sized_to_room(40, 0.95, 5.0 - 2.0) == pytest.approx(3.15)
    assert sh.sized_to_room(2, 0.5, 100.0) == 2.0 and sh.sized_to_room(5, 0.5, 0.0) == 0.0


def test_a_bid_that_fits_is_untouched():
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 14)]))
    assert res.upserts[0]["count"] == 14.0 and res.pass_doc["market_capped"] == []


def test_partly_used_market_is_sized_to_what_is_left():
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]),
                 held=[_held_ep(T1, 4.0)])
    (ep,) = res.upserts
    assert ep["count"] == 10.0 and ep["capped"] is False               # (9.0 - 4) / 0.5


def test_market_at_the_cap_gets_no_new_bid_and_is_recorded_capped():
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]),
                 held=[_held_ep(T1, 9.0)])
    (ep,) = res.upserts
    assert ep["capped"] is True and ep["cap_reason"] == "market_cap" and ep["count"] == 60.0
    assert ep["pnl"] is None and ep["trades_read"] is True and ep["fill"] == {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    assert res.pass_doc["capped_new"] == 1 and res.pass_doc["market_capped"] == [T1]
    # less than one contract of room is also refused
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]),
                 held=[_held_ep(T1, 8.8)])
    assert res.upserts[0]["capped"] is True and res.upserts[0]["cap_reason"] == "market_cap"


def test_other_markets_are_unaffected():
    st = FixedStrategy([(T1, "yes", "bid", 0.5, 30), (T2, "yes", "bid", 0.5, 12), (T3, "yes", "bid", 0.5, 12)])
    res = _cycle(_md(("KXDEMOA", [T1, T2]), ("KXDEMOB", [T3])), st, held=[_held_ep(T1, 9.0)],
                 series=("KXDEMOA", "KXDEMOB"))
    by = {u["ticker"]: u for u in res.upserts}
    assert by[T1]["capped"] is True and by[T1]["cap_reason"] == "market_cap"
    assert by[T2]["count"] == 12.0 and by[T2]["capped"] is False        # 6.0 fits under 9.0
    assert by[T3]["count"] == 12.0 and by[T3]["capped"] is False
    assert res.pass_doc["market_capped"] == [T1]


def test_both_sides_of_one_market_share_the_cap():
    st = FixedStrategy([(T1, "yes", "bid", 0.5, 10), (T1, "no", "bid", 0.4, 15)])
    res = _cycle(_md(("KXDEMOA", [T1])), st)
    live = [u for u in res.upserts if not u["capped"]]
    assert sum(u["cost"] for u in live) <= CAP + 1e-9
    by = {u["side"]: u for u in live}
    assert by["yes"]["count"] == 10.0                                   # 5.0 fits whole
    assert by["no"]["count"] == pytest.approx(10.0) and by["no"]["cost"] == pytest.approx(4.0)   # (9.0 - 5.0) / 0.4
    # held on the other side counts too
    res = _cycle(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 10)]),
                 held=[_held_ep(T1, 6.0, price=0.4, side="no")])
    assert res.upserts[0]["count"] == 6.0                               # 3.0 / 0.5


def test_a_market_cap_never_loosens_the_series_budget():
    """The cap only shrinks: a market with room is still bounded by the run budget and the open-exposure limits."""
    st = BudgetStrategy({T1: 0.5})
    res = sh.run_cycle(_md(("KXDEMOA", [T1])), st, sh.CycleInput(
        series=["KXDEMOA"], run_budget=3.0, max_order=15.0, now=NOW, capital=CAPITAL, max_open_frac=FRAC,
        fee_schedule=None, series_share_limit=None, market_share_limit=LIMIT))
    assert res.upserts[0]["cost"] <= 3.0 + 1e-9


# ================================================================ stability over passes
def test_two_identical_passes_do_not_cancel_or_replace():
    sim = Sim(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]), series=["KXDEMOA"],
              market_limit=LIMIT)
    sim.cycle(NOW)
    (first,) = sim.live()
    for k in range(1, 4):
        res = sim.cycle(NOW + timedelta(minutes=10 * k))
        assert res.pass_doc["new_eps"] == 0 and res.pass_doc["ended_eps"] == 0
        assert [e["ep_id"] for e in sim.live()] == [first["ep_id"]]
    assert sim.live()[0]["count"] == 18.0


def test_a_capped_market_stays_one_capped_episode_across_passes():
    sim = Sim(_md(("KXDEMOA", [T1])), FixedStrategy([(T1, "yes", "bid", 0.5, 60)]), series=["KXDEMOA"],
              market_limit=LIMIT)
    sim.eps["h"] = _held_ep(T1, 9.0)
    sim.cycle(NOW)
    sim.cycle(NOW + timedelta(minutes=10))
    capped = [e for e in sim.eps.values() if e.get("capped")]
    assert len(capped) == 1 and capped[0]["cap_reason"] == "market_cap"
    assert sim.passes[1]["new_eps"] == 0


def test_stacking_one_market_is_bounded():
    """The audit's falling-price case: each level is traded through, so every re-quote fills. Without the market
    cap one market stacks far past the cap; with it, held plus resting never exceeds it."""
    def run(limit):
        sim = Sim(SimMD({"KXDEMOA": [T1]}), BudgetStrategy({}), series=["KXDEMOA"], market_limit=limit)
        peaks = []
        for k in range(12):
            now = NOW + timedelta(minutes=10 * k)
            p = round(0.90 - 0.01 * k, 2)
            sim.st.prices[T1] = p
            sim.md.books[T1] = book(p, 5)
            sim.md.trade_rows.setdefault(T1, []).append(trade(now + timedelta(minutes=5), p - 0.01, 1000))
            sim.cycle(now)
            peaks.append(sim.held(T1) + sim.resting(T1))
        return sim, peaks

    _loose, peaks_loose = run(None)
    tight, peaks_tight = run(LIMIT)
    assert max(peaks_loose) > CAP * 2                                   # stacked far above the test cap
    assert max(peaks_tight) <= CAP + 1e-6
    assert any(e.get("cap_reason") == "market_cap" for e in tight.eps.values() if e.get("capped"))
    assert all(e["pnl"] is None for e in tight.eps.values() if e.get("capped"))


@pytest.mark.parametrize("seed", range(6))
def test_property_held_plus_resting_per_market_never_exceeds_the_cap(seed):
    rng = random.Random(seed)
    tickers = [T1, T2, T3]
    sim = Sim(SimMD({"KXDEMOA": [T1, T2], "KXDEMOB": [T3]}), BudgetStrategy({}), series=["KXDEMOA", "KXDEMOB"],
              market_limit=rng.choice([0.05, 0.08, 0.2]), run_budget=rng.choice([20.0, 40.0]), max_order=15.0)
    cap = sim.market_limit * FRAC * CAPITAL
    for k in range(25):
        now = NOW + timedelta(minutes=10 * k)
        for t in tickers:
            p = round(rng.uniform(0.55, 0.9), 2)
            sim.st.prices[t] = p
            sim.md.books[t] = book(p, rng.randint(1, 20))
            if rng.random() < 0.5:                                      # sometimes the level is traded through
                sim.md.trade_rows.setdefault(t, []).append(
                    trade(now + timedelta(minutes=rng.randint(1, 9)), p - 0.01, rng.randint(1, 400)))
        sim.cycle(now)
        for t in tickers:
            assert sim.held(t) + sim.resting(t) <= cap + sh.CAP_TOL + 1e-6, (seed, k, t)
    assert sim.passes[-1]["market_capped"] is not None
