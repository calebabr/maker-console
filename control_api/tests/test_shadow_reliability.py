"""Sprint 2A-3 shadow reliability fixes F1-F8 (services/shadow.py run_cycle + services/shadow_model.py summarize).

Synthetic episodes only (KXDEMO* tickers, fake series names); no strategy logic is written here. The cases mirror
the audit's three findings: re-quotes stacking fills on one market (F1), an all-win sample (F2/F3) and trade-through
fills that made lo = mid = hi (F4).
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow as sh
from control_api.services import shadow_model as m

UTC = timezone.utc
NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
FAR = NOW + timedelta(days=3)                      # markets close long after the simulated cycles


@pytest.fixture(autouse=True)
def _no_fee_file(tmp_path, monkeypatch):
    """Every test starts with no fee schedule (the public default), whatever an overlaid copy ships."""
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee_schedule.json"))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(tmp_path / "absent_shadow_limits.json"))


# ================================================================ fakes
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


class BudgetStrategy:
    """One yes bid per market at the price the test sets, sized like a budget-respecting strategy:
    floor(min(max_order, budget left) / price) contracts."""

    def __init__(self, prices: dict[str, float]):
        self.prices = prices
        self.calls: list[dict] = []

    def desired_quotes(self, markets, books, now, max_order_dollars, budget):
        self.calls.append({"max_order": max_order_dollars, "budget": budget})
        out, left = [], float(budget)
        for mk in markets:
            p = self.prices.get(mk["ticker"])
            if p is None:
                continue
            n = math.floor(min(max_order_dollars, left) / p + 1e-9)
            if n < 1:
                continue
            out.append(Quote(mk["ticker"], "yes", p, float(n), "bid", p, ("c", 1)))
            left -= n * p
        return out


class SimMD:
    """Same four read methods as ReadOnlyMarketData, over an in-memory market."""

    def __init__(self, series_markets: dict[str, list[str]], close=FAR):
        self.mkts = {s: [{"ticker": t, "close_time": close.isoformat()} for t in ts]
                     for s, ts in series_markets.items()}
        self.books: dict[str, dict] = {}
        self.trade_rows: dict[str, list] = {}
        self.results: dict[str, dict] = {}

    def markets(self, series, status, min_close_ts=None):
        return list(self.mkts.get(series, [])), False

    def orderbook(self, ticker, depth=None):
        return self.books.get(ticker, {})

    def market(self, ticker):
        return self.results.get(ticker, {"ticker": ticker, "result": ""})

    def trades(self, ticker, min_ts, max_ts):
        out = []
        for t in self.trade_rows.get(ticker, []):
            ts = datetime.fromisoformat(t["created_time"]).timestamp()
            if min_ts <= ts <= max_ts:
                out.append(t)
        return out, False


def trade(ts, yes_price, count, taker="no"):
    return {"trade_id": f"t{ts.timestamp()}-{yes_price}-{count}", "created_time": ts.isoformat(),
            "yes_price_dollars": f"{yes_price:.4f}", "count_fp": str(count), "taker_side": taker}


def book(price, size):
    return {"yes_dollars": [[f"{price:.4f}", str(size)]]}


class Sim:
    """Carries the episode store between cycles exactly as the runner's store reads would."""

    def __init__(self, md, strategy, *, series, capital=150.0, frac=0.5, run_budget=40.0, max_order=15.0,
                 series_limit=None):
        self.md, self.st, self.series_limit = md, strategy, series_limit
        self.series, self.capital, self.frac = series, capital, frac
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
            capital=self.capital, max_open_frac=self.frac, fee_schedule=None, series_share_limit=self.series_limit)
        res = sh.run_cycle(self.md, self.st, inp)
        for u in res.upserts:
            self.eps[u["ep_id"]] = {**self.eps.get(u["ep_id"], {}), **u}
        self.passes.append(res.pass_doc)
        return res

    def held(self) -> float:
        return sum(sh.held_cost(e) for e in self.eps.values())

    def filled_on(self, ticker):
        return [e for e in self.eps.values() if e["ticker"] == ticker and not e.get("capped")
                and (e.get("fill") or {}).get("mid", 0) > 0]


