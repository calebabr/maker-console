# Analytics and terminology

What the read-only analytics endpoints return and how the numbers are defined. The code is the reference
(`control_api/services/analytics.py` and `control_api/routes/analytics.py`); this page is the short version.

## Endpoints

All need a signed-in session and no 2FA. They take `days` (7, 30, 90 or 180), `env` (demo or live) and, where it
makes sense, filters for side, kind, status, result, price band, series and source. Bad values return 422.

| Route | Returns |
|---|---|
| `GET /api/analytics/summary` | Totals, the P&L curve with drawdown, results by cell, series, price band and time to close (with confidence intervals), weekly fill quality and the arbitrage summary |
| `GET /api/analytics/trades` | A page of trade rows |
| `GET /api/analytics/my-trades` | The same rows for my own manual trades |
| `GET /api/analytics/trades.csv` | A CSV export, capped in size |

## Definitions

- **Settled fill:** a fill whose market has a settlement result of yes or no. P&L is `count x (win - price) - fee`.
- **Kind:** maker (not a taker fill) or taker.
- **Price band:** the price ranges of the installed strategy's cells, derived from the configuration and never
  hard-coded in this repository; anything else is "other".
- **Hours to close:** market close minus fill time. When the close time is only known from the settlement time, it is an
  upper bound.
- **Win rate interval:** Wilson score, 95%. **Edge** is win rate minus average price.
- **Drawdown:** the cumulative P&L minus its running maximum, with the maximum starting at zero.
- **Arbitrage** is judged per set, not per fill, because a set of hedged legs always has losing legs.

## Terminology and help icons

The plain-English definitions live in one place, `control_app/src/utils/glossary.js`, which exports `GLOSSARY` (an
array of `{id, term, short, body, group, related}` with kebab-case ids) and `getTerm(id)`. A help icon such as
`<HelpIcon term="fill-rate" />` links to `/terminology#fill-rate`, and a test fails if any help icon in `src/` names a
term that is missing from the glossary.
