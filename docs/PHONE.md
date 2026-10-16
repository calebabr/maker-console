# Using the app on your phone

## Install
- **iPhone (Safari):** open the app URL > Share > **Add to Home Screen** > Add.
- **Android (Chrome):** open the URL > menu (three dots) > **Install app** / Add to Home screen.

## Logging in
Username and password, then the 6-digit code from your authenticator app.

**2FA codes work once.** Login uses the current code. The first protected action afterwards (marked with a lock) needs the NEXT code, so wait up to 30 seconds. After 5 wrong codes the account locks for 15 minutes.

## Stop (always available)
The red **Stop** button is in the header on every page. One tap plus one confirmation, no 2FA. It stops the maker **and arbitrage**, cancels resting bot orders and blocks new ones. It works even when the database is down. If the app will not load, see "Emergency stop without the app" in `docs/DEPLOY.md`.

## Pages
- **Overview:** status badge, cash/exposure/headroom, an Arbitrage row, P&L (7 and 30 days) vs expected, a **Fill quality** block (your trade-through share and 60-minute move next to the tape baseline), **Stop**, **Start** (2FA) and **Run once** (one pass now; refused with `too_soon` if the last pass was under 60 s ago).
- **Markets:** each series with its results, a verdict (`pause` / `keep`) and fill quality; pause or resume a market (2FA). The strategy cells are read-only.
- **Activity:** a 7/30-day P&L strip, then recent orders and fills.
- **Health:** latest weekly health-check report; button to run it now.
- **Settings:** budget, order size, max open fraction, poll minutes (2FA to save; limits are enforced).
- **Mode:** demo or live. Live needs a 2FA code and typing `LIVE`. **After any server restart the bot comes back in demo**; you get an urgent alert and must re-arm Live here.
- **Server:** keep-awake and pinger intervals (1-14 min), cron job status, **Sync now** (2FA). A **Sessions** card has **Log out all sessions** (2FA): ends the login on every device, including this one. Use it if a phone is lost.
- **Arbitrage** (More menu): bracket arbitrage, a secondary strategy. States: **Off**, **Log only** (default; records opportunities, sends no orders) and **Live**.
  - **Turn on / Turn off** (2FA). **Go live** needs typing `LIVE` (exact capitals), then a fresh code; it is disabled unless the server allows live. **Log only** from live needs a code but no typing.
  - If a set was cut short (a partial fill, "legging"), the kill switch trips and arbitrage turns itself off. Turning it on again asks you to acknowledge the one-sided position first.
  - Shows scanner stats, arb P&L (a dash until a set has filled), the honest expectation text, recent opportunities, and settings (max contracts, minimum profit, series, poll seconds; 2FA to save).
  - The red **Stop** works here too and flips arbitrage to Off ("Kill switch set by Stop").
- **Analytics** (More menu): read-only. It never places orders and never changes settings; only the red **Stop** works here as everywhere. Pick a window (7, 30, 90 or 180 days) and an account (current mode, or demo + live). From the top:
  - **Is the edge holding?** One sentence, then each strategy "cell" (a price and time-to-close window) marked **On track**, **Too early** (fewer than 20 markets so far), **Below expectations** or **Above expectations**. "Expectations" come from the optional private benchmark file (none: no comparison); a cell is only called below or above when the whole uncertainty range is on one side.
  - **Totals:** profit or loss, return on money spent (ROI), settled trades, win rate, worst drop from a peak (max drawdown), trades still open.
  - **Profit chart:** running total with a strip below showing how far under the previous best you are. Drag to see a day; switch All, Maker or Taker.
  - **Edge breakdown** by Cell, Series, Price or Time left. Each row shows the win rate with a bar for the range it could really be in, and a tick for the average price paid (break-even). Green bar fully right of the tick means winning more than the price implies; red means less; grey means unclear. The bar groups bets on the same market together, because they win or lose together, so it is wider (more honest) than counting each bet separately. Rows with few trades are dimmed.
  - **Calibration:** at each price, how often did we really win? Dots above the dashed fair-price line are edge.
  - **Fill rate:** how many of our bids got filled, how long they rested, and how many were missed.
  - **Fill quality and queue** by week, **Arbitrage history**.
  - **Trade history** with filters (series, side, maker/taker, settled/open, price band, cell, win/loss), **Load more**, and **Download CSV** (current filters, up to 5,000 rows; opens in Excel or Sheets).
  - Messages: "Too many requests - try again in N s" means wait a moment; the database-down banner means Atlas is unreachable (Stop still works). If a page crashes you see "This page could not be shown" with Reload and Try again; the header Stop button and bottom bar still work.
