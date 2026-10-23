"""Sprint 2A-3 integration: shadow reliability (caps, honest headline, fee file, cloud settings) end to end with the
existing fakes. Cloud settings through the API with fresh 2FA, a capped cycle through the runner into the store and
the API, the headline gate (7 filled close days and 30 filled episodes), the optional fee file, and the period filter
with the new fields. Synthetic data only (KXDEMO* / KXFAKE* names); no strategy logic written here."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from control_api.services import shadow as sh
from control_api.services import shadow_model as sm
from control_api.tests.test_shadow_cycle import T1, FakeStrategy, live_md, use_strategy, yes_quote
from control_api.tests.test_shadow_reliability import BudgetStrategy, Sim, SimMD, _falling_market, T1 as RT1

UTC = timezone.utc
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_fee_file(tmp_path, monkeypatch):
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(tmp_path / "absent_fee_schedule.json"))
    monkeypatch.delenv(sh.SHADOW_VARIANTS_ENV, raising=False)


def _arm(env, monkeypatch, md, strategy=None):
    r = env.runner
    env.settings.shadow_enabled = True
    r.cloud["shadow_enabled"] = True
    monkeypatch.setattr(r, "role", lambda: "leader")
    r.shadow_factory = lambda: md
    use_strategy(monkeypatch, r, strategy or FakeStrategy([yes_quote()]))
    return r


def _ep(i, join, *, result="yes", fill=5.0, price=0.9, capped=False, count=10.0):
    close = join + timedelta(hours=10)
    return {"methodology": 2,
            "ep_id": f"rie{i}", "env": "prod", "ticker": f"KXDEMOA-R{i}", "series": "KXDEMOA", "side": "yes",
            "book_side": "bid", "price": price, "order_price": price, "count": count, "cost": count * price,
            "cell": ["a"], "close_time": close, "join_ts": join, "last_seen_ts": join,
            "end_ts": join + timedelta(hours=1), "end_reason": "cancel", "trades_read": True,
            "fill": {"lo": fill, "mid": fill, "hi": fill}, "result": result, "settled_ts": close, "pnl": None,
            "standin": False, "ts": join, "capped": capped, "cap_reason": "open_cap" if capped else None}


def _day_eps(days: int, per_day: int, *, start=0, now=None):
    """`per_day` filled episodes on each of `days` distinct close days (all in the last two weeks)."""
    now = now or datetime.now(UTC)
    out = []
    for d in range(days):
        for k in range(per_day):
            e = _ep(f"{d}-{k}", now - timedelta(days=d + 1 + start, hours=k), result="yes" if (d + k) % 7 else "no")
            out.append(e)
    return out


async def _summary(env, q=""):
    r = await env.client.get(f"/api/shadow/summary{q}", headers=env.auth)
    assert r.status_code == 200, r.text
    return r.json()


async def _as_period_one_history(env, eps):
    """Store the episodes and the runner's cached summary (what the whole-history views serve)."""
    await env.store.shadow_upsert_episodes(eps)
    await env.store.set_shadow_summary(sh.build_summary(eps, [], datetime.now(UTC)))


# ================================================================ (a) cloud settings through the API
async def test_shadow_cloud_settings_round_trip_with_fresh_2fa(env):
    r = await env.client.put("/api/cloud", headers=await env.h2fa(),
                             json={"shadow_capital": 300, "shadow_intervals": [10, 5]})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["shadow_capital"] == 300 and s["shadow_intervals"] == [5, 10]          # sorted by the validator
    g = (await env.client.get("/api/cloud", headers=env.auth)).json()
    assert g["settings"]["shadow_capital"] == 300 and g["settings"]["shadow_intervals"] == [5, 10]
    assert g["schema"]["shadow_capital"]["min"] == 20 and g["schema"]["shadow_capital"]["max"] == 100000
    assert g["schema"]["shadow_intervals"]["allowed"] == [5, 7, 10]
    assert env.runner.cloud["shadow_capital"] == 300 and sh.capital_of(env.runner.cloud) == 300.0
    assert (await env.store.get_cloud_settings())["shadow_capital"] == 300