T1 = "KXDEMOA-26OCT19-T1"


def _falling_market(sim: Sim, cycles: int):
    """The audit's stacking case: the bid price falls one cent a cycle and each level is traded through, so every
    re-quote is a new episode that fills in full."""
    held = []
    for k in range(cycles):
        now = NOW + timedelta(minutes=10 * k)
        p = round(0.90 - 0.01 * k, 2)
        sim.st.prices[T1] = p
        sim.md.books[T1] = book(p, 5)
        sim.md.trade_rows.setdefault(T1, []).append(trade(now + timedelta(minutes=5), p - 0.01, 1000))
        sim.cycle(now)
        held.append(sim.held())
    return held


# ================================================================ F1 caps
def test_f1_stacking_reproduced_without_caps_and_bounded_with_them():
    loose = Sim(SimMD({"KXDEMOA": [T1]}), BudgetStrategy({}), series=["KXDEMOA"], capital=100000.0)
    held_loose = _falling_market(loose, 12)
    assert len(loose.filled_on(T1)) >= 10                       # one market, many stacked fills (the audit's F1)
    assert held_loose[-1] > 0.5 * 150                           # far above what a $150 account could hold

    tight = Sim(SimMD({"KXDEMOA": [T1]}), BudgetStrategy({}), series=["KXDEMOA"], capital=150.0, frac=0.5)
    held_tight = _falling_market(tight, 12)
    assert max(held_tight) <= 0.5 * 150 + 1e-6                  # never above max_open_frac x capital
    capped = [e for e in tight.eps.values() if e.get("capped")]
    assert capped and all(e["cap_reason"] == "open_cap" for e in capped)
    assert all(e["fill"] == {"lo": 0.0, "mid": 0.0, "hi": 0.0} and e["trades_read"] is True for e in capped)
    assert tight.passes[-1]["headroom"] < 40 and tight.passes[-1]["held_cost"] == pytest.approx(held_tight[-2])
    assert sum(p["capped_new"] for p in tight.passes) == len(capped)


def test_f1_series_cap_from_the_optional_limits_file(tmp_path, monkeypatch):
    sim = Sim(SimMD({"KXDEMOA": [T1]}), BudgetStrategy({}), series=["KXDEMOA"], capital=150.0, frac=0.5,
              series_limit=0.4)                                 # a test value (the real one is private)
    _falling_market(sim, 8)
    reasons = {e["cap_reason"] for e in sim.eps.values() if e.get("capped")}
    assert "series_cap" in reasons
    assert any(p["series_capped"] == ["KXDEMOA"] for p in sim.passes)
    # the file: absent (the public repo) -> no series cap
    assert sh.load_series_limit() is None
    f = tmp_path / "limits.json"
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(f))
    f.write_text(json.dumps({"series_share_limit": 0.4}))
    assert sh.load_series_limit() == 0.4
    for bad in (json.dumps({"series_share_limit": 0}), json.dumps({"series_share_limit": 1.5}),
                json.dumps({"series_share_limit": True}), json.dumps({"series_share_limit": "0.4"}),
                json.dumps({"other": 0.4}), json.dumps([0.4]), "not json", "[" * 5000 + "]" * 5000):
        f.write_text(bad)
        assert sh.load_series_limit() is None                   # invalid or out of range: no series cap
    f.write_text(json.dumps({"series_share_limit": 0.4, "pad": "x" * sh.SHADOW_LIMITS_MAX_BYTES}))
    assert sh.load_series_limit() is None                       # too large
    # run_cycle reads the file when the input says "file"
    f.write_text(json.dumps({"series_share_limit": 0.4}))
    held = [{"ep_id": "h", "ticker": "KXDEMOA-26OCT19-T9", "series": "KXDEMOA", "side": "yes", "price": 0.5,
             "count": 70, "end_ts": NOW, "result": None, "trades_read": True, "fill": {"lo": 70, "mid": 70, "hi": 70}}]
    md = SimMD({"KXDEMOA": [T1]})
    md.books[T1] = book(0.5, 1)
    res = sh.run_cycle(md, BudgetStrategy({T1: 0.5}), sh.CycleInput(series=["KXDEMOA"], run_budget=40, max_order=15,
                                                                    now=NOW, held=held, fee_schedule=None))
    assert res.pass_doc["series_capped"] == ["KXDEMOA"]       # 35 held >= 0.4 x 75
    assert res.upserts[0]["cap_reason"] == "series_cap"


