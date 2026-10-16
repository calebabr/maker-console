# UX sprint interface contract (wave 0, lead-owned)

Single source for names, routes and API fields that the UX sprint agents build against. If a name here is wrong, tell the lead; don't invent a second one. Item numbers refer to the project backlog for that sprint. The project safety rules apply: Stop stays one tap, always visible, no 2FA; no changes to `cells`, quoting, sizing or order code.

## 1. Frontend helpers (owner: FE-Shell; everyone else imports, never redefines)

All in `control_app/src/utils/format.js` (the existing `money`, `pct`, `time`, `ago` stay exported as aliases so nothing breaks):

| Helper | Behaviour |
|---|---|
| `formatMoney(v, {sign=false, compact=false})` | `$12.30`, 2 decimals; `sign` adds `+`/`−` (P&L always passes `sign`); `compact` gives `$1.2k` only where a phone is tight; `null` → `—` |
| `formatPct(v, {sign=true, digits=2})` | rates as `%`, never "pts" in the main view |
| `formatTime(v, {withDate=false})` | device time zone, `9:41 PM`; returns `{text, title}` form via `formatTimeParts` so the tooltip carries exact UTC |
| `formatAgo(v)` / `formatUntil(v)` | "4 min ago" / "in 5 min" |
| `titleCase(s)` | Title Case; small words (a an the and or of by at to in on for) lowercase unless first; ticker codes (`/^KX[A-Z0-9-]+$/`) and tokens with digits unchanged; `Re-Quoting` style hyphen parts capitalised; sentences are NOT passed through it |

Export CSV stays UTC. A test scans rendered labels for lowercase-first words and for raw unformatted money.

## 2. Theme and display preferences (FE-Shell)

- CSS variables on `:root` (`--bg --surface --surface2 --border --text --muted --accent --accent-text --ok --warn --danger --chart2 --overlay`), set from `utils/theme.js`. `data-theme` attribute values: `midnight` (default), `teal`, `graphite`. Hex values: `utils/theme.js`.
- Meanings are fixed in every theme: red = Stop/loss, green = gain/on track, amber = warning.
- localStorage keys (per device, no 2FA, try/catch every access): `keel.theme`, `keel.textSize` (`small|normal|large`, sets root font-size 14/16/18px), `keel.lastVisit` (ISO, written by FE-Overview), `keel.lastSeenVersion`.
- Exports for pages: `useDisplayPrefs()` returning `{theme, setTheme, textSize, setTextSize}` from `context/DisplayContext.jsx`.

## 3. App name, logo, titles

App name **Keel** (`APP_NAME` in `utils/routes.js`); `document.title` = `<Page> · Keel`. `components/KeelLogo.jsx` (`<KeelLogo size={24} />`, SVG from theme variables, geometry in item 28). The retired app name is not left in `src/` (grep test, docs excluded).

## 4. Routes and navigation (FE-Shell owns `App.jsx`, `NavBar.jsx`, `utils/routes.js`)

Bottom tabs (phones): **Overview `/` · Analytics `/analytics` · Activity `/activity` · Markets `/markets` · More `/more`**. `/more` is a menu page listing: Health `/health`, Settings `/settings`, Account `/account` (new, Y3), Alerts `/alerts` (new, item 37), Mode `/mode`, Server `/server`, Logs `/logs`, Arbitrage `/arb`, Terminology `/terminology`.

≥ 1024 px: left sidebar with the same entries instead of bottom tabs. ≥ 768 px: the floating status widget (item 25) may appear. Stop in the header is always visible at every width.

FE-Shell creates **placeholder pages** `pages/More.jsx`, `pages/Account.jsx`, `pages/Alerts.jsx` and their routes in wave 1 so wave 2 agents only fill in files they own. Wave-2 ownership: Account → FE-Settings, Alerts → FE-Settings, More → FE-Shell.

