"""kalshi_bot: PUBLIC STAND-IN for the private trading bot.

This directory ships a demo placeholder strategy with the same module names and interface the control API uses
(maker_strategy, maker_monitor, arb_scanner, config, kalshi_client). It holds no trading edge. The real strategy
is private and is overlaid at deploy time by scripts/overlay_private.py, which also writes _overlay.py with
IS_STANDIN = False. While IS_STANDIN is True the control API refuses LIVE (real-money) mode.

The control API imports these files as top-level modules (it puts this directory on sys.path), so this
__init__ only exists to expose IS_STANDIN to package-style imports and tools.
"""
try:  # written by scripts/overlay_private.py after the private files are copied in
    from ._overlay import IS_STANDIN  # type: ignore[import-not-found]
except ImportError:
    IS_STANDIN = True

__all__ = ["IS_STANDIN"]