def test_f1_no_cap_binding_means_no_change():
    """With room to spare the capped path is never taken: one strategy call per series, nothing capped."""
    st = BudgetStrategy({T1: 0.5})
    md = SimMD({"KXDEMOA": [T1]})
    md.books[T1] = book(0.5, 10)
    sim = Sim(md, st, series=["KXDEMOA"], capital=150.0)
    res = sim.cycle(NOW)
    assert len(st.calls) == 1 and st.calls[0]["budget"] == 40.0
    [ep] = res.upserts
    assert ep["capped"] is False and ep["cap_reason"] is None and ep["fee_mode"] == "maker"
    assert ep["interval"] == 10 and ep["through_vol"] is None
    assert res.pass_doc["capped_new"] == 0 and res.pass_doc["headroom"] == 40.0


def test_f1_capped_episode_is_tracked_and_converts_when_affordable():
    st = BudgetStrategy({T1: 0.5})
    md = SimMD({"KXDEMOA": [T1]})
    md.books[T1] = book(0.5, 10)
    held = [{"ep_id": "h1", "ticker": "KXDEMOA-26OCT19-T9", "series": "KXDEMOA", "side": "yes", "price": 0.9,
             "count": 100, "end_ts": NOW - timedelta(hours=1), "result": None, "trades_read": True,
             "fill": {"lo": 84.0, "mid": 84.0, "hi": 84.0}}]
    inp = dict(series=["KXDEMOA"], run_budget=40.0, max_order=15.0, capital=150.0, max_open_frac=0.5,
               fee_schedule=None)
    res = sh.run_cycle(md, st, sh.CycleInput(now=NOW, held=held, **inp))
    [cap] = res.upserts                                         # held 75.6 > ceiling 75: no headroom
    assert cap["capped"] is True and cap["cap_reason"] == "open_cap" and cap["end_ts"] is None
    # next cycle, still held: the same capped episode continues (no new one each cycle)
    res = sh.run_cycle(md, st, sh.CycleInput(now=NOW + timedelta(minutes=10), held=held, open_eps=[cap], **inp))
    assert [u["ep_id"] for u in res.upserts] == [cap["ep_id"]] and res.pass_doc["new_eps"] == 0
    # the held position settles away: the capped episode ends and a normal one starts at the same key
    res = sh.run_cycle(md, st, sh.CycleInput(now=NOW + timedelta(minutes=20), held=[], open_eps=[cap], **inp))
    by = {u["ep_id"]: u for u in res.upserts}
    assert by[cap["ep_id"]]["end_reason"] == "cancel"
    [new] = [u for k, u in by.items() if k != cap["ep_id"]]
    assert new["capped"] is False and new["trades_read"] is False


def test_f1_capped_episodes_are_never_scored():
    ep = {"ep_id": "c", "capped": True, "result": "yes", "side": "yes", "price": 0.9, "count": 10,
          "fill": {"lo": 0.0, "mid": 0.0, "hi": 0.0}}
    sh._maybe_score(ep)
    assert ep["pnl"] is None
    assert sh.held_cost(dict(ep, result=None, end_ts=NOW)) == 0.0


