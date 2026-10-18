"""Shadow mode can never place an order: the three read-only layers, a spy-transport run of full cycles through the
real client chain, and a static check of services/shadow.py."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from control_api.services import botfiles
from control_api.services import shadow as sh
from control_api.tests.test_shadow_cycle import CLOSE, NOW, T1, FakeStrategy, inp, yes_quote

botfiles.ensure_bot_path()
from config import BASE_URLS  # noqa: E402

BASE = BASE_URLS["prod"].rstrip("/")
PREFIX = urlsplit(BASE).path.rstrip("/")


class SpyAdapter(BaseAdapter):
    """Records every request that reaches the transport and answers from canned public data. No network."""

    def __init__(self):
        super().__init__()
        self.seen: list[tuple[str, str, dict]] = []

    def send(self, request, **kw):
        self.seen.append((request.method, request.url, dict(request.headers)))
        path = urlsplit(request.url).path[len(PREFIX):]
        q = parse_qs(urlsplit(request.url).query)
        if path == "/markets":
            body = {"markets": [{"ticker": T1, "close_time": CLOSE.isoformat()}], "cursor": ""}
        elif path == f"/markets/{T1}/orderbook":
            body = {"orderbook_fp": {"yes_dollars": [["0.4000", "50"]]}}
        elif path == "/markets/trades":
            assert q.get("ticker", [T1]) == [T1]
            body = {"trades": [{"trade_id": "x", "created_time": (NOW + timedelta(minutes=1)).isoformat(),
                                "yes_price_dollars": "0.3900", "count_fp": "3", "taker_side": "no"}], "cursor": ""}
        elif path == f"/markets/{T1}":
            body = {"market": {"ticker": T1, "result": "no"}}
        else:
            body = {}
        r = requests.Response()
        r.status_code, r._content, r.url, r.request = 200, json.dumps(body).encode(), request.url, request
        r.headers["Content-Type"] = "application/json"
        return r

    def close(self):
        pass


# ---- layer 1: the wrapper
def test_wrapper_exposes_no_order_or_account_attributes():
    md = sh.build_market_data(adapter=SpyAdapter())
    for name in ("request", "create_order", "cancel_order", "get_balance", "get_orders", "get_positions",
                 "get_fills", "session", "client", "batch_create_orders", "amend_order"):
        assert not hasattr(md, name), name
    assert {n for n in dir(md) if not n.startswith("_")} == {"markets", "orderbook", "market", "trades"}
    with pytest.raises(AttributeError):
        md.extra = 1                                   # __slots__: nothing can be bolted on


def test_wrapper_refuses_a_client_with_credentials():
    class Authed:
        key_id, private_key, has_credentials = "kid", object(), True
    with pytest.raises(PermissionError):
        sh.ReadOnlyMarketData(Authed())


def test_built_client_has_no_credentials():
    md = sh.build_market_data(adapter=SpyAdapter())
    client = object.__getattribute__(md, "_client")
    assert client.key_id is None and client.private_key is None and client.has_credentials is False
    assert isinstance(client.session, sh.GetOnlySession)


def test_wrapper_rejects_path_tricks_in_tickers():
    md = sh.build_market_data(adapter=SpyAdapter())
    for bad in ("../portfolio/balance", "T1/../../portfolio", "", "a b", "%2e%2e"):
        with pytest.raises(ValueError):
            md.orderbook(bad)


# ---- layer 2: the session
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_session_rejects_non_get(method):
    s = sh.GetOnlySession(BASE)
    s.mount("https://", SpyAdapter())
    with pytest.raises(PermissionError):
        s.request(method, BASE + "/markets")
    with pytest.raises(PermissionError):           # the send() layer on its own (e.g. a prepared request)
        s.send(requests.Request(method, BASE + "/markets").prepare())


@pytest.mark.parametrize("path", ["/portfolio/balance", "/portfolio/orders", "/portfolio/events/orders",
                                  "/portfolio/events/orders/abc", "/exchange/status", "/markets/T1/orderbook/x",
                                  "/markets/../portfolio/orders", "/markets/%2e%2e/portfolio", "/markets/..",
                                  "/events", "", "/markets/T1/trades/extra"])
def test_session_rejects_paths_outside_the_allowlist(path):
    s = sh.GetOnlySession(BASE)
    spy = SpyAdapter()
    s.mount("https://", spy)
    with pytest.raises(PermissionError):
        s.get(BASE + path)
    assert spy.seen == []


def test_session_rejects_other_hosts_and_credentials():
    s = sh.GetOnlySession(BASE)
    spy = SpyAdapter()
    s.mount("https://", spy)
    s.mount("http://", spy)
    for url in (BASE_URLS["demo"] + "/markets", "https://evil.example.com" + PREFIX + "/markets",
                BASE.replace("https://", "http://") + "/markets"):
        with pytest.raises(PermissionError):
            s.get(url)
    with pytest.raises(PermissionError):
        s.get(BASE + "/markets", headers={"KALSHI-ACCESS-KEY": "x"})
    with pytest.raises(PermissionError):
        s.get(BASE + "/markets", headers={"Authorization": "Bearer x"})
    assert spy.seen == []
    for path in ("/markets", f"/markets/{T1}", f"/markets/{T1}/orderbook", "/markets/trades"):
        assert s.get(BASE + path).status_code == 200
    assert len(spy.seen) == 4


# ---- full cycles through the real chain: only GETs, never /portfolio, never credentials
def test_spy_transport_full_lifecycle_records_only_public_gets():
    spy = SpyAdapter()
    md = sh.build_market_data(adapter=spy)
    st = FakeStrategy([yes_quote()])
    [ep] = sh.run_cycle(md, st, inp()).upserts                                  # join
    st.quotes = []
    [ep] = sh.run_cycle(md, st, inp(open_eps=[ep], now=NOW + timedelta(minutes=5))).upserts   # cancel
    assert ep["end_reason"] == "cancel"
    [ep] = sh.run_cycle(md, st, inp(ended_unread=[ep], now=NOW + timedelta(minutes=10))).upserts  # trades
    assert ep["through"] is True and ep["fill"]["mid"] == ep["count"]
    [ep] = sh.run_cycle(md, st, inp(unsettled=[ep], now=CLOSE + timedelta(hours=1))).upserts    # settle
    assert ep["result"] == "no" and ep["pnl"]["zero"]["mid"] < 0
    assert spy.seen, "no requests reached the transport"
    for method, url, headers in spy.seen:
        assert method == "GET"
        assert url.startswith(BASE + "/markets")
        assert "/portfolio" not in url
        assert not {h.lower() for h in headers} & {"authorization", "kalshi-access-key", "kalshi-access-signature"}


# ---- static source check
FORBIDDEN = ("create_order", "cancel_order", "kalshi_factory", "kalshi_env", "mode_keys", "KALSHI_PROD", "from_env",
             "_ENV_LOCK")


def test_shadow_service_source_has_no_order_or_credential_paths():
    src = Path(sh.__file__).read_text(encoding="utf-8")
    for word in FORBIDDEN:
        assert word not in src, word
    assert not re.search(r"\bauth\s*=\s*True", src)
    assert "_pass_lock" not in src and "_stop_executor" not in src and "_fence_ok" not in src


def test_shadow_route_source_is_read_only():
    from control_api.routes import shadow as route
    src = Path(route.__file__).read_text(encoding="utf-8")
    assert "@router.get" in src
    for verb in ("@router.post", "@router.put", "@router.delete", "@router.patch"):
        assert verb not in src
    for word in FORBIDDEN:
        assert word not in src, word


def test_runner_shadow_methods_never_touch_trading_paths():
    import inspect
    from control_api.runner import Runner
    names = [n for n in vars(Runner) if "shadow" in n]
    assert "shadow_cycle" in names and "_shadow_loop" in names
    for n in names:
        obj = vars(Runner)[n]
        src = inspect.getsource(obj.fget if isinstance(obj, property) else obj)
        for word in FORBIDDEN + ("_pass_lock.acquire", "async with self._pass_lock", "_stop_executor",
                                 "_fence_ok", "public_factory", "_tel_executor"):
            assert word not in src, (n, word)
