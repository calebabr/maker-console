"""book_recorder.py: fake HTTP layer only (no network, no real sleeps)."""
from __future__ import annotations

import gzip
import json
import re
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import book_recorder as br  # noqa: E402

NOW = 1_790_000_000.0  # fixed epoch for deterministic file names


class FakeApi:
    def __init__(self):
        self.markets: dict[str, list[dict]] = {}
        self.books: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: list[int] = []
        self.bad_series: set[str] = set()

    def add(self, series, n=2, close="2026-10-24T08:00:00Z"):
        for i in range(n):
            tk = f"{series}-26OCT24-T{70 + i}"
            self.markets.setdefault(series, []).append({
                "ticker": tk, "event_ticker": f"{series}-26OCT24", "status": "active",
                "close_time": close.replace("08", f"0{8 - i}") if i else close,
                "yes_bid_dollars": "0.9100", "yes_ask_dollars": "0.9300", "yes_bid_size_fp": "10.00",
                "yes_ask_size_fp": "5.00", "no_bid_dollars": "0.0700", "no_ask_dollars": "0.0900",
                "last_price_dollars": "0.9200", "volume_fp": "100.00", "open_interest_fp": "50.00"})
            self.books[tk] = {"orderbook_fp": {
                "yes_dollars": [["0.8800", "3.00"], ["0.8900", "4.00"], ["0.9000", "5.00"], ["0.9100", "10.00"]],
                "no_dollars": [["0.0500", "1.00"], ["0.0600", "2.00"], ["0.0700", "5.00"]]}}

    def opener(self, url):
        u = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path.replace("/trade-api/v2", "")
        self.calls.append((path, q))
        if self.fail:
            return self.fail.pop(0), {}, b""
        if path == "/markets":
            s = q["series_ticker"]
            if s in self.bad_series:
                return 500, {}, b""
            return 200, {}, json.dumps({"markets": self.markets.get(s, []), "cursor": ""}).encode()
        m = re.match(r"^/markets/(.+)/orderbook$", path)
        if m:
            return 200, {}, json.dumps(self.books.get(m.group(1), {})).encode()
        return 404, {}, b""


def client(api, **kw):
    return br.Client(opener=api.opener, sleep=lambda s: None, **kw)