@pytest.mark.parametrize("seed", range(12))
def test_f1_property_held_never_exceeds_the_ceiling(seed):
    rng = random.Random(seed)
    tickers = {"KXDEMOA": [f"KXDEMOA-26OCT19-T{i}" for i in range(3)],
               "KXDEMOB": [f"KXDEMOB-26OCT19-T{i}" for i in range(3)]}
    capital = rng.choice([20.0, 60.0, 150.0, 400.0])
    frac = rng.choice([0.2, 0.5, 0.9])
    st = BudgetStrategy({})
    md = SimMD(tickers)
    sim = Sim(md, st, series=list(tickers), capital=capital, frac=frac, run_budget=rng.choice([10.0, 40.0, 200.0]),
              max_order=rng.choice([5.0, 15.0, 50.0]), series_limit=rng.choice([None, 0.4, 0.6]))
    for k in range(14):
        now = NOW + timedelta(minutes=10 * k)
        for ts in tickers.values():
            for t in ts:
                p = round(rng.uniform(0.80, 0.97), 2)
                st.prices[t] = p
                md.books[t] = book(p, rng.choice([0, 3, 40]))
                r = rng.random()
                if r < 0.4:                                    # trade-through
                    md.trade_rows.setdefault(t, []).append(trade(now + timedelta(minutes=3), p - 0.02,
                                                                 rng.choice([1, 50, 500])))
                elif r < 0.7:                                  # volume at the price
                    md.trade_rows.setdefault(t, []).append(trade(now + timedelta(minutes=4), p, rng.choice([5, 100])))
        sim.cycle(now)
        assert sim.held() <= frac * capital + 1e-6
        for p in sim.passes:
            assert p["headroom"] <= sim.run_budget + 1e-9


def test_capital_and_open_frac_inputs():
    assert sh.capital_of({}) == sh.SHADOW_CAPITAL_DEFAULT == 150.0
    assert sh.capital_of({"shadow_capital": 500}) == 500.0
    assert sh.capital_of({"shadow_capital": 1}) == 20.0 and sh.capital_of({"shadow_capital": 1e9}) == 100000.0
    for bad in (None, True, "x", float("nan"), float("inf")):
        assert sh.capital_of({"shadow_capital": bad}) == 150.0
    assert sh.open_frac_of({"max_open_frac": 0.3}) == 0.3
    assert sh.open_frac_of({"max_open_frac": 0.9}, 0.5) == 0.5          # the API ceiling wins
    assert sh.open_frac_of({}) == 0.5 and sh.open_frac_of({"max_open_frac": "x"}) == 0.5


# ================================================================ F4 trade-through lo, F8 exclusive end
def _ended(q0, count, end_reason="cancel"):
    return {"ep_id": "e1", "ticker": T1, "series": "KXDEMOA", "side": "yes", "book_side": "bid", "price": 0.9,
            "order_price": 0.9, "count": count, "cost": count * 0.9, "q0": q0, "level_min": q0, "join_ts": NOW,
            "end_ts": NOW + timedelta(minutes=10), "end_reason": end_reason, "trades_read": False, "fill": None,
            "close_time": FAR, "result": None}


def _read(md, ep):
    [u] = sh.run_cycle(md, BudgetStrategy({}), sh.CycleInput(series=["KXDEMOA"], run_budget=40, max_order=15,
                                                             now=NOW + timedelta(minutes=30), ended_unread=[ep],
                                                             fee_schedule=None)).upserts
    return u


