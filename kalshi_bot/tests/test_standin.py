"""Public stand-in contract. Before the overlay: the demo placeholder is harmless and flagged IS_STANDIN.
After scripts/overlay_private.py: the marker says IS_STANDIN = False and the real strategy does not claim True."""
import importlib
import json
import socket
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parents[1]
OVERLAID = (BOT / "_overlay.py").exists()
standin_only = pytest.mark.skipif(OVERLAID, reason="private strategy overlaid")
overlay_only = pytest.mark.skipif(not OVERLAID, reason="public stand-in (no overlay)")


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the stand-in must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)


@standin_only
def test_standin_flag_set():
    import maker_strategy as ms
    assert ms.IS_STANDIN is True
    assert _package().IS_STANDIN is True


def _package():
    """kalshi_bot/__init__.py loaded as a package (the tests run with kalshi_bot/ itself on sys.path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kalshi_bot_pkg_under_test", BOT / "__init__.py",
                                                  submodule_search_locations=[str(BOT)])
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod                     # relative imports (._overlay) need the parent registered
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
        sys.modules.pop(spec.name + "._overlay", None)
    return mod


@standin_only
def test_example_config_validates_and_matches_defaults():
    import maker_strategy as ms
    cfg = ms.load_config(BOT / "maker_config.json")
    assert cfg["cells"] == [tuple(c) for c in ms.DEFAULTS["cells"]]
    raw = json.loads((BOT / "maker_config.json").read_text(encoding="utf-8"))
    assert "EXAMPLE" in raw["_cells"]
    with pytest.raises(ValueError, match="cells"):
        ms.validate_config({**cfg, "cells": [(0.1, 0.2, 0, 1, "yes")]})
    with pytest.raises(ValueError, match="max_order"):
        ms.validate_config({**cfg, "max_order": 0})


@standin_only
def test_main_places_nothing_and_needs_no_network(no_network, capsys, monkeypatch, tmp_path):
    import maker_strategy as ms
    monkeypatch.setattr(ms, "LOG", tmp_path / "journal.csv")
    assert ms.main() == 0
    assert "no quotes placed" in capsys.readouterr().out
    assert not (tmp_path / "journal.csv").exists()


@standin_only
def test_in_cell_uses_example_cells():
    import maker_strategy as ms
    lo, hi, h0, h1, side = ms.DEFAULTS["cells"][0]
    assert ms.in_cell((lo + hi) / 2, (h0 + h1) / 2, side) == tuple(ms.DEFAULTS["cells"][0])
    assert ms.in_cell(0.01, 1000, side) is None


@standin_only
def test_monitor_writes_summary_and_never_applies(no_network, monkeypatch, tmp_path):
    import maker_monitor as mm
    cfg = tmp_path / "maker_config.json"
    cfg.write_text((BOT / "maker_config.json").read_text(encoding="utf-8"), encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    monkeypatch.setattr(mm, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(mm, "JOURNAL", tmp_path / "logs" / "maker_journal.csv")
    monkeypatch.setattr(mm, "CONFIG", cfg)
    assert mm.main(["--apply"]) == 0
    s = json.loads((tmp_path / "logs" / "monitor_summary.json").read_text(encoding="utf-8"))
    assert s["actions"]["pause"] == [] and s["expected_roi"] is None and "per_series" in s
    assert cfg.read_text(encoding="utf-8") == before


@standin_only
def test_arb_textbook_check():
    import arb_scanner as sc
    books = {f"E-B{i}": {"yes_dollars": [["0.50", "10"]], "no_dollars": []} for i in range(3)}
    op = sc.best_opportunity("E", books, 2)
    assert op and op.kind == "NO" and op.contracts == 2 and op.profit > 0 and len(op.legs) == 3
    fair = {f"E-B{i}": {"yes_dollars": [["0.30", "10"]], "no_dollars": [["0.60", "10"]]} for i in range(3)}
    assert sc.best_opportunity("E", fair, 2) is None


@overlay_only
def test_overlay_marker_and_real_strategy():
    ov = importlib.import_module("_overlay")
    assert ov.IS_STANDIN is False and _package().IS_STANDIN is False
    import maker_strategy as ms
    assert getattr(ms, "IS_STANDIN", False) is False
