"""Shared fixtures for the public stand-in tests (the private overlay replaces this file)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    """Routes (METHOD, path-without-query) -> list of responses (last one repeats)."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def add(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)

    def request(self, method, url, headers=None, json=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        self.calls.append({"method": method, "url": url, "path": path, "headers": headers or {}, "json": json})
        for (m, p), resps in self.routes.items():
            if m == method and (path == p or (p.endswith("*") and path.startswith(p[:-1]))):
                return resps.pop(0) if len(resps) > 1 else resps[0]
        return FakeResponse(404, {"error": "not found", "path": path})


@pytest.fixture
def rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def rsa_pem(rsa_key):
    from cryptography.hazmat.primitives import serialization
    return rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode()