def test_f4_trade_through_lo_is_the_volume_beyond_the_queue():
    md = SimMD({"KXDEMOA": [T1]})
    md.trade_rows[T1] = [trade(NOW + timedelta(minutes=1), 0.90, 30), trade(NOW + timedelta(minutes=2), 0.88, 25)]
    u = _read(md, _ended(q0=50, count=10))
    assert u["through"] is True and u["through_vol"] == 25
    assert u["fill"] == {"lo": 5.0, "mid": 7.5, "hi": 10.0}     # lo = 30 + 25 - 50; hi = count; mid halfway
    md.trade_rows[T1] = [trade(NOW + timedelta(minutes=2), 0.88, 1)]
    assert _read(md, _ended(q0=50, count=10))["fill"] == {"lo": 0.0, "mid": 5.0, "hi": 10.0}
    md.trade_rows[T1] = [trade(NOW + timedelta(minutes=2), 0.88, 999)]
    assert _read(md, _ended(q0=50, count=10))["fill"] == {"lo": 10.0, "mid": 10.0, "hi": 10.0}   # never above count


def test_f4_old_episodes_without_through_vol_keep_the_full_fill():
    assert m.fill_scenarios(500, 400, 0, True, 12) == {"lo": 12.0, "mid": 12.0, "hi": 12.0}
    assert m.fill_scenarios(500, 400, 0, True, 12, through_vol=None) == {"lo": 12.0, "mid": 12.0, "hi": 12.0}
    r = m.fill_scenarios(40, 10, 10, True, 20, through_vol=45)
    assert r == {"lo": 15.0, "mid": 17.5, "hi": 20.0} and r["lo"] <= r["mid"] <= r["hi"]


def test_f8_cancelled_episode_owns_an_exclusive_end():
    md = SimMD({"KXDEMOA": [T1]})
    end = NOW + timedelta(minutes=10)
    md.trade_rows[T1] = [trade(end, 0.90, 40)]                  # exactly at the end: the next episode's trade
    assert _read(md, _ended(q0=0, count=10))["traded_vol"] == 0
    assert _read(md, _ended(q0=0, count=10, end_reason="closed"))["traded_vol"] == 40
    md.trade_rows[T1] = [trade(end - timedelta(seconds=1), 0.90, 40)]
    assert _read(md, _ended(q0=0, count=10))["traded_vol"] == 40


# ================================================================ F7 fee schedule
def test_s5_deeply_nested_files_count_as_missing(tmp_path, monkeypatch):
    f = tmp_path / "nested.json"
    f.write_text("[" * 30000 + "]" * 30000)                     # under the size cap, deeper than the parser allows
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(f))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(f))
    assert sh.load_fee_schedule() is None and sh.load_series_limit() is None
    f.write_text('{"default": "maker", "prefix_rules": ' + "[" * 30000 + "]" * 30000 + "}")
    assert sh.load_fee_schedule() is None
    md = SimMD({"KXDEMOA": [T1]})
    md.books[T1] = book(0.5, 1)
    res = sh.run_cycle(md, BudgetStrategy({T1: 0.5}), sh.CycleInput(series=["KXDEMOA"], run_budget=40, max_order=15,
                                                                    now=NOW))
    assert res.upserts[0]["fee_mode"] == "maker"               # the cycle still runs: maker fee, no series cap


def test_f7_fee_schedule_file_and_resolution(tmp_path, monkeypatch):
    assert sh.load_fee_schedule() is None                       # absent (the public repo): every series maker
    assert sh.fee_mode_for("KXFAKEZ-26OCT19-T1", None) == "maker"
    f = tmp_path / "fees.json"
    f.write_text(json.dumps({"default": "maker", "prefix_rules": [{"series_prefix": "KXFAKEZ", "mode": "zero"},
                                                                  {"series_prefix": "KXFAKE", "mode": "maker"}]}))
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(f))
    sched = sh.load_fee_schedule()
    assert sh.fee_mode_for("KXFAKEZOO-26OCT19-T1", sched) == "zero"           # first matching rule wins
    assert sh.fee_mode_for("KXFAKEY-26OCT19-T1", sched) == "maker"
    assert sh.fee_mode_for("KXOTHER-26OCT19-T1", sched) == "maker"            # default
    assert sh.fee_mode_for("KXOTHER", {"default": "zero", "rules": []}) == "zero"
    for bad in ("not json", json.dumps([]), json.dumps({"default": "taker"}),
                json.dumps({"prefix_rules": [{"series_prefix": "", "mode": "zero"}]}),
                json.dumps({"prefix_rules": [{"series_prefix": "KX", "mode": "free"}]}),
                json.dumps({"prefix_rules": "KX"})):
        f.write_text(bad)
        assert sh.load_fee_schedule() is None                   # invalid: the conservative default
    f.write_text("x" * (sh.FEE_SCHEDULE_MAX_BYTES + 1))
    assert sh.load_fee_schedule() is None


