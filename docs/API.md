# API additions (Sprint 2A-1)

All routes need a normal login; every POST/DELETE below needs fresh 2FA and is audited. Stop is unaffected by any of them.

## Periods and cash flows
| Route | Notes |
|---|---|
| `GET /api/periods` | `{periods:[{id,name,started_at,ended_at,note,current,implicit}], cash_flows:[{id,ts,kind,amount,note,created_by}]}`, oldest first. `p1` is implicit (`started_at` null). |
| `POST /api/periods` `{name (1-60), note? (<=200)}` | Marker starting now. 409 `period_too_soon` if the last marker is under 5 min old. Audit `period.create`. |
| `POST /api/cash-flows` `{kind: deposit or withdrawal, amount (0<x<=100000), ts?, note?}` | `ts` may not be in the future. Audit `cash_flow.create`. |
| `DELETE /api/cash-flows/{id}` | 404 `cash_flow_not_found`. Audit `cash_flow.delete`. |
| `GET /api/periods/{id or current}/summary?env=&source=` | Fills, settled, wins, P&L, money spent, `return_on_settled_cost`, `return_on_all_cost`, open at-risk, and a `cash_flows` block (deposits, withdrawals, equity start/end, `return_on_capital`; never netted into P&L). |
| `GET /api/pnl/by-source?period=&env=` | P&L per source for a period. |

The write routes return the same body as `GET /api/periods` plus `created` or `deleted`. Errors: 400 (validation messages), 409, 422 (unknown period), 503 `store_unavailable`.

## Period filter
`period=<id>|current|all` (default `all`, response unchanged) on `/api/analytics/summary`, `/trades`, `/trades.csv`, `/my-trades`, `/api/activity`, `/api/history`, `/api/pnl/by-source`. Attribution is by fill date. With a period, `days` is replaced by the smallest allowed value reaching the period start. `/api/status` `pnl` and `/api/since` are not period-aware.

## Arbitrage
`GET/PUT /api/arb`: settings gain `arb_scan_cap` (15), `arb_day_cap` (45), `arb_series_cap_frac` (0.35), `arb_demo_execute` (false, schema type `boolean`); `ceilings` carries their maxima; `status.demo_execute`. `pnl` is live only; `pnl_demo_execute` `{d7,d30,sets,demo:true}` is separate. Opportunity `mode` is `live`, `demo-execute` or `log-only`; gate refusals read `skipped: gate <reason>`. Changing any of these needs fresh 2FA.

## Other
- `POST /api/bot/start`: 409 `invalid_config: <reasons>` if the reloaded config fails validation (the bot stays stopped). To stop: `POST /api/bot/stop` or the app's Stop button (no 2FA).
- `GET /api/status`: new `write_report` `{cancel, place, paused, series_capped, utc, mode}` or null; `lease_expires_at` follows each renew.

Example: `curl -X POST "$API/api/periods" -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -d '{"name":"Demo Period 2"}'` (include the fresh-2FA proof your session requires).
