"""Shadow: stale and legacy ended episodes leave the held position; a full ceiling is reported, not hidden.

Root cause (2026-10-27): markets that Kalshi itself had not settled (status closed, result empty) four days after
close kept their legacy episodes in the held position (counted at central fill x price), so the simulated ceiling
stayed full and every new bid was refused. Fixtures use KXDEMO* tickers and a fake strategy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow as sh
from control_api.tests.test_shadow_cycle import FakeMD, FakeStrategy, yes_quote

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
OLD_T = "KXDEMOA-26OCT22-B9"


@pytest.fixture(autouse=True)
def _no_private_files(tmp_path, monkeypatch):
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee.json"))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(tmp_path / "absent_limits.json"))


def held_ep(eid, cost, *, ticker=OLD_T, close=None, methodology=None, join=None):
    price = 0.5
    n = cost / price
    ep = {"ep_id": eid, "ticker": ticker, "series": ticker.split("-")[0], "side": "yes", "price": price,
          "count": n, "end_ts": NOW - timedelta(days=1), "result": None, "trades_read": True,
          "fill": {"lo": n, "mid": n, "hi": n}}
    if close is not None:
        ep["close_time"] = close
    if methodology is not None:
        ep["methodology"] = methodology
    if join is not None:
        ep["join_ts"] = join
    return ep


def cycle(held, *, now=NOW, strategy=None, capital=150.0):
    inp = sh.CycleInput(series=["KXDEMOA"], run_budget=40.0, max_order=15.0, now=now, held=list(held),
                        capital=capital, max_open_frac=0.5, fee_schedule=None, series_share_limit=None,
                        market_share_limit=None)
    return sh.run_cycle(FakeMD(close=now + timedelta(hours=6)), strategy or FakeStrategy([yes_quote()]), inp)


# ---------------------------------------------------------------- the diagnosis
def test_unresolved_market_is_rechecked_not_a_settlement_bug():
    """Kalshi reports status closed and an empty result for the stuck markets: the code keeps them queued (checked
    stamp only) and voids them only after SHADOW_VOID_AFTER, exactly as designed."""
    md = FakeMD()
    md.results[OLD_T] = {"ticker": OLD_T, "status": "closed", "result": ""}
    close = NOW - timedelta(days=4)
    ep = held_ep("e1", 10.0, close=close, methodology=1)

    def run(now):
        return sh.run_cycle(md, FakeStrategy(), sh.CycleInput(series=["KXDEMOA"], run_budget=40.0, max_order=15.0,
                                                              now=now, unsettled=[ep])).upserts[0]
    u = run(NOW)
    assert u["result"] is None and u["settle_checked_ts"] == NOW
    assert run(close + sh.SHADOW_VOID_AFTER + timedelta(minutes=1))["result"] == "void"


# ---------------------------------------------------------------- the held position
def test_stale_legacy_held_episodes_no_longer_fill_the_ceiling():
    close = NOW - timedelta(days=4)
    held = [held_ep(f"L{i}", 10.0, close=close, methodology=1) for i in range(25)]      # $250 vs a $75 ceiling
    res = cycle(held)
    d = res.pass_doc
    assert d["held_cost"] == 0 and d["held_stale_ignored"] == 25 and d["held_stale_cost"] == pytest.approx(250.0)
    assert d["headroom"] == pytest.approx(40.0) and d["capped_new"] == 0
    assert [u for u in res.upserts if u.get("capped") is False]                        # a bid is opened again


def test_stale_methodology2_episode_is_ignored_after_the_grace_period_only():
    inside = held_ep("in", 10.0, close=NOW - sh.SHADOW_HELD_GRACE + timedelta(minutes=1), methodology=2)
    outside = held_ep("out", 6.0, close=NOW - sh.SHADOW_HELD_GRACE - timedelta(minutes=1), methodology=2)
    d = cycle([inside, outside]).pass_doc
    assert d["held_cost"] == pytest.approx(10.0) and d["held_stale_ignored"] == 1
    assert d["held_stale_cost"] == pytest.approx(6.0)


def test_legacy_by_join_time_follows_the_headline_rule():
    """A stored 1 that joined after the capped code went live reads as methodology 2 (methodology_of), so it counts."""
    close = NOW - timedelta(hours=2)
    after = held_ep("a", 5.0, close=close, methodology=1, join=sh.METHODOLOGY2_SINCE + timedelta(hours=1))
    before = held_ep("b", 7.0, close=close, methodology=1, join=sh.METHODOLOGY2_SINCE - timedelta(hours=1))
    d = cycle([after, before]).pass_doc
    assert d["held_cost"] == pytest.approx(5.0) and d["held_stale_ignored"] == 1


def test_numbers_identical_without_stale_or_legacy_held_episodes():
    """Fresh methodology-2 (and bare, no-close-time) held episodes: every pass-doc number is what it always was."""
    fresh = [held_ep("f1", 12.0, close=NOW - timedelta(hours=3), methodology=2),
             held_ep("f2", 8.0, close=NOW + timedelta(hours=5), methodology=2),
             held_ep("f3", 5.0)]                                                         # no methodology, no close
    d = cycle(fresh).pass_doc
    bare = cycle([{k: v for k, v in e.items() if k not in ("methodology", "close_time")} for e in fresh]).pass_doc
    assert d["held_cost"] == pytest.approx(25.0) and d["held_stale_ignored"] == 0 and d["held_stale_cost"] == 0
    for k in ("held_cost", "held_markets", "ceiling", "headroom", "new_eps", "capped_new", "series_capped"):
        assert d[k] == bare[k], k
    assert d["headroom"] == pytest.approx(min(40.0, 75.0 - 25.0))


def test_fresh_held_over_the_ceiling_still_caps():
    held = [held_ep(f"h{i}", 10.0, close=NOW + timedelta(hours=2), methodology=2) for i in range(8)]   # $80 > $75
    res = cycle(held)
    assert res.pass_doc["held_cost"] == pytest.approx(80.0) and res.pass_doc["headroom"] == 0
    assert res.pass_doc["held_stale_ignored"] == 0
    new = [u for u in res.upserts if u.get("join_ts") == NOW]
    assert new and all(u.get("capped") is True for u in new)


# ---------------------------------------------------------------- the ceiling-full warning
def _p(minutes_ago, *, headroom=40.0, new=2, capped=0, held=10.0, ceiling=75.0, interval=None):
    p = {"ts": NOW - timedelta(minutes=minutes_ago), "ok": True, "headroom": headroom, "new_eps": new,
         "capped_new": capped, "held_cost": held, "ceiling": ceiling}
    if interval is not None:
        p["interval"] = interval
    return p


def test_ceiling_full_needs_two_consecutive_full_passes():
    full = dict(headroom=0.0, held=248.5)
    assert sh.ceiling_status([_p(0, **full)]) == {"ceiling_full": False}                   # one is not enough
    assert sh.ceiling_status([_p(0, **full), _p(10)]) == {"ceiling_full": False}            # newest two: full, ok
    assert sh.ceiling_status([_p(0), _p(10, **full)]) == {"ceiling_full": False}
    got = sh.ceiling_status([_p(10, **full), _p(0, **full), _p(20)])                         # order does not matter
    assert got == {"ceiling_full": True, "held_cost": 248.5, "ceiling": 75.0}


def test_ceiling_full_by_more_capped_than_uncapped():
    assert sh.pass_ceiling_full(_p(0, new=5, capped=3)) is True
    assert sh.pass_ceiling_full(_p(0, new=5, capped=2)) is False
    assert sh.pass_ceiling_full(_p(0, new=0, capped=0)) is False
    assert sh.pass_ceiling_full({"ts": NOW}) is False                                       # an old pass doc


def test_summary_carries_ceiling_full_from_baseline_passes_only():
    full = dict(headroom=0.0, held=248.51)
    s = sh.build_summary([], [_p(0, **full), _p(10, **full), _p(0, interval=5), _p(5, interval=5)], NOW)
    assert s["status"]["ceiling_full"] is True and s["status"]["held_cost"] == 248.51 and s["status"]["ceiling"] == 75.0
    ok = sh.build_summary([], [_p(0, interval=5, **full), _p(10, interval=5, **full), _p(0), _p(10)], NOW)
    assert ok["status"]["ceiling_full"] is False and "held_cost" not in ok["status"]
    assert sh.build_summary([], [], NOW)["status"]["ceiling_full"] is False
