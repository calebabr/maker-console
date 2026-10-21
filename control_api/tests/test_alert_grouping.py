"""Grouped order-error alerts from the strategy's write_errors: / series_capped: / trading_paused lines (contract
section 4, runner half). One alert per pass at most, throttled to one per 30 minutes with the suppressed passes
counted into the next; trading_paused alerts at most once an hour; a pass with order errors is still ok."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from control_api import runner as rn

pytestmark = pytest.mark.asyncio

OUT = ("placing...\n"
       "series_capped: KXDEMOA exposure=21.50 cap=20.65\n"
       "series_capped: KXDEMOB exposure=$9 cap=8.00\n"
       "write_errors: cancel=2 place=1 paused=0\n")


@pytest.fixture
def demo_keys(monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_API_KEY_ID", "id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "pk")


def _order_alerts(r):
    return [a for a in r.alerter.sent if a["title"].startswith("Order errors")]


def _paused_alerts(r):
    return [a for a in r.alerter.sent if a["title"].startswith("Trading paused")]


async def test_parse_write_report():
    rep = rn.parse_write_report(OUT)
    assert rep["cancel"] == 2 and rep["place"] == 1 and rep["paused"] is False
    assert rep["series_capped"] == [{"series": "KXDEMOA", "exposure": 21.5, "cap": 20.65},
                                    {"series": "KXDEMOB", "exposure": 9.0, "cap": 8.0}]
    assert rep["series_cap_skipped"] is False
    assert rn.parse_write_report(None) == rn.parse_write_report("") == {
        "cancel": 0, "place": 0, "paused": False, "series_capped": [], "series_cap_skipped": False}
    rep = rn.parse_write_report("trading_paused\nseries_cap_skipped: positions unreadable\n"
                                "write_errors: cancel=0 place=3 paused=1\n")
    assert rep["paused"] and rep["place"] == 3 and rep["series_cap_skipped"]
    # only well-formed lines count; the last write_errors line wins
    rep = rn.parse_write_report("  write_errors: cancel=9 place=9 paused=1\nwrite_errors: cancel=x\n"
                                "write_errors: cancel=1 place=0 paused=0\nwrite_errors: cancel=0 place=4 paused=0")
    assert (rep["cancel"], rep["place"], rep["paused"]) == (0, 4, False)


async def test_one_grouped_alert_then_throttled_with_a_count(env):
    r = env.runner
    t0 = rn.utcnow()
    await r._handle_write_report(OUT, "demo", now=t0)
    alerts = _order_alerts(r)
    assert len(alerts) == 1 and alerts[0]["title"] == "Order errors: 2 cancel, 1 place"
    assert alerts[0]["priority"] == "high"
    for m in (5, 10, 29):                                             # suppressed: within 30 minutes
        await r._handle_write_report(OUT, "demo", now=t0 + timedelta(minutes=m))
    assert len(_order_alerts(r)) == 1
    await r._handle_write_report("write_errors: cancel=0 place=1 paused=0", "demo", now=t0 + timedelta(minutes=30))
    alerts = _order_alerts(r)
    assert len(alerts) == 2 and alerts[1]["title"] == "Order errors: 0 cancel, 1 place"
    assert "3 more pass(es)" in alerts[1]["message"]
    assert r.last_write_report["place"] == 1 and r.last_write_report["mode"] == "demo"


async def test_no_alert_without_errors(env):
    r = env.runner
    await r._handle_write_report("series_capped: KXDEMOA exposure=1 cap=1\nall good\n", "demo")
    assert _order_alerts(r) == [] and _paused_alerts(r) == []
    assert r.last_write_report["series_capped"][0]["series"] == "KXDEMOA"


async def test_paused_alert_at_most_once_an_hour(env):
    r = env.runner
    t0 = rn.utcnow()
    out = "trading_paused\nwrite_errors: cancel=0 place=0 paused=1\n"
    await r._handle_write_report(out, "demo", now=t0)
    await r._handle_write_report(out, "demo", now=t0 + timedelta(minutes=59))
    assert len(_paused_alerts(r)) == 1 and _order_alerts(r) == []
    await r._handle_write_report(out, "demo", now=t0 + timedelta(minutes=60))
    assert len(_paused_alerts(r)) == 2


async def test_alert_text_has_no_secrets(env, monkeypatch):
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----SECRETBODY")
    r = env.runner
    await r._handle_write_report(OUT + "order o-123 failed: key SECRETBODY\n", "demo")
    a = _order_alerts(r)[0]
    assert "SECRETBODY" not in a["message"] and "o-123" not in a["message"]


async def test_a_pass_with_order_errors_is_still_ok(env, demo_keys, monkeypatch):
    r = env.runner
    r.last_monitor_utc = rn.utcnow()

    def main(*a, **k):
        print(OUT, end="")
        return 0
    monkeypatch.setattr(r.ms, "main", main)
    r.desired = "running"
    assert (await r.tick("t"))["ran"] is True
    while r._bg:
        await asyncio.gather(*list(r._bg), return_exceptions=True)
    hb = await env.store.last_heartbeat()
    assert hb["ok"] is True and r.last_error is None
    assert len(_order_alerts(r)) == 1
    assert not any(a["title"].startswith("Trading pass error") for a in r.alerter.sent)
    st = (await env.client.get("/api/status", headers=env.auth)).json()
    assert st["write_report"]["cancel"] == 2 and st["write_report"]["series_capped"][0]["series"] == "KXDEMOA"


async def test_handler_never_raises(env):
    r = env.runner

    class Boom:
        sent: list = []

        async def send(self, *a, **k):
            raise RuntimeError("ntfy down")
    r.alerter = Boom()
    rep = await r._handle_write_report(OUT, "demo")
    assert rep["cancel"] == 2