def test_f7_new_episodes_record_their_fee_mode_and_the_summary_uses_it():
    st = BudgetStrategy({"KXFAKEZ-26OCT19-T1": 0.9, "KXFAKEM-26OCT19-T1": 0.9})
    md = SimMD({"KXFAKEZ": ["KXFAKEZ-26OCT19-T1"], "KXFAKEM": ["KXFAKEM-26OCT19-T1"]})
    for t in ("KXFAKEZ-26OCT19-T1", "KXFAKEM-26OCT19-T1"):
        md.books[t] = book(0.9, 1)
    sched = {"default": "maker", "rules": [("KXFAKEZ", "zero")]}
    res = sh.run_cycle(md, st, sh.CycleInput(series=["KXFAKEZ", "KXFAKEM"], run_budget=40, max_order=15, now=NOW,
                                             fee_schedule=sched))
    modes = {u["series"]: u["fee_mode"] for u in res.upserts}
    assert modes == {"KXFAKEZ": "zero", "KXFAKEM": "maker"}
    eps = [_scored(0, fee_mode="zero"), _scored(1, fee_mode="maker"), _scored(2)]
    s = m.summarize(eps, NOW)
    sc = [m.score_episode(e) for e in eps]
    assert s["headline"]["pnl"]["mid"] == pytest.approx(sc[0]["zero"]["mid"] + sc[1]["maker"]["mid"]
                                                        + sc[2]["maker"]["mid"], abs=1e-4)
    assert s["fee_mode"] == "per_series" and s["fee_modes"] == {"zero": 1, "maker": 2}
    assert m.summarize(eps[1:], NOW)["fee_mode"] == "maker"


# ================================================================ summary: F1 count, F2, F3, F5, F6
def _scored(i, *, day=0, price=0.9, count=10, fill=(10.0, 10.0, 10.0), result="no", side="no", ticker=None,
            join_h=0, fee_mode=None, capped=False, through=False):
    base = datetime(2026, 10, 18, 4, 0, tzinfo=UTC) + timedelta(days=day)
    ep = {"ep_id": f"ep{i}", "ticker": ticker or f"KXDEMOA-26OCT{18 + day}-T{i}", "series": "KXDEMOA",
          "side": side, "price": price, "count": count, "cost": count * price, "cell": ["c", 1],
          "join_ts": base + timedelta(hours=join_h), "end_ts": base + timedelta(hours=join_h + 1),
          "through": through, "through_ts": base + timedelta(hours=join_h, minutes=30) if through else None,
          "close_time": base + timedelta(hours=20), "settled_ts": base + timedelta(hours=20), "result": result,
          "fill": dict(zip(("lo", "mid", "hi"), fill)), "capped": capped}
    if fee_mode is not None:
        ep["fee_mode"] = fee_mode
    return ep


def _all_win_sample():
    """The audit's F2 shape in miniature: every filled bid won, prices around 0.9, three close days."""
    eps, i = [], 0
    for day, prices in enumerate(([0.88, 0.92, 0.90], [0.86, 0.91, 0.93, 0.89], [0.90, 0.94])):
        for p in prices:
            eps.append(_scored(i, day=day, price=p))
            i += 1
    return eps


