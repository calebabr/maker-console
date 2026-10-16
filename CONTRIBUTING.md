# Contributing

## Commit messages

This repo uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<area>): <what changed, imperative mood, at most 72 characters>

<why it changed and anything a reader should know: 1-5 short lines>
Tests: <which suites ran and passed>
```

**Types**

| Type | Use for |
|---|---|
| `feat` | a new feature or user-visible behaviour |
| `fix` | a bug fix |
| `perf` | faster or lighter, same behaviour |
| `refactor` | code restructure, same behaviour |
| `security` | a security fix or hardening |
| `test` | tests only |
| `docs` | documentation only |
| `ci` | CI workflows, hooks, release automation |
| `chore` | dependencies, tooling, housekeeping |

**Areas:** `app` (phone app), `api` (control API), `bot` (trading bot), `db`, `auth`, `deploy`, `tasks` (backlog), `research`.

**Rules**
- **Subject line:**
  - imperative mood ("add", "fix", not "added" or "adds");
  - no trailing period;
  - no internal shorthand (no "item 45" or "UX sprint");
  - say what changed in plain words.
- **Body:** say *why*, plus anything surprising, and end with the test suites that passed. A one-line docs or chore commit may skip the body.
- **Breaking changes:** add `!` after the area (`feat(api)!: ...`) and a `BREAKING CHANGE:` line in the body.

Examples:
```
feat(app): add sticky page title that hides after 2.5 s idle

Shows which page you are on while scrolling without covering content.
Never covers the header Stop button.
Tests: control_api, kalshi_bot, app (vitest, build, e2e) green
```
```
perf(test): run API tests in parallel and mark 24 slow tests
docs(tasks): plan the release-process sprint
```

## Commit size and frequency

One **logical change** per commit: a single feature, fix or refactor that can be understood, reviewed and reverted on its own.

- **Not too small.**
  - No commit per typo, per backlog line or per tiny step.
  - Fold small follow-ups into the change they belong to before committing.
  - Backlog/docs edits from one working session go into **one** `docs(...)` commit.
- **Not too big.**
  - Split unrelated changes, even if they were made together.
  - Guideline: about 50-500 changed lines of hand-written code per commit. Tests, lock files and generated files don't count, but stay in the same commit as the code they belong to.
  - Bigger than that is fine only when it is genuinely one change (e.g. a new page).
- **When to commit:** as soon as a logical change is complete and the quick tests pass.
- **When to push:** after the full test run passes (see CLAUDE/agent rules). Push at the end of each work block, not mid-change.
- **Every commit must leave the tests green.** This keeps history bisectable.
- **Documentation:** docs that describe a feature go in the same commit as the feature; standalone doc work gets its own `docs` commit.

## Checks that enforce this (hooks and CI)

Run `scripts/install_hooks.sh` once per clone.

| Where | Check | Needs |
|---|---|---|
| `commit-msg` hook, and the `commit-messages` CI job | Subject matches `<type>(<area>): <summary up to 72 chars>`; agent attribution trailers are refused | python |
| `pre-commit` hook | Private denylist scan on the staged diff, then gitleaks on staged changes | the denylist, gitleaks |
| `pre-push` hook | Denylist scan of new commits and gitleaks over the history (refuses if gitleaks is missing) | the denylist, gitleaks |
| `tests` CI workflow | Hash-locked install, bot, API and script tests, app tests, build, audit, CSP check, mock end-to-end | none |
| `gitleaks` CI workflow | Secret scan of the full history and the working tree (pinned binary and checksum) | none |
| `semgrep` CI workflow | Python, JavaScript/React, GitHub Actions and Dockerfile rule packs, compared with `.github/semgrep-baseline.json` | none |

Never use `--no-verify`.

### Triaging a semgrep finding

1. Try to fix the code. Most findings are real or can be written so the rule no longer matches.
2. If it is a false positive, run `python scripts/semgrep_triage.py --report semgrep-report.json --print-baseline` for a draft entry and add it to `.github/semgrep-baseline.json` with a plain-words `reason` (why it cannot be exploited). A baseline entry follows the flagged source text, not the line number: editing the line asks for a new review.
3. Remove baseline entries when the code is fixed (CI prints stale ones).

### Pull requests and releases

The pull-request template carries the security-review checklist (strategy and orders, Stop and auth, schema, release and CI, dependencies). `.github/CODEOWNERS` lists the paths that need the owner's review. Production deploys only happen through the manual `release` workflow (`.github/workflows/release.yml`, see `docs/RELEASE_PROCESS.md`); agents never run or approve it. Release notes come from the commit subjects (`scripts/changelog.py`), which is one more reason to write them in plain words.

