"""The autouse guard in conftest must fail real outbound connections, and the default env uses a fake public client."""
import socket

import httpx
import pytest

from control_api.tests.conftest import FakePublic, NetworkBlocked


def test_external_connect_is_blocked():
    s = socket.socket()
    try:
        with pytest.raises(NetworkBlocked):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_external_dns_is_blocked():
    with pytest.raises(NetworkBlocked):
        socket.getaddrinfo("api.elections.kalshi.com", 443)


@pytest.mark.slow
def test_httpx_to_external_host_is_blocked():
    with pytest.raises(Exception) as ei:
        httpx.get("https://api.elections.kalshi.com/", timeout=2)
    chain, e = [], ei.value
    while e is not None:
        chain.append(e)
        e = e.__cause__ or e.__context__
    assert any(isinstance(x, NetworkBlocked) for x in chain)


def test_loopback_dns_is_allowed():
    assert socket.getaddrinfo("localhost", 80)


@pytest.mark.allow_network
def test_marker_disables_the_guard():
    assert socket.getaddrinfo.__module__ != __name__   # the real function, not the conftest wrapper
    assert "conftest" not in socket.getaddrinfo.__code__.co_filename if hasattr(socket.getaddrinfo, "__code__") else True


@pytest.mark.asyncio
async def test_default_env_runner_uses_fake_public_client(env):
    assert isinstance(env.runner.public_factory("demo"), FakePublic)
    assert isinstance(env.runner.public_factory("live"), FakePublic)