def test_f2_break_even_vs_observed_win_rate_on_an_all_win_sample():
    eps = _all_win_sample()
    h = m.summarize(eps, NOW)["headline"]
    avg_price = sum(e["price"] * 10 for e in eps) / (10 * len(eps))
    assert h["break_even_win_rate"] == pytest.approx(avg_price, abs=1e-6)
    assert h["observed_win_rate"] == 1.0 and h["n_settled_fills"] == len(eps)
    # with no loss the ROI is mechanically the cost-weighted (1 - p) / p: not an edge estimate
    assert h["roi"]["mid"] == pytest.approx((1 - avg_price) / avg_price, rel=0.05)
    assert h["headline_status"] == "too_early"
    lose = dict(eps[0], result="yes")
    h2 = m.summarize([lose] + eps[1:], NOW)["headline"]
    assert h2["observed_win_rate"] == pytest.approx(1 - 1 / len(eps), abs=1e-6)


def test_f3_no_interval_while_too_early_and_one_once_the_gate_passes():
    s = m.summarize(_all_win_sample(), NOW)
    for k in ("headline", "headline_zero_fee"):
        assert s[k]["pnl"]["ci90"] is None and s[k]["roi"]["ci90"] is None and s[k]["ci_reliable"] is False
    many = [_scored(i, day=i % 8, result="no" if i % 5 else "yes") for i in range(40)]
    s = m.summarize(many, NOW, draws=300)
    assert s["status"]["too_early"] is False and s["status"]["headline_status"] == "ok"
    assert s["headline"]["ci_reliable"] is True and len(s["headline"]["pnl"]["ci90"]) == 2
    # a zero-fill day is not resampled: adding one leaves the interval unchanged
    empty_day = _scored(99, day=9, fill=(0.0, 0.0, 0.0))
    s2 = m.summarize(many + [empty_day], NOW, draws=300)
    assert s2["headline"]["pnl"]["ci90"] == s["headline"]["pnl"]["ci90"]


def test_f6_days_covered_counts_only_days_with_a_fill():
    eps = _all_win_sample() + [_scored(50, day=5, fill=(0.0, 0.0, 0.0))]
    s = m.summarize(eps, NOW)
    assert s["status"]["days_covered"] == 3 and s["status"]["settled_days"] == 4 and s["day_basis"] == "close_day"
    # seven settled days but only six with a fill is still too early
    six = [_scored(i, day=i, count=10, fill=(5.0, 6.0, 7.0)) for i in range(6)] + [_scored(9, day=6, fill=(0, 0, 0))]
    st = m.summarize(six, NOW)["status"]
    assert st["settled_days"] == 7 and st["days_covered"] == 6 and st["too_early"] is True


def test_f5_peak_held_capital_and_return_on_it():
    a = _scored(0, day=0, price=0.9, count=10, fill=(10.0, 10.0, 10.0))       # held 9 from end to settle
    b = _scored(1, day=0, price=0.8, count=10, fill=(0.0, 5.0, 10.0), join_h=2)   # held 4, overlaps a
    c = _scored(2, day=2, price=0.9, count=10, fill=(10.0, 10.0, 10.0))       # alone, later
    s = m.summarize([a, b, c], NOW)
    h = s["headline"]
    assert h["peak_held_capital"] == pytest.approx(13.0)
    assert h["return_on_held"] == pytest.approx(h["pnl"]["mid"] / 13.0, abs=1e-6)
    # a trade-through is held from its through time
    t = _scored(3, day=0, price=0.9, count=10, through=True)
    assert m._peak_held([{"ep": t, "fill": {"mid": 10.0}, "price": 0.9}], NOW) == pytest.approx(9.0)


