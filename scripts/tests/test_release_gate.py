"""release_gate.py truth table: every refusal reason, every bypass rule, and the CI check-runs reader."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import release_gate as rg  # noqa: E402


def status(**over):
    base = {"version": "2026.10.16-1", "schema_version": 1, "lease_holder": "abc", "generation": 4, "role": "leader",
            "running": True, "stopped": False, "mode": "demo", "arb_executing": False, "healthy": True}
    base.update(over)
    return base


def ev(st, **kw):
    kw.setdefault("ci_green", True)
    return rg.evaluate(st, **kw)


# ---- the happy path ------------------------------------------------------------------------------------------------
def test_everything_ok_allows_the_deploy():
    d = ev(status())
    assert d.ok and d.reasons == []


# ---- one test per refusal reason (and what does / does not bypass it) -----------------------------------------------
def test_ci_not_green_refuses_even_when_everything_else_is_fine_and_cannot_be_bypassed():
    d = ev(status(), ci_green=False)
    assert not d.ok and d.reasons == [rg.CI_NOT_GREEN]
    assert rg.CI_NOT_GREEN in ev(None, ci_green=False, bot_stopped_confirmed=True).reasons


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_ci_flag_must_be_exactly_true(bad):
    assert rg.CI_NOT_GREEN in rg.evaluate(status(), ci_green=bad).reasons


def test_status_unavailable_refuses_unless_the_owner_attests_the_bot_is_stopped():
    assert ev(None).reasons == [rg.STATUS_UNAVAILABLE]
    assert ev({}).reasons == [rg.STATUS_UNAVAILABLE]
    d = ev(None, bot_stopped_confirmed=True)
    assert d.ok and d.notes


@pytest.mark.parametrize("broken", [
    "unreadable", [], 5,
    {k: v for k, v in status().items() if k != "healthy"},                  # missing field
    {**status(), "extra": 1},                                                # extra field (exact shape only)
    status(running="yes"), status(generation=True), status(generation=-1), status(role="boss"),
    status(schema_version="1"), status(lease_holder=5),
])
def test_malformed_status_refuses_unless_attested(broken):
    assert ev(broken).reasons == [rg.STATUS_MALFORMED]
    assert ev(broken, bot_stopped_confirmed=True).ok


def test_arb_executing_refuses_and_nothing_bypasses_it():
    assert ev(status(arb_executing=True)).reasons == [rg.ARB_EXECUTING]
    assert rg.ARB_EXECUTING in ev(status(arb_executing=True, stopped=True, running=False),
                                  bot_stopped_confirmed=True).reasons


def test_unhealthy_server_with_a_running_bot_refuses_but_is_fine_when_stopped():
    assert ev(status(healthy=False)).reasons == [rg.UNHEALTHY_BOT_RUNNING]
    assert ev(status(healthy=False, stopped=True, running=False)).ok


def test_not_handover_capable_refuses_unless_stopped():
    old = status(version="2026.09.01-1")
    assert ev(old, min_handover_version="2026.10.16-1").reasons == [rg.NOT_HANDOVER_CAPABLE]
    assert ev(old, min_handover_version="2026.10.16-1").reasons != []
    assert ev(status(version="0.0.0-dev"), min_handover_version="2026.10.16-1").reasons == [rg.NOT_HANDOVER_CAPABLE]
    assert ev(old, min_handover_version="2026.10.16-1", **{}).ok is False
    stopped = status(version="2026.09.01-1", stopped=True, running=False)
    assert ev(stopped, min_handover_version="2026.10.16-1").ok
    assert ev(status(version="2026.10.16-1"), min_handover_version="2026.10.16-1").ok
    assert ev(status(version="2026.10.17-1"), min_handover_version="2026.10.16-1").ok
    assert ev(status(version="2026.09.01-1")).ok          # no minimum configured: any server that answers is capable


def test_unparsable_minimum_fails_closed():
    assert ev(status(), min_handover_version="not-a-version").reasons == [rg.NOT_HANDOVER_CAPABLE]


def test_schema_newer_than_the_release_refuses_even_when_stopped():
    d = ev(status(schema_version=2, stopped=True, running=False), release_schema_version=1)
    assert d.reasons == [rg.SCHEMA_NEWER]
    assert ev(status(schema_version=1), release_schema_version=1).ok
    assert ev(status(schema_version=2)).ok                # release schema not supplied: not checked


def test_multiple_reasons_are_all_reported():
    d = ev(status(arb_executing=True, healthy=False), ci_green=False)
    assert set(d.reasons) == {rg.CI_NOT_GREEN, rg.ARB_EXECUTING, rg.UNHEALTHY_BOT_RUNNING}


def test_every_reason_code_has_an_explanation():
    assert set(rg.EXPLAIN) == set(rg.ALL_REASONS)


# ---- CI check-runs ------------------------------------------------------------------------------------------------
def run(name, status_="completed", conclusion="success", started="2026-10-16T10:00:00Z"):
    return {"name": name, "status": status_, "conclusion": conclusion, "started_at": started}


def runs(**over):
    d = {n: run(n) for n in rg.DEFAULT_REQUIRED_CHECKS}
    d.update(over)
    return {"total_count": len(d), "check_runs": list(d.values())}


def test_ci_all_required_checks_green():
    assert rg.ci_green_from_check_runs(runs()) == (True, [])


@pytest.mark.parametrize("name", rg.DEFAULT_REQUIRED_CHECKS)
@pytest.mark.parametrize("bad", [("completed", "failure"), ("completed", "skipped"), ("completed", "cancelled"),
                                 ("in_progress", None), ("queued", None), ("completed", "neutral")])
def test_ci_any_required_check_not_successful_is_not_green(name, bad):
    ok, problems = rg.ci_green_from_check_runs(runs(**{name: run(name, *bad)}))
    assert not ok and problems and name in problems[0]


def test_ci_missing_check_and_unreadable_payload_are_not_green():
    p = runs()
    p["check_runs"] = [r for r in p["check_runs"] if r["name"] != "semgrep"]
    p["total_count"] = len(p["check_runs"])
    ok, problems = rg.ci_green_from_check_runs(p)
    assert not ok and "semgrep: missing" in problems
    for junk in (None, [], {}, {"check_runs": "x"}, "text"):
        assert not rg.ci_green_from_check_runs(junk)[0]


def test_ci_uses_the_newest_run_of_a_name_and_detects_truncation():
    p = runs()
    p["check_runs"].append(run("python", "completed", "failure", started="2026-10-16T11:00:00Z"))
    p["total_count"] = len(p["check_runs"])
    assert not rg.ci_green_from_check_runs(p)[0]          # a newer failed re-run wins
    p["check_runs"].append(run("python", "completed", "success", started="2026-10-16T12:00:00Z"))
    p["total_count"] = len(p["check_runs"])
    assert rg.ci_green_from_check_runs(p)[0]              # and a newer green re-run fixes it
    p["total_count"] = 500
    assert not rg.ci_green_from_check_runs(p)[0]          # more runs exist than were fetched


# ---- CLI ----------------------------------------------------------------------------------------------------------
def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "release_gate.py"), *args], capture_output=True, text=True)


def test_cli_exit_codes_and_messages(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(status()), encoding="utf-8")
    ok = cli("gate", "--status-file", str(f), "--ci-green", "true")
    assert ok.returncode == 0 and "deploy allowed" in ok.stdout
    bad = cli("gate", "--status-file", str(f), "--ci-green", "false")
    assert bad.returncode == 1 and "CI_NOT_GREEN" in bad.stdout
    missing = cli("gate", "--status-file", str(tmp_path / "nope.json"), "--ci-green", "true")
    assert missing.returncode == 1 and "STATUS_UNAVAILABLE" in missing.stdout
    attested = cli("gate", "--status-file", str(tmp_path / "nope.json"), "--ci-green", "true", "--bot-stopped-confirmed")
    assert attested.returncode == 0
    f.write_text("{not json", encoding="utf-8")
    assert "STATUS_MALFORMED" in cli("gate", "--status-file", str(f), "--ci-green", "true").stdout
    assert cli("gate", "--ci-green", "maybe").returncode == 2


def test_cli_ci_subcommand(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(runs()), encoding="utf-8")
    assert cli("ci", "--checks-file", str(f)).returncode == 0
    f.write_text(json.dumps(runs(e2e=run("e2e", "completed", "failure"))), encoding="utf-8")
    r = cli("ci", "--checks-file", str(f))
    assert r.returncode == 1 and "e2e" in r.stdout
    assert cli("ci", "--checks-file", str(tmp_path / "missing.json")).returncode == 1
