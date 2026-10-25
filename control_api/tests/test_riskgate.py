"""Account risk gate (services/riskgate.py) and the arb gate settings (services/arb.py). Pure, no I/O."""
from __future__ import annotations

import math

import pytest

from control_api.services import arb as arbsvc
from control_api.services import riskgate as rg

CAPS = {"arb_scan_cap": 10, "arb_day_cap": 30, "arb_series_cap_frac": 0.35}


def check(cost=2.0, series="KXA", by=None, cash=100.0, frac=0.5, scan=0.0, day=0.0, caps=None):
    return rg.check_arb(cost, series, {} if by is None else by, cash, frac, scan, day, CAPS if caps is None else caps)


def test_allows_a_small_set_on_an_empty_account():
    assert check() == (True, "ok")


def test_total_exposure_ceiling():
    # ceiling = 0.5 * (52 + 48) = 50; exposure 48 (spread over series) + 3 > 50
    by = {"KXB": 16.0, "KXC": 16.0, "KXD": 16.0}
    ok, why = check(cost=3.0, by=by, cash=52.0, caps={**CAPS, "arb_series_cap_frac": 0.9})
    assert not ok and why.startswith("total exposure")
    assert check(cost=2.0, by=by, cash=52.0, caps={**CAPS, "arb_series_cap_frac": 0.9})[0]


def test_series_cap_is_35_percent_of_the_ceiling():
    # ceiling = 0.5 * (100 + 20) = 60; series cap = 21; KXA holds 19
    by = {"KXA": 19.0, "KXB": 1.0}
    ok, why = check(cost=2.5, by=by, cash=100.0)
    assert not ok and why.startswith("series cap KXA")
    assert check(cost=1.9, by=by, cash=100.0)[0]
    assert check(cost=2.5, series="KXB", by=by, cash=100.0)[0]          # another series is not affected


def test_scan_and_day_caps():
    ok, why = check(cost=3.0, scan=8.0)
    assert not ok and why.startswith("scan cap")
    ok, why = check(cost=3.0, day=28.0)
    assert not ok and why.startswith("day cap")
    assert check(cost=2.0, scan=8.0, day=28.0)[0]                          # exactly at both caps is allowed


def test_cash_is_a_hard_limit():
    ok, why = check(cost=6.0, cash=5.0, frac=1.0, caps={**CAPS, "arb_series_cap_frac": 0.9})
    assert not ok and why.startswith("cash")


@pytest.mark.parametrize("kw", [
    {"cost": 0}, {"cost": -1}, {"cost": math.nan}, {"cost": "x"}, {"cost": True}, {"cash": math.inf},
    {"cash": -1}, {"frac": 0}, {"frac": 1.5}, {"scan": -1}, {"day": None}, {"series": ""}, {"series": None},
    {"by": {"KXA": -1}}, {"by": {"KXA": math.nan}}, {"caps": {**CAPS, "arb_day_cap": None}},
    {"caps": {**CAPS, "arb_series_cap_frac": 0}},
])
def test_bad_inputs_fail_closed(kw):
    assert check(**kw) == (False, "bad input")


def test_positions_none_fails_closed():
    assert rg.check_arb(1, "KXA", None, 100, 0.5, 0, 0, CAPS) == (False, "bad input")


def test_exposure_by_series_groups_on_the_ticker_prefix():
    pos = [{"ticker": "KXA-26OCT21-B70", "market_exposure_dollars": "3.5"},
           {"ticker": "KXA-26OCT21-B72", "market_exposure_dollars": -1.5},
           {"ticker": "KXB-26OCT21-T1", "market_exposure_dollars": 2},
           {"ticker": "KXC-X", "market_exposure_dollars": 0}]
    assert rg.exposure_by_series(pos) == {"KXA": 5.0, "KXB": 2.0}
    assert rg.series_of("KXA-26OCT21") == "KXA" and rg.series_of(None) == ""


@pytest.mark.parametrize("bad", [None, [1], [{"ticker": "A-1", "market_exposure_dollars": "nan"}],
                                 [{"ticker": "A-1", "market_exposure_dollars": "x"}]])
def test_exposure_by_series_raises_on_unreadable(bad):
    with pytest.raises(ValueError):
        rg.exposure_by_series(bad)


