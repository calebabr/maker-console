# kalshi_bot (public stand-in)

This folder is a **demo placeholder**. The real trading strategy is private and installed at deploy time; nothing about it is described here.

It has the module names and interface the control API uses, a generic
Kalshi API client, and no trading strategy:

| File | What it is |
|---|---|
| `kalshi_client.py` | Generic Kalshi Trade API v2 client (RSA-PSS signing, retries). Real, shared with the private bot. |
| `config.py` | Kalshi base URLs and env handling. |
| `maker_strategy.py` | Placeholder strategy: validates `maker_config.json`, places **no** orders, makes no network calls. Sets `IS_STANDIN = True`. |
| `maker_config.json` | Example settings. The `cells` are made-up example values, not a strategy. |
| `maker_monitor.py` | Placeholder health check: writes a summary in the expected shape, never changes settings. |
| `arb_scanner.py` | Textbook bracket-arbitrage check (best level only), same interface as the private scanner. |
| `__init__.py` | Exposes `IS_STANDIN` (True unless the overlay marker says False). |

While the stand-in is installed, the control API **refuses LIVE (real-money) mode** with the error
`strategy_not_installed` (maker mode switch, Start in live mode, a live pass, and arbitrage live). Demo mode, the
app, the tests and **Stop** work as normal.

## Overlay contract

The real strategy lives in a private repository. At deploy time `scripts/overlay_private.py` copies the private
files listed in `scripts/overlay_manifest.txt` over this folder:

```
python scripts/overlay_private.py --private /path/to/private-checkout [--target DIR] [--with-tests] [--dry-run]
```

1. Only paths under `kalshi_bot/` and `control_api/data/` can be overlaid; each file is written atomically.
2. The private `maker_strategy.py` must **not** set `IS_STANDIN = True` (the overlay checks this before and after
   copying and fails otherwise).
3. Shadow-mode interface (`docs/SHADOW_CONTRACT.md`): the installed `maker_strategy.py` must define
   `desired_quotes(markets, books, now, max_order_dollars, budget)` (exactly these 5 parameters) and a `Quote`
   class with fields `ticker, side, price, count, book_side, order_price, cell`; the overlay checks this on the
   syntax tree and exits 3 otherwise. The stand-in defines both and `desired_quotes` returns `[]`.
4. Only after every file is copied does the overlay write `kalshi_bot/_overlay.py` with `IS_STANDIN = False`.
   `control_api.services.botfiles.is_standin()` reads both the strategy module and this marker and is fail-closed:
   anything other than "strategy without `IS_STANDIN = True` **and** marker `IS_STANDIN = False`" counts as the
   stand-in.
5. Run it in a build or temporary copy. A git work tree is refused unless `--allow-git-worktree` is given (for a
   throwaway deploy clone); never commit overlaid files (`_overlay.py` and `control_api/data/cell_expectations.json`
   are git-ignored).
6. Secrets never come from the private repository: Kalshi keys stay in environment variables.

`tests/test_standin.py` checks the stand-in before the overlay and the marker after it.