async def test_shadow_cloud_settings_need_fresh_2fa_and_change_nothing_without_it(env):
    before = dict(env.runner.cloud)
    r = await env.client.put("/api/cloud", headers=env.auth, json={"shadow_capital": 999})
    assert r.status_code == 403
    assert env.runner.cloud.get("shadow_capital") == before.get("shadow_capital")
    # a used code is rejected as well
    h = await env.h2fa()
    assert (await env.client.put("/api/cloud", headers=h, json={"shadow_capital": 400})).status_code == 200
    assert (await env.client.put("/api/cloud", headers=h, json={"shadow_capital": 500})).status_code in (401, 403)
    assert env.runner.cloud["shadow_capital"] == 400


@pytest.mark.parametrize("body", [
    {"shadow_capital": 19}, {"shadow_capital": 0}, {"shadow_capital": -5}, {"shadow_capital": 100001},
    {"shadow_capital": True}, {"shadow_capital": "150"},
    {"shadow_intervals": []}, {"shadow_intervals": [6]}, {"shadow_intervals": [5, 5]},
    {"shadow_intervals": [5, 7, 10, 5]}, {"shadow_intervals": "10"}, {"shadow_intervals": [True]},
    {"shadow_intervals": [15]}, {"shadow_intervals": [0]},
])
async def test_shadow_cloud_settings_out_of_range_rejected(env, body):
    r = await env.client.put("/api/cloud", headers=await env.h2fa(), json=body)
    assert r.status_code in (400, 422), r.text
    assert env.runner.cloud.get("shadow_capital") in (None, 150) and env.runner.cloud.get("shadow_intervals") in (None, [10])


@pytest.mark.parametrize("body", [{"shadow_capital": 20}, {"shadow_capital": 100000}, {"shadow_capital": 20.5},
                                  {"shadow_intervals": [5]}, {"shadow_intervals": [5, 7, 10]},
                                  {"shadow_intervals": [7, 5, 10]}])
async def test_shadow_cloud_settings_boundaries_accepted(env, body):
    r = await env.client.put("/api/cloud", headers=await env.h2fa(), json=body)
    assert r.status_code == 200, r.text


async def test_shadow_capital_from_the_api_drives_the_caps(env, monkeypatch):
    """The setting changed through the API is the capital the next cycle's caps use."""
    md = live_md()
    r = _arm(env, monkeypatch, md)
    now = datetime.now(UTC)
    await env.store.shadow_upsert_episodes([{
        "methodology": 2,
        "ep_id": "held-api", "env": "prod", "ticker": "KXDEMOA-26OCT17-B9", "series": "KXDEMOA", "side": "yes",
        "book_side": "bid", "price": 0.5, "order_price": 0.5, "count": 20, "cost": 10.0,
        "join_ts": now - timedelta(hours=2), "end_ts": now - timedelta(hours=1), "end_reason": "cancel",
        "trades_read": True, "result": None, "fill": {"lo": 20.0, "mid": 20.0, "hi": 20.0},
        "close_time": now + timedelta(hours=6)}])
    assert (await env.client.put("/api/cloud", headers=await env.h2fa(), json={"shadow_capital": 20})).status_code == 200
    out = await r.shadow_cycle()
    assert out["ran"] and out["pass"]["held_cost"] == pytest.approx(10.0) and out["pass"]["headroom"] == 0
    assert out["pass"]["capped_new"] == 1
    r._shadow_summary_utc = None
    assert (await env.client.put("/api/cloud", headers=await env.h2fa(), json={"shadow_capital": 100000})).status_code == 200
    out2 = await r.shadow_cycle()
    assert out2["pass"]["headroom"] > 0
    await r.shutdown()


