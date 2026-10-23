#!/usr/bin/env python3
"""Keyless public order-book snapshot recorder.

Every pass it reads the open markets of the series listed in a text file, and for each market stores the
best bid/ask plus the top 3 price levels of each side, appended to hourly gzip JSONL files. It talks to the
public, unauthenticated read API only.

    book_recorder.py --once --series-file series.txt --out /var/lib/book-recorder     one pass (systemd timer)
    book_recorder.py --series-file series.txt --out DIR --interval-minutes 5            loop until SIGTERM/SIGINT

Safety properties (each one is covered by a test):
  * GET only, to a fixed https host, and only these paths: /markets, /markets/{ticker}/orderbook,
    /series, /series/{series}. Anything else raises before a request is made. No redirects are followed.
  * No credentials and no environment reads: nothing in this file reads the environment or holds a key.
  * The series list comes from a file argument and is never part of the repository. Every entry and every
    ticker the API returns is validated against a strict pattern before it can reach a URL.
  * Politeness: at most 5 requests per second (the flag can only lower it); exponential backoff with jitter
    on 429 and 5xx, honouring Retry-After; a per-pass time budget so a slow pass cannot overlap the next.
  * Disk: output is capped (oldest hourly files are deleted first, the newest is never deleted) and a pass
    refuses to write when the free space is below a floor. Output file names are generated, never taken from
    input.
  * Clean shutdown: SIGTERM/SIGINT finish the current request, write what was collected, and exit 0.

Output record (one JSON object per market per pass), prices and sizes kept as the API's decimal strings:
  {"snap": ISO pass start, "t": ISO fetch time, "ticker", "series", "event", "status", "close_time",
   "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size", "no_bid", "no_ask", "last", "volume",
   "open_interest", "yes_levels": [[price, size] x3 best first], "no_levels": [[price, size] x3 best first]}
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import re
import shutil
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MAX_RPS = 5.0
DEFAULT_RPS = 4.0
MAX_ATTEMPTS = 6
MAX_RETRY_AFTER = 300.0  # seconds; a server-supplied Retry-After is never honoured beyond this
FILE_MODE = 0o640        # new output files: owner read/write, group read (for the weekly copy-back)
DIR_MODE = 0o750
PAGE_LIMIT = 1000
LEVELS = 3
USER_AGENT = "book-recorder/1 (read-only public data; GET only)"

TICKER_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}"
# Always used with fullmatch: `$` would also match before a trailing newline.
SERIES_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
TICKER_OK = re.compile(TICKER_RE)
# The only paths this program can ever request.
ALLOWED_PATH_RES = (
    re.compile(r"/markets"),
    re.compile(rf"/markets/{TICKER_RE}/orderbook"),
    re.compile(r"/series"),
    re.compile(r"/series/[A-Za-z0-9][A-Za-z0-9_-]{0,63}"),
)
FILE_RE = re.compile(r"book-\d{8}T\d{2}\.jsonl\.gz")


class FetchError(Exception):
    pass


class Stopped(Exception):
    pass


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------ http
class Throttle:
    """Shared request slots at least 1/rps apart."""

    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 1.0 / max(0.05, min(float(rps), MAX_RPS))
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            self._sleep(slot - now)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # a 3xx surfaces as an HTTPError, never followed


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen(url: str) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


def retry_delay(headers: dict, attempt: int) -> float:
    """Backoff seconds: exponential with jitter; Retry-After is honoured only when it is a finite positive
    number and never beyond MAX_RETRY_AFTER."""
    try:
        ra = float(headers.get("Retry-After", 0))
    except (TypeError, ValueError, AttributeError):
        ra = 0.0
    if not (math.isfinite(ra) and ra > 0):
        ra = 0.0
    ra = min(ra, MAX_RETRY_AFTER)
    return max(ra, min(60.0, 1.5 * 2 ** attempt)) * (0.75 + 0.5 * random.random())


class Client:
    def __init__(self, rps: float = DEFAULT_RPS, opener: Callable[[str], tuple[int, dict, bytes]] | None = None,
                 sleep: Callable[[float], None] = time.sleep, stop: threading.Event | None = None,
                 throttle: Throttle | None = None):
        self._opener = opener or _urlopen
        self._sleep = sleep
        self.throttle = throttle or Throttle(rps, sleep=sleep)
        self.stop = stop or threading.Event()
        self.requests = 0
        self.retries = 0

    @staticmethod
    def allowed(path: str) -> bool:
        return isinstance(path, str) and any(r.fullmatch(path) for r in ALLOWED_PATH_RES)

    def get(self, path: str, params: dict | None = None) -> dict:
        if not self.allowed(path):
            raise ValueError(f"path not allowed: {path!r}")
        q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(q) if q else "")
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            if self.stop.is_set():
                raise Stopped()
            self.throttle.wait()
            self.requests += 1
            try:
                status, headers, body = self._opener(url)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                status, headers, body = 0, {}, str(e).encode()
            if status == 200:
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        return parsed
                    last = "json is not an object"
                except ValueError:
                    last = "bad json"
            elif status == 404:
                return {}
            elif status == 429 or status >= 500 or status == 0:
                last = f"http {status}"
            else:
                raise FetchError(f"{path} http {status}")
            self.retries += 1
            self._sleep(retry_delay(headers, attempt))
        raise FetchError(f"{path} failed after {MAX_ATTEMPTS} attempts ({last})")


# ------------------------------------------------------------------------------------------ snapshot
def read_series_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not SERIES_RE.fullmatch(line):
            raise ValueError(f"bad series entry in {path}: {line!r}")
        if line.upper() not in out:
            out.append(line.upper())
    if not out:
        raise ValueError(f"no series in {path}")
    return out


def _levels(raw) -> list[list[str]]:
    """API lists are ascending by price (best bid last); return the best LEVELS, best first."""
    items = [[str(e[0]), str(e[1])] for e in (raw or []) if isinstance(e, (list, tuple)) and len(e) == 2
             and e[0] is not None]
    return list(reversed(items))[:LEVELS]


def make_record(market: dict, book: dict, series: str, snap: str, now: float) -> dict:
    ob = book.get("orderbook_fp") or book.get("orderbook") or {}
    return {
        "snap": snap, "t": iso(now), "ticker": market["ticker"], "series": series,
        "event": market.get("event_ticker"), "status": market.get("status"),
        "close_time": market.get("close_time"),
        "yes_bid": market.get("yes_bid_dollars"), "yes_ask": market.get("yes_ask_dollars"),
        "yes_bid_size": market.get("yes_bid_size_fp"), "yes_ask_size": market.get("yes_ask_size_fp"),
        "no_bid": market.get("no_bid_dollars"), "no_ask": market.get("no_ask_dollars"),
        "last": market.get("last_price_dollars"), "volume": market.get("volume_fp"),
        "open_interest": market.get("open_interest_fp"),
        "yes_levels": _levels(ob.get("yes_dollars") or ob.get("yes")),
        "no_levels": _levels(ob.get("no_dollars") or ob.get("no")),
    }


def open_markets(client: Client, series: str, cap: int) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    while True:
        j = client.get("/markets", {"series_ticker": series, "status": "open", "limit": PAGE_LIMIT, "cursor": cursor})
        out += [m for m in (j.get("markets") or []) if isinstance(m, dict) and TICKER_OK.fullmatch(str(m.get("ticker", "")))]
        cursor = j.get("cursor") or ""
        if not cursor or not j.get("markets") or len(out) >= cap:
            break
    out.sort(key=lambda m: m.get("close_time") or "")  # nearest to close first, so a time cut drops the far ones
    return out[:cap]


def snapshot_pass(client: Client, series_list: list[str], now: Callable[[], float] = time.time,
                  max_markets: int = 400, budget_s: float | None = None,
                  clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = lambda m: None) -> tuple[list[dict], dict]:
    """One pass. Returns (records, stats). A fetch error on one market or series skips it, never the pass."""
    snap = iso(now())
    started = clock()
    recs: list[dict] = []
    stats = {"series": 0, "markets": 0, "records": 0, "errors": 0, "partial": False}
    try:
        for s in series_list:
            if budget_s is not None and clock() - started > budget_s:
                stats["partial"] = True
                break
            try:
                markets = open_markets(client, s, max_markets)
            except FetchError as e:
                stats["errors"] += 1
                log(f"series {s}: {e}")
                continue
            stats["series"] += 1
            stats["markets"] += len(markets)
            for m in markets:
                if budget_s is not None and clock() - started > budget_s:
                    stats["partial"] = True
                    break
                try:
                    book = client.get(f"/markets/{m['ticker']}/orderbook", {"depth": LEVELS})
                    recs.append(make_record(m, book, s, snap, now()))
                except (FetchError, ValueError, TypeError, AttributeError, KeyError) as e:
                    stats["errors"] += 1  # one bad market never aborts the pass
                    log(f"orderbook {m.get('ticker')!r}: {type(e).__name__}")
                    continue
            if stats["partial"]:
                break
    except Stopped:
        stats["partial"] = True
    stats["records"] = len(recs)
    stats["requests"] = client.requests
    stats["retries"] = client.retries
    return recs, stats


# ------------------------------------------------------------------------------------------ output
def ensure_dir(out: Path) -> None:
    """Create the output directory 0750 (a directory that already exists is left alone)."""
    if not out.exists():
        out.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        try:
            os.chmod(out, DIR_MODE)
        except OSError:
            pass


def hourly_path(out: Path, epoch: float) -> Path:
    return out / f"book-{datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y%m%dT%H')}.jsonl.gz"


def write_records(out: Path, records: list[dict], epoch: float) -> Path | None:
    """Append one gzip member per pass to the hour's file (gzip members concatenate cleanly)."""
    if not records:
        return None
    ensure_dir(out)
    p = hourly_path(out, epoch)
    payload = ("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)).encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)  # the process umask can only tighten it
    with os.fdopen(fd, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as gz:
        gz.write(payload)
    try:
        os.chmod(p, FILE_MODE)  # exact mode even under a looser umask
    except OSError:
        pass
    return p


def rotate(out: Path, max_bytes: int, keep: Path | None = None) -> list[str]:
    """Delete the oldest hourly files until the total is within max_bytes; never the newest or `keep`."""
    files = sorted(p for p in out.glob("book-*.jsonl.gz") if FILE_RE.match(p.name))
    total = sum(p.stat().st_size for p in files)
    removed: list[str] = []
    newest = files[-1] if files else None
    for p in files:
        if total <= max_bytes:
            break
        if p == newest or p == keep:
            continue
        sz = p.stat().st_size
        p.unlink()
        total -= sz
        removed.append(p.name)
    return removed


def free_ok(out: Path, min_free_bytes: int) -> bool:
    ensure_dir(out)
    return shutil.disk_usage(out).free >= min_free_bytes


# ------------------------------------------------------------------------------------------ main
def run_once(client: Client, series_list: list[str], out: Path, max_bytes: int, min_free_bytes: int,
             max_markets: int, budget_s: float | None, now: Callable[[], float] = time.time,
             log: Callable[[str], None] = lambda m: None) -> int:
    rotate(out, max_bytes) if out.exists() else None
    if not free_ok(out, min_free_bytes):
        log("free disk below the floor; not writing")
        return 4
    recs, stats = snapshot_pass(client, series_list, now=now, max_markets=max_markets, budget_s=budget_s, log=log)
    p = write_records(out, recs, now())
    removed = rotate(out, max_bytes, keep=p)
    log(json.dumps({**stats, "file": p.name if p else None, "rotated": len(removed)}))
    if stats["series"] == 0 and stats["errors"]:
        return 2  # nothing could be read at all
    return 0


def build_parser() -> argparse.ArgumentParser:
    a = argparse.ArgumentParser(description="Keyless public order-book snapshot recorder (GET only).")
    a.add_argument("--series-file", required=True, help="text file, one series ticker per line (# comments ok)")
    a.add_argument("--out", required=True, help="output directory for hourly book-YYYYMMDDTHH.jsonl.gz files")
    a.add_argument("--once", action="store_true", help="one pass then exit (for a systemd timer / tests)")
    a.add_argument("--interval-minutes", type=float, default=5.0, help="loop mode: minutes between passes")
    a.add_argument("--rps", type=float, default=DEFAULT_RPS, help="requests per second (hard cap 5)")
    a.add_argument("--max-markets-per-series", type=int, default=400)
    a.add_argument("--time-budget-seconds", type=float, default=200.0, help="a pass stops (partial) after this long")
    a.add_argument("--max-gb", type=float, default=3.0, help="disk cap for the output directory")
    a.add_argument("--min-free-gb", type=float, default=1.0, help="refuse to write below this free space")
    return a


def main(argv: list[str] | None = None, client: Client | None = None, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], float] = time.time) -> int:
    a = build_parser().parse_args(argv)
    stop = threading.Event()
    client = client or Client(rps=min(a.rps, MAX_RPS), stop=stop)
    stop = client.stop

    def on_signal(*_):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass

    def log(msg: str) -> None:
        print(f"{iso(now())} {msg}", file=sys.stderr, flush=True)

    try:
        series_list = read_series_file(Path(a.series_file))
    except (OSError, ValueError) as e:
        log(f"series file: {e}")
        return 2
    out = Path(a.out)
    max_bytes, min_free = int(a.max_gb * 1e9), int(a.min_free_gb * 1e9)
    rc = 0
    while True:
        started = now()
        rc = run_once(client, series_list, out, max_bytes, min_free, a.max_markets_per_series,
                      a.time_budget_seconds, now=now, log=log)
        if a.once or stop.is_set():
            return rc
        wait = max(1.0, a.interval_minutes * 60.0 - (now() - started))
        end = time.monotonic() + wait
        while time.monotonic() < end and not stop.is_set():
            sleep(min(1.0, max(0.0, end - time.monotonic())))
        if stop.is_set():
            return rc


if __name__ == "__main__":
    os.umask(0o027)  # new files default to 0640, directories to 0750 (no access for other users)
    raise SystemExit(main())
