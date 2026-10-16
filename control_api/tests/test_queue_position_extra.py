"""Queue-position telemetry, extra coverage: Stop invariants with the queue task running/hanging/failing, estimator
edge cases and malformed payloads, reprice window boundaries, the daily cap, TTL/index definitions, and the contract
with the frontend component. Fakes only: no network, no Kalshi, no Atlas."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from control_api.db import MemoryStore
from control_api.db import schemas as S
from control_api.services import queuepos as qp
from control_api.tests.test_queue_position import (  # reuse the fakes of the main suite
    BUCKET_KEYS, SHAPE_KEYS, FakeAccount, FakePublic, T, _iso, _setup, cancel_row, quote_row, resting, write_journal)
from control_api.tests.test_security_regressions import NO_2FA, _expected_2fa, _mutating_routes

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def _assert_stopped(runner):
    assert runner.desired == "stopped" and runner.arb_desired == "stopped"
    assert runner.kill_path.exists() and runner.arb_kill_path.exists()


async def _cleanup(task):
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001
            pass


# =============================================================== Stop with the queue task running / hanging
async def test_stop_never_waits_on_a_hanging_queue_task_and_ends_stopped(env, monkeypatch):
    gate = asyncio.Event()

    async def hang(mode):
        await gate.wait()
        return {}
    monkeypatch.setattr(env.runner, "evaluate_queue_position", hang)
    env.runner.desired = "running"
    task = env.runner._start_queue_position("demo")
    await asyncio.sleep(0)
    t0 = time.monotonic()
    res = await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 1.0 and res["desired"] == "stopped"
    assert not task.done()                                   # Stop did not cancel it or wait for it
    _assert_stopped(env.runner)
    assert env.kalshi.cancelled == ["o1", "o3"]              # only mm- orders, manual-1 untouched
    r = await env.client.post("/api/bot/stop", headers=env.auth)   # HTTP Stop: no 2FA header, still fine
    assert r.status_code == 200
    await _cleanup(task)


async def test_stop_while_queue_reads_block_in_a_worker_thread(env):
    """Kalshi reads of the queue task block (worker thread); Stop's own cancel uses its own client and returns."""
    release = threading.Event()
    now = datetime.now(timezone.utc)

    class Blocking(FakeAccount):
        def get_orders(self, status=None):
            release.wait(5)
            return []
    calls = []

    def factory(mode):
        calls.append(mode)
        return Blocking() if len(calls) == 1 else env.kalshi
    env.runner.kalshi_factory = factory
    env.runner.public_factory = lambda e: FakePublic()
    write_journal(env.paths / "journal.csv", [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    env.runner.desired = "running"
    task = env.runner._start_queue_position("demo")
    await asyncio.sleep(0.2)                                  # the worker thread is now blocked inside get_orders
    t0 = time.monotonic()
    res = await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 1.0 and res["cancelled"] == 2
    _assert_stopped(env.runner)
    release.set()
    await asyncio.wait_for(task, 5)
    _assert_stopped(env.runner)


async def test_store_hanging_inside_the_queue_task_does_not_hold_stop(env, monkeypatch):
    never = asyncio.Event()

    async def hang(*a, **k):
        await never.wait()
    monkeypatch.setattr(env.store, "list_quote_episodes", hang)
    env.runner.desired = "running"
    task = env.runner._start_queue_position("demo")
    await asyncio.sleep(0)
    t0 = time.monotonic()
    await env.runner.stop(by="owner")
    assert time.monotonic() - t0 < 1.0
    _assert_stopped(env.runner)
    await _cleanup(task)


@pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError("y"), KeyError("z"), OSError("net")])
async def test_queue_task_raising_never_escapes_and_stop_still_works(env, monkeypatch, exc):
    async def boom(mode):
        raise exc
    monkeypatch.setattr(env.runner, "evaluate_queue_position", boom)
    env.runner.desired = "running"
    task = env.runner._start_queue_position("demo")
    res = await asyncio.wait_for(task, 2)                     # run_queue_position swallows it
    assert res == {"error": type(exc).__name__}
    assert (await env.runner.stop(by="owner"))["desired"] == "stopped"
    _assert_stopped(env.runner)