Shared UI pieces FE-Shell exports in wave 1 for wave 2 to reuse: `components/FoldCard.jsx` (folded-by-default card with chevron, `defaultOpen`, `summary` slot), `components/Chip.jsx` (`tone: ok|warn|danger|muted|accent`), `components/LinkButton`-style "Open in Kalshi" helper `utils/kalshi.js` → `kalshiMarketUrl(ticker)`, and `components/Popover.jsx` (used by HelpIcon in FE-Help's item 31; FE-Shell only provides the primitive: focus trap, Esc/outside closes).

## 5. Backend API additions (owner: Backend; all read endpoints behind `current_user`, writes follow settings rules)

**`GET /api/status`** adds (no existing field removed or renamed):
- `pnl_today`: realised P&L since local-midnight UTC day, `{maker: n|null, arb: n|null, you: n|null, total: n|null}`.
- `recent_passes`: last 12 passes, newest last: `[{ts, ok: bool, late: bool, errors: int}]`; `[]` if none.
- `next_run_at`: ISO or null; `last_run_at`: ISO or null.
- Check what `/status` and the runner already return before adding anything.

**`since=` (item 30):** `GET /api/activity?since=<ISO>` and `GET /api/history?since=<ISO>` filter to rows after `since`. New `GET /api/since?ts=<ISO>` returns `{fills, settled, pnl_change, since}` for the Overview card.

**Source and strategy (Y1):**
- Every fill/order row gets `source: "bot"|"you"` and `strategy: "maker"|"arb"|null`. Fills map via `order_id` to the bot's journal/arb log; unknown order ids → `you`. Backfill existing rows. `/activity`, `/history` and analytics expose them. Manual trades are **excluded** from every bot statistic.
- Strategy registry, `control_api/services/strategies.py`: `STRATEGIES = {"maker": {name:"Bot · Maker", color:"var(--accent)", prefix:"mm-"}, "arb": {name:"Bot · Arb", color:"var(--chart2)", prefix:"arb-"}}`; `GET /api/strategies` returns the list plus the `you` entry (`{id:"you", name:"You", ...}`). UI reads names/colours from here; new strategy = one registry entry.
- `GET /api/account/positions` (Y3): open positions and resting orders `[{ticker, title, side, count, price, value, payout_if_wins, source, strategy}]`, read-only, from Kalshi.
- `GET /api/analytics/my-trades` + `GET/PUT /api/trades/{id}/note` (Y5 journal, note ≤ 200 chars, plain text).

**Alerts (37, 38):**
- `GET /api/alerts?type=&limit=&before=` → `[{id, ts, type, title, body}]` newest first (types: `fill, settled, daily, error, stop, breaker, test`). New TTL'd collection `alert_history` (store interface + Mongo + Memory); no secrets, body ≤ 200 chars.
- `GET /api/alerts/settings`, `PUT /api/alerts/settings` (fresh 2FA, validated, atomic): `{daily_summary_time: "HH:MM", mode: "problems"|"every_fill", quiet_hours: {enabled, start, end}}`. Stop/critical always bypass quiet hours.

**Settings undo (40):** `GET /api/config/versions?limit=` (existing store data) and `POST /api/config/undo` (fresh 2FA, re-applies previous version through `validate_config`, never touches `cells`).

**Push (45):** `GET /api/push/public-key`, `POST /api/push/subscribe`, `DELETE /api/push/subscribe` (auth; subscription endpoint+keys stored, never logged or returned), `POST /api/push/test`. VAPID private key from env only. `pywebpush` only after a supply-chain check by Backend (pin hashes in `requirements.txt`). Respect alert settings; log sends to `alert_history`; delete subscriptions on HTTP 404/410. Critical alerts keep going to ntfy.

**Changelog (44):** `GET /api/changelog` → `[{version, date, items:[str]}]` from `control_api/data/changelog.json`.

**Toasts (39):** Stop returns `{stopped_at, orders_cancelled}`; settings PUT returns `changes: [{key, from, to}]`. Add these keys without removing any existing ones.

Ceilings, auth dependencies and rate limits follow the existing patterns.

## 6. Mock data

`control_app/src/services/mock.js` / `mockAnalytics.js` gain: `source`/`strategy` on fills, some manual trades (Fed, NBA, etc.), `/status` new fields, alerts, positions, strategies, since, changelog. Owner of each mock endpoint = the page agent that consumes it; FE-Shell owns `/status`, `/strategies` and `/changelog` mocks.

## 7. Rules for wave 2 (pages)

- Title Case (item 21) via `titleCase()` on your own pages only.
- Folded rows use `FoldCard`; chips use `Chip`; money/time only via the helpers.
- Don't edit another agent's files; need a shared change → ask the lead.
- Write a short report (APIs used, gaps, test counts) and propose commit splits. Agents never commit.
- Run quick tests while building; do not run the full suite (tests agent does).
