"""Pure shadow estimate model: queue math, fees, scoring, summary, bootstrap."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow_model as m

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _ep(i=0, *, day=0, side="yes", result="yes", price=0.9, count=10, fill=None,
        series="KXDEMOA", cell=("a", 1), join_h=0, end_h=2, standin=False):
    base = datetime(2026, 10, 1, 8, 0, tzinfo=UTC) + timedelta(days=day)
    f = fill if fill is not None else {"lo": 2.0, "mid": 5.0, "hi": 8.0}
    return {
        "ep_id": f"ep{i}", "ticker": f"KXDEMO-{i}", "series": series, "side": side,
        "book_side": side, "price": price, "order_price": price, "count": count,
        "cost": count * price, "cell": list(cell), "close_time": base + timedelta(hours=12),
        "join_ts": base + timedelta(hours=join_h), "end_ts": base + timedelta(hours=end_h),
        "result": result, "settled_ts": base + timedelta(hours=13), "fill": f,
        "standin": standin,
    }


# ---------------- fill_scenarios ----------------
def test_queue_math_each_scenario():
    # q0=100, queue shrank to 60, 130 traded: lo=30, hi=70 capped to count 50 -> 50, mid a=80 -> 50
    r = m.fill_scenarios(100, 60, 130, False, 50)
    assert r == {"lo": 30.0, "mid": 50.0, "hi": 50.0}
    # a smaller trade: lo=0 (still behind 100), mid=130-80... use vol=90
    r = m.fill_scenarios(100, 60, 90, False, 50)
    assert r["lo"] == 0.0
    assert r["mid"] == pytest.approx(10.0)   # 90 - (100+60)/2
    assert r["hi"] == pytest.approx(30.0)    # 90 - 60


def test_scenarios_ordered_and_capped():
    r = m.fill_scenarios(40, 10, 55, False, 100)
    assert r["lo"] <= r["mid"] <= r["hi"]
    assert r["lo"] == 15.0 and r["hi"] == 45.0 and r["mid"] == pytest.approx(30.0)
    for v in m.fill_scenarios(0, 0, 9999, False, 7).values():
        assert v == 7.0


def test_through_trade_is_full_fill_all_scenarios():
    r = m.fill_scenarios(500, 400, 0, True, 12)
    assert r == {"lo": 12.0, "mid": 12.0, "hi": 12.0}


def test_partial_fills():
    r = m.fill_scenarios(10, 10, 14, False, 20)
    assert r == {"lo": 4.0, "mid": 4.0, "hi": 4.0}   # no queue shrink: scenarios agree


def test_nothing_traded_no_fill_and_no_negative():
    assert m.fill_scenarios(50, 20, 0, False, 10) == {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    assert all(v >= 0 for v in m.fill_scenarios(50, 20, 30, False, 10).values())


def test_level_min_above_q0_does_not_help():
    # queue ahead can only be as small as the smaller of q0 and the level minimum
    assert m.fill_scenarios(10, 99, 15, False, 20) == m.fill_scenarios(10, 10, 15, False, 20)


def test_zero_count_and_missing_values():
    assert m.fill_scenarios(5, 5, 100, True, 0) == {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    r = m.fill_scenarios(None, None, None, False, 5)
    assert r == {"lo": 0.0, "mid": 0.0, "hi": 0.0}


# ---------------- fees ----------------
def test_fee_zero_mode():
    assert m.maker_fee(100, 0.5, "zero") == 0.0


def test_fee_maker_ceil_rounding():
    # 0.0175*10*.5*.5*100 = 4.375 -> ceil 5 -> $0.05
    assert m.maker_fee(10, 0.5, "maker") == 0.05
    # one contract at .9: 0.0175*1*.9*.1*100 = 0.1575 -> 1 cent
    assert m.maker_fee(1, 0.9, "maker") == 0.01
    assert m.maker_fee(0, 0.9, "maker") == 0.0


def test_fee_exact_cent_boundary_not_bumped():
    # 0.0175*c*.25*100 where c=... choose price .5 count 4: 0.0175*4*.25*100 = 1.75 -> 2
    assert m.maker_fee(4, 0.5, "maker") == 0.02
    # an exact integer: count 400 price .5 -> 175.0 raw cents -> exactly $1.75
    assert m.maker_fee(400, 0.5, "maker") == 1.75


def test_fee_bad_mode():
    with pytest.raises(ValueError):
        m.maker_fee(1, 0.5, "taker")


# ---------------- pnl / scoring ----------------
def test_episode_pnl_win_and_lose():
    assert m.episode_pnl(10, 0.9, True, 0.0) == pytest.approx(1.0)
    assert m.episode_pnl(10, 0.9, False, 0.0) == pytest.approx(-9.0)
    assert m.episode_pnl(10, 0.9, True, 0.05) == pytest.approx(0.95)


def test_score_win_by_side_vs_result():
    win = m.score_episode(_ep(side="yes", result="yes", price=0.9))
    assert win["zero"]["mid"] == pytest.approx(5 * 0.1)
    assert win["zero"]["lo"] == pytest.approx(2 * 0.1)
    assert win["zero"]["hi"] == pytest.approx(8 * 0.1)
    lose = m.score_episode(_ep(side="yes", result="no", price=0.9))
    assert lose["zero"]["mid"] == pytest.approx(-5 * 0.9)
    # a "no" bid wins when the result is "no"
    no_win = m.score_episode(_ep(side="no", result="no", price=0.85))
    assert no_win["zero"]["mid"] == pytest.approx(5 * 0.15)
    no_lose = m.score_episode(_ep(side="no", result="yes", price=0.85))
    assert no_lose["zero"]["mid"] == pytest.approx(-5 * 0.85)


def test_score_maker_fee_applied_and_nofill_no_fee():
    ep = _ep(price=0.9, fill={"lo": 0.0, "mid": 5.0, "hi": 10.0})
    s = m.score_episode(ep)
    assert s["maker"]["lo"] == 0.0  # no fill, no fee
    assert s["maker"]["mid"] == pytest.approx(s["zero"]["mid"] - m.maker_fee(5, 0.9, "maker"))
    assert s["maker"]["hi"] < s["zero"]["hi"]


def test_score_requires_result_and_fill():
    with pytest.raises(ValueError):
        m.score_episode(_ep(result=None))
    ep = _ep()
    ep["fill"] = None
    with pytest.raises(ValueError):
        m.score_episode(ep)


# ---------------- summarize ----------------
def test_empty_input_shape():
    s = m.summarize([], NOW)
    assert s["status"]["too_early"] is True
    assert s["status"]["days_covered"] == 0 and s["status"]["settled_episodes"] == 0
    assert s["headline"]["pnl"] == {"lo": 0.0, "mid": 0.0, "hi": 0.0, "ci90": None}      # 2A-3 F3
    assert s["headline"]["ci_reliable"] is False and s["headline"]["headline_status"] == "too_early"
    assert s["headline"]["peak_capital"] == 0.0
    assert s["by_series"] == [] and s["by_cell"] == [] and s["daily"] == []
    assert s["fee_mode"] == "maker"
    assert s["caveats"] and all(isinstance(c, str) for c in s["caveats"])
    assert set(s["headline_zero_fee"]) == set(s["headline"])
    assert set(m.empty_summary()) == set(s)


def test_summary_top_level_keys_match_contract():
    s = m.summarize([_ep()], NOW)
    assert set(s) == {"enabled", "standin", "status", "headline", "headline_zero_fee",
                      "by_series", "by_cell", "daily", "fee_mode", "caveats",
                      # additive (Sprint 2A-2 S2/S3); the contract fields above are unchanged
                      "by_event_day", "day_stats", "reliability_note", "arb",
                      # additive (Sprint 2A-3)
                      "day_basis", "fee_modes", "variants_enabled", "variants", "variant_compare",
                      "methodology", "legacy", "legacy_banner"}
    assert set(s["status"]) == {"days_covered", "settled_episodes", "central_fills", "too_early",
                                "last_pass_ts", "pass_ok_pct", "truncated",
                                "headline_status", "capped_episodes", "settled_days"}
    assert set(s["headline"]) == {"pnl", "roi", "fill_rate", "peak_capital",
                                  "ci_reliable", "headline_status", "break_even_win_rate", "observed_win_rate",
                                  "n_settled_fills", "peak_held_capital", "return_on_held",
                                  "first_fill_per_market"}
    assert set(s["headline"]["pnl"]) == {"lo", "mid", "hi", "ci90"}
    assert set(s["headline"]["fill_rate"]) == {"lo", "mid", "hi"}


def test_unsettled_episodes_not_scored():
    open_ep = _ep(1, result=None)
    open_ep["settled_ts"] = None
    s = m.summarize([open_ep], NOW)
    assert s["status"]["settled_episodes"] == 0
    assert s["headline"]["pnl"]["mid"] == 0.0
    assert s["headline"]["peak_capital"] > 0  # still counts toward capital


def test_headline_totals_and_roi_and_fill_rate():
    eps = [_ep(0, day=0, result="yes"), _ep(1, day=1, result="no")]
    s = m.summarize(eps, NOW)
    zero = s["headline_zero_fee"]
    assert zero["pnl"]["mid"] == pytest.approx(5 * 0.1 - 5 * 0.9)
    assert zero["fill_rate"]["mid"] == pytest.approx(10 / 20)
    assert zero["fill_rate"]["lo"] == pytest.approx(4 / 20)
    assert zero["fill_rate"]["hi"] == pytest.approx(16 / 20)
    assert zero["roi"]["mid"] == pytest.approx(zero["pnl"]["mid"] / (10 * 0.9), abs=1e-5)
    h = s["headline"]
    assert h["pnl"]["mid"] < zero["pnl"]["mid"]  # fee drag
    assert h["pnl"]["lo"] <= h["pnl"]["mid"] <= h["pnl"]["hi"]
    assert h["roi"]["lo"] <= h["roi"]["mid"] <= h["roi"]["hi"]


def test_pnl_range_ordering_for_winners():
    s = m.summarize([_ep(i, day=i) for i in range(5)], NOW)
    p = s["headline"]["pnl"]
    assert p["lo"] <= p["mid"] <= p["hi"]


def test_too_early_gate():
    # 2A-3 V2: 7 close days with a fill AND 30 filled episodes (the same rule as the reliability note)
    eps = [_ep(i, day=i % 7, count=10, fill={"lo": 1.0, "mid": 5.0, "hi": 9.0}) for i in range(30)]
    s = m.summarize(eps, NOW)
    assert s["status"]["days_covered"] == 7 and s["headline"]["n_settled_fills"] == 30
    assert s["status"]["too_early"] is False and s["reliability_note"] is None
    # six days is still early
    assert m.summarize([e for e in eps if e["ep_id"] not in {f"ep{i}" for i in range(6, 30, 7)}],
                       NOW)["status"]["too_early"] is True
    # seven days but 29 filled episodes is early, however many contracts they hold
    few = [_ep(i, day=i % 7, count=100, fill={"lo": 0.0, "mid": 50.0, "hi": 90.0}) for i in range(29)]
    f = m.summarize(few, NOW)
    assert f["status"]["days_covered"] == 7 and f["status"]["central_fills"] > 1000
    assert f["status"]["too_early"] is True and f["reliability_note"] is not None
    # enough filled episodes on too few days is early
    big = [_ep(i, day=i % 3, count=100, fill={"lo": 10.0, "mid": 50.0, "hi": 90.0}) for i in range(40)]
    assert m.summarize(big, NOW)["status"]["too_early"] is True


def test_roi_is_null_for_a_scenario_with_no_cost():
    # 2A-3 V3: lo fills nothing, so it has no ROI; the band is the scenarios that have a cost
    eps = [_ep(i, day=i, result="no", fill={"lo": 0.0, "mid": 5.0, "hi": 8.0}) for i in range(3)]
    r = m.summarize(eps, NOW)["headline"]["roi"]
    assert r["mid"] is not None and r["lo"] <= r["mid"] <= r["hi"] and r["hi"] < 0
    assert m.summarize([], NOW)["headline"]["roi"]["mid"] is None


def test_peak_capital_is_max_simultaneous_cost():
    a = _ep(0, count=10, price=0.9, join_h=0, end_h=4)    # cost 9
    b = _ep(1, count=20, price=0.8, join_h=2, end_h=6)    # cost 16, overlaps a
    c = _ep(2, count=100, price=0.9, join_h=8, end_h=9)   # cost 90 alone later
    s = m.summarize([a, b], NOW)
    assert s["headline"]["peak_capital"] == pytest.approx(25.0)
    s = m.summarize([a, b, c], NOW)
    assert s["headline"]["peak_capital"] == pytest.approx(90.0)


def test_peak_capital_back_to_back_not_double_counted_and_open_runs_to_now():
    a = _ep(0, count=10, price=0.5, join_h=0, end_h=2)    # 5
    b = _ep(1, count=10, price=0.5, join_h=2, end_h=4)    # 5 starts as a ends
    assert m.summarize([a, b], NOW)["headline"]["peak_capital"] == pytest.approx(5.0)
    o = _ep(2, count=10, price=0.5, result=None)
    o["end_ts"] = None
    o["close_time"] = None
    o["join_ts"] = NOW - timedelta(hours=1)
    other = _ep(3, count=10, price=0.5, join_h=0, end_h=1)
    other["join_ts"] = NOW - timedelta(minutes=30)
    other["end_ts"] = NOW - timedelta(minutes=10)
    assert m.summarize([o, other], NOW)["headline"]["peak_capital"] == pytest.approx(10.0)


def test_by_series_and_by_cell():
    eps = [
        _ep(0, day=0, series="KXDEMOB", cell=("b", 2)),
        _ep(1, day=0, series="KXDEMOA", cell=("a", 1)),
        _ep(2, day=1, series="KXDEMOA", cell=("a", 1), result="no"),
    ]
    s = m.summarize(eps, NOW)
    assert [r["series"] for r in s["by_series"]] == ["KXDEMOA", "KXDEMOB"]
    a = s["by_series"][0]
    assert a["episodes"] == 2 and a["fills_mid"] == 10.0
    assert a["pnl"]["mid"] == pytest.approx(
        sum(m.score_episode(e)["maker"]["mid"] for e in eps[1:]), abs=1e-3)
    assert [r["cell"] for r in s["by_cell"]] == [["a", 1], ["b", 2]]
    assert s["by_cell"][1]["episodes"] == 1
    # group totals add up to the headline
    assert sum(r["pnl"]["mid"] for r in s["by_series"]) == pytest.approx(s["headline"]["pnl"]["mid"], abs=1e-3)
    assert sum(r["pnl"]["mid"] for r in s["by_cell"]) == pytest.approx(s["headline"]["pnl"]["mid"], abs=1e-3)


def test_daily_cumulative_sorted_with_band():
    eps = [_ep(0, day=2), _ep(1, day=0, result="no"), _ep(2, day=0), _ep(3, day=1)]
    s = m.summarize(eps, NOW)
    days = [d["day"] for d in s["daily"]]
    assert days == sorted(days) and len(days) == 3
    mids = [d["cum_mid"] for d in s["daily"]]
    # last cumulative equals the headline mid
    assert mids[-1] == pytest.approx(s["headline"]["pnl"]["mid"], abs=1e-3)
    d0 = sum(m.score_episode(e)["maker"]["mid"] for e in eps if e["ep_id"] in ("ep1", "ep2"))
    assert mids[0] == pytest.approx(d0, abs=1e-3)
    assert all(d["cum_lo"] <= d["cum_mid"] <= d["cum_hi"] for d in s["daily"])


def test_pass_health_fields():
    passes = [
        {"ts": NOW - timedelta(minutes=10), "ok": True, "truncated": False},
        {"ts": NOW - timedelta(minutes=5), "ok": False, "truncated": True},
        {"ts": NOW - timedelta(minutes=1), "ok": True, "truncated": False},
    ]
    st = m.summarize([], NOW, passes=passes)["status"]
    assert st["last_pass_ts"] == (NOW - timedelta(minutes=1)).isoformat()
    assert st["pass_ok_pct"] == pytest.approx(66.7, abs=0.05)
    assert st["truncated"] is True


def test_flags_passthrough():
    s = m.summarize([], NOW, enabled=False, standin=True)
    assert s["enabled"] is False and s["standin"] is True


# ---------------- bootstrap ----------------
def _many(n=20):
    out = []
    for i in range(n):
        out.append(_ep(i, day=i % 10, result="yes" if i % 4 else "no"))
    return out


def test_bootstrap_deterministic_same_seed():
    eps = _many()
    a = m.summarize(copy.deepcopy(eps), NOW, seed=7, draws=500)
    b = m.summarize(copy.deepcopy(eps), NOW, seed=7, draws=500)
    assert a == b
    # default seed is 7
    assert m.summarize(eps, NOW, draws=500) == a


def test_bootstrap_different_seed_changes_interval_not_point():
    eps = [_ep(i, day=i % 15, result="yes" if i % 3 else "no", count=5 + 3 * (i % 15),
               fill={"lo": 1.0, "mid": 2.0 + i % 15, "hi": 4.0 + i % 15}) for i in range(30)]
    a = m.summarize(eps, NOW, seed=1, draws=500)
    b = m.summarize(eps, NOW, seed=2, draws=500)
    assert a["headline"]["pnl"]["mid"] == b["headline"]["pnl"]["mid"]
    assert a["headline"]["pnl"]["ci90"] != b["headline"]["pnl"]["ci90"]


def test_bootstrap_input_order_independent():
    eps = _many()
    a = m.summarize(eps, NOW, draws=300)
    b = m.summarize(list(reversed(eps)), NOW, draws=300)
    assert a["headline"]["pnl"] == b["headline"]["pnl"]
    assert a["daily"] == b["daily"]


def test_bootstrap_does_not_mutate_input():
    eps = _many(6)
    before = copy.deepcopy(eps)
    m.summarize(eps, NOW, draws=100)
    assert eps == before


def test_ci_brackets_point_estimate_on_mixed_data():
    s = m.summarize(_many(40), NOW, draws=1000)
    lo, hi = s["headline"]["pnl"]["ci90"]
    assert lo < hi
    assert lo <= s["headline"]["pnl"]["mid"] <= hi


def _days_of(eps):
    """Per-day central rows as summarize() feeds the bootstrap."""
    days = {}
    for e in eps:
        d = days.setdefault(e["settled_ts"].date(), {"pnl_maker": 0.0, "pnl_zero": 0.0, "cost": 0.0})
        sc = m.score_episode(e)
        d["pnl_maker"] += sc["maker"]["mid"]
        d["pnl_zero"] += sc["zero"]["mid"]
        d["cost"] += e["fill"]["mid"] * e["price"]
    return [days[k] for k in sorted(days)]


def test_day_clustering_single_day_has_no_spread():
    # all episodes settle on one day: resampling days can only return that day
    eps = [_ep(i, day=0, result="yes" if i % 2 else "no") for i in range(6)]
    ci = m._bootstrap(_days_of(eps), 7, 300)["pnl_maker"]
    mid = sum(m.score_episode(e)["maker"]["mid"] for e in eps)
    assert ci[0] == pytest.approx(mid, abs=1e-3) and ci[1] == pytest.approx(mid, abs=1e-3)
    # summarize() itself shows no interval on one day (too early, 2A-3 F3)
    assert m.summarize(eps, NOW, draws=300)["headline"]["pnl"]["ci90"] is None


def test_day_clustering_resamples_whole_days():
    # two days: +1 on day A (win), a big loss on day B. Each resample picks 2 days
    # with replacement, so the sum can only be A+A, A+B or B+B, never a mix inside a day.
    win = _ep(0, day=0, result="yes", price=0.9, count=10, fill={"lo": 10.0, "mid": 10.0, "hi": 10.0})
    lose = _ep(1, day=1, result="no", price=0.9, count=10, fill={"lo": 10.0, "mid": 10.0, "hi": 10.0})
    pw = m.score_episode(win)["zero"]["mid"]
    pl = m.score_episode(lose)["zero"]["mid"]
    ci = m._bootstrap(_days_of([win, lose]), 7, 400)["pnl_zero"]
    allowed = [2 * pw, pw + pl, 2 * pl]
    assert min(allowed) - 1e-3 <= ci[0] <= max(allowed) + 1e-3
    assert min(allowed) - 1e-3 <= ci[1] <= max(allowed) + 1e-3
    # 5th percentile lands on the worst pile, 95th on the best pile
    assert ci[0] == pytest.approx(2 * pl, abs=1e-3)
    assert ci[1] == pytest.approx(2 * pw, abs=1e-3) or ci[1] == pytest.approx(pw + pl, abs=1e-3)


def test_same_day_episodes_are_one_cluster():
    # same total episodes, but spread over more days gives more distinct outcomes
    a = [_ep(i, day=0, result="yes" if i % 2 else "no") for i in range(8)]
    b = [_ep(i, day=i, result="yes" if i % 2 else "no") for i in range(8)]
    wa = m._bootstrap(_days_of(a), 7, 500)["pnl_maker"]
    wb = m._bootstrap(_days_of(b), 7, 500)["pnl_maker"]
    assert (wb[1] - wb[0]) > (wa[1] - wa[0])


def test_caveats_are_generic_strings():
    joined = " ".join(m.CAVEATS).lower()
    for word in ("own orders", "queue", "cash limit", "outages", "small sample"):
        assert word in joined
    assert len(m.CAVEATS) == 5