def read_all(out: Path):
    rows = []
    for p in sorted(out.glob("book-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rows += [json.loads(l) for l in f if l.strip()]
    return rows


def write_series(tmp_path, text="AAA\nBBB  # comment\n\n# whole line\n"):
    p = tmp_path / "series.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ safety properties
def test_source_has_no_secrets_env_or_non_get():
    src = Path(br.__file__).read_text(encoding="utf-8")
    for bad in ("os.environ", "getenv", "Authorization", "KALSHI-ACCESS", "api_key", "private_key", "PRIVATE KEY",
                ".post(", "method=\"POST\"", "method=\"PUT\"", "method=\"DELETE\"", "import requests", "/portfolio",
                "/orders", "dotenv"):
        assert bad not in src, bad
    assert src.count('method="GET"') == 1
    assert br.BASE_URL.startswith("https://")


@pytest.mark.parametrize("path", [
    "/portfolio/orders", "/portfolio/balance", "/exchange/status", "/markets/trades", "/markets/A/orders",
    "/markets/../portfolio/orderbook", "/markets//orderbook", "/markets/.hidden/orderbook", "/series/",
    "/series/A/B", "markets", "", "/historical/markets", "/markets/A B/orderbook", "/markets/A?x=1/orderbook",
])
def test_paths_outside_the_allowlist_raise_before_any_request(path):
    api = FakeApi()
    with pytest.raises(ValueError):
        client(api).get(path)
    assert api.calls == []


@pytest.mark.parametrize("path", ["/markets", "/series", "/series/ABC-1", "/markets/ABC-26OCT24-B93.5/orderbook"])
def test_allowed_paths(path):
    assert br.Client.allowed(path)


def test_redirects_are_not_followed():
    assert br._NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil") is None


def test_rps_is_capped_at_five():
    assert br.Throttle(100).interval == pytest.approx(0.2)
    now = [0.0]

    def sleep(d):
        now[0] += d

    t = br.Throttle(50, clock=lambda: now[0], sleep=sleep)
    stamps = []
    for _ in range(12):
        t.wait()
        stamps.append(now[0])
    assert (len(stamps) - 1) / (stamps[-1] - stamps[0]) <= 5.0 + 1e-9


def test_backoff_429_5xx_and_retry_after():
    api = FakeApi()
    api.add("AAA", 1)
    api.fail = [429, 503]
    sleeps = []
    c = br.Client(opener=api.opener, sleep=sleeps.append, throttle=br.Throttle(5, sleep=lambda s: None))
    j = c.get("/markets", {"series_ticker": "AAA"})
    assert j["markets"] and c.retries == 2 and sleeps[0] < sleeps[1] * 1.7

    calls = []

    def opener(url):
        calls.append(url)
        return (429, {"Retry-After": "20"}, b"") if len(calls) == 1 else (200, {}, b"{}")

    s2 = []
    br.Client(opener=opener, sleep=s2.append, throttle=br.Throttle(5, sleep=lambda s: None)).get("/series")
    assert s2[0] >= 20 * 0.75


def test_hard_failures():
    always = br.Client(opener=lambda u: (500, {}, b""), sleep=lambda s: None)
    with pytest.raises(br.FetchError):
        always.get("/series")
    assert always.requests == br.MAX_ATTEMPTS
    with pytest.raises(br.FetchError):
        br.Client(opener=lambda u: (403, {}, b""), sleep=lambda s: None).get("/series")


# ------------------------------------------------------------------ series file
def test_series_file_parsing(tmp_path):
    assert br.read_series_file(write_series(tmp_path)) == ["AAA", "BBB"]
    assert br.read_series_file(write_series(tmp_path, "aaa\nAAA\n")) == ["AAA"]
    for bad in ("../x\n", "A/B\n", "A B\n", "A?x\n", "\n# only a comment\n"):
        with pytest.raises(ValueError):
            br.read_series_file(write_series(tmp_path, bad))


# ------------------------------------------------------------------ snapshot content
def test_record_has_best_bid_ask_and_top_three_levels_best_first():
    api = FakeApi()
    api.add("AAA", 1)
    recs, stats = br.snapshot_pass(client(api), ["AAA"], now=lambda: NOW)
    assert stats["records"] == 1 and stats["errors"] == 0 and not stats["partial"]
    r = recs[0]
    assert r["yes_bid"] == "0.9100" and r["yes_ask"] == "0.9300" and r["yes_bid_size"] == "10.00"
    assert r["yes_levels"] == [["0.9100", "10.00"], ["0.9000", "5.00"], ["0.8900", "4.00"]]  # best first, top 3
    assert r["no_levels"] == [["0.0700", "5.00"], ["0.0600", "2.00"], ["0.0500", "1.00"]]
    assert r["series"] == "AAA" and r["snap"] == br.iso(NOW)
    assert ("/markets/AAA-26OCT24-T70/orderbook", {"depth": "3"}) in api.calls


def test_unsafe_ticker_from_api_is_dropped():
    api = FakeApi()
    api.add("AAA", 1)
    api.markets["AAA"].append({"ticker": "../portfolio", "status": "active"})
    api.markets["AAA"].append({"ticker": "A B", "status": "active"})
    recs, _ = br.snapshot_pass(client(api), ["AAA"], now=lambda: NOW)
    assert [r["ticker"] for r in recs] == ["AAA-26OCT24-T70"]
    assert all(br.Client.allowed(p) for p, _ in api.calls)


def test_one_bad_series_does_not_stop_the_pass():
    api = FakeApi()
    api.add("AAA", 1)
    api.add("BBB", 1)
    api.bad_series = {"AAA"}
    recs, stats = br.snapshot_pass(client(api), ["AAA", "BBB"], now=lambda: NOW)
    assert [r["series"] for r in recs] == ["BBB"] and stats["errors"] == 1


def test_per_series_cap_keeps_nearest_to_close():
    api = FakeApi()
    api.add("AAA", 2)  # second market closes earlier
    recs, _ = br.snapshot_pass(client(api), ["AAA"], now=lambda: NOW, max_markets=1)
    assert len(recs) == 1 and recs[0]["close_time"] < "2026-10-24T08:00:00Z"


def test_time_budget_makes_a_partial_pass():
    api = FakeApi()
    api.add("AAA", 2)
    api.add("BBB", 2)
    tick = [0.0]

    def clock():
        tick[0] += 10.0
        return tick[0]

    recs, stats = br.snapshot_pass(client(api), ["AAA", "BBB"], now=lambda: NOW, budget_s=25.0, clock=clock)
    assert stats["partial"] is True and len(recs) < 4


# ------------------------------------------------------------------ output, rotation, disk
def test_once_writes_hourly_gzip_jsonl_and_appends(tmp_path):
    api = FakeApi()
    api.add("AAA", 2)
    out = tmp_path / "out"
    sf = write_series(tmp_path, "AAA\n")
    for _ in range(2):
        assert br.main(["--once", "--series-file", str(sf), "--out", str(out)], client=client(api), now=lambda: NOW) == 0
    files = list(out.glob("book-*.jsonl.gz"))
    assert len(files) == 1
    assert re.match(r"^book-\d{8}T\d{2}\.jsonl\.gz$", files[0].name)
    rows = read_all(out)
    assert len(rows) == 4  # two passes appended as two gzip members, both readable
    assert set(rows[0]) >= {"snap", "t", "ticker", "yes_bid", "yes_ask", "yes_levels", "no_levels"}


def test_new_hour_new_file(tmp_path):
    api = FakeApi()
    api.add("AAA", 1)
    out = tmp_path / "out"
    sf = write_series(tmp_path, "AAA\n")
    br.main(["--once", "--series-file", str(sf), "--out", str(out)], client=client(api), now=lambda: NOW)
    br.main(["--once", "--series-file", str(sf), "--out", str(out)], client=client(api), now=lambda: NOW + 3600)
    assert len(list(out.glob("book-*.jsonl.gz"))) == 2


def test_rotation_deletes_oldest_never_newest(tmp_path):
    out = tmp_path
    names = ["book-20260101T00.jsonl.gz", "book-20260101T01.jsonl.gz", "book-20260101T02.jsonl.gz"]
    for n in names:
        (out / n).write_bytes(b"x" * 1000)
    (out / "notes.txt").write_text("keep me")
    removed = br.rotate(out, max_bytes=1500)
    assert removed == names[:2]
    assert (out / names[2]).exists() and (out / "notes.txt").exists()
    assert br.rotate(out, max_bytes=0) == []  # the newest file is never deleted


def test_refuses_to_write_when_disk_is_low(tmp_path, monkeypatch):
    api = FakeApi()
    api.add("AAA", 1)
    sf = write_series(tmp_path, "AAA\n")
    monkeypatch.setattr(br, "free_ok", lambda out, floor: False)
    rc = br.main(["--once", "--series-file", str(sf), "--out", str(tmp_path / "o")], client=client(api), now=lambda: NOW)
    assert rc == 4 and not list((tmp_path / "o").glob("*.gz")) and api.calls == []


def test_bad_series_file_exits_2(tmp_path):
    assert br.main(["--once", "--series-file", str(tmp_path / "missing.txt"), "--out", str(tmp_path / "o")]) == 2


def test_everything_failing_exits_2(tmp_path):
    api = FakeApi()
    api.add("AAA", 1)
    api.bad_series = {"AAA"}
    sf = write_series(tmp_path, "AAA\n")
    assert br.main(["--once", "--series-file", str(sf), "--out", str(tmp_path / "o")], client=client(api), now=lambda: NOW) == 2


# ------------------------------------------------------------------ shutdown and requests made
def test_stop_flag_exits_cleanly_with_partial_output(tmp_path):
    api = FakeApi()
    api.add("AAA", 3)
    c = client(api)
    orig = api.opener
    seen = {"n": 0}

    def opener(url):
        seen["n"] += 1
        if seen["n"] == 3:
            c.stop.set()  # a SIGTERM arrives mid-pass
        return orig(url)

    c._opener = opener
    sf = write_series(tmp_path, "AAA\n")
    rc = br.main(["--once", "--series-file", str(sf), "--out", str(tmp_path / "o")], client=c, now=lambda: NOW)
    assert rc == 0
    assert 0 < len(read_all(tmp_path / "o")) < 3  # what was collected was written, file is valid gzip


def test_loop_mode_stops_on_signal_flag(tmp_path):
    api = FakeApi()
    api.add("AAA", 1)
    c = client(api)
    sleeps = []

    def sleep(d):
        sleeps.append(d)
        c.stop.set()

    sf = write_series(tmp_path, "AAA\n")
    rc = br.main(["--series-file", str(sf), "--out", str(tmp_path / "o"), "--interval-minutes", "5"],
                 client=c, sleep=sleep, now=lambda: NOW)
    assert rc == 0 and sleeps and len(read_all(tmp_path / "o")) == 1


def test_all_requests_are_get_on_allowlisted_paths(tmp_path):
    api = FakeApi()
    api.add("AAA", 3)
    api.add("BBB", 2)
    sf = write_series(tmp_path)
    br.main(["--once", "--series-file", str(sf), "--out", str(tmp_path / "o")], client=client(api), now=lambda: NOW)
    assert api.calls
    assert all(br.Client.allowed(p) for p, _ in api.calls)
    assert all(not ({"key", "token", "signature"} & set(q)) for _, q in api.calls)
