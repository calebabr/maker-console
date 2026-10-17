"""Static safety rules for every GitHub workflow (contract section 4 security requirements) and render.yaml.

These run in CI too, so a workflow edit that adds an unpinned action, a write permission, pull_request_target, a
schedule, or an expression inside a shell script fails the build.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
SHA_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))        # PyYAML reads the bare key `on` as boolean True
    return data


def steps(wf: dict):
    for job_id, job in wf.get("jobs", {}).items():
        for st in job.get("steps", []):
            yield job_id, st


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"tests.yml", "gitleaks.yml", "semgrep.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_rules(path):
    wf = load(path)
    triggers = wf["on"]
    names = set(triggers) if isinstance(triggers, dict) else set(triggers if isinstance(triggers, list) else [triggers])
    assert "pull_request_target" not in names, "pull_request_target runs untrusted code with secrets"
    assert "schedule" not in names, "CI must never run on a schedule"
    assert "permissions" in wf, "set top-level permissions explicitly"
    top = wf["permissions"] or {}
    assert all(v == "read" for v in top.values()) if isinstance(top, dict) else False, "top-level permissions must be read-only or none"
    for job_id, job in wf["jobs"].items():
        assert "timeout-minutes" in job, f"{job_id}: set timeout-minutes"
        for scope, level in (job.get("permissions") or {}).items():
            if level == "write":
                assert path.name == "release.yml" and job_id == "finalize" and scope == "contents", \
                    f"{job_id}: unexpected write permission {scope}"
    for job_id, st in steps(wf):
        if "uses" in st:
            assert SHA_RE.match(st["uses"]), f"{job_id}: {st['uses']} is not pinned by a full commit SHA"
            if st["uses"].startswith("actions/checkout@"):
                assert (st.get("with") or {}).get("persist-credentials") is False, f"{job_id}: checkout must not persist credentials"
        if "run" in st:
            assert "${{" not in st["run"], f"{job_id}: an expression inside run: is a shell-injection risk; pass it through env:"
            assert "secrets." not in st["run"]


RELEASE_SECRETS = ("RENDER_DEPLOY_HOOK", "VERCEL_DEPLOY_HOOK", "RELEASE_TOKEN", "DEPLOY_STATUS_KEY", "API_BASE_URL",
                   "APP_URL", "NTFY_TOPIC")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_jobs_using_release_secrets_run_in_the_production_environment(path):
    """S-55: those secrets are environment secrets of `production` (restricted to main); a job that reads one must
    declare the environment or it would silently read nothing, and a repository-level copy would be reachable from any branch."""
    for job_id, job in load(path)["jobs"].items():
        uses = [n for n in RELEASE_SECRETS if any(f"secrets.{n}" in str(st) for st in job.get("steps", []))]
        if uses:
            assert job.get("environment") == "production", f"{job_id} uses {uses} without environment: production"


def test_release_workflow_refuses_reruns_and_regates_before_deploying():
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "github.run_attempt" in text and "re-running a release is not allowed" in text
    wf = load(REPO / ".github" / "workflows" / "release.yml")
    deploy = wf["jobs"]["deploy-backend"]["steps"]
    names = [s.get("name", "") for s in deploy]
    gate_i = next(i for i, n in enumerate(names) if n.startswith("Re-run the gate"))
    hook_i = next(i for i, n in enumerate(names) if n.startswith("Trigger the Render"))
    assert gate_i < hook_i
    assert "only the current tip of main" in text


def test_release_workflow_is_manual_main_only_and_gated():
    wf = load(REPO / ".github" / "workflows" / "release.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    assert wf["permissions"] == {}
    assert "refs/heads/main" in wf["jobs"]["gate"]["if"]
    for job in ("approve", "deploy-backend", "deploy-frontend", "finalize"):
        assert wf["jobs"][job]["needs"], job
    assert "approve" in wf["jobs"]["deploy-backend"]["needs"]
    assert "deploy-backend" in wf["jobs"]["deploy-frontend"]["needs"]          # backend first, then frontend
    assert wf["concurrency"]["cancel-in-progress"] is False
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--timeout 1800" in text                                            # 30 minute approval window


def test_check_names_the_release_gate_requires_exist_as_job_ids():
    required = {"python", "frontend", "e2e", "gitleaks", "semgrep", "commit-messages"}
    found = set()
    for p in WORKFLOWS:
        for job_id, job in load(p)["jobs"].items():
            found.add(job.get("name", job_id))
    assert required <= found
    import release_gate  # noqa: PLC0415
    assert set(release_gate.DEFAULT_REQUIRED_CHECKS) == required


def test_semgrep_lock_is_hash_pinned_and_baseline_has_reasons():
    lock = (REPO / ".github" / "semgrep" / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(r"^[A-Za-z0-9_.-]+==[^\s\\]+ \\$", lock, re.M)
    assert pins and lock.count("--hash=sha256:") >= len(pins)
    import json  # noqa: PLC0415
    base = json.loads((REPO / ".github" / "semgrep-baseline.json").read_text(encoding="utf-8"))
    assert base["version"] == 1 and all(e["reason"].strip() and not e["reason"].startswith("TODO") for e in base["findings"])


# ---- render.yaml -----------------------------------------------------------------------------------------------------
def render():
    return yaml.safe_load((REPO / "render.yaml").read_text(encoding="utf-8"))["services"][0]


def test_render_release_settings():
    svc = render()
    assert svc["autoDeployTrigger"] == "off"            # only the release workflow deploys
    assert "maxShutdownDelaySeconds" not in svc          # Render rejects it on the free plan
    assert svc["plan"] == "free" and "--workers 1" in svc["startCommand"]
    env = {e["key"]: e for e in svc["envVars"]}
    for name in ("APP_VERSION", "HANDOVER_HMAC_KEY", "DEPLOY_STATUS_KEY", "RELEASE_TOKEN"):
        assert name in env, name
    for name in ("HANDOVER_HMAC_KEY", "DEPLOY_STATUS_KEY", "RELEASE_TOKEN"):
        assert env[name].get("sync") is False and "value" not in env[name], f"{name} must be a dashboard-only secret"
    assert "value" not in env["APP_VERSION"]


def test_render_has_no_literal_secret_values():
    for e in render()["envVars"]:
        if re.search(r"(KEY|TOKEN|SECRET|URI|PASSWORD|PRIVATE)", e["key"]) and not e["key"].endswith("_REF"):
            assert "value" not in e, e["key"]


@pytest.fixture(autouse=True)
def _scripts_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
