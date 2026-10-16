# Changelog

## Unreleased - release pipeline and CI (Sprint 1)
- `release` workflow: manual, main only. Gate (CI green on the exact commit, running server healthy, no arbitrage set executing, handover-capable or stopped), pending release approved in the app with 2FA (30 minutes), backend deploy and verify, frontend deploy and verify, ntfy, tag and notes.
- Render: auto-deploy off, 120 s shutdown delay, new env names `APP_VERSION`, `HANDOVER_HMAC_KEY`, `DEPLOY_STATUS_KEY`, `RELEASE_TOKEN` (values only in the dashboard).
- CI: semgrep workflow with a reviewed baseline, Dependabot (monthly, grouped), commit-message check in the hook and in CI, PR template with a security checklist, CODEOWNERS, workflow safety tests.
- Scripts: `release_gate.py`, `release_ctl.py`, `changelog.py`, `commit_msg_check.py`, `semgrep_triage.py`, each with tests.

## 0.9.0 - 2026-10-15 (Summary-first UX, unreleased; not yet browser-verified)
- New name and logo: Keel; three dark themes and text size.
- Overview: headline, today's P&L, last 12 runs, Since You Last Checked, P&L and At Risk by source.
- Your own Kalshi trades labelled "You" and kept out of bot stats.
- Markets folded cards; Analytics tabs (Summary, Edge, Execution, Trades); My Trades and notes.
- Terminology search v2 and help popover.
- Alert history, alert settings (daily summary, mode, quiet hours), web push (VAPID, no new dependency).
- Settings change preview and Undo Last Change.
- API: new endpoints and env vars `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` are described in the Summary-first UX section of `docs/UX_CONTRACT.md`.