# ================================================================ (b) capped cycle, end to end
async def test_capped_cycle_stores_a_capped_episode_that_is_never_scored(env, monkeypatch):
    r = _arm(env, monkeypatch, live_md())
    r.cloud["shadow_capital"] = 20                                  # ceiling max_open_frac x 20
    now = datetime.now(UTC)
    held = {"methodology": 2,
            "ep_id": "held1", "env": "prod", "ticker": "KXDEMOA-26OCT17-B9", "series": "KXDEMOA", "side": "yes",
            "book_side": "bid", "price": 0.5, "order_price": 0.5, "count": 20, "cost": 10.0,
            "join_ts": now - timedelta(hours=2), "end_ts": now - timedelta(hours=1), "end_reason": "cancel",
            "trades_read": True, "result": None, "fill": {"lo": 20.0, "mid": 20.0, "hi": 20.0},
            "close_time": now + timedelta(hours=6)}
    await env.store.shadow_upsert_episodes([held])
    out = await r.shadow_cycle()
    assert out["ran"] is True and out["pass"]["capped_new"] == 1 and out["pass"]["new_eps"] == 1
    [cap] = [e for e in await env.store.shadow_open_episodes()]
    assert cap["capped"] is True and cap["cap_reason"] in ("open_cap", "series_cap", "run_budget")
    assert cap["fill"] == {"lo": 0.0, "mid": 0.0, "hi": 0.0}
    # both settle: the held one wins, the capped one "wins" too but is never scored
    done = [dict(held, result="yes", settled_ts=now),
            dict(cap, end_ts=now, end_reason="closed", trades_read=True, result="yes", settled_ts=now)]
    await env.store.shadow_upsert_episodes(done)
    s = sh.build_summary(await env.store.shadow_settled(now - timedelta(days=30), scored_only=True), [], now)
    assert s["status"]["settled_episodes"] == 1 and s["status"]["capped_episodes"] == 1
    assert s["headline"]["pnl"]["mid"] == pytest.approx(sm.score_episode(done[0])["maker"]["mid"], abs=1e-3)   # held only
    api = (await env.client.get("/api/shadow/episodes?limit=10", headers=env.auth)).json()["episodes"]
    by_id = {e["ep_id"]: e for e in api}
    assert by_id[cap["ep_id"]]["capped"] is True and by_id[cap["ep_id"]]["cap_reason"] == cap["cap_reason"]
    assert by_id["held1"]["capped"] in (False, None)
    await r.shutdown()


async def test_stacking_one_market_stops_at_the_cap_and_the_summary_excludes_capped(env):
    tight = Sim(SimMD({"KXDEMOA": [RT1]}), BudgetStrategy({}), series=["KXDEMOA"], capital=150.0, frac=0.5)
    held = _falling_market(tight, 12)
    assert max(held) <= 0.5 * 150 + 1e-6
    capped = [e for e in tight.eps.values() if e.get("capped")]
    assert capped, "the cap never bound: the case does not test anything"
    last = max(p["ts"] for p in tight.passes)
    eps = []
    for e in tight.eps.values():
        e = dict(e)
        if e.get("end_ts") is None:
            e.update(end_ts=last, end_reason="cancel", trades_read=True, fill={"lo": 0.0, "mid": 0.0, "hi": 0.0})
        e.update(result="yes", settled_ts=e["close_time"], join_ts=e["join_ts"], env="prod")
        eps.append(e)
    summ = sm.summarize(eps, last + timedelta(days=5))
    assert summ["status"]["capped_episodes"] == len(capped)
    assert summ["status"]["settled_episodes"] == len(eps) - len(capped)
    # the headline is exactly the uncapped episodes' sum: capped ones add nothing
    uncapped = [e for e in eps if not e.get("capped")]
    assert summ["headline"]["pnl"]["mid"] == pytest.approx(sm.summarize(uncapped, last + timedelta(days=5))["headline"]["pnl"]["mid"])
    await env.store.shadow_upsert_episodes([dict(e, env="prod") for e in eps])
    api = (await env.client.get("/api/shadow/episodes?limit=200", headers=env.auth)).json()["episodes"]
    assert sum(1 for e in api if e.get("capped")) == len(capped)


