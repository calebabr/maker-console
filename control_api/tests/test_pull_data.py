import asyncio
import csv
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from control_api.db import MemoryStore
from control_api.db.seed import seed
from control_api.tools import pull_data

FILES = ["state.json", "journal.csv", "account_snapshots.csv", "fills.csv", "fill_pnl.csv", "settlements.csv",
         "heartbeats.csv", "quote_episodes.csv", "arb_opportunities.csv", "audit.csv", "monitor_summaries.json",
         "config_versions.json", "periods.json", "cash_flows.json", "SUMMARY.md", "DATA_DICTIONARY.md"]
LEAKS = ["password_hash", "totp", "argon2", "s3cr3t-value", "-----begin", "eyjhbgcioijiuzi1nij9", "mongodb+srv://",
         "203.0.113.24", "iphone", "hunter2", "tick-tok-123", "private_key", "jwt_secret"]


class ReadOnlyStore:
    """Wraps a store: allows only read methods, never users / revoked tokens; records calls."""
    ALLOWED = re.compile(r"^(get_state|get_config|get_cloud_settings|last_\w+|list_\w+|close)$")

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __getattr__(self, name):
        if not self.ALLOWED.match(name):
            raise AssertionError(f"pull_data must not call store.{name}")
        self.calls.append(name)
        return getattr(self.inner, name)


async def _rich_store(now):
    store = MemoryStore()
    await seed(store, now=now)
    # one more of every collection seed doesn't cover, plus secrets in places they should never be
    await store.upsert_quote_episodes([{
        "order_id": "ord-1", "ts": now - timedelta(hours=3), "env": "demo", "series": "KXDEMOB",
        "ticker": "KXDEMOB-26OCT13-B70.5", "side": "no", "book_side": "ask", "price": 0.43, "order_price": 0.07,
        "count": 5, "status": "closed", "outcome": "filled", "queue_ahead_at_join": 12.0, "filled_count": 5,
        "fill_ids": ["demo-fill-0001"], "front_fill": True, "front_eval": True,
        "last_obs": {"ts": now - timedelta(hours=2), "level": 30, "traded": 12, "through": False,
                     "lo": 0, "hi": 0, "nc": 0, "kq": None, "rem": 0}}])
    await store.add_arb_opportunity({"ts": now - timedelta(hours=1), "event": "KXDEMOB-26OCT13", "kind": "YES",
                                     "contracts": 3, "profit": 0.5, "cost": 2.4, "legs": [["t", "bid", 0.4, 3]],
                                     "executed": "FILLED {}", "mode": "demo"})
    await store.add_monitor_summary({"ts": now - timedelta(days=9), "by": "weekly",
                                     "summary": {"overall": {"pnl": 1.0}}, "report_text": "ok"})
    await store.add_monitor_summary({"ts": now - timedelta(days=200), "by": "weekly", "summary": {}})  # outside window
    await store.set_fill_fields("demo-fill-0001", {"fill_quality": {"trade_through": True, "move_60m_c": -1.5}})
    await store.add_audit({"ts": now - timedelta(hours=1), "user": "owner", "action": "login", "ok": True,
                           "details": {"password": "hunter2", "note": "Authorization: Bearer abc.def.ghi",
                                       "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig"},
                           "ip": "203.0.113.24", "ua": "iPhone"})
    await store.add_heartbeat({"ts": now - timedelta(minutes=1), "ok": False,
                               "error": "connect mongodb+srv://u:pw@host failed; secret=s3cr3t-value",
                               "private_key_hint": "-----BEGIN RSA PRIVATE KEY-----x-----END RSA PRIVATE KEY-----"})
    await store.add_period({"id": "pd-2", "name": "Demo Period 2", "started_at": now - timedelta(days=1),
                            "note": "after Sprint 2A-1", "created_by": "owner"})
    await store.add_cash_flow({"id": "cf-1", "ts": now - timedelta(days=3), "kind": "deposit", "amount": 50.0,
                               "note": "top up", "created_by": "owner"})
    await store.save_cloud_settings({"pinger_minutes": 5, "webhook_secret_hint": "s3cr3t-value"}, by="t")
    # users / revoked tokens exist but must never be read
    await store.save_user({"username": "owner", "password_hash": "$argon2id$v=19$xyz", "totp_secret_enc": "gAAA"})
    await store.revoke_jti("jti-1", now + timedelta(days=1))
    return store


def _pull(store, out, days=90):
    data = asyncio.run(pull_data.collect(store, days))
    return pull_data.write_all(data, out, days, "test")


def _header(path):
    with path.open(encoding="utf-8", newline="") as f:
        return next(csv.reader(f))


