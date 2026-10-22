"""Sprint 2A-3 wave 2: shadow interval variants (env SHADOW_VARIANTS, cloud shadow_intervals).

Off by default; each cadence has its own episodes (tagged `interval`) and its own capped position; a shared short
read cache in front of the read-only client; a load guard; the summary's `variants` and `variant_compare`.
Synthetic data only (KXDEMO* tickers); no strategy logic here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.services import shadow as sh
from control_api.services import shadow_model as m
from control_api.tests.test_shadow_cycle import FakeMD, FakeStrategy, inp, leader_runner, live_md, yes_quote

UTC = timezone.utc
NOW = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_fee_file(tmp_path, monkeypatch):
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setenv(sh.SHADOW_LIMITS_ENV, str(tmp_path / "absent_limits.json"))


@pytest.fixture
def variants_on(monkeypatch):
    monkeypatch.setenv(sh.SHADOW_VARIANTS_ENV, "true")


# ================================================================ off by default, baseline unchanged
def test_variants_are_off_by_default(monkeypatch):
    monkeypatch.delenv(sh.SHADOW_VARIANTS_ENV, raising=False)
    assert sh.variants_enabled() is False
    assert sh.variant_intervals({"shadow_intervals": [5, 7, 10]}) == []
    assert sh.summary_intervals({"shadow_intervals": [5, 7]}) is None
    s = sh.build_summary([], [], NOW)
    assert s["variants_enabled"] is False and s["variants"] == [] and s["variant_compare"] == []


def test_baseline_episode_ids_are_unchanged_and_variants_differ():
    import hashlib
    old = hashlib.sha256(f"T|bid|0.4000|{NOW.isoformat()}".encode()).hexdigest()
    assert sh.ep_id_for("T", "bid", 0.4, NOW) == old == sh.ep_id_for("T", "bid", 0.4, NOW, 10)
    assert sh.ep_id_for("T", "bid", 0.4, NOW, 5) != old != sh.ep_id_for("T", "bid", 0.4, NOW, 7)


def test_variant_interval_parsing(variants_on):
    assert sh.variant_intervals({"shadow_intervals": [10, 7, 5, 5]}) == [5, 7]
    assert sh.variant_intervals({"shadow_intervals": [10]}) == [] and sh.summary_intervals({}) == []
    for bad in ([6], [True], "5", None, [5.0]):
        assert sh.variant_intervals({"shadow_intervals": bad}) == []
    assert sh.interval_of({}) == 10 and sh.interval_of({"interval": 5}) == 5
    assert sh.interval_of({"interval": True}) == 10 and sh.interval_of({"interval": "5"}) == 10


def test_run_cycle_tags_episodes_and_caps_the_variant_share():
    res = sh.run_cycle(FakeMD(), FakeStrategy([yes_quote()]), inp(interval=5))
    [ep] = res.upserts
    assert ep["interval"] == 5 and ep["ep_id"] == sh.ep_id_for(ep["ticker"], "bid", 0.4, NOW, 5)
    res = sh.run_cycle(FakeMD(), FakeStrategy([yes_quote()]), inp(interval=5, new_today=10, new_cap=10))
    assert res.upserts == [] and res.pass_doc["truncated"] is True
    # retire: open episodes of a cadence that was turned off end shadow_off at their last observation
    res = sh.run_cycle(FakeMD(), FakeStrategy(), inp(retire=[ep], now=NOW + timedelta(minutes=10)))
    [u] = res.upserts
    assert u["end_reason"] == "shadow_off" and u["end_ts"] == NOW


# ================================================================ isolation: read cache, GET only, no credentials
def test_cached_view_exposes_only_the_four_reads_and_never_caches_failures():
    calls = []

    class MD:
        def markets(self, series, status, min_close_ts=None):
            calls.append("markets")
            return [{"ticker": "KXDEMOA-1"}], False

        def orderbook(self, ticker, depth=None):
            calls.append("orderbook")
            if ticker == "bad":
                raise ConnectionError("down")
            return {"yes_dollars": [["0.4000", "5"]]}

        def market(self, ticker):
            calls.append("market")
            return {"ticker": ticker}

        def trades(self, ticker, min_ts, max_ts):
            calls.append("trades")
            return [], False

    cache = sh.ReadCache(ttl_s=60)
    v = cache.view(MD())
    for name in ("request", "create_order", "cancel_order", "get_balance", "get_positions", "_client", "session"):
        assert not hasattr(v, name)
    a = v.orderbook("KXDEMOA-1")
    a["yes_dollars"].append("mutated")                          # callers get copies: the cache stays clean
    assert v.orderbook("KXDEMOA-1") == {"yes_dollars": [["0.4000", "5"]]}
    v.markets("KXDEMOA", "open"), v.markets("KXDEMOA", "open"), v.market("X")
    v.trades("X", 1, 2), v.trades("X", 1, 2)
    assert calls.count("orderbook") == 1 and calls.count("markets") == 1 and cache.hits == 2
    assert calls.count("trades") == 2 and len(cache) == 3      # S2: trades are never cached
    for _ in range(2):
        with pytest.raises(ConnectionError):
            v.orderbook("bad")
    assert calls.count("orderbook") == 3                        # a failure is retried, never cached
    expired = sh.ReadCache(ttl_s=0)
    ev = expired.view(MD())
    ev.market("Y"), ev.market("Y")
    assert calls.count("market") == 3


def test_s2_read_cache_prunes_expired_entries_on_every_insert_and_stays_bounded(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(sh.time, "monotonic", lambda: clock["t"])
    c = sh.ReadCache(ttl_s=60, max_items=5)
    for i in range(50):
        c.get(("k", i), lambda: i)
    assert len(c) == 5                                          # bounded: the oldest are dropped first
    assert c.get(("k", 49), lambda: -1) == 49 and c.get(("k", 0), lambda: -1) == -1
    clock["t"] += 61                                            # everything expires
    c.get(("new", 1), lambda: 1)
    assert len(c) == 1                                          # expired entries go on the next insert


def test_spy_transport_through_the_cache_records_only_public_gets():
    from control_api.tests.test_shadow_guard import BASE, SpyAdapter
    spy = SpyAdapter()
    cache = sh.ReadCache()
    md = cache.view(sh.build_market_data(adapter=spy))
    st = FakeStrategy([yes_quote()])
    [ep] = sh.run_cycle(md, st, inp(interval=5)).upserts
    n = len(spy.seen)
    sh.run_cycle(md, st, inp(interval=7))                       # same reads within the cache window: reused
    assert len(spy.seen) == n and cache.hits > 0
    st.quotes = []
    sh.run_cycle(md, st, inp(interval=5, open_eps=[ep], now=NOW + timedelta(minutes=5)))
    for method, url, headers in spy.seen:
        assert method == "GET" and url.startswith(BASE + "/markets") and "/portfolio" not in url
        assert not {h.lower() for h in headers} & {"authorization", "kalshi-access-key", "kalshi-access-signature"}


# ================================================================ runner: load guard, isolation of positions
@pytest.mark.asyncio
async def test_runner_variant_is_off_without_the_env(settings, bot_paths, monkeypatch):
    monkeypatch.delenv(sh.SHADOW_VARIANTS_ENV, raising=False)
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.cloud["shadow_intervals"] = [5, 10]
    assert (await r.shadow_cycle(interval=5)) == {"ran": False, "reason": "variant_off"}
    assert await store.shadow_recent(10) == []
    out = await r.shadow_cycle()                                # the baseline runs exactly as before
    assert out["ran"] is True and "interval" not in out["pass"]
    summary = await store.get_shadow_summary()
    assert summary["variants_enabled"] is False and summary["variants"] == []
    assert r._shadow_variant_task is None
    await r.shutdown()


@pytest.mark.asyncio
async def test_runner_variant_load_guard(settings, bot_paths, monkeypatch, variants_on):
    from control_api.services import botfiles
    monkeypatch.setattr(botfiles, "is_standin", lambda: False)
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.cloud["shadow_intervals"] = [5, 10]
    assert (await r.shadow_cycle(interval=7))["reason"] == "variant_off"          # not in shadow_intervals
    r._shadow_last_duration_s = sh.SHADOW_VARIANT_LOAD_S + 1
    assert (await r.shadow_cycle(interval=5))["reason"] == "load"
    r._shadow_last_duration_s = 1.0
    r.running_pass = True
    assert (await r.shadow_cycle(interval=5))["reason"] == "pass_running"
    r.running_pass = False
    r._stopping = True
    assert (await r.shadow_cycle(interval=5))["reason"] == "stop_running"
    r._stopping = False
    async with r._shadow_cycle_lock:                            # another cadence is writing: skip, never queue
        assert (await r.shadow_cycle(interval=5))["reason"] == "busy"
        assert (await r.shadow_cycle())["reason"] == "busy"
    monkeypatch.setattr(r, "role", lambda: "standby")
    assert (await r.shadow_cycle(interval=5))["reason"] == "standby"           # leader only
    assert await store.shadow_recent(10) == []
    await r.shutdown()


@pytest.mark.asyncio
async def test_runner_variant_has_its_own_episodes_position_and_summary_rows(settings, bot_paths, monkeypatch,
                                                                             variants_on):
    from control_api.services import botfiles
    monkeypatch.setattr(botfiles, "is_standin", lambda: False)
    store = MemoryStore()
    r = await leader_runner(settings, store, live_md(), monkeypatch, strategy=FakeStrategy([yes_quote()]))
    r.cloud["shadow_intervals"] = [5, 10]
    r.cloud["shadow_capital"] = 20
    now = datetime.now(UTC)
    await store.shadow_upsert_episodes([{                      # a large held BASELINE position
        "ep_id": "held-base", "env": "prod", "ticker": "KXDEMOA-26OCT17-B9", "series": "KXDEMOA", "side": "yes",
        "book_side": "bid", "price": 0.5, "order_price": 0.5, "count": 20, "cost": 10.0,
        "join_ts": now - timedelta(hours=2), "end_ts": now - timedelta(hours=1), "end_reason": "cancel",
        "trades_read": True, "result": None, "fill": {"lo": 20.0, "mid": 20.0, "hi": 20.0},
        "close_time": now + timedelta(hours=6), "interval": 10}])
    base = await r.shadow_cycle()
    var = await r.shadow_cycle(interval=5)
    assert base["ran"] and var["ran"]
    assert base["pass"]["held_cost"] == pytest.approx(10.0) and base["pass"]["headroom"] == 0
    assert var["pass"]["held_cost"] == 0 and var["pass"]["headroom"] > 0 and var["pass"]["interval"] == 5
    opened = await store.shadow_open_episodes()
    by_iv = {sh.interval_of(e): e for e in opened}
    assert by_iv[10]["capped"] is True and by_iv[5]["capped"] is False
    passes = await store.shadow_recent_passes(10)
    assert sorted(sh.interval_of(p) for p in passes) == [5, 10]
    r._shadow_summary_utc = None
    await r.shadow_cycle()
    s = await store.get_shadow_summary()
    assert s["variants_enabled"] is True and [v["interval"] for v in s["variants"]] == [5, 10]
    assert s["status"]["capped_episodes"] >= 1                 # the baseline headline sees only interval 10
    [cmp] = s["variant_compare"]
    assert cmp["interval"] == 5 and cmp["vs"] == 10 and cmp["too_early"] is True and cmp["pnl_diff"]["ci90"] is None
    # turning the variant off retires its open episodes on the next baseline cycle
    r.cloud["shadow_intervals"] = [10]
    await r.shadow_cycle()
    still = [e for e in await store.shadow_open_episodes() if sh.interval_of(e) == 5]
    assert still == []
    await r.shutdown()


# ================================================================ summary blocks: math
def _ep(i, *, iv, day, result="no", price=0.9, fill=6.0):
    base = datetime(2026, 10, 1, 8, 0, tzinfo=UTC) + timedelta(days=day)
    return {"ep_id": f"{iv}-{i}", "ticker": f"KXDEMOA-{day}-{i}", "series": "KXDEMOA", "side": "no",
            "result": result, "price": price, "count": 10.0, "cost": 10 * price, "cell": ["c"],
            "join_ts": base, "end_ts": base + timedelta(hours=1), "close_time": base + timedelta(hours=20),
            "settled_ts": base + timedelta(hours=20), "fill": {"lo": fill, "mid": fill, "hi": fill}, "interval": iv}


def _paired(days=8, per_day=5, var_loses_on=(3,)):
    eps = []
    for d in range(days):
        for i in range(per_day):
            eps.append(_ep(i, iv=10, day=d))
            eps.append(_ep(i, iv=5, day=d, result="yes" if (d in var_loses_on and i == 0) else "no", fill=7.0))
    return eps


def test_variant_compare_is_the_paired_daily_difference_with_a_ci_past_the_gate():
    eps = _paired()
    variants, [c] = m.variant_blocks(eps, datetime(2026, 11, 1, tzinfo=UTC), [5], draws=400,
                                     interval_of=sh.interval_of)
    rows = {v["interval"]: v for v in variants}
    assert rows[10]["baseline"] is True and rows[5]["baseline"] is False
    assert rows[10]["too_early"] is False and rows[5]["too_early"] is False
    pb = sum(m.score_episode(e)["maker"]["mid"] for e in eps if e["interval"] == 10)
    pv = sum(m.score_episode(e)["maker"]["mid"] for e in eps if e["interval"] == 5)
    assert rows[10]["pnl"]["mid"] == pytest.approx(pb, abs=1e-3)
    assert rows[5]["pnl"]["mid"] == pytest.approx(pv, abs=1e-3)
    assert c["pnl_diff"]["mid"] == pytest.approx(pv - pb, abs=1e-3) and c["days"] == 8
    lo, hi = c["pnl_diff"]["ci90"]
    assert lo <= c["pnl_diff"]["mid"] <= hi and lo < hi and c["ci_reliable"] is True
    assert c["roi_diff_mid"] == pytest.approx(rows[5]["roi"]["mid"] - rows[10]["roi"]["mid"], abs=1e-6)
    assert c["fill_rate_diff_mid"] == pytest.approx(0.1, abs=1e-6)
    assert "Paired by close day over 8 days" in c["reliability_note"]
    again = m.variant_blocks(eps, datetime(2026, 11, 1, tzinfo=UTC), [5], draws=400, interval_of=sh.interval_of)
    assert again == (variants, [c])                             # deterministic


def test_variant_compare_is_null_while_either_side_is_too_early():
    eps = [e for e in _paired(days=3)]
    _, [c] = m.variant_blocks(eps, NOW, [5], interval_of=sh.interval_of)
    assert c["too_early"] is True and c["pnl_diff"]["ci90"] is None and c["ci_reliable"] is False
    assert c["reliability_note"].startswith("Too early to compare")
    s = sh.build_summary(eps, [], NOW, intervals=[5])
    assert s["status"]["settled_episodes"] == 15                # the headline is the baseline only
    assert s["variants_enabled"] is True and len(s["variants"]) == 2


@pytest.mark.asyncio
async def test_period_filter_still_works_with_variants(env, monkeypatch, variants_on):
    env.runner.cloud["shadow_intervals"] = [5, 10]
    now = datetime.now(UTC)

    def ep(i, iv, days_ago):
        e = _ep(i, iv=iv, day=0)
        j = now - timedelta(days=days_ago)
        e.update(ep_id=f"pf{iv}-{i}", join_ts=j, end_ts=j + timedelta(hours=1), close_time=j + timedelta(hours=10),
                 settled_ts=j + timedelta(hours=10), env="prod", book_side="bid", order_price=0.9, trades_read=True)
        return e
    await env.store.shadow_upsert_episodes([ep(1, 10, 6), ep(2, 10, 1), ep(3, 5, 6), ep(4, 5, 1)])
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    r = await env.client.get("/api/shadow/summary?period=per2", headers=env.auth)
    assert r.status_code == 200
    s = r.json()
    assert s["status"]["settled_episodes"] == 1 and s["variants_enabled"] is True
    rows = {v["interval"]: v for v in s["variants"]}
    assert rows[5]["settled_episodes"] == 1 and rows[10]["settled_episodes"] == 1
    e = (await env.client.get("/api/shadow/episodes?interval=5", headers=env.auth)).json()["episodes"]
    assert {x["ep_id"] for x in e} == {"pf5-3", "pf5-4"}
