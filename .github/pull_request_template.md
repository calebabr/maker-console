## What and why

<!-- One logical change. Plain words. Link the task if there is one. -->

## Tests

- [ ] `python -m pytest -q -n auto control_api/tests` (full)
- [ ] `cd kalshi_bot && python -m pytest -q tests`
- [ ] `python -m pytest -q scripts/tests`
- [ ] `cd control_app && npm test && npm run build && npm run e2e`

## Security-review checklist

Tick every box that applies and write one line on how the change stays safe. The paths that need it are listed in CODEOWNERS.
If none applies, write "none" here: ____

- [ ] **Strategy / orders / sizing / quoting** (`kalshi_bot/`, runner order paths): no change to the validated cells, quoting or sizing, or the owner asked for it in writing. Demo first.
- [ ] **Stop / auth / 2FA / secrets / security headers**: Stop still works with one tap, no 2FA and no network; Start, live mode and settings changes still need fresh 2FA; no secret reaches git, logs, the DB, API responses or the app.
- [ ] **Database schema** (`control_api/db/`): additive only (expand, then contract); old documents still load; the `schema_v1` fixtures pass; indexes reviewed.
- [ ] **Release / lease / handover / CI** (`.github/`, `scripts/`, `render.yaml`): no new secret in CI, workflow permissions stay read-only, actions pinned by full SHA, no `pull_request_target`, no `${{ }}` inside `run:`, fail closed.
- [ ] **Dependencies**: lock files regenerated with hashes; the supply-chain notes are in the PR.
- [ ] **Public repo hygiene**: no real cells, tickers, sizing or research numbers; the denylist scan and gitleaks are clean.

## Rollout

- [ ] Safe to deploy while the bot runs (handover works), or the bot must be stopped first: ____
