"""Synthetic cells for the analytics tests. Made up for the test arithmetic; NOT a strategy and not the installed
config (tests that need the installed cells read botfiles.strategy().DEFAULTS["cells"] instead)."""
TEST_CELLS = [[0.34, 0.38, 5, 11, "no"], [0.38, 0.45, 5, 11, "no"], [0.45, 0.49, 0, 3, "yes"], [0.30, 0.34, 3, 5, "yes"]]


def mid_price(cell) -> float:
    """A cent price strictly inside the cell's price range."""
    lo, hi = float(cell[0]), float(cell[1])
    return round((lo + hi) / 2, 2) if hi - lo >= 0.02 else round(lo, 2)


def mid_hours(cell) -> float:
    return (float(cell[2]) + float(cell[3])) / 2

