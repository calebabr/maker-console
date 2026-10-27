"""P&L labels slice: Today's P&L from the viewer's local midnight (validated IANA zone, UTC fallback, per-zone cache)
and the additive pnl_arb / pnl_total blocks on GET /api/status. Storage and settlements stay UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from control_api import runner as runner_mod
from control_api.services import pnl as pnlsvc
from control_api.tests.test_analytics import env  # noqa: F401  (env fixture)
from control_api.tests.test_periods import _fill, _settle

pytestmark = pytest.mark.asyncio
UTC = timezone.utc
CHI = "America/Chicago"
# 2026-10-28 03:00Z = 22:00 CDT on Oct 27: Chicago's day began 2026-10-27 05:00Z, the UTC day began 2026-10-28 00:00Z.
FROZEN = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
CHI_MIDNIGHT = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------- zone validation (pure)
@pytest.mark.parametrize("bad", [None, "", "   ", "Not/AZone", "x" * 500, "America/" + "x" * 80, "../../etc/hosts",
                                 "America/Chicago\n", "America/Chicago; drop", "america/chicago", "UTC\x00", 123,
                                 "A/B/C/D/E", "Etc/GMT+99", "ünicode/zone", {"a": 1}])
async def test_unknown_malformed_or_oversized_zone_falls_back_to_utc(bad):
    tz, name = pnlsvc.resolve_tz(bad)
    assert name == "UTC" and tz is UTC


@pytest.mark.parametrize("good", [CHI, "America/New_York", "Asia/Kolkata", "Europe/London", "Etc/GMT+5", "UTC"])
async def test_known_zones_resolve_to_their_own_name(good):
    tz, name = pnlsvc.resolve_tz(good)
    assert name == good and isinstance(tz, (ZoneInfo, type(UTC)))


# ---------------------------------------------------------------- local midnight (pure)
async def test_local_midnight_before_and_after_the_zone_midnight():
    tz = ZoneInfo(CHI)
    assert pnlsvc.local_midnight_utc(FROZEN, tz) == CHI_MIDNIGHT                              # 22:00 local
    assert pnlsvc.local_midnight_utc(CHI_MIDNIGHT, tz) == CHI_MIDNIGHT                        # exactly midnight
    assert pnlsvc.local_midnight_utc(CHI_MIDNIGHT - timedelta(seconds=1), tz) == CHI_MIDNIGHT - timedelta(days=1)
    assert pnlsvc.local_midnight_utc(FROZEN, UTC) == datetime(2026, 10, 28, tzinfo=UTC)


async def test_half_hour_zone_midnight():
    tz = ZoneInfo("Asia/Kolkata")                       # UTC+5:30: local midnight is 18:30Z the day before
    assert pnlsvc.local_midnight_utc(datetime(2026, 10, 28, 12, 0, tzinfo=UTC), tz) == datetime(2026, 10, 27, 18, 30, tzinfo=UTC)


async def test_daylight_time_change_days_use_the_offset_in_force_at_midnight():
    tz = ZoneInfo(CHI)
    # Fall back (clocks go back at 02:00 CDT on 2026-11-01): midnight is still CDT (UTC-5), so 05:00Z, a 25-hour day.
    assert pnlsvc.local_midnight_utc(datetime(2026, 11, 1, 15, 0, tzinfo=UTC), tz) == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    assert pnlsvc.local_midnight_utc(datetime(2026, 11, 2, 1, 0, tzinfo=UTC), tz) == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    # The next day's midnight is CST (UTC-6): 06:00Z.
    assert pnlsvc.local_midnight_utc(datetime(2026, 11, 2, 12, 0, tzinfo=UTC), tz) == datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
    # Spring forward (02:00 CST -> 03:00 CDT on 2026-03-08): midnight is CST, 06:00Z, a 23-hour day.
    assert pnlsvc.local_midnight_utc(datetime(2026, 3, 8, 20, 0, tzinfo=UTC), tz) == datetime(2026, 3, 8, 6, 0, tzinfo=UTC)


async def test_zone_whose_dst_skips_midnight_starts_the_day_at_its_first_instant():
    # Havana springs forward at 00:00 -> 01:00 on 2026-03-08, so local 00:00 does not exist that day.
    tz = ZoneInfo("America/Havana")
    start = pnlsvc.local_midnight_utc(datetime(2026, 3, 8, 15, 0, tzinfo=UTC), tz)
    assert start.astimezone(tz).date() == datetime(2026, 3, 8).date()
    assert (start - timedelta(minutes=1)).astimezone(tz).date() == datetime(2026, 3, 7).date()


async def test_combine_windows_sums_the_blocks_and_keeps_missing_as_none():
    assert pnlsvc.combine_windows({"d7": 1.5, "d30": 4.0}, {"d7": None, "d30": 2.25}) == {"d7": 1.5, "d30": 6.25}
    assert pnlsvc.combine_windows({"d7": 1.0, "d30": 1.0}, None) == {"d7": 1.0, "d30": 1.0}
    assert pnlsvc.combine_windows(None, None) is None
    assert pnlsvc.combine_windows({"d7": None, "d30": None}, None) is None


# ---------------------------------------------------------------- /api/status end to end
@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(runner_mod, "utcnow", lambda: FROZEN)


async def _seed(store):
    """Winning maker fills (NO at 0.4, 10 contracts => +6.00), each on its own market, settled at the given instants:
    just BEFORE Chicago midnight, just AFTER it, and after the UTC midnight."""
    await store.upsert_fills([_fill("F1", "KXDEMOB-1", ts=FROZEN - timedelta(days=1)),
                              _fill("F2", "KXDEMOB-2", ts=FROZEN - timedelta(days=1)),
                              _fill("F3", "KXDEMOB-3", ts=FROZEN - timedelta(days=1))])
    await store.upsert_settlements([_settle("KXDEMOB-1", "no", CHI_MIDNIGHT - timedelta(minutes=1)),
                                    _settle("KXDEMOB-2", "no", CHI_MIDNIGHT + timedelta(minutes=1)),
                                    _settle("KXDEMOB-3", "no", datetime(2026, 10, 28, 1, 0, tzinfo=UTC))])


async def _status(env, **params):
    r = await env.client.get("/api/status", params=params or None, headers=env.auth)
    assert r.status_code == 200
    return r.json()


async def test_without_a_zone_today_is_the_utc_day_exactly_as_before(env, frozen):
    await _seed(env.store)
    body = await _status(env)
    assert body["pnl_today"]["maker"] == 6.0 and body["pnl_today"]["total"] == 6.0       # only the post-UTC-midnight one
    assert body["pnl_today_tz"] == "UTC" and body["pnl_today_since"].startswith("2026-10-28T00:00:00")


async def test_a_zone_moves_only_the_boundary_to_local_midnight(env, frozen):
    await _seed(env.store)
    body = await _status(env, tz=CHI)
    assert body["pnl_today"]["maker"] == 12.0                       # settled 1 min after local midnight + after UTC midnight
    assert body["pnl_today_tz"] == CHI and body["pnl_today_since"].startswith("2026-10-27T05:00:00")
    assert body["pnl"]["d30"] == 18.0                               # the rolling windows are untouched (UTC settlement time)


async def test_a_settlement_one_minute_before_local_midnight_is_not_today(env, frozen):
    await env.store.upsert_fills([_fill("F1", "KXDEMOB-1", ts=FROZEN - timedelta(days=1))])
    await env.store.upsert_settlements([_settle("KXDEMOB-1", "no", CHI_MIDNIGHT - timedelta(minutes=1))])
    today = (await _status(env, tz=CHI))["pnl_today"]
    assert today["maker"] is None and today["total"] is None


@pytest.mark.parametrize("bad", ["Not/AZone", "x" * 500, "../../etc/hosts", "America/Chicago%0Ax", "", "ü"])
async def test_bad_zone_values_are_ignored_not_errors(env, frozen, bad):
    await _seed(env.store)
    body = await _status(env, tz=bad)
    assert body["pnl_today_tz"] == "UTC" and body["pnl_today"]["maker"] == 6.0


async def test_nothing_settled_is_none_but_a_zero_net_is_zero(env, frozen):
    await env.store.upsert_fills([_fill("W", "KXDEMOB-W", ts=FROZEN - timedelta(days=1), price=0.4),
                                  _fill("L", "KXDEMOB-L", ts=FROZEN - timedelta(days=1), price=0.6)])
    empty = (await _status(env, tz=CHI))["pnl_today"]
    assert empty["total"] is None and empty["maker"] is None                       # nothing settled: no figure at all
    runner = env.runner
    runner._pnl_today.clear()
    await env.store.upsert_settlements([_settle("KXDEMOB-W", "no", CHI_MIDNIGHT + timedelta(hours=1)),
                                        _settle("KXDEMOB-L", "yes", CHI_MIDNIGHT + timedelta(hours=1))])
    flat = (await _status(env, tz=CHI))["pnl_today"]
    assert flat["maker"] == 0.0 and flat["total"] == 0.0 and flat["maker"] is not None   # +6 and -6: a real zero


async def test_cache_is_per_mode_and_zone_and_hits_within_the_ttl(env, frozen, monkeypatch):
    await _seed(env.store)
    calls = []
    real = env.store.list_settlements

    async def counting(*a, **kw):
        calls.append(kw.get("since"))
        return await real(*a, **kw)

    monkeypatch.setattr(env.store, "list_settlements", counting)
    runner = env.runner
    runner._pnl_today.clear()
    await runner.pnl_today(CHI)
    await runner.pnl_today(CHI)
    assert len(calls) == 1                                          # second call is a cache hit
    await runner.pnl_today(None)
    await runner.pnl_today("garbage")                               # both are the UTC entry
    assert len(calls) == 2
    assert {k[1] for k in runner._pnl_today} == {CHI, "UTC"}
    assert calls[0] == CHI_MIDNIGHT and calls[1] == datetime(2026, 10, 28, tzinfo=UTC)


async def test_cache_size_is_bounded(env, frozen):
    runner = env.runner
    runner._pnl_today.clear()
    names = sorted(n for n in __import__("zoneinfo").available_timezones() if "/" in n)[:60]
    for n in names:
        await runner.pnl_today(n)
    assert len(runner._pnl_today) <= runner_mod.PNL_TODAY_CACHE_MAX
    # the newest zone is still cached
    assert (runner.mode, names[-1]) in runner._pnl_today


async def test_a_store_error_gives_the_empty_shape_and_is_not_cached(env, frozen, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(env.store, "list_settlements", boom)
    env.runner._pnl_today.clear()
    body = await _status(env, tz=CHI)
    assert body["pnl_today"]["total"] is None and body["pnl_today_tz"] == CHI
    assert not env.runner._pnl_today


# ---------------------------------------------------------------- pnl_arb / pnl_total
async def test_status_has_arb_and_combined_blocks_beside_the_unchanged_maker_block(env):
    now = datetime.now(UTC).replace(microsecond=0)
    await env.store.upsert_fills([_fill("M", "KXDEMOB-M", ts=now - timedelta(days=2), price=0.4),
                                  _fill("A", "KXDEMOB-A", ts=now - timedelta(days=2), price=0.3, strat="arb")])
    await env.store.upsert_settlements([_settle("KXDEMOB-M", "no", now - timedelta(days=1)),
                                        _settle("KXDEMOB-A", "no", now - timedelta(days=1))])
    body = await _status(env)
    assert body["pnl"]["d30"] == 6.0 and body["pnl"]["settled_fills"] == 1             # maker only, as before
    assert body["pnl_arb"]["d30"] == 7.0
    assert body["pnl_total"] == {"d7": 13.0, "d30": 13.0}
    per = await _status(env, period="all")
    assert per["pnl"]["d30"] == 6.0 and per["pnl_arb"]["d30"] == 7.0 and per["pnl_total"]["d30"] == 13.0
    assert per["pnl"]["basis"] == "fill_date" and per["pnl_arb"]["basis"] == "fill_date"


async def test_no_data_means_null_blocks(env, frozen):
    body = await _status(env)
    assert body["pnl"] is None and body["pnl_arb"] is None and body["pnl_total"] is None
