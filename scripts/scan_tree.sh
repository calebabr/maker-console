#!/usr/bin/env bash
# Scan the whole working tree (everything git would publish) against the private denylist. Run before the first
# push and whenever the tree was bulk-edited. Denylist: env MAKER_DENYLIST or ../tools/.denylist.txt (fails closed).
#   scripts/scan_tree.sh [DIR]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 2>/dev/null || true)"
"$PY" -c '' >/dev/null 2>&1 || PY="$(command -v python || true)"
[ -n "$PY" ] || { echo "scan_tree: python not found (refusing)" >&2; exit 3; }
exec "$PY" "$HERE/denylist_scan.py" tree "$@"
