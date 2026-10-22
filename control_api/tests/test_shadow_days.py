"""Sprint 2A-2 S2: per-event-day returns, day stats and the reliability note (additive; headline unchanged)."""
from __future__ import annotations

import copy

import pytest

from control_api.services import shadow_model as m
from control_api.tests.test_shadow_model import NOW, _ep


def _days(spec):
    """spec: [(day, result, fill_mid)] -> episodes (yes side, price 0.9, count 10)."""
    return [_ep(i, day=d, result=r, fill={"lo": f, "mid": f, "hi": f}) for i, (d, r, f) in enumerate(spec)]


def test_headline_is_unchanged_by_the_new_fields():
    eps = _days([(0, "yes", 5), (1, "no", 5), (2, "yes", 3)])
    s = m.summarize(copy.deepcopy(eps), NOW)
    # the headline is still exactly the sum of the episode scores
    assert s["headline"]["pnl"]["mid"] == pytest.approx(sum(m.score_episode(e)["maker"]["mid"] for e in eps), abs=1e-4)
    assert [r["day"] for r in s["by_event_day"]] == [d["day"] for d in s["daily"]]


def test_by_event_day_rows():
    eps = _days([(0, "yes", 5), (0, "yes", 0), (1, "no", 4)])
    rows = m.summarize(eps, NOW)["by_event_day"]
    assert [r["day"] for r in rows] == ["2026-10-01", "2026-10-02"]
    d0, d1 = rows
    assert d0["episodes"] == 2 and d0["filled_episodes"] == 1
    assert d0["cost"] == {"lo": 4.5, "mid": 4.5, "hi": 4.5}
    win = m.score_episode(eps[0])["maker"]["mid"]
    assert d0["pnl"]["mid"] == pytest.approx(win, abs=1e-4)
    assert d0["return"]["mid"] == pytest.approx(win / 4.5, abs=1e-6)
    assert d1["filled_episodes"] == 1 and d1["pnl"]["mid"] < 0 and d1["return"]["mid"] < 0
    assert set(d0) == {"day", "episodes", "filled_episodes", "cost", "pnl", "return"}


def test_day_without_fills_has_no_return():
    rows = m.summarize(_days([(0, "yes", 0)]), NOW)["by_event_day"]
    assert rows[0]["return"] == {"lo": None, "mid": None, "hi": None}


def test_return_range_ordering_with_scenarios():
    ep = _ep(0, result="no", fill={"lo": 1.0, "mid": 4.0, "hi": 8.0})
    r = m.summarize([ep], NOW)["by_event_day"][0]
    assert r["pnl"]["lo"] <= r["pnl"]["mid"] <= r["pnl"]["hi"]
    assert r["return"]["lo"] <= r["return"]["mid"] <= r["return"]["hi"]


def test_day_stats_mean_sd_best_worst_and_shares():
    eps = _days([(0, "yes", 5), (1, "yes", 10), (2, "no", 1), (3, "yes", 1)])
    s = m.summarize(eps, NOW)
    st = s["day_stats"]
    assert sum(r["pnl"]["mid"] for r in s["by_event_day"]) > 0
    rets = [r["return"]["mid"] for r in s["by_event_day"]]
    mu = sum(rets) / 4
    assert st["event_days"] == 4 and st["days_with_fills"] == 4 and st["filled_episodes"] == 4
    assert st["mean_return_mid"] == pytest.approx(mu, abs=1e-6)
    sd = (sum((x - mu) ** 2 for x in rets) / 3) ** 0.5
    assert st["sd_return_mid"] == pytest.approx(sd, abs=1e-6)
    assert st["worst_day"]["day"] == "2026-10-03" and st["worst_day"]["return_mid"] < 0
    assert st["best_day"]["return_mid"] == max(rets)
    pnls = sorted((r["pnl"]["mid"] for r in s["by_event_day"]), reverse=True)
    total = sum(pnls)
    assert st["best_day_pnl_share"] == pytest.approx(pnls[0] / total, abs=1e-4)
    assert st["top3_pnl_share"] == pytest.approx(sum(pnls[:3]) / total, abs=1e-4)


def test_shares_are_none_when_total_is_not_positive():
    st = m.summarize(_days([(0, "no", 5), (1, "no", 5)]), NOW)["day_stats"]
    assert st["best_day_pnl_share"] is None and st["top3_pnl_share"] is None
    assert st["mean_return_mid"] < 0


def test_single_day_has_no_sd():
    st = m.summarize(_days([(0, "yes", 5)]), NOW)["day_stats"]
    assert st["sd_return_mid"] is None and st["best_day"] == st["worst_day"]
    assert st["best_day_pnl_share"] == 1.0 and st["top3_pnl_share"] == 1.0


def test_reliability_note_until_7_days_and_30_filled_episodes():
    # 7 days x 4 filled = 28 filled episodes: still noted
    few = _days([(d, "yes", 5) for d in range(7) for _ in range(4)])
    note = m.summarize(few, NOW)["reliability_note"]
    assert note.startswith(m.TOO_EARLY_TEXT) and "7 of 7 days" in note and "28 of 30 filled" in note
    # 6 days x 5 = 30 filled, but only 6 days: noted
    assert m.summarize(_days([(d, "yes", 5) for d in range(6) for _ in range(5)]), NOW)["reliability_note"]
    # 7 days, 35 filled: no note
    ok = _days([(d, "yes", 5) for d in range(7) for _ in range(5)])
    assert m.summarize(ok, NOW)["reliability_note"] is None


def test_unfilled_days_do_not_count_toward_the_note():
    eps = _days([(d, "yes", 5) for d in range(6) for _ in range(6)] + [(9, "yes", 0)])
    s = m.summarize(eps, NOW)
    assert s["day_stats"]["event_days"] == 7 and s["day_stats"]["days_with_fills"] == 6
    assert s["reliability_note"] is not None


def test_empty_summary_has_the_new_fields():
    s = m.empty_summary()
    assert s["by_event_day"] == [] and s["day_stats"]["event_days"] == 0
    assert s["reliability_note"].startswith(m.TOO_EARLY_TEXT)
    assert s["arb"]["days"] == [] and s["arb"]["enabled"] is False
    assert set(m.summarize([], NOW)) == set(s)