async def test_stop_concurrent_with_start_and_a_running_queue_task_ends_stopped(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")
    gate = asyncio.Event()

    async def slow(mode):
        await gate.wait()
        return {}
    monkeypatch.setattr(env.runner, "evaluate_queue_position", slow)
    env.runner.desired = "running"
    task = env.runner._start_queue_position("demo")
    h = await env.h2fa()
    start = asyncio.create_task(env.client.post("/api/bot/start", headers=h))
    stops = [asyncio.create_task(env.client.post("/api/bot/stop", headers=env.auth)) for _ in range(3)]
    rs = await asyncio.gather(start, *stops, return_exceptions=True)
    await env.runner.stop(by="owner")            # a Start that began before the last Stop returned must not win
    for r in rs[1:]:
        assert not isinstance(r, Exception) and r.status_code == 200
    _assert_stopped(env.runner)
    await _cleanup(task)


async def test_stop_works_when_queue_telemetry_state_is_corrupted(env):
    env.runner._stop_cancelled = None                       # the cancel loop's bookkeeping must be best-effort
    res = await env.runner.stop(by="owner")
    assert res["desired"] == "stopped" and res["cancelled"] == 2
    _assert_stopped(env.runner)
    assert env.runner.last_stop_utc is not None


async def test_queue_evaluation_is_read_only_it_never_cancels_or_places_orders(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A")], qpos={"A": 5})
    pub = FakePublic(books={T: {"no_dollars": [["0.4500", "30"]]}})
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    await env.runner.run_queue_position("demo")
    assert acct.cancelled == [] and env.runner.desired == "stopped"
    assert all(c[0] in ("orders", "fills", "request") for c in acct.calls)
    assert all(c[0] in ("orderbook", "trades") for c in pub.calls)


async def test_a_pass_that_does_not_run_the_strategy_starts_no_queue_task(env):
    env.runner.desired = "stopped"
    await env.runner._pass("test")
    assert env.runner._qp_task is None


async def test_queue_task_and_errors_never_leak_secrets_into_logs_or_results(env, caplog):
    now = datetime.now(timezone.utc)
    SECRET = "PRIVATE-KEY-SECRET-123"

    class Leaky(FakeAccount):
        def get_orders(self, status=None):
            raise RuntimeError(SECRET)

        def get_fills(self, min_ts=None):
            raise RuntimeError(SECRET)
    write_journal(_setup(env, Leaky(), FakePublic(fail=True)), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    with caplog.at_level(logging.DEBUG):
        res = await env.runner.run_queue_position("demo")
    assert SECRET not in json.dumps(res, default=str) and SECRET not in caplog.text
    assert SECRET not in json.dumps((await env.client.get("/api/status", headers=env.auth)).json())


# =============================================================== estimator edge cases
async def test_trade_through_exactly_at_the_price_is_not_a_through_one_cent_worse_is():
    def t(yes_price, **k):
        return {"created_time": _iso(T0 + timedelta(minutes=1)), "yes_price_dollars": yes_price, "count": 7, **k}
    assert qp.scan_trades([t("0.5500")], "no", 0.45, T0) == (7.0, False)           # at our price: volume, no through
    assert qp.scan_trades([t("0.5600")], "no", 0.45, T0) == (0.0, True)            # NO 0.44 < 0.45
    assert qp.scan_trades([t("0.5400")], "no", 0.45, T0) == (0.0, False)           # NO 0.46: better price, no signal
    assert qp.scan_trades([t("0.4600")], "yes", 0.47, T0) == (0.0, True)           # YES side through
    assert qp.scan_trades([t("0.4700")], "yes", 0.47, T0) == (7.0, False)
    assert qp.scan_trades([t("0.5500", taker_side="no")], "no", 0.45, T0) == (0.0, False)   # wrong aggressor side
    assert qp.scan_trades([t("0.5600", taker_side="no")], "no", 0.45, T0) == (0.0, True)    # through ignores taker_side


async def test_trade_window_boundaries_are_inclusive():
    def t(dt):
        return {"created_time": _iso(dt), "yes_price_dollars": "0.55", "count": 1}
    trades = [t(T0), t(T0 + timedelta(minutes=5)), t(T0 - timedelta(seconds=1)),
              t(T0 + timedelta(minutes=5, seconds=1))]
    assert qp.scan_trades(trades, "no", 0.45, T0, T0 + timedelta(minutes=5)) == (2.0, False)


async def test_bounds_clip_at_zero_and_never_go_negative():
    b = qp.bounds(-5, 0, False, -3, 1)                                  # negative inputs (corrupt data)
    assert b["nc"] == 0.0 and b["hi"] == 0.0 and b["lo"] == 0.0
    assert qp.bounds(10, 9999, False, 0, 50) == {"nc": 0.0, "hi": 0.0, "lo": 0.0}
    assert qp.bounds(10, 0, False, 5, 50)["hi"] == 0.0                  # level smaller than our own remaining size
    assert qp.bounds(10, 0, False, 100, None)["hi"] == 10.0             # unknown remaining treated as 0
    assert qp.bounds(0, 0, False, 0, 0) == {"nc": 0.0, "hi": 0.0, "lo": 0.0}
    assert qp.bounds(10, 0, False, 100, 0, kq=40)["lo"] == 10.0         # exact value never above the upper bound
    assert qp.bounds(10, 0, True, 100, 0, kq=40) == {"nc": 0.0, "hi": 0.0, "lo": 0.0}


@pytest.mark.parametrize("row", [
    {}, {"event": "quote"}, {"event": "quote", "note": "live order_id=A"},
    {"utc": "2026-10-13T12:00:00+00:00", "event": "quote", "ticker": T, "side": "maybe", "price": 0.4, "count": 1,
     "note": "live order_id=A"},
    {"utc": "not a time", "event": "quote", "ticker": T, "side": "no", "price": 0.4, "count": 1,
     "note": "live order_id=A"},
    {"utc": "2026-10-13T12:00:00+00:00", "event": "quote", "ticker": T, "side": "no", "price": "abc", "count": 1,
     "note": "live order_id=A"},
    {"utc": "2026-10-13T12:00:00+00:00", "event": "quote", "ticker": "", "side": "no", "price": 0.4, "count": 1,
     "note": "live order_id=A"},
    {"utc": "2026-10-13T12:00:00+00:00", "event": "quote", "ticker": T, "side": "no", "price": 0.4, "count": None,
     "note": None},
])
async def test_episode_from_quote_rejects_unreadable_rows(row):
    assert qp.episode_from_quote(row, "demo", ["KXDEMOA"]) is None


@pytest.mark.parametrize("note", [None, "", "garbage", "[]", "[1,2]", '["T","bid"]', '["","bid","0.5"]',
                                  '["T","sideways","0.5"]', '["T","bid","x"]', '{"a":1}', "null", "123"])
async def test_cancel_key_rejects_malformed_notes(note):
    assert qp.cancel_key({"note": note}) is None


async def test_cancel_key_accepts_yes_no_and_bid_ask_spellings():
    assert qp.cancel_key({"note": json.dumps([T, "yes", 0.05])}) == (T, "bid", "0.0500")
    assert qp.cancel_key({"note": json.dumps([T, "no", "0.4500"])}) == (T, "ask", "0.4500")


@pytest.mark.parametrize("book", [None, 5, "x", [], {"no_dollars": 7}, {"no_dollars": [["0.45"]]},
                                  {"no_dollars": [["0.45", "1", "2"]]}, {"no_dollars": {"a": "b"}},
                                  {"no_dollars": [None]}, {"no": [["a", "b"]]}])
async def test_level_size_never_raises_on_malformed_books(book):
    r = qp.level_size(book, "no", 0.45)
    assert r is None or isinstance(r, float)


async def test_apply_public_with_negative_or_small_level_clips_the_join_to_zero():
    ep = qp.episode_from_quote(quote_row(T0, "A", count=10), "demo", ["KXDEMOA"])
    ep["_remaining"] = 10
    qp.apply_public([ep], {"books": {T: {"no_dollars": [["0.4500", "4"]]}}, "trades": {}}, now=T0, new_ids={"A"})
    assert ep["queue_ahead_at_join"] == 0.0 and ep["join_source"] == "book"        # 4 - 10 clipped, not -6
    ep2 = qp.episode_from_quote(quote_row(T0, "B", count=10), "demo", ["KXDEMOA"])
    qp.apply_public([ep2], {"books": {T: {"no_dollars": [["0.4500", "-50"]]}}, "trades": {}}, now=T0, new_ids={"B"})
    assert ep2["queue_ahead_at_join"] == 0.0


async def test_apply_public_with_no_data_at_all_adds_no_observation_and_no_guess():
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    qp.apply_public([ep], {"books": {}, "trades": {}}, now=T0, new_ids={"A"})
    assert ep["queue_ahead_at_join"] is None and ep["last_obs"] is None and ep["obs_count"] == 0


async def test_observations_are_capped_but_counted():
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    data = {"books": {T: {"no_dollars": [["0.4500", "50"]]}}, "trades": {}}
    for i in range(9):
        qp.apply_public([ep], data, now=T0 + timedelta(minutes=i), new_ids={"A"} if i == 0 else set())
    assert len(ep["obs"]) == qp.MAX_OBS and ep["obs_count"] == 9 and ep["last_obs"] == ep["obs"][-1]


async def test_front_at_fill_missing_fields_and_trade_through_before_fill():
    ep = {"ts": T0, "side": "no", "price": 0.45, "queue_ahead_at_join": 80.0,
          "first_fill_utc": T0 + timedelta(minutes=9)}
    through = [{"created_time": _iso(T0 + timedelta(minutes=3)), "yes_price_dollars": "0.57", "count": 1}]
    assert qp.front_at_fill(ep, through, []) == (0.0, True)           # level exhausted before our fill: front
    assert qp.front_at_fill({**ep, "first_fill_utc": None}, through, []) == (None, None)
    assert qp.front_at_fill({**ep, "ts": None}, through, []) == (None, None)
    assert qp.front_at_fill({**ep, "queue_ahead_at_join": 0.0}, [], []) == (0.0, True)   # joined an empty level


async def test_front_at_fill_just_under_one_contract_counts_as_front_one_does_not():
    ep = {"ts": T0, "side": "no", "price": 0.45, "queue_ahead_at_join": 10.5,
          "first_fill_utc": T0 + timedelta(minutes=9)}

    def tr(n):
        return [{"created_time": _iso(T0 + timedelta(minutes=3)), "yes_price_dollars": "0.55", "count": n}]
    assert qp.front_at_fill(ep, tr(10), []) == (0.5, True)
    assert qp.front_at_fill(ep, tr(9.5), []) == (1.0, False)


# ------------------------------------------------ Kalshi queue_positions endpoint failing -> book fallback
async def test_collect_account_queue_positions_failure_is_recorded_not_raised_and_other_reads_survive():
    class A(FakeAccount):
        def request(self, *a, **k):
            raise RuntimeError("404 not found")
    out = qp.collect_account(A(resting=[{"order_id": "A", "client_order_id": "mm-1"},
                                        {"order_id": "M", "client_order_id": "manual-1"}]), [T], T0)
    assert out["qpos"] == {} and [o["order_id"] for o in out["resting"]] == ["A"]    # manual orders filtered out
    assert out["fills"] == [] and out["errors"] == ["queue_positions: RuntimeError"]


async def test_collect_account_second_qpos_chunk_is_skipped_after_a_failure_but_first_kept():
    class A(FakeAccount):
        n = 0

        def request(self, method, endpoint, params=None, auth=False, **kw):
            A.n += 1
            if A.n == 2:
                raise RuntimeError("x")
            return {"queue_positions": [{"order_id": "A", "queue_position": 3}]}
    tickers = [f"K{i}" for i in range(45)]                           # 3 chunks wanted, only MAX_QPOS_CALLS allowed
    out = qp.collect_account(A(), tickers, T0)
    assert out["qpos"] == {"A": 3.0} and A.n == 2 and out["calls"] == 2 + 2


@pytest.mark.parametrize("body", [None, [], "oops", 5, {}, {"queue_positions": None}, {"queue_positions": "x"},
                                  {"queue_positions": [None, 5, "x"]}, {"queue_positions": [{}]},
                                  {"queue_positions": [{"order_id": "A"}]},
                                  {"queue_positions": [{"order_id": "A", "queue_position": "n/a"}]},
                                  {"queue_positions": [{"order_id": "A", "queue_position": True}]}])
async def test_collect_account_malformed_queue_positions_payloads_never_raise(body):
    class A(FakeAccount):
        def request(self, *a, **k):
            return body
    out = qp.collect_account(A(), [T], T0)
    assert out["qpos"] == {}


async def test_collect_account_reads_the_fp_field_and_prefers_it():
    class A(FakeAccount):
        def request(self, *a, **k):
            return {"queue_positions": [{"order_id": "A", "queue_position_fp": "12.5", "queue_position": 99},
                                        {"order_id": "B", "queue_position": 0}]}
    assert qp.collect_account(A(), [T], T0)["qpos"] == {"A": 12.5, "B": 0.0}


async def test_collect_account_with_no_client_or_no_request_method():
    assert qp.collect_account(None, [T], T0)["resting"] is None

    class Bare:
        def get_orders(self, status=None):
            return None

        def get_fills(self, min_ts=None):
            return None
    out = qp.collect_account(Bare(), [T], T0)
    assert out["resting"] == [] and out["fills"] == [] and out["qpos"] == {} and out["errors"] == []


async def test_runner_falls_back_to_the_book_when_the_qpos_endpoint_fails_but_resting_works(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A", 10)], qpos=None)           # endpoint raises
    pub = FakePublic(books={T: {"no_dollars": [["0.4500", "70"]]}})
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    res = await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["join_source"] == "book" and ep["queue_ahead_at_join"] == 60.0 and ep["last_obs"]["kq"] is None
    assert any("queue_positions" in e for e in res["errors"])


async def test_kalshi_exact_value_is_not_overwritten_by_the_book_on_later_passes(env):
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A", 10)], qpos={"A": 11})
    pub = FakePublic(books={T: {"no_dollars": [["0.4500", "300"]]}})
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    await env.runner.evaluate_queue_position("demo")
    acct.qpos = None                                                    # endpoint starts failing
    pub.books[T] = {"no_dollars": [["0.4500", "20"]]}
    await env.runner.evaluate_queue_position("demo")
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["queue_ahead_at_join"] == 11 and ep["join_source"] == "kalshi" and ep["obs_count"] == 2


# ------------------------------------------------ malformed public/account payloads
@pytest.mark.parametrize("trades", [[{}], [{"created_time": "bad"}],
                                    [{"created_time": _iso(T0), "yes_price_dollars": "abc"}],
                                    [{"created_time": _iso(T0), "yes_price_dollars": "0.55", "count_fp": "n/a"}],
                                    [{"created_time": _iso(T0), "yes_price_dollars": None, "yes_price": None}]])
async def test_scan_trades_never_raises_on_malformed_trade_rows(trades):
    vol, through = qp.scan_trades(trades, "no", 0.45, T0 - timedelta(days=1))
    assert isinstance(vol, float) and isinstance(through, bool)


@pytest.mark.parametrize("trades", [[None], ["x"], [5], [[]]])
async def test_scan_trades_ignores_non_dict_trade_rows(trades):
    vol, through = qp.scan_trades(trades, "no", 0.45, T0 - timedelta(days=1))
    assert (vol, through) == (0.0, False)


@pytest.mark.parametrize("body", [None, [], "oops", {"trades": None}, {"trades": "x"}, {"trades": 5}, {"cursor": 5}])
async def test_collect_public_malformed_trade_pages_never_raise(body):
    class P(FakePublic):
        def request(self, *a, **k):
            return body
    out = qp.collect_public(P(), [], [(T, T0)])
    assert isinstance(out["trades"], dict)                              # an error is recorded or the page is empty


@pytest.mark.parametrize("ob", [None, [], "x", {"orderbook_fp": None}, {"orderbook_fp": 5}, {"orderbook": []}])
async def test_collect_public_malformed_orderbooks_never_raise_and_yield_no_guess(ob):
    class P(FakePublic):
        def get_orderbook(self, ticker, depth=None):
            return ob
    out = qp.collect_public(P(), [T], [])
    assert qp.level_size(out["books"].get(T), "no", 0.45) in (None, 0.0)


async def test_collect_public_pagination_is_bounded_and_flags_truncation():
    class P(FakePublic):
        n = 0

        def request(self, method, endpoint, params=None, **kw):
            P.n += 1
            return {"trades": [{"created_time": _iso(T0), "yes_price_dollars": "0.55", "count": 1}],
                    "cursor": f"c{P.n}"}
    out = qp.collect_public(P(), [], [(T, T0)])
    assert P.n == qp.MAX_PAGES and T in out["truncated"] and len(out["trades"][T]) == qp.MAX_PAGES


async def test_apply_public_with_garbage_trades_in_the_runner_does_not_lose_the_pass(env):
    """A trade list holding junk rows must not abort the whole evaluation (the episode is still written)."""
    now = datetime.now(timezone.utc)
    acct = FakeAccount(resting=[resting("A", 10)])

    class Junk(FakePublic):
        def request(self, method, endpoint, params=None, **kw):
            return {"trades": [None, "x", {"created_time": _iso(now), "yes_price_dollars": "0.55", "count": 3}],
                    "cursor": None}
    pub = Junk(books={T: {"no_dollars": [["0.4500", "40"]]}})
    write_journal(_setup(env, acct, pub), [quote_row(now - timedelta(minutes=1), "A")])
    await env.runner._sync_journal("demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert "error" not in res, res
    ep = (await env.store.list_quote_episodes())[0]
    assert ep["queue_ahead_at_join"] == 30.0 and ep["obs_count"] == 1


@pytest.mark.parametrize("fills", [
    [{}], [{"order_id": "A"}], [{"order_id": "A", "is_taker": False, "count_fp": "x", "created_time": "bad"}],
    [{"order_id": "A", "is_taker": True, "count_fp": "5"}], [{"fill_id": "f", "order_id": None}],
])
async def test_apply_account_malformed_fills_do_not_raise(fills):
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    qp.apply_account([ep], {"fills": fills, "resting": [resting("A")], "qpos": {}}, now=T0, new_ids={"A"},
                     stop_ids=set(), last_stop_utc=None)
    assert ep["status"] == "open" and ep["outcome"] is None


async def test_taker_fills_never_count_as_maker_fills():
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    taker = {"order_id": "A", "fill_id": "f1", "is_taker": True, "count_fp": "10", "created_time": _iso(T0)}
    qp.apply_account([ep], {"fills": [taker], "resting": [], "qpos": {}}, now=T0 + timedelta(hours=1),
                     new_ids=set(), stop_ids=set(), last_stop_utc=None)
    assert ep["filled_count"] == 0.0 and ep["outcome"] == "expired"


async def test_partial_fill_then_gone_is_expired_not_filled_and_keeps_its_fills():
    ep = qp.episode_from_quote(quote_row(T0, "A", count=10), "demo", ["KXDEMOA"])
    f = {"order_id": "A", "fill_id": "f1", "is_taker": False, "count_fp": "4",
         "created_time": _iso(T0 + timedelta(minutes=1))}
    qp.apply_account([ep], {"fills": [f], "resting": [], "qpos": {}}, now=T0 + timedelta(hours=1), new_ids=set(),
                     stop_ids=set(), last_stop_utc=None)
    assert ep["outcome"] == "expired" and ep["filled_count"] == 4.0 and ep["fill_ids"] == ["f1"]


async def test_resting_read_failure_leaves_young_episodes_open_and_expires_ancient_ones():
    young = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    old = qp.episode_from_quote(quote_row(T0 - qp.OPEN_MAX_AGE - timedelta(hours=1), "B"), "demo", ["KXDEMOA"])
    qp.apply_account([young, old], {"fills": [], "resting": None, "qpos": {}}, now=T0, new_ids=set(),
                     stop_ids=set(), last_stop_utc=None)
    assert young["status"] == "open" and old["outcome"] == "expired"


async def test_stop_classification_uses_stop_ids_and_stop_time():
    a = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    b = qp.episode_from_quote(quote_row(T0, "B"), "demo", ["KXDEMOA"])
    c = qp.episode_from_quote(quote_row(T0 + timedelta(hours=2), "C"), "demo", ["KXDEMOA"])   # placed after Stop
    qp.apply_account([a, b, c], {"fills": [], "resting": [], "qpos": {}}, now=T0 + timedelta(hours=3),
                     new_ids=set(), stop_ids={"A"}, last_stop_utc=T0 + timedelta(hours=1))
    assert (a["outcome"], b["outcome"], c["outcome"]) == ("stopped", "stopped", "expired")


# =============================================================== reprice detection window (180 s)
def _reprice_outcome(gap_s, **quote_kw):
    t_cancel = T0 + timedelta(minutes=5)
    open_eps: dict = {}
    qp.apply_journal([quote_row(T0, "A", 0.45)], open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=T0)
    q = {"price": 0.46, "side": "no", "ticker": T, **quote_kw}
    rows = [cancel_row(t_cancel, 0.45), quote_row(t_cancel + timedelta(seconds=gap_s), "B", **q)]
    _, closed, _ = qp.apply_journal(rows, open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=t_cancel)
    return closed[0]["outcome"]


async def test_reprice_window_is_180_seconds():
    assert qp.REPRICE_WINDOW == timedelta(seconds=180)


@pytest.mark.parametrize("gap,expected", [(0, "repriced"), (1, "repriced"), (179, "repriced"), (180, "repriced"),
                                          (180.001, "cancelled"), (181, "cancelled"), (600, "cancelled")])
async def test_reprice_window_boundaries(gap, expected):
    assert _reprice_outcome(gap) == expected


async def test_requote_at_the_same_price_is_not_a_reprice():
    assert _reprice_outcome(5, price=0.45) == "cancelled"


async def test_requote_on_another_side_or_ticker_is_not_a_reprice():
    assert _reprice_outcome(5, side="yes") == "cancelled"
    assert _reprice_outcome(5, ticker="KXDEMOB-X") == "cancelled"


async def test_a_quote_before_the_cancel_is_not_a_reprice():
    t_cancel = T0 + timedelta(minutes=5)
    open_eps: dict = {}
    qp.apply_journal([quote_row(T0, "A", 0.45)], open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=T0)
    rows = [quote_row(t_cancel - timedelta(seconds=10), "B", 0.46), cancel_row(t_cancel, 0.45)]
    _, closed, _ = qp.apply_journal(rows, open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=t_cancel)
    assert closed[0]["outcome"] == "cancelled"


async def test_a_far_later_row_ends_the_search_even_if_a_matching_quote_follows_it():
    t_cancel = T0 + timedelta(minutes=5)
    open_eps: dict = {}
    qp.apply_journal([quote_row(T0, "A", 0.45)], open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=T0)
    rows = [cancel_row(t_cancel, 0.45),
            {"utc": (t_cancel + timedelta(minutes=10)).isoformat(), "event": "heartbeat"},
            quote_row(t_cancel + timedelta(seconds=5), "B", 0.46)]       # out of order row: after the break
    _, closed, _ = qp.apply_journal(rows, open_eps, env="demo", series_list=["KXDEMOA"], allowance=9, now=t_cancel)
    assert closed[0]["outcome"] == "cancelled"


async def test_two_cancels_close_the_two_oldest_matching_episodes_one_each():
    open_eps: dict = {}
    qp.apply_journal([quote_row(T0, "A", 0.45), quote_row(T0 + timedelta(minutes=1), "B", 0.45)], open_eps,
                     env="demo", series_list=["KXDEMOA"], allowance=9, now=T0)
    t = T0 + timedelta(minutes=5)
    _, closed, _ = qp.apply_journal([cancel_row(t, 0.45)], open_eps, env="demo", series_list=["KXDEMOA"],
                                    allowance=9, now=t)
    assert [e["order_id"] for e in closed] == ["A"] and set(open_eps) == {"B"}


async def test_cancel_never_closes_an_episode_of_another_env():
    open_eps: dict = {}
    qp.apply_journal([quote_row(T0, "L", 0.45)], open_eps, env="live", series_list=["KXDEMOA"], allowance=9, now=T0)
    t = T0 + timedelta(minutes=5)
    _, closed, _ = qp.apply_journal([cancel_row(t, 0.45)], open_eps, env="demo", series_list=["KXDEMOA"],
                                    allowance=9, now=t)
    assert closed == [] and "L" in open_eps


# =============================================================== daily cap
async def test_daily_cap_constant_and_allowance_rollover(env):
    assert qp.DAILY_NEW_CAP == 1200
    r = env.runner
    d1 = datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)
    d2 = datetime(2026, 10, 14, 0, 1, tzinfo=timezone.utc)
    assert r._qp_allowance(d1) == 1200
    r._qp_day_count = 1199
    assert r._qp_allowance(d1) == 1
    r._qp_day_count = 5000
    assert r._qp_allowance(d1) == 0                                   # never negative
    assert r._qp_allowance(d2) == 1200 and r._qp_day_count == 0       # new UTC day resets


async def test_daily_cap_through_the_runner_drops_the_excess_and_logs(env, caplog):
    now = datetime.now(timezone.utc)
    _setup(env, FakeAccount(), FakePublic())
    env.runner._capture_queue_rows([quote_row(now - timedelta(minutes=1), f"O{i}") for i in range(1250)], "demo")
    with caplog.at_level(logging.WARNING):
        res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 1200 and res["dropped"] == 50 and "error" not in res
    assert "daily cap reached" in caplog.text
    assert len(await env.store.list_quote_episodes(limit=5000)) == 1200
    # same day, more quotes: nothing more is tracked
    env.runner._capture_queue_rows([quote_row(now, f"P{i}") for i in range(10)], "demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 0 and res["dropped"] == 10
    # next UTC day: tracking resumes
    env.runner._qp_day = "2000-01-01"
    env.runner._capture_queue_rows([quote_row(now, "Q1")], "demo")
    res = await env.runner.evaluate_queue_position("demo")
    assert res["new"] == 1 and res["dropped"] == 0


async def test_cap_allowance_is_shared_by_rows_of_different_envs_within_one_run(env):
    now = datetime.now(timezone.utc)
    _setup(env, FakeAccount(), FakePublic())
    env.runner._qp_day_count = qp.DAILY_NEW_CAP - 3
    env.runner._qp_day = now.strftime("%Y-%m-%d")
    env.runner._capture_queue_rows([quote_row(now, f"D{i}") for i in range(2)], "demo")
    env.runner._capture_queue_rows([quote_row(now, f"L{i}") for i in range(2)], "live")
    res = await env.runner.evaluate_queue_position("demo")
    # allowance is read per env, so one run can exceed the cap by at most (envs - 1) x remaining; bounded, see report
    assert res["new"] in (3, 4)
    assert env.runner._qp_day_count <= qp.DAILY_NEW_CAP + 1


# =============================================================== TTL / index / storage definitions
async def test_ttl_and_retention_constants():
    assert S.QUEUE_RETENTION_DAYS == 45 and S.QUEUE_RETENTION_SECONDS == 45 * 24 * 3600
    assert S.QUOTE_EPISODES == "quote_episodes" and S.QUOTE_EPISODES in S.COLLECTIONS
    assert "ts" in S.QUOTE_EPISODE_IMMUTABLE and "order_id" in S.QUOTE_EPISODE_IMMUTABLE


async def test_mongo_creates_exactly_the_three_indexes_with_the_agreed_definitions():
    from control_api.db.mongo import MongoStore
    rec = []

    class Coll:
        def __init__(self, name):
            self.name = name

        async def create_index(self, keys, **kw):
            rec.append((self.name, keys, kw))

    class DB:
        def __getitem__(self, n):
            return Coll(n)

    class Client:
        def __getitem__(self, n):
            return DB()
    await MongoStore("", client=Client()).ensure_indexes()
    mine = [(k, kw) for n, k, kw in rec if n == S.QUOTE_EPISODES]
    names = {kw["name"]: (k, kw) for k, kw in mine}
    assert set(names) == {"order_id_unique", "ts_ttl", "status_env_ts"} and len(mine) == 3
    assert names["order_id_unique"][1].get("unique") is True
    assert names["ts_ttl"][0] == [("ts", 1)] and names["ts_ttl"][1]["expireAfterSeconds"] == 45 * 86400
    assert [f for f, _ in names["status_env_ts"][0]] == ["status", "env", "ts"]
    assert "expireAfterSeconds" not in names["status_env_ts"][1] and "unique" not in names["status_env_ts"][1]
    # only the ts index may expire documents (a TTL on another field would delete open episodes early)
    assert [kw["name"] for k, kw in mine if "expireAfterSeconds" in kw] == ["ts_ttl"]


async def test_episode_documents_match_the_documented_schema_and_stay_small():
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    assert set(ep) <= set(S.QUOTE_EPISODE_DOC), set(ep) - set(S.QUOTE_EPISODE_DOC)
    assert set(S.QUOTE_EPISODE_IMMUTABLE) - {"close_time"} <= set(ep)     # close_time is added only when known
    obs = {"ts": T0, "level": 123.0, "traded": 45.0, "through": False, "nc": 1.0, "hi": 1.0, "lo": 0.0, "kq": 3.0,
           "rem": 10.0}
    full = {**ep, "obs": [obs] * qp.MAX_OBS, "last_obs": obs, "obs_count": 99999,
            "fill_ids": ["x" * 36] * qp.MAX_FILL_IDS, "status": "closed", "outcome": "expired",
            "ticker": "KXDEMOA-26OCT13-B70.5", "order_id": "o" * 36}
    size = len(json.dumps(full, default=str))
    # Atlas M0 budget: worst case DAILY_NEW_CAP x 45 days must stay far below 512 MB
    assert size < 4096 and size * qp.DAILY_NEW_CAP * 45 < 300 * 1024 * 1024
    assert qp.strip_private({**ep, "_kq": 1, "_remaining": 2, "_trade_ids": ["t"]}).keys() == ep.keys()


async def test_memory_store_immutables_cannot_be_moved_by_a_later_write():
    s = MemoryStore()
    ep = qp.episode_from_quote(quote_row(T0, "A"), "demo", ["KXDEMOA"])
    await s.upsert_quote_episodes([ep])
    evil = {k: ("X" if isinstance(v, str) else v) for k, v in ep.items() if k in S.QUOTE_EPISODE_IMMUTABLE}
    await s.upsert_quote_episodes([{**evil, "order_id": "A", "status": "closed"}])
    got = (await s.list_quote_episodes())[0]
    assert got["ticker"] == T and got["env"] == "demo" and got["ts"] == T0 and got["status"] == "closed"


# =============================================================== contract with QueuePosition.jsx
def _component_src():
    return (ROOT / "control_app" / "src" / "components" / "QueuePosition.jsx").read_text(encoding="utf-8")


async def test_component_reads_only_fields_the_api_provides():
    src = _component_src()
    top, per_bucket = set(re.findall(r"\bqs\.(\w+)", src)), set(re.findall(r"\br\.(\w+)", src))
    assert top and per_bucket
    assert top <= SHAPE_KEYS, f"component reads fields the API does not send: {top - SHAPE_KEYS}"
    assert per_bucket <= BUCKET_KEYS, per_bucket - BUCKET_KEYS
    labels = re.search(r"BUCKETS\s*=\s*\[([^\]]*)\]", src).group(1).replace("'", "").replace(" ", "").split(",")
    assert labels == list(qp.BUCKETS)                                 # same five labels, same order


async def test_mock_data_uses_the_same_field_names_as_the_api():
    src = (ROOT / "control_app" / "src" / "services" / "mock.js").read_text(encoding="utf-8")
    block = src[src.index("queueStats"):]
    for k in SHAPE_KEYS | BUCKET_KEYS:
        assert k in block, k


def _check_shape(st):
    assert set(st) == SHAPE_KEYS
    assert [b["bucket"] for b in st["by_queue"]] == list(qp.BUCKETS)
    for b in st["by_queue"]:
        assert set(b) == BUCKET_KEYS
        assert isinstance(b["episodes"], int) and isinstance(b["fills"], int)
        for k in ("fill_rate", "trade_through_share"):
            assert b[k] is None or 0.0 <= b[k] <= 1.0
    for k in ("requote_reset_share", "front_fill_share"):
        assert st[k] is None or 0.0 <= st[k] <= 1.0
    assert st["median_queue_ahead_at_join"] is None or st["median_queue_ahead_at_join"] >= 0
    assert isinstance(st["episodes"], int) and isinstance(st["fills"], int) and st["fills"] <= st["episodes"]
    datetime.fromisoformat(st["updated_utc"].replace("Z", "+00:00"))


async def test_status_and_markets_shape_empty_all_nulls_and_json_roundtrip(env):
    st = (await env.client.get("/api/status", headers=env.auth)).json()["queue_stats"]
    _check_shape(st)
    assert st["episodes"] == 0 and st["median_queue_ahead_at_join"] is None and st["requote_reset_share"] is None
    assert st["front_fill_share"] is None
    assert all(b["episodes"] == 0 and b["fill_rate"] is None and b["trade_through_share"] is None
               for b in st["by_queue"])
    mk = (await env.client.get("/api/markets", headers=env.auth)).json()
    _check_shape(mk["queue_stats"])
    assert mk["markets"]
    for m in mk["markets"]:
        _check_shape(m["queue_stats"])
    assert json.loads(json.dumps(st)) == st


async def test_status_and_markets_shape_with_data_across_all_buckets(env):
    now = datetime.now(timezone.utc)
    docs = []
    for i, (join, filled) in enumerate([(0, 10), (30, 10), (100, 0), (500, 0), (5000, 0), (None, 0)]):
        docs.append({**qp.episode_from_quote(quote_row(now - timedelta(hours=1), f"E{i}"), "demo", ["KXDEMOA"]),
                     "queue_ahead_at_join": join, "filled_count": filled, "fill_ids": ["f"] if filled else [],
                     "front_fill": True if filled else None,
                     "status": "closed" if filled else "open", "outcome": "filled" if filled else None})
    await env.store.upsert_quote_episodes(docs)
    await env.runner.refresh_queue_stats()
    st = (await env.client.get("/api/status", headers=env.auth)).json()["queue_stats"]
    _check_shape(st)
    assert st["episodes"] == 6 and st["fills"] == 2                  # the join-less episode counts but is not bucketed
    assert [b["episodes"] for b in st["by_queue"]] == [1, 1, 1, 1, 1]
    assert [b["fill_rate"] for b in st["by_queue"]] == [1.0, 1.0, 0.0, 0.0, 0.0]
    mk = (await env.client.get("/api/markets", headers=env.auth)).json()
    lax = next(m for m in mk["markets"] if m["series"] == "KXDEMOA")
    assert lax["queue_stats"]["episodes"] == 6
    for m in mk["markets"]:
        _check_shape(m["queue_stats"])


async def test_status_and_markets_null_when_the_store_is_down(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("atlas")
    monkeypatch.setattr(env.store, "list_quote_episodes", boom)
    assert await env.runner.refresh_queue_stats() is None
    st = await env.client.get("/api/status", headers=env.auth)
    mk = await env.client.get("/api/markets", headers=env.auth)
    assert st.status_code == 200 and st.json()["queue_stats"] is None
    assert mk.status_code == 200 and mk.json()["queue_stats"] is None
    assert all(m.get("queue_stats") is None for m in mk.json()["markets"])


async def test_queue_stats_endpoints_still_require_a_login(env):
    for p in ("/api/status", "/api/markets"):
        assert (await env.client.get(p)).status_code == 401


# =============================================================== route guard still classifies every route
async def test_route_guard_still_classifies_all_routes_and_adds_no_queue_route(env):
    routes = _mutating_routes(env.app)
    assert [r for r in routes if r not in NO_2FA and r not in _expected_2fa()] == []
    paths = [getattr(r, "path", "") for r in env.app.routes]
    assert not [p for p in paths if "queue" in p.lower()], "queue telemetry must stay read-only data on existing routes"
