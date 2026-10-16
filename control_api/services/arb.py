"""Bracket-arbitrage helpers: the arb_scanner import, the settings block (schema, validation, API ceilings),
the kill-switch reason and the opportunity doc. arb_scanner's math is used as is and never changed here.

Settings live in the maker config's "arb" block (stored and versioned with the maker settings; `cells` untouched).
On/off and live are NOT settings: they live in the store's state doc (arb_desired / arb_mode), so one Stop
write stops both strategies.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from . import botfiles

ARB_FIELDS = ("arb_max_contracts", "arb_min_profit", "arb_series", "arb_poll_seconds")
DEFAULT_MAX_CONTRACTS = 5          # small on purpose: a NO-arb set of N brackets costs about (N-1) x contracts $
DEFAULT_MIN_PROFIT = 0.25
DEFAULT_POLL_SECONDS = 15
POLL_MIN, POLL_MAX = 5, 60         # plan: 5-60 s (Kalshi read rate limit, Render's 0.1 CPU)
MIN_PROFIT_MIN, MIN_PROFIT_MAX = 0.01, 100.0
EXPECTATION = ("Bracket arbitrage is rare and small at a small balance. Opportunities last seconds, so a "
               "15-second poll catches only some. The maker strategy is the main earner.")


def scanner():
    """kalshi_bot/arb_scanner.py as a top-level module (kalshi_bot is not a package)."""
    botfiles.ensure_bot_path()
    import arb_scanner
    return arb_scanner


def defaults() -> dict:
    return {"arb_max_contracts": DEFAULT_MAX_CONTRACTS, "arb_min_profit": DEFAULT_MIN_PROFIT,
            "arb_series": list(scanner().DEFAULT_SERIES), "arb_poll_seconds": DEFAULT_POLL_SECONDS}


def current(config: dict | None) -> dict:
    """Effective arb settings: defaults overlaid with the config's "arb" block (unknown keys dropped)."""
    out = defaults()
    block = (config or {}).get("arb")
    if isinstance(block, dict):
        out.update({k: v for k, v in block.items() if k in ARB_FIELDS})
    return out


def schema(settings: Any) -> dict:
    return {
        "arb_max_contracts": {"type": "integer", "unit": "contracts per leg", "min": 1,
                              "max": int(settings.max_arb_contracts),
                              "help": "Largest set size. A NO-arb on N brackets costs about (N-1) x this in $.",
                              "editable": True},
        "arb_min_profit": {"type": "number", "unit": "USD per set after fees", "min": MIN_PROFIT_MIN,
                           "max": MIN_PROFIT_MAX, "help": "Smallest guaranteed profit worth acting on.",
                           "editable": True},
        "arb_series": {"type": "list", "unit": "Kalshi series", "min": 1, "max": len(scanner().DEFAULT_SERIES),
                       "options": list(scanner().DEFAULT_SERIES),
                       "help": "Bracket-event series to scan.", "editable": True},
        "arb_poll_seconds": {"type": "integer", "unit": "seconds", "min": POLL_MIN, "max": POLL_MAX,
                             "help": "How often to scan (keeps under Kalshi's read rate limit).",
                             "editable": True},
    }


def ceilings(settings: Any) -> dict:
    return {"arb_max_contracts": int(settings.max_arb_contracts), "arb_poll_seconds": POLL_MAX}


def _whole(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and int(v) == v


def validate(cur: dict, update: dict, settings: Any) -> dict:
    """Merge a partial arb-settings update into `cur`; raise ValueError([msgs]) on any problem."""
    errs: list[str] = []
    out = dict(cur)
    allowed = set(scanner().DEFAULT_SERIES)
    for k, v in update.items():
        if k not in ARB_FIELDS:
            errs.append(f"{k}: not an arbitrage setting")
            continue
        if k == "arb_max_contracts":
            if not _whole(v):
                errs.append("arb_max_contracts: must be a whole number")
            elif not 1 <= int(v) <= int(settings.max_arb_contracts):
                errs.append(f"arb_max_contracts: contracts per leg, must be 1-{int(settings.max_arb_contracts)} "
                            "(API ceiling)")
            else:
                out[k] = int(v)
        elif k == "arb_min_profit":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                errs.append("arb_min_profit: must be a number")
            elif not MIN_PROFIT_MIN <= float(v) <= MIN_PROFIT_MAX:
                errs.append(f"arb_min_profit: USD per set, must be {MIN_PROFIT_MIN}-{MIN_PROFIT_MAX:g}")
            else:
                out[k] = round(float(v), 4)
        elif k == "arb_poll_seconds":
            if not _whole(v):
                errs.append("arb_poll_seconds: must be a whole number")
            elif not POLL_MIN <= int(v) <= POLL_MAX:
                errs.append(f"arb_poll_seconds: seconds, must be {POLL_MIN}-{POLL_MAX}")
            else:
                out[k] = int(v)
        elif k == "arb_series":
            if not isinstance(v, list) or not v or not all(isinstance(s, str) for s in v):
                errs.append("arb_series: must list at least 1 series")
            elif len(v) != len(set(v)):
                errs.append("arb_series: duplicate entries are not allowed")
            elif not set(v) <= allowed:
                errs.append(f"arb_series: only these series are allowed {sorted(allowed)}")
            else:
                out[k] = list(v)
    if errs:
        raise ValueError(errs)
    return out


def kill_reason(path: Path) -> str | None:
    """None if no arb kill switch; "legging" if arb_scanner (or the runner) wrote it after a partial set or a
    failed leg; "stop" if Stop wrote it; "unknown" if it can't be read (treated like legging by the API)."""
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    if text.startswith("stopped by"):
        return "stop"
    try:
        doc = json.loads(text)
        if isinstance(doc, dict) and "legging" in str(doc.get("reason", "")):
            return "legging"
    except ValueError:
        pass
    return "unknown"


def opportunity_doc(op: Any, executed: str, mode: str) -> dict:
    d = asdict(op) if is_dataclass(op) else dict(op)
    d["legs"] = [list(leg) for leg in d.get("legs") or []]
    d["executed"] = executed
    d["mode"] = mode
    return d