class S:
    max_arb_contracts = 50


def test_arb_settings_defaults_and_validation():
    d = arbsvc.defaults()
    assert (d["arb_scan_cap"], d["arb_day_cap"], d["arb_series_cap_frac"], d["arb_demo_execute"]) == \
        (15.0, 45.0, 0.35, False)
    out = arbsvc.validate(d, {"arb_scan_cap": 5, "arb_day_cap": 100, "arb_series_cap_frac": 0.5,
                              "arb_demo_execute": True}, S())
    assert out["arb_scan_cap"] == 5 and out["arb_day_cap"] == 100 and out["arb_series_cap_frac"] == 0.5
    assert out["arb_demo_execute"] is True
    with pytest.raises(ValueError) as e:
        arbsvc.validate(d, {"arb_scan_cap": 101, "arb_day_cap": 0.5, "arb_series_cap_frac": 0.95,
                            "arb_demo_execute": "yes"}, S())
    msgs = " ".join(e.value.args[0])
    for k in ("arb_scan_cap", "arb_day_cap", "arb_series_cap_frac", "arb_demo_execute"):
        assert k in msgs
    with pytest.raises(ValueError):
        arbsvc.validate(d, {"arb_scan_cap": True}, S())
    c = arbsvc.ceilings(S())
    assert c["arb_scan_cap"] == 100 and c["arb_day_cap"] == 500 and c["arb_series_cap_frac"] == 0.9
    assert arbsvc.gate_caps({}) == {"arb_scan_cap": 15.0, "arb_day_cap": 45.0, "arb_series_cap_frac": 0.35}


def test_series_cap_help_says_arbitrage_only():
    h = arbsvc.schema(S())["arb_series_cap_frac"]["help"]
    assert "arbitrage only" in h and "maker and arbitrage together" not in h


class C:
    def __init__(self, url):
        self.base_url = url


@pytest.mark.parametrize("url,ok", [
    ("https://external-api.demo.kalshi.co/trade-api/v2", True),
    ("https://demo-api.kalshi.co/trade-api/v2/", True),
    ("https://external-api.kalshi.com/trade-api/v2", False),
    ("https://api.elections.kalshi.com/trade-api/v2", False),
    ("https://external-api.demo.kalshi.co.evil.example/trade-api/v2", False),
    ("", False), (None, False),
])
def test_demo_base_ok(url, ok):
    assert arbsvc.demo_base_ok(C(url)) is ok
    assert arbsvc.demo_base_ok(object()) is False


# ---------------------------------------------------------------- opposite sets on one event (always on)
def _doc(event="E1", kind="NO", executed="FILLED {}", mode="demo-execute"):
    return {"event": event, "kind": kind, "executed": executed, "mode": mode}


def test_held_sets_counts_only_executed_sets_of_the_mode():
    docs = [_doc(), _doc(kind="YES", executed="PARTIAL {}", event="E2"),
            _doc(kind="YES", executed="FILLED (completed) {}", event="E3"),
            _doc(kind="YES", executed="skipped: gate day cap", event="E4"), _doc(kind="YES", executed="log-only",
                                                                               event="E5"),
            _doc(kind="YES", executed="ERROR RuntimeError", event="E6"), _doc(kind="YES", mode="live", event="E7"),
            "junk", _doc(kind="MAYBE", event="E8")]
    assert rg.held_sets(docs, "demo-execute") == {"E1": {"NO"}, "E2": {"YES"}, "E3": {"YES"},
                                                  "E6": {"YES"}}                 # ERROR may hold legs
    assert rg.held_sets(docs, "live") == {"E7": {"YES"}}
    assert rg.held_sets(None, "live") == {}


def test_opposite_set_held():
    held = {"E1": {"NO"}}
    assert rg.opposite_set_held("E1", "YES", held) is True               # opposite blocked
    assert rg.opposite_set_held("E1", "NO", held) is False               # same kind allowed (caps govern it)
    assert rg.opposite_set_held("E2", "YES", held) is False              # another event allowed
    assert rg.opposite_set_held("E1", "YES", None) is True               # unknown state: fail closed
    assert rg.opposite_set_held("E1", "BOTH", held) is True
    assert rg.opposite_set_held("", "YES", held) is True
