import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from conftest import FakeResponse, FakeSession
from kalshi_client import KalshiClient, KalshiError, CredentialsMissing, load_private_key_from_env

BASE = "https://external-api.demo.kalshi.co/trade-api/v2"


def verify(pub, sig_b64, msg):
    pub.verify(base64.b64decode(sig_b64), msg.encode(),
               padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
               hashes.SHA256())


def test_signature_covers_ts_method_full_path_without_query(rsa_key):
    sess = FakeSession()
    sess.add("GET", "/trade-api/v2/portfolio/orders", FakeResponse(200, {"orders": []}))
    c = KalshiClient(BASE, key_id="test-key-id", private_key=rsa_key, session=sess)
    c.get_orders(ticker="KXDEMOA-26SEP27-T83", status="resting")
    call = sess.calls[0]
    assert "?" in call["url"]  # query was sent...
    h = call["headers"]
    assert h["KALSHI-ACCESS-KEY"] == "test-key-id"
    ts = h["KALSHI-ACCESS-TIMESTAMP"]
    assert ts.isdigit() and len(ts) == 13  # milliseconds
    # ...but is not part of the signed message; the /trade-api/v2 prefix is.
    verify(rsa_key.public_key(), h["KALSHI-ACCESS-SIGNATURE"], ts + "GET" + "/trade-api/v2/portfolio/orders")


def test_signature_fixed_timestamp(rsa_key):
    c = KalshiClient(BASE, key_id="k", private_key=rsa_key, session=FakeSession())
    h = c.auth_headers("post", "/trade-api/v2/portfolio/events/orders?x=1", ts_ms=1703123456789)
    assert h["KALSHI-ACCESS-TIMESTAMP"] == "1703123456789"
    verify(rsa_key.public_key(), h["KALSHI-ACCESS-SIGNATURE"], "1703123456789POST/trade-api/v2/portfolio/events/orders")


def test_public_requests_are_unsigned():
    sess = FakeSession()
    sess.add("GET", "/trade-api/v2/markets", FakeResponse(200, {"markets": [{"ticker": "A"}], "cursor": "c1"}),
             FakeResponse(200, {"markets": [{"ticker": "B"}], "cursor": ""}))
    c = KalshiClient(BASE, session=sess)
    assert [m["ticker"] for m in c.get_markets(series_ticker="KXDEMOA")] == ["A", "B"]
    assert "KALSHI-ACCESS-KEY" not in sess.calls[0]["headers"]
    assert "cursor=c1" in sess.calls[1]["url"]


def test_auth_without_credentials_raises():
    c = KalshiClient(BASE, session=FakeSession())
    with pytest.raises(CredentialsMissing):
        c.get_balance()


def test_retry_on_429_with_backoff_then_success(rsa_key):
    sleeps = []
    sess = FakeSession()
    sess.add("GET", "/trade-api/v2/portfolio/balance",
             FakeResponse(429, {"error": "too many requests"}, {"Retry-After": "2"}),
             FakeResponse(503, {}),
             FakeResponse(200, {"balance": 1234}))
    c = KalshiClient(BASE, key_id="k", private_key=rsa_key, session=sess, sleep=sleeps.append)
    assert c.get_balance()["balance"] == 1234
    assert len(sess.calls) == 3 and sleeps[0] == 2.0 and len(sleeps) == 2
    # each attempt is re-signed with a fresh timestamp header present
    assert all("KALSHI-ACCESS-SIGNATURE" in call["headers"] for call in sess.calls)


def test_gives_up_after_max_retries():
    sess = FakeSession()
    sess.add("GET", "/trade-api/v2/markets", FakeResponse(429, {"error": "too many requests"}))
    c = KalshiClient(BASE, session=sess, sleep=lambda s: None, max_retries=2)
    with pytest.raises(KalshiError) as e:
        c.get_markets()
    assert e.value.status == 429 and len(sess.calls) == 3


def test_create_order_v2_body(rsa_key):
    sess = FakeSession()
    sess.add("POST", "/trade-api/v2/portfolio/events/orders",
             FakeResponse(201, {"order_id": "o1", "fill_count": "3.00", "remaining_count": "0.00", "ts_ms": 1}))
    c = KalshiClient(BASE, key_id="k", private_key=rsa_key, session=sess)
    r = c.create_order(ticker="KXDEMOA-26SEP27-T83", side="ask", count="3.00", price="0.1500",
                       client_order_id="abc", time_in_force="immediate_or_cancel")
    assert r["order_id"] == "o1"
    body = sess.calls[0]["json"]
    assert body == {"ticker": "KXDEMOA-26SEP27-T83", "side": "ask", "count": "3.00", "price": "0.1500",
                    "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
                    "client_order_id": "abc", "post_only": False}
    with pytest.raises(ValueError):
        c.create_order(ticker="x", side="yes", count="1", price="0.5", client_order_id="z")


def test_cancel_order_v2_path(rsa_key):
    sess = FakeSession()
    sess.add("DELETE", "/trade-api/v2/portfolio/events/orders/o1", FakeResponse(200, {"order_id": "o1"}))
    c = KalshiClient(BASE, key_id="k", private_key=rsa_key, session=sess)
    c.cancel_order("o1", market_ticker="T")
    assert "market_ticker=T" in sess.calls[0]["url"] and "exchange_index=-1" in sess.calls[0]["url"]


def test_client_error_not_retried(rsa_key):
    sess = FakeSession()
    sess.add("POST", "/trade-api/v2/portfolio/events/orders", FakeResponse(400, {"error": "bad"}))
    c = KalshiClient(BASE, key_id="k", private_key=rsa_key, session=sess)
    with pytest.raises(KalshiError) as e:
        c.create_order(ticker="x", side="bid", count="1.00", price="0.5000", client_order_id="z")
    assert e.value.status == 400 and len(sess.calls) == 1


def test_load_key_from_env_pem_and_path(monkeypatch, rsa_pem, tmp_path):
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", rsa_pem.replace("\n", "\\n"))  # single-line secret form
    assert load_private_key_from_env() is not None
    monkeypatch.delenv("KALSHI_PRIVATE_KEY")
    f = tmp_path / "k.pem"
    f.write_text(rsa_pem)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(f))
    assert load_private_key_from_env() is not None
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH")
    assert load_private_key_from_env() is None


def test_clock_skew_recorded():
    sess = FakeSession()
    sess.add("GET", "/trade-api/v2/exchange/status",
             FakeResponse(200, {"trading_active": True}, {"Date": "Sat, 26 Sep 2026 17:00:00 GMT"}))
    c = KalshiClient(BASE, session=sess)
    c.get_exchange_status()
    assert c.clock_skew_seconds is not None
