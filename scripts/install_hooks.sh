#!/usr/bin/env bash
# One-time, per clone: point git at the versioned hooks (git does NOT run .githooks/ until this is set).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git rev-parse --is-inside-work-tree >/dev/null
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh 2>/dev/null || true
[ "$(git config --get core.hooksPath)" = ".githooks" ] || { echo "install_hooks: failed to set core.hooksPath" >&2; exit 1; }
echo "hooks enabled (core.hooksPath=.githooks)."
echo "  needs the private denylist (MAKER_DENYLIST or ../tools/.denylist.txt) and, for pushes, gitleaks on PATH."
