"""Sprint 2A-3 integration: shadow interval variants end to end (env SHADOW_VARIANTS, cloud shadow_intervals set
through the API). Off: only the baseline runs. On with [10, 7, 5]: each cadence records episodes tagged by interval,
the summary carries `variants` and the paired `variant_compare`, and the load guard skips a variant cycle while a real
pass runs. Synthetic data only (KXDEMO* tickers); fakes from the existing shadow tests."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from control_api import runner as runmod
from control_api.services import shadow as sh
from control_api.tests.test_shadow_cycle import FakeStrategy, live_md, use_strategy, yes_quote

UTC = timezone.utc
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_fee_file(tmp_path, monkeypatch):
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee_schedule.json"))


@pytest.fixture
def variants_on(monkeypatch):
    monkeypatch.setenv(sh.SHADOW_VARIANTS_ENV, "true")


def _arm(env, monkeypatch, md=None, strategy=None):
    r = env.runner
    env.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.shadow_factory = lambda: md or live_md()
    use_strategy(monkeypatch, r, strategy or FakeStrategy([yes_quote()]))
    return r


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not await cond():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


async def _set_intervals(env, value):
    r = await env.client.put("/api/cloud", headers=await env.h2fa(), json={"shadow_intervals": value})
    assert r.status_code == 200, r.text
    return r.json()["settings"]["shadow_intervals"]


async def _summary(env, q=""):
    r = await env.client.get(f"/api/shadow/summary{q}", headers=env.auth)
    assert r.status_code == 200, r.text
    return r.json()


def _pair_ep(i, iv, d, *, now, result="no", fill=6.0):
    join = now - timedelta(days=d + 2, hours=i)
    close = join + timedelta(hours=20)
    return {"ep_id": f"vi{iv}-{d}-{i}", "env": "prod", "ticker": f"KXDEMOA-V{d}-{i}", "series": "KXDEMOA", "side": "no",
            "book_side": "bid", "price": 0.9, "order_price": 0.9, "count": 10.0, "cost": 9.0, "cell": ["c"],
            "join_ts": join, "end_ts": join + timedelta(hours=1), "end_reason": "cancel", "close_time": close,
            "settled_ts": close, "trades_read": True, "result": result, "interval": iv, "capped": False,
            "fill": {"lo": fill, "mid": fill, "hi": fill}, "standin": False, "ts": join}


# ================================================================ (c) off by default: only the baseline
async def test_variants_off_only_the_baseline_runs(env, real_strategy, monkeypatch):
    monkeypatch.delenv(sh.SHADOW_VARIANTS_ENV, raising=False)
    r = _arm(env, monkeypatch)
    assert await _set_intervals(env, [5, 7, 10]) == [5, 7, 10]
    assert sh.variants_enabled() is False and sh.variant_intervals(r.cloud) == []
    for iv in (5, 7):
        assert await r.shadow_cycle(interval=iv) == {"ran": False, "reason": "variant_off"}
    out = await r.shadow_cycle()
    assert out["ran"] is True
    eps = await env.store.shadow_open_episodes()
    assert len(eps) == 1 and sh.interval_of(eps[0]) == 10 and eps[0]["interval"] == 10
    passes = await env.store.shadow_recent_passes(10)
    assert len(passes) == 1 and "interval" not in passes[0]
    s = await _summary(env, "?period=all")
    assert s["variants_enabled"] is False and s["variants"] == [] and s["variant_compare"] == []
    assert r._shadow_variant_task is None
    await r.shutdown()


async def test_variants_off_even_with_intervals_set_the_loop_writes_nothing(env, real_strategy, monkeypatch):
    monkeypatch.delenv(sh.SHADOW_VARIANTS_ENV, raising=False)
    monkeypatch.setattr(runmod, "SHADOW_VARIANT_TICK_S", 0.01)
    r = _arm(env, monkeypatch)
    await _set_intervals(env, [5, 7, 10])
    task = asyncio.create_task(r._shadow_variant_loop())
    await asyncio.sleep(0.15)
    task.cancel()
    assert await env.store.shadow_open_episodes() == [] and await env.store.shadow_recent_passes(10) == []
    await r.shutdown()


# ================================================================ (c) on with [10, 7, 5]
async def test_variants_on_each_cadence_records_tagged_episodes_and_the_summary_compares(env, real_strategy,
                                                                                         monkeypatch, variants_on):
    md = live_md()
    r = _arm(env, monkeypatch, md)
    assert await _set_intervals(env, [10, 7, 5]) == [5, 7, 10]
    assert sh.variant_intervals(r.cloud) == [5, 7]
    base = await r.shadow_cycle()
    n_markets_base = sum(1 for c in md.calls if c[0] == "markets")
    v7, v5 = await r.shadow_cycle(interval=7), await r.shadow_cycle(interval=5)
    assert base["ran"] and v7["ran"] and v5["ran"]
    assert v7["pass"]["interval"] == 7 and v5["pass"]["interval"] == 5 and "interval" not in base["pass"]
    eps = await env.store.shadow_open_episodes()
    assert sorted(sh.interval_of(e) for e in eps) == [5, 7, 10]
    assert len({e["ep_id"] for e in eps}) == 3                      # distinct ids per cadence for the same bid
    assert sum(1 for c in md.calls if c[0] == "markets") < 3 * n_markets_base   # the short read cache is shared
    for iv in (5, 7, 10):
        rows = (await env.client.get(f"/api/shadow/episodes?interval={iv}", headers=env.auth)).json()["episodes"]
        assert [sh.interval_of(e) for e in rows] == [iv]
    # the baseline summary refresh ran in the baseline cycle; refresh once more so it sees every cadence
    r._shadow_summary_utc = None
    assert (await r.shadow_cycle())["ran"]
    s = await _summary(env, "?period=all")
    assert s["variants_enabled"] is True
    assert [v["interval"] for v in s["variants"]] == [5, 7, 10]
    assert [v["baseline"] for v in s["variants"]] == [False, False, True]
    assert [c["interval"] for c in s["variant_compare"]] == [5, 7] and all(c["vs"] == 10 for c in s["variant_compare"])
    for c in s["variant_compare"]:
        assert c["too_early"] is True and c["pnl_diff"]["ci90"] is None and c["ci_reliable"] is False
        assert c["reliability_note"].startswith("Too early to compare")
    await r.shutdown()


async def test_variant_compare_paired_ci_through_the_period_api(env, variants_on):
    now = datetime.now(UTC)
    eps = []
    for d in range(8):
        for i in range(5):
            eps.append(_pair_ep(i, 10, d, now=now))
            eps.append(_pair_ep(i, 5, d, now=now, result="yes" if (d == 3 and i == 0) else "no", fill=7.0))
    await env.store.shadow_upsert_episodes(eps)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=30)})
    env.runner.cloud["shadow_intervals"] = [5, 10]
    s = await _summary(env, "?period=per2")
    rows = {v["interval"]: v for v in s["variants"]}
    assert rows[10]["too_early"] is False and rows[5]["too_early"] is False
    [c] = s["variant_compare"]
    assert c["interval"] == 5 and c["days"] == 8 and c["ci_reliable"] is True
    lo, hi = c["pnl_diff"]["ci90"]
    assert lo <= c["pnl_diff"]["mid"] <= hi
    assert c["fill_rate_diff_mid"] == pytest.approx(0.1, abs=1e-6)
    assert s["status"]["settled_episodes"] == 40                    # the headline is the baseline only
    # the same period with variants removed from the setting: no comparison, headline unchanged
    env.runner.cloud["shadow_intervals"] = [10]
    s2 = await _summary(env, "?period=per2")
    assert s2["variant_compare"] == [] and s2["status"]["settled_episodes"] == 40


async def test_load_guard_skips_a_variant_cycle_while_a_pass_runs_then_resumes(env, real_strategy, monkeypatch,
                                                                              variants_on):
    monkeypatch.setattr(runmod, "SHADOW_VARIANT_TICK_S", 0.01)
    r = _arm(env, monkeypatch)
    await _set_intervals(env, [5, 10])
    r.running_pass = True                                           # a real maker pass is running
    task = asyncio.create_task(r._shadow_variant_loop())
    await asyncio.sleep(0.2)
    assert [e for e in await env.store.shadow_open_episodes() if sh.interval_of(e) == 5] == []
    assert r.shadow_variant_state[5]["last_reason"] == "pass_running"
    r.running_pass = False                                          # it ends: the next tick runs the variant

    async def done():
        return any(sh.interval_of(e) == 5 for e in await env.store.shadow_open_episodes())
    await _until(done)
    task.cancel()
    # slow last pass: the guard says "load" and writes nothing
    r._shadow_last_duration_s = sh.SHADOW_VARIANT_LOAD_S + 1
    assert (await r.shadow_cycle(interval=5))["reason"] == "load"
    await r.shutdown()


async def test_variant_cycle_never_runs_as_standby_or_with_the_stand_in(env, monkeypatch, variants_on):
    r = _arm(env, monkeypatch)
    await _set_intervals(env, [5, 10])
    from control_api.services import botfiles
    monkeypatch.setattr(botfiles, "is_standin", lambda: True)
    assert (await r.shadow_cycle(interval=5))["reason"] == "standin"
    monkeypatch.setattr(botfiles, "is_standin", lambda: False)
    monkeypatch.setattr(r, "role", lambda: "standby")
    assert (await r.shadow_cycle(interval=5))["ran"] is False
    assert await env.store.shadow_open_episodes() == []
    await r.shutdown()
