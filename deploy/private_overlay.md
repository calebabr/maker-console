# Private strategy overlay at build time (owner steps)

This repo ships a demo stand-in for the trading strategy. Live (real-money) mode is refused until the real strategy
is installed. The build installs it for you when it can read the private repo. Without a token the build still
succeeds and prints `demo-only build ... live mode will be refused`.

What runs: `scripts/render_build.sh` (Render) or the `overlay` stage of the `Dockerfile` calls
`scripts/fetch_private_overlay.sh`, which shallow-fetches the private repo, runs `scripts/overlay_private.py`,
deletes the fetched copy, and prints `IS_STANDIN=False` (installed) or `IS_STANDIN=True` (stand-in). If the token is
set but anything fails, the build fails. The token is passed to git through a temporary askpass helper: it is never
in a URL, a command line, `.git/config` or the build log.

## Create the token (about 3 minutes)

1. Sign in to GitHub as the account that owns the private repo (`calebabr`).
2. Click your profile picture (top right) > **Settings**.
3. Left sidebar, scroll to the bottom > **Developer settings**.
4. **Personal access tokens** > **Fine-grained tokens** > **Generate new token**.
5. Token name: `render-build-overlay`. Expiration: **90 days** (put a reminder in your calendar to rotate it).
6. Resource owner: your account. Repository access: **Only select repositories** > choose the private repo only
   (your strategy repository). Do not choose "All repositories".
7. Permissions > Repository permissions > **Contents: Read-only**. Leave everything else at "No access"
   (Metadata: Read-only is added automatically and is fine).
8. Click **Generate token** and copy it once (it starts with `github_pat_`). GitHub never shows it again.
9. Render dashboard > your `kalshi-control` service > **Environment** > **Add Environment Variable**:
   key `PRIVATE_REPO_TOKEN`, value = the token. Optionally also `PRIVATE_REPO_REF` (default `main`; use a tag or
   commit hash to pin a release). **Save changes**, then **Manual Deploy > Deploy latest commit**.
10. In the build log look for `private strategy installed` and `IS_STANDIN=False`. `IS_STANDIN=True` with a token
    set means the build failed and you will see an error instead.

Never paste the token into git, an issue, a chat or the app. To revoke: GitHub > Settings > Developer settings >
Fine-grained tokens > the token > Delete (then remove `PRIVATE_REPO_TOKEN` in Render).

## Docker / VM

```
PRIVATE_REPO_TOKEN=<token> docker build --secret id=private_repo_token,env=PRIVATE_REPO_TOKEN \
    --build-arg PRIVATE_REPO_REF=main -t kalshi-control .
```
The secret is a BuildKit secret (not stored in any layer) and `git` exists only in the discarded first stage.

## Optional private files

The overlay also installs `control_api/data/cell_expectations.json` if the private repo has it. Without it, the
Analytics edge verdict compares against a default only.

## Residual risk: the token is also in the running service's environment (S-44)

Render does not offer a build-only environment variable (the Blueprint `envVars` entries are visible to both the
build and the running service; `sync: false` only keeps the value out of git). So `PRIVATE_REPO_TOKEN` is readable by
the running app process. What limits the damage:

- It must be a FINE-GRAINED token limited to the one private repo, Contents read-only, expiring in 90 days. Never a
  classic token, never "all repositories". Worst case someone who gets code execution in the service could read that
  one repository.
- Rotate it every 90 days (calendar reminder); revoke it at once if the service or the Render account is ever suspect.
- The app must never log environment values. `control_api/services/logbuffer.py` redacts a fixed list of env names;
  `PRIVATE_REPO_TOKEN` must be on that list (and `github_pat_...` / `ghp_...` shaped strings redacted). This is tracked
  for the backend owner; until it lands, treat any log export as sensitive.
- Optional hardening: after the first successful deploy, delete `PRIVATE_REPO_TOKEN` from the Render environment. The
  running service does not need it; the next deploy then builds as demo-only, so add it back (a new token) only when you
  deliberately redeploy a new strategy version.