async def test_headline_gate_needs_7_filled_days_and_30_filled_episodes(env):
    now = datetime.now(UTC)
    # 7 close days x 5 filled episodes = 35: both conditions met
    ok = _day_eps(7, 5)
    await _as_period_one_history(env, ok)
    s = await _summary(env, "?period=all")
    assert s["status"]["headline_status"] == "ok" and s["headline"]["headline_status"] == "ok"
    assert s["status"]["days_covered"] == 7 and s["headline"]["n_settled_fills"] == 35
    assert s["headline"]["ci_reliable"] is True
    assert isinstance(s["headline"]["pnl"]["ci90"], list) and len(s["headline"]["pnl"]["ci90"]) == 2
    assert s["day_basis"] == "close_day"
    # 7 days x 4 = 28 filled: too few episodes
    few = _day_eps(7, 4)
    await env.store.set_shadow_summary(sh.build_summary(few, [], now))
    s = await _summary(env, "?period=all")
    assert s["headline"]["headline_status"] == "too_early" and s["headline"]["n_settled_fills"] == 28
    assert s["headline"]["pnl"]["ci90"] is None and s["headline"]["roi"]["ci90"] is None
    assert s["headline"]["ci_reliable"] is False
    # 6 days x 6 = 36 filled: too few days
    short = _day_eps(6, 6)
    await env.store.set_shadow_summary(sh.build_summary(short, [], now))
    s = await _summary(env, "?period=all")
    assert s["headline"]["headline_status"] == "too_early" and s["status"]["days_covered"] == 6
    assert s["headline"]["pnl"]["ci90"] is None


async def test_unfilled_days_do_not_count_toward_the_gate(env):
    now = datetime.now(UTC)
    eps = _day_eps(6, 6) + [_ep(f"z{k}", now - timedelta(days=9 + k), fill=0.0) for k in range(3)]
    await env.store.set_shadow_summary(sh.build_summary(eps, [], now))
    s = await _summary(env, "?period=all")
    assert s["status"]["days_covered"] == 6 and s["status"]["settled_days"] >= 7
    assert s["headline"]["headline_status"] == "too_early" and s["headline"]["pnl"]["ci90"] is None


async def test_headline_extras_present_and_first_fill_per_market(env):
    eps = _day_eps(3, 4)
    twin = dict(eps[0], ep_id="twin", price=0.85, cost=8.5)         # same market again: counted once in first-fill
    await _as_period_one_history(env, eps + [twin])
    h = (await _summary(env, "?period=all"))["headline"]
    for k in ("break_even_win_rate", "observed_win_rate", "n_settled_fills", "peak_held_capital", "return_on_held",
              "first_fill_per_market", "peak_capital", "headline_status"):
        assert k in h, k
    assert h["first_fill_per_market"]["markets"] == len(eps)
    assert h["n_settled_fills"] == len(eps) + 1


# ================================================================ (d) the optional fee file
async def _fee_cycle(env, monkeypatch):
    r = _arm(env, monkeypatch, live_md())
    out = await r.shadow_cycle()
    assert out["ran"] is True
    [ep] = await env.store.shadow_open_episodes()
    await r.shutdown()
    return ep


async def test_fee_file_missing_means_maker_everywhere(env, monkeypatch):
    assert sh.load_fee_schedule() is None
    assert (await _fee_cycle(env, monkeypatch))["fee_mode"] == "maker"


async def test_fee_file_valid_gives_per_series_modes(env, monkeypatch, tmp_path):
    f = tmp_path / "fees.json"
    f.write_text(json.dumps({"default": "maker", "prefix_rules": [{"series_prefix": "KXDEMOA", "mode": "zero"}]}))
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(f))
    ep = await _fee_cycle(env, monkeypatch)
    assert ep["fee_mode"] == "zero"
    sched = sh.load_fee_schedule()
    assert sh.fee_mode_for("KXFAKEZ-26OCT19-T1", sched) == "maker" and sh.fee_mode_for("KXDEMOA-26OCT19-T1", sched) == "zero"
    # a second series with a different prefix keeps the default
    f.write_text(json.dumps({"default": "maker", "prefix_rules": [{"series_prefix": "KXFAKEZ", "mode": "zero"}]}))
    assert sh.fee_mode_for("KXDEMOA-26OCT19-T1", sh.load_fee_schedule()) == "maker"
    assert sh.fee_mode_for("KXFAKEZ-26OCT19-T1", sh.load_fee_schedule()) == "zero"


