#!/usr/bin/env bash
# Render native build: hash-pinned dependencies, then the optional private overlay, then a final check.
# PRIVATE_REPO_TOKEN must be a FINE-GRAINED token limited to the private repo (Contents: read-only, 90-day expiry): Render
# also exposes build env vars to the running service (see deploy/private_overlay.md).
# Render sets the build env (including PRIVATE_REPO_TOKEN / PRIVATE_REPO_REF when the owner added them).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

pip install --require-hashes --only-binary :all: --no-deps -r control_api/requirements.txt

HAD_TOKEN=0
[ -n "${PRIVATE_REPO_TOKEN:-}" ] && HAD_TOKEN=1
# Render builds in a throwaway git clone that is never pushed back: the one place a git work tree is allowed.
OVERLAY_ALLOW_GIT_WORKTREE=1 bash scripts/fetch_private_overlay.sh

# The app's own gate decides what the running service will do; print it (True/False only, nothing private).
python - "$HAD_TOKEN" <<'PYEOF'
import sys
from control_api.services.botfiles import is_standin
standin = is_standin()
print(f"IS_STANDIN={standin}")
if sys.argv[1] == "1" and standin:
    sys.exit("build failed: a private token was set but the app still sees the stand-in")
PYEOF
