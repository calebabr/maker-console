"""Optional research benchmarks (read-only): per-cell expectations, the default ROI and the fill-quality market
baseline used for "compared with the research" labels.

The numbers are private. They are never in this repository: the file is supplied at deploy time (for example a
Render secret file named by CELL_EXPECTATIONS_PATH, or control_api/data/cell_expectations.json written by the
private overlay, which is git-ignored here). With no file every benchmark is null and the app simply shows no
comparison. The file's contents are never logged.

File shape (all keys optional):
    {"source": "...", "default": {"win_rate": null, "roi": <float>},
     "cells": {"<cell id>": {"win_rate": <float>, "roi": <float>, ...}},
     "fill_quality_baseline": {"trade_through_share": <float>, "avg_move_60m_c": <float>}}
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

EXPECTATIONS_PATH = Path(__file__).resolve().parents[1] / "data" / "cell_expectations.json"
FQ_KEYS = ("trade_through_share", "avg_move_60m_c")

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "doc": None}


def path() -> Path:
    """CELL_EXPECTATIONS_PATH if set, else control_api/data/cell_expectations.json (absent in the public repo)."""
    env = (os.environ.get("CELL_EXPECTATIONS_PATH") or "").strip()
    return Path(env) if env else EXPECTATIONS_PATH


def load_doc(p: Path | str | None = None) -> dict | None:
    """The parsed file (a dict) or None if missing/invalid. Cached by (path, mtime, size); never logs contents."""
    p = Path(p) if p else path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["doc"]
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict):
        doc = None
    with _LOCK:
        _CACHE["key"], _CACHE["doc"] = key, doc
    return doc


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def default_roi() -> float | None:
    doc = load_doc() or {}
    d = doc.get("default") if isinstance(doc.get("default"), dict) else {}
    return _num(d.get("roi"))


def fill_quality_baseline() -> dict:
    """{trade_through_share, avg_move_60m_c}: numbers from the private file, else both null."""
    doc = load_doc() or {}
    b = doc.get("fill_quality_baseline") if isinstance(doc.get("fill_quality_baseline"), dict) else {}
    return {k: _num(b.get(k)) for k in FQ_KEYS}