@pytest.mark.parametrize("text", ["not json {", "[]", json.dumps({"default": "taker"}),
                                  json.dumps({"prefix_rules": [{"series_prefix": "KXDEMOA", "mode": "free"}]}),
                                  json.dumps({"prefix_rules": "KXDEMOA"}), ""])
async def test_fee_file_malformed_means_maker_everywhere(env, monkeypatch, tmp_path, text):
    f = tmp_path / "fees.json"
    f.write_text(text)
    monkeypatch.setenv(sh.FEE_SCHEDULE_ENV, str(f))
    assert sh.load_fee_schedule() is None
    assert (await _fee_cycle(env, monkeypatch))["fee_mode"] == "maker"


async def test_fee_modes_reach_the_summary_and_the_api(env):
    now = datetime.now(UTC)
    eps = [dict(_ep(f"f{k}", now - timedelta(days=1 + k)), fee_mode="zero" if k % 2 else "maker") for k in range(6)]
    await _as_period_one_history(env, eps)
    s = await _summary(env, "?period=all")
    assert s["fee_modes"] == {"zero": 3, "maker": 3} and s["fee_mode"] == "per_series"
    only = [dict(e, fee_mode="maker") for e in eps]
    await env.store.set_shadow_summary(sh.build_summary(only, [], now))
    assert (await _summary(env, "?period=all"))["fee_mode"] == "maker"


# ================================================================ (e) the period filter with the new fields
async def test_period_filter_scopes_capped_count_and_headline_gate_fields(env):
    now = datetime.now(UTC)
    old = [_ep("o1", now - timedelta(days=6)), _ep("o2", now - timedelta(days=6, hours=2), capped=True, fill=0.0),
           _ep("o3", now - timedelta(days=5), capped=True, fill=0.0)]
    new = [_ep("n1", now - timedelta(days=1), fill=8.0), _ep("n2", now - timedelta(days=1, hours=1), fill=4.0)]
    await env.store.shadow_upsert_episodes(old + new)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=3)})
    p1, p2 = await _summary(env, "?period=p1"), await _summary(env, "?period=per2")
    assert p1["status"]["capped_episodes"] == 2 and p2["status"]["capped_episodes"] == 0
    assert p1["status"]["settled_episodes"] == 1 and p2["status"]["settled_episodes"] == 2
    assert p1["headline"]["n_settled_fills"] == 1 and p2["headline"]["n_settled_fills"] == 2
    assert p2["status"]["days_covered"] == 1 and p2["day_basis"] == "close_day"
    for p in (p1, p2):
        assert p["headline"]["headline_status"] == "too_early" and p["headline"]["pnl"]["ci90"] is None
        assert p["period_rule"] == "join_ts"
    ep = (await env.client.get("/api/shadow/episodes?period=per2&limit=50", headers=env.auth)).json()["episodes"]
    assert {e["ep_id"] for e in ep} == {"rien1", "rien2"} and all("capped" in e for e in ep)
    ep1 = (await env.client.get("/api/shadow/episodes?period=p1&limit=50", headers=env.auth)).json()["episodes"]
    assert sum(1 for e in ep1 if e["capped"]) == 2


async def test_period_filter_with_enough_data_shows_a_ci_in_that_period_only(env):
    now = datetime.now(UTC)
    eps = _day_eps(8, 5, now=now)                                   # 40 filled over 8 days, all recent
    await env.store.shadow_upsert_episodes(eps)
    await env.store.add_period({"id": "per2", "name": "Period 2", "started_at": now - timedelta(days=20)})
    s = await _summary(env, "?period=per2")
    assert s["headline"]["headline_status"] == "ok" and len(s["headline"]["pnl"]["ci90"]) == 2
    await env.store.add_period({"id": "per3", "name": "Period 3", "started_at": now})
    s3 = await _summary(env, "?period=per3")
    assert s3["status"]["settled_episodes"] == 0 and s3["headline"]["pnl"]["ci90"] is None