def test_f1_first_fill_per_market_and_capped_count():
    same = [_scored(i, day=0, price=round(0.88 - 0.01 * i, 2), ticker="KXDEMOA-26OCT19-T67", join_h=i)
            for i in range(5)]
    other = [_scored(10, day=1, price=0.9, ticker="KXDEMOA-26OCT20-T73")]
    cap = _scored(20, day=1, fill=(0.0, 0.0, 0.0), capped=True)
    open_cap = dict(_scored(21, day=1, capped=True), result=None, end_ts=None, settled_ts=None)
    s = m.summarize(same + other + [cap, open_cap], NOW)
    ff = s["headline"]["first_fill_per_market"]
    assert ff["markets"] == 2
    assert ff["cost"] == pytest.approx(10 * 0.88 + 10 * 0.9)
    assert ff["pnl"] == pytest.approx(sum(m.score_episode(e)["maker"]["mid"] for e in (same[0], other[0])), abs=1e-4)
    assert ff["roi"] == pytest.approx(ff["pnl"] / ff["cost"], abs=1e-6)
    assert s["status"]["capped_episodes"] == 2 and s["status"]["settled_episodes"] == 6
    assert s["headline"]["peak_capital"] == m.summarize(same + other, NOW)["headline"]["peak_capital"]


def test_unchanged_math_where_no_fix_applies():
    """Uncapped, maker-fee, every day filled, past the gate: pnl / roi / fill rate / daily are the old formulas."""
    eps = [_scored(i, day=i % 8, price=0.85 + 0.01 * (i % 5), count=12, fill=(2.0, 6.0, 9.0),
                   result="no" if i % 6 else "yes") for i in range(48)]
    s = m.summarize(eps, NOW, draws=200)
    sc = [m.score_episode(e) for e in eps]
    for k in ("lo", "mid", "hi"):
        vals = [sum(x["maker"][q] for x in sc) for q in ("lo", "mid", "hi")]
        want = {"lo": min(vals), "mid": vals[1], "hi": max(vals)}[k]
        assert s["headline"]["pnl"][k] == pytest.approx(want, abs=1e-4)
    cost_mid = sum(6.0 * e["price"] for e in eps)
    assert s["headline"]["roi"]["mid"] == pytest.approx(sum(x["maker"]["mid"] for x in sc) / cost_mid, abs=1e-6)
    assert s["headline"]["fill_rate"]["mid"] == pytest.approx(6.0 / 12.0)
    assert s["status"]["days_covered"] == 8 and s["status"]["too_early"] is False
    assert s["daily"][-1]["cum_mid"] == pytest.approx(s["headline"]["pnl"]["mid"], abs=1e-3)


# ================================================================ runner wiring (store held read + cloud capital)
@pytest.mark.asyncio
async def test_runner_passes_held_position_and_shadow_capital(settings, bot_paths, monkeypatch):
    from control_api.db import MemoryStore
    from control_api.tests.test_shadow_cycle import FakeStrategy, leader_runner, live_md, yes_quote
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.cloud["shadow_capital"] = 20                              # ceiling max_open_frac x 20 <= 10
    now = datetime.now(UTC)
    await store.shadow_upsert_episodes([{
        "ep_id": "held1", "env": "prod", "ticker": "KXDEMOA-26OCT17-B9", "series": "KXDEMOA", "side": "yes",
        "book_side": "bid", "price": 0.5, "order_price": 0.5, "count": 20, "cost": 10.0,
        "join_ts": now - timedelta(hours=2),
        "end_ts": now - timedelta(hours=1), "end_reason": "cancel", "trades_read": True, "result": None,
        "fill": {"lo": 20.0, "mid": 20.0, "hi": 20.0}, "close_time": now + timedelta(hours=6)}])
    out = await r.shadow_cycle()
    assert out["ran"] is True
    p = out["pass"]
    frac = sh.open_frac_of({**r.ms.DEFAULTS, **r.config}, settings.max_open_frac)
    assert p["held_cost"] == pytest.approx(10.0) and p["ceiling"] == pytest.approx(frac * 20) and p["headroom"] == 0
    [ep] = await store.shadow_open_episodes()
    assert ep["capped"] is True and ep["cap_reason"] == "open_cap"
    summary = await store.get_shadow_summary()
    assert summary["headline"]["peak_capital"] == 0 and summary["status"]["capped_episodes"] == 1
    await r.shutdown()