- **Terminology** (More menu): plain-English meanings of every term in the app (fill, quote, spread, edge, ROI, drawdown, confidence interval, queue position, and more), grouped, with a search box. While you type, the results become one list ranked by relevance: a term whose name (or nickname, such as "profit" or "pnl" for P&L) matches comes before one that only mentions your word in its explanation. Clear the box to get the groups back. The filter box stays pinned under the page title. Small **(?)** icons next to terms on every page open the matching entry; the back button returns you.
- **Page title and charts (all pages):** the page name shows in a bar under the top header. Scroll down and it stays while you scroll, fades after a couple of seconds without scrolling, and returns as soon as you scroll again; the red Stop in the header is never covered. The browser tab says "<Page> · Keel". Every chart labels its axes with units: dollars on the balance and profit charts, dates along the bottom, cents paid and win rate % on calibration, weeks on fill quality, counts per day on arbitrage, and a percent scale on the range bars.
- **Logs / Audit:** recent server log lines and who did what.

**Fill quality** compares your fills with the market tape: the share of fills where the price later traded through your level within 60 minutes and the 60-minute price move, next to the market baseline when the private benchmark file supplies one. "Worse than the tape" appears only after 50 scored fills. It is the quickest way to learn whether the strategy works for your orders. Full feature list: `docs/FEATURES.md`.

**P&L** shows realised maker P&L for 7 and 30 days (from settled fills). A dash with "No settled fills yet" means no data, not zero.

**Database down:** a banner "Server can't reach the database. Stop still works. Showing the last data received." means Atlas is paused or unreachable. Everything except Stop is unavailable until it is back.

A banner "Waking up the server" means the free server is starting; wait a minute. Offline, the app shows the last data it saw.

Before Start or Live, remember: Stop is the red button on every page.

If you get "session expired" at login, all sessions were logged out; just log in again.

## Push notifications on iPhone (step by step)
Push only works from the Home Screen app, not from a normal browser tab. iOS 16.4 or newer is needed.
1. Open the app URL in Safari (or Chrome) on the iPhone.
2. Tap the **Share** button (square with an arrow), scroll down, tap **Add to Home Screen**, then **Add**.
3. Close the browser. Open the app from the new Keel icon on your Home Screen and log in.
4. Tap **More** > **Alerts** (or **Settings** > Notifications) and tap **Enable notifications**.
5. iOS asks "Allow notifications?". Tap **Allow**. If you tapped Don't Allow by mistake: iPhone Settings > Notifications > Keel > Allow Notifications, then try again.
6. Tap **Send test**. A notification should arrive within seconds.

**Push Devices card:** More > **Settings**, directly under the Notifications card. It lists every phone or browser registered for alerts (push service name and when it was added). **Clear All** asks you to confirm, then removes them all (login only, no 2FA). Re-enable on each phone afterwards. Use it after a lost phone or when re-testing.

Stop is the red button on every page; last resort, suspend the service in Render, or `stop_maker.ps1` on the desktop.

## Update 2026-10-15 (Keel)
- **Install for notifications:** push only works from the Home Screen app. iPhone: Share > **Add to Home Screen** (Chrome or Safari), open from the icon, then More > Alerts > **Enable notifications** > **Send test**. Android: Chrome menu > Install app, then the same.
- **Tabs:** Overview, Analytics, Activity, Markets, More. Overview shows the headline, Stop, today's P&L, recent runs and what changed since your last visit. Activity is grouped by day with Bot or You chips.
- **Settings > Display:** theme (three dark) and text size. **Settings:** saving shows what changed; **Undo Last Change** reverts it (needs a 2FA code).
- **Alerts:** history, daily summary time, every fill or problems only, quiet hours (Stop and breaker alerts always come through).
- **Stop** is on the status card on every state; last resort, suspend the service in Render, or `stop_maker.ps1` on the desktop.
