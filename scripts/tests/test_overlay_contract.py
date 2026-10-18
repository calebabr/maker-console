"""Overlay shadow-mode contract: desired_quotes (5 parameters) and Quote (7 fields). Fake temp trees only."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import overlay_private as op  # noqa: E402

GOOD = '''
from dataclasses import dataclass
@dataclass
class Quote:
    ticker: str
    side: str
    price: float
    count: int
    book_side: str
    order_price: float
    cell: tuple
def desired_quotes(markets, books, now, max_order_dollars, budget):
    return []
'''


def tree(tmp_path: Path, src: str) -> Path:
    d = tmp_path / "kalshi_bot"
    d.mkdir()
    (d / "maker_strategy.py").write_text(src, encoding="utf-8")
    return tmp_path


def code_for(tmp_path, src) -> int:
    try:
        op.check_contract(tree(tmp_path, src))
    except op.OverlayError as e:
        return e.code
    return 0


def test_good_overlay_passes(tmp_path):
    assert code_for(tmp_path, GOOD) == 0


def test_missing_function_fails_with_exit_3(tmp_path):
    assert code_for(tmp_path, GOOD.replace("desired_quotes", "other")) == 3


@pytest.mark.parametrize("sig", ["markets, books, now, budget", "markets, books, now, max_order_dollars, budget, extra",
                                 "markets, books, now, budget, max_order_dollars", "markets, books, now, max_order_dollars, budget, *a"])
def test_wrong_parameters_fail_with_exit_3(tmp_path, sig):
    assert code_for(tmp_path, GOOD.replace("markets, books, now, max_order_dollars, budget", sig)) == 3


@pytest.mark.parametrize("field", ["ticker", "side", "price", "count", "book_side", "order_price", "cell"])
def test_missing_quote_field_fails_with_exit_3(tmp_path, field):
    src = "\n".join(l for l in GOOD.splitlines() if not l.startswith(f"    {field}:"))
    assert code_for(tmp_path, src) == 3


def test_missing_quote_class_fails_with_exit_3(tmp_path):
    assert code_for(tmp_path, GOOD.replace("class Quote", "class Other")) == 3


def test_standin_flag_still_refused(tmp_path):
    assert code_for(tmp_path, GOOD + "\nIS_STANDIN = True\n") == 3


def test_real_standin_in_this_repo_meets_the_contract():
    op.check_shadow_interface(SCRIPTS.parent / "kalshi_bot" / "maker_strategy.py")