@pytest.mark.slow
def test_pull_seed_demo(tmp_path, capsys):
    out = tmp_path / "ld"
    assert pull_data.main(["--seed-demo", "--out", str(out), "--days", "90"]) == 0
    for name in FILES:
        assert (out / name).is_file(), name
    assert (out / "SUMMARY.md").read_text(encoding="utf-8").strip()
    for p in (f for f in out.rglob("*") if f.is_file()):
        text = p.read_text(encoding="utf-8").lower()
        assert "password_hash" not in text and "totp_secret" not in text, p.name
        assert "203.0.113.24" not in text, p.name                      # seed audit IP never exported
    assert str(out) in capsys.readouterr().out
    rows = list(csv.DictReader((out / "fill_pnl.csv").open(encoding="utf-8")))
    assert rows and any(r["status"] == "settled" and r["pnl"] for r in rows)


@pytest.mark.slow
def test_every_collection_exported_read_only_and_scrubbed(tmp_path):
    now = datetime.now(timezone.utc)
    inner = asyncio.run(_rich_store(now))
    store = ReadOnlyStore(inner)
    out = tmp_path / "ld"
    counts = _pull(store, out)
    for name in FILES:
        assert (out / name).is_file(), name
    for name in ("journal.csv", "fills.csv", "settlements.csv", "account_snapshots.csv", "heartbeats.csv",
                 "quote_episodes.csv", "arb_opportunities.csv", "audit.csv", "fill_pnl.csv"):
        assert counts[name] > 0, name
    assert counts["monitor_summaries.json"] == 2                       # seed's + ours; the 200-day-old one excluded
    assert counts["config_versions.json"] == 2
    assert counts["periods.json"] == 2 and counts["cash_flows.json"] == 3          # seed's + ours
    assert "Demo Period 2" in [p["name"] for p in json.loads((out / "periods.json").read_text(encoding="utf-8"))]
    assert not any("user" in c or "revoke" in c for c in store.calls)
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert st["cloud_settings"]["pinger_minutes"] == 5 and "webhook_secret_hint" not in st["cloud_settings"]
    assert st["config"] and st["state"]["mode"] == "demo"
    assert "fill_quality.trade_through" in _header(out / "fills.csv")
    assert "last_obs.level" in _header(out / "quote_episodes.csv")
    assert _header(out / "audit.csv") == pull_data.AUDIT_KEYS
    for p in (f for f in out.rglob("*") if f.is_file()):
        text = p.read_text(encoding="utf-8").lower()
        for leak in LEAKS:
            assert leak not in text, (p.name, leak)
    summary = (out / "SUMMARY.md").read_text(encoding="utf-8")
    for heading in ("Settlements and realised P&L", "Quotes and queue position", "Bracket arbitrage", "Monitor"):
        assert heading in summary
    assert re.search(r"executed: [1-9]", summary) and re.search(r"Quote episodes \(maker orders\): [1-9]", summary)
    assert "ord-1" in (out / "quote_episodes.csv").read_text(encoding="utf-8")


@pytest.mark.slow
def test_days_window_respected(tmp_path):
    now = datetime.now(timezone.utc)
    store = asyncio.run(_rich_store(now))
    out = tmp_path / "ld"
    _pull(store, out, days=1)
    cutoff = now - timedelta(days=1, minutes=1)
    for name in ("journal.csv", "fills.csv", "settlements.csv", "heartbeats.csv", "account_snapshots.csv",
                 "quote_episodes.csv", "arb_opportunities.csv", "audit.csv"):
        for r in csv.DictReader((out / name).open(encoding="utf-8")):
            assert datetime.fromisoformat(r["ts"]) >= cutoff, (name, r["ts"])
    assert json.loads((out / "monitor_summaries.json").read_text(encoding="utf-8")) == []


