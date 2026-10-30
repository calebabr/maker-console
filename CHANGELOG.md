# Changelog

## Unreleased - rename to Prior
- The phone app is now called Prior, with a new logo (a ring holding a wide prior curve and a narrow posterior curve) in all three themes. Page titles, notifications and the install name say Prior.
- Saved theme, text size and last-visit preferences carry over from the old name.

## Unreleased - honest P&L labels
- Overview: the 30-day tile is now "Maker P&L (30 days)" with a combined Maker + Arbitrage total and a Maker / Arbitrage split; every P&L figure on Overview, Analytics, Account, Arbitrage and Shadow carries a one-line scope (what it measures, date rule) from one registry, `utils/pnlScopes.js`.
- Today's P&L starts at the viewer's local midnight (`GET /api/status?tz=`, UTC fallback for any unknown name) and says "No settlements yet today" instead of a dash; `$0.00` means settled and netted to zero.
- Arbitrage: realized profit beside guaranteed profit, with a note on why they differ. Shadow: "No estimate yet" instead of a zeroed headline.
- API (additive): `pnl_arb`, `pnl_total`, `pnl_today_tz`, `pnl_today_since` on `/api/status`.

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
- API: new endpoints and env vars `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` are described in `docs/DEPLOY.md`.
