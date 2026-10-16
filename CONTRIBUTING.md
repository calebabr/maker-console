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
