#!/usr/bin/env bash
# Shared helper for the hooks: finds python and runs scripts/denylist_scan.py <mode>. Fails closed.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$(command -v python3 2>/dev/null || true)"
"$py" -c '' >/dev/null 2>&1 || py="$(command -v python || true)"
if [ -z "$py" ]; then echo "hook: python not found, refusing (fail closed)." >&2; exit 3; fi
exec "$py" "$here/../scripts/denylist_scan.py" "$@"