@pytest.mark.slow
def test_dictionary_covers_every_file_and_column(tmp_path):
    store = asyncio.run(_rich_store(datetime.now(timezone.utc)))
    out = tmp_path / "ld"
    _pull(store, out)
    dd = (out / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    sections = dict(re.findall(r"^## (\S+)\n(.*?)(?=^## |\Z)", dd, flags=re.M | re.S))
    for p in out.iterdir():
        assert (p.name + "/" if p.is_dir() else p.name) in sections, p.name
        if p.suffix == ".csv":
            for col in _header(p):
                assert f"| `{col}` |" in sections[p.name], (p.name, col)
    assert "UTC" in dd
    # table rows stay well-formed: every row has exactly 2 unescaped cells
    for line in dd.splitlines():
        if line.startswith("| `"):
            assert len(re.split(r"(?<!\\)\|", line)) == 4, line


def test_empty_store(tmp_path):
    out = tmp_path / "ld"
    counts = _pull(MemoryStore(), out)
    for name in FILES:
        assert (out / name).is_file(), name
    assert counts["fills.csv"] == 0
    assert "no quote episodes" in (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "| `empty` |" in (out / "DATA_DICTIONARY.md").read_text(encoding="utf-8")


def test_stale_analytics_file_removed_when_unavailable(tmp_path, monkeypatch):
    out = tmp_path / "ld"
    out.mkdir()
    (out / "analytics_summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pull_data, "try_analytics", lambda *a: (None, "skipped"))
    _pull(MemoryStore(), out)
    assert not (out / "analytics_summary.json").exists()


def test_analytics_written_when_available(tmp_path, monkeypatch):
    out = tmp_path / "ld"
    monkeypatch.setattr(pull_data, "try_analytics", lambda *a: ({"totals": {"pnl": 1.0}}, ""))
    counts = _pull(MemoryStore(), out)
    assert counts["analytics_summary.json"] == 1
    assert "analytics_summary.json" in (out / "DATA_DICTIONARY.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["password", "TOTP_secret", "api_key", "private_key", "jwt_secret", "tick_token",
                                 "cronjob_api_key", "mongodb_uri", "ip", "ua", "access_token", "client_ip"])
def test_scrub_drops_secret_keys(key):
    assert pull_data.clean({key: "x", "keep": 1, "nested": [{key: "y", "row_hash": "h"}]}) == \
        {"keep": 1, "nested": [{"row_hash": "h"}]}


def test_env_local_parser(tmp_path):
    f = tmp_path / ".env.local"
    f.write_text("# c\nMONGODB_URI='mongodb://x'\nMONGODB_DB=bot\nbad\n", encoding="utf-8")
    assert pull_data.load_env_local(f) == {"MONGODB_URI": "mongodb://x", "MONGODB_DB": "bot"}


def test_list_monitor_summaries_parity():
    """MemoryStore implements the list; the Store default falls back to the last summary."""
    async def go():
        s = MemoryStore()
        t = datetime.now(timezone.utc)
        for i in range(3):
            await s.add_monitor_summary({"ts": t - timedelta(days=i), "n": i})
        got = await s.list_monitor_summaries(since=t - timedelta(days=1, minutes=1))
        assert [d["n"] for d in got] == [0, 1]
        from control_api.db.store import Store
        assert [d["n"] for d in await Store.list_monitor_summaries(s, limit=5)] == [0]
    asyncio.run(go())


@pytest.mark.slow
def test_real_analytics_summary_when_service_exists(tmp_path):
    pytest.importorskip("control_api.services.analytics")
    store = asyncio.run(_rich_store(datetime.now(timezone.utc)))
    out = tmp_path / "ld"
    counts = _pull(store, out, days=45)
    assert counts.get("analytics_summary.json") == 1
    a = json.loads((out / "analytics_summary.json").read_text(encoding="utf-8"))
    assert a["days"] == 30 and a["env"] == "demo" and "totals" in a and "by_cell" in a


# ---------------------------------------------------------------- history + archive (sprint extension item 8)
@pytest.mark.slow
def test_history_merge_dedupes_across_two_pulls(tmp_path):
    store = asyncio.run(_rich_store(datetime.now(timezone.utc)))
    out = tmp_path / "ld"
    c1 = _pull(store, out)
    c2 = _pull(store, out)
    for name in pull_data.HISTORY_KEYS:
        assert c2[f"history/{name}.csv"] == c1[f"history/{name}.csv"] == c1[f"{name}.csv"], name
        keys = pull_data.HISTORY_KEYS[name]
        rows = list(csv.DictReader((out / "history" / f"{name}.csv").open(encoding="utf-8")))
        ids = [tuple(r[k] for k in keys) for r in rows]
        assert len(ids) == len(set(ids)), name


def test_history_survives_data_ageing_out_and_updates_rows(tmp_path):
    now = datetime.now(timezone.utc)
    store = MemoryStore()
    f = {"fill_id": "f1", "ticker": "KXDEMOB-26OCT10-B70.5", "side": "no", "count": 2, "no_price_dollars": "0.4",
         "is_taker": False, "ts": now - timedelta(days=5)}
    asyncio.run(store.upsert_fills([f]))
    out = tmp_path / "ld"
    _pull(store, out)
    first = list(csv.DictReader((out / "history" / "fill_pnl.csv").open(encoding="utf-8")))
    assert [r["status"] for r in first] == ["open"]
    # next pull: the old fill has aged out of the store, a new fill + the old fill's settlement arrive
    store2 = MemoryStore()
    asyncio.run(store2.upsert_fills([f, {**f, "fill_id": "f2", "ts": now - timedelta(hours=1)}]))
    asyncio.run(store2.upsert_settlements([{"ticker": f["ticker"], "market_result": "no", "ts": now}]))
    _pull(store2, out)
    store3 = MemoryStore()                                     # third pull: everything has aged out
    _pull(store3, out)
    rows = {r["fill_id"]: r for r in csv.DictReader((out / "history" / "fill_pnl.csv").open(encoding="utf-8"))}
    assert set(rows) == {"f1", "f2"}
    assert rows["f1"]["status"] == "settled" and float(rows["f1"]["pnl"]) == pytest.approx(1.2)
    assert {r["fill_id"] for r in csv.DictReader((out / "history" / "fills.csv").open(encoding="utf-8"))} == {"f1", "f2"}
    assert (out / "fills.csv").read_text(encoding="utf-8").strip() == "empty"   # top-level file = this pull only


@pytest.mark.slow
def test_history_is_scrubbed(tmp_path):
    out = tmp_path / "ld"
    hist = out / "history"
    hist.mkdir(parents=True)
    # a history file written by some older/buggy version that leaked a secret column and value
    (hist / "heartbeats.csv").write_text(
        "ts,ok,api_key,error\n2026-01-01T00:00:00+00:00,False,k-123456,mongodb+srv://u:pw@h/x\n", encoding="utf-8")
    store = asyncio.run(_rich_store(datetime.now(timezone.utc)))
    _pull(store, out)
    for p in hist.iterdir():
        text = p.read_text(encoding="utf-8").lower()
        for leak in LEAKS + ["api_key", "k-123456"]:
            assert leak not in text, (p.name, leak)
    assert "2026-01-01T00:00:00+00:00" in (hist / "heartbeats.csv").read_text(encoding="utf-8")


def test_archive_snapshot_and_pruning(tmp_path):
    out = tmp_path / "ld"
    arch = out / "archive"
    (arch / "2020-01-01-x0").mkdir(parents=True)               # not a dated folder: never pruned
    for i in range(95):
        d = arch / (datetime(2020, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        (d / "SUMMARY.md").write_text("old", encoding="utf-8")
    (arch / "notes").mkdir()
    counts = _pull(MemoryStore(), out)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert counts[f"archive/{today}"] == 1
    assert (arch / today / "SUMMARY.md").read_text(encoding="utf-8").startswith("# Bot data summary")
    dated = sorted(d.name for d in arch.iterdir() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name))
    assert len(dated) == pull_data.ARCHIVE_KEEP and dated[-1] == today
    assert "2020-01-01" not in dated and (arch / "notes").is_dir() and (arch / "2020-01-01-x0").is_dir()


@pytest.mark.slow
def test_dictionary_covers_history_and_archive(tmp_path):
    store = asyncio.run(_rich_store(datetime.now(timezone.utc)))
    out = tmp_path / "ld"
    _pull(store, out)
    dd = (out / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    sections = dict(re.findall(r"^## (\S+)\n(.*?)(?=^## |\Z)", dd, flags=re.M | re.S))
    for name in ("history/", "archive/", "pull.log"):
        assert name in sections, name
    for p in (out / "history").iterdir():
        sec = sections[f"history/{p.name}"]
        for col in _header(p):
            assert f"| `{col}` |" in sec, (p.name, col)


def test_summary_edge_verdict_lines_guarded():
    assert pull_data.analytics_lines({}) == []
    assert pull_data.analytics_lines({"by_cell": [{"key": "x"}], "fill_rate": None, "calibration": []}) == []
    lines = pull_data.analytics_lines({
        "expectations": {"source": "fake benchmark", "loaded": True},
        "by_cell": [{"cell": "0.38-0.45|5-11h|no", "markets": 25, "n": 80, "win_rate": 0.45, "expected_win_rate": 0.44,
                     "verdict": "on_track", "verdict_reason": "Within | the CI."},
                    {"cell": "0.34-0.38|5-11h|no", "markets": 3, "verdict": "too_early", "expected_roi": 0.01}],
        "fill_rate": {"totals": {"quotes": 10, "filled": 4, "fill_rate": 0.4}},
        "calibration": [{"bucket": "0.34-0.35", "n": 0}, {"bucket": "0.40-0.41", "n": 5}]})
    text = "\n".join(lines)
    assert "on_track" in text and "too_early" in text and "ROI 0.01" in text and "Within / the CI." in text
    assert "fill_rate=0.4" in text and "1 of 2" in text and "loaded=True" in text


@pytest.mark.slow
def test_schedule_script_parses():
    import shutil
    import subprocess
    from pathlib import Path
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        pytest.skip("no PowerShell available")
    script = Path(pull_data.REPO_ROOT) / "scripts" / "schedule_pull_data.ps1"
    text = script.read_text(encoding="utf-8")
    for needle in ("-StartWhenAvailable", "LogonType Interactive", "pull.log", "control_api.tools.pull_data",
                   "--days $Days", "[int]$Days = 180", '"07:00"'):
        assert needle in text, needle
    assert "-Password" not in text and "Register-ScheduledTask" in text
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                        f"$null = [scriptblock]::Create((Get-Content -Raw '{script}')); 'ok'"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
