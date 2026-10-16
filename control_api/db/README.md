# control_api/db: storage layer

Atlas (MongoDB M0, 512 MB, no backups) is the **source of truth**; Render's disk is wiped on every restart.
Database name: `kalshi_control` (`MONGODB_DB`). The URI comes only from `settings.mongodb_uri` (`MONGODB_URI`).

| File | What |
|---|---|
| `__init__.py` | `get_store(settings)` returns `MongoStore` when `settings.store == "mongo"`, otherwise `MemoryStore`. It never connects. |
| `store.py` | Abstract async `Store` (the contract in `DASHBOARD_PLAN.md`) and `MemoryStore` (in-process, for dev and tests) |
| `mongo.py` | `MongoStore(uri, db_name)` built on `pymongo.AsyncMongoClient(tz_aware=True, serverSelectionTimeoutMS=5000)` |
| `schemas.py` | Collection names, document shapes, defaults, `row_hash(row)`, normalisation helpers that both stores share |
| `seed.py` | `await seed(store)` writes realistic fake data. `python -m control_api.db.seed` prints the counts. |

## Conventions (both stores)

- Every method is `async`.
- Results are fresh plain dicts. They **never include `_id`** and use **tz-aware UTC datetimes** with millisecond precision, which is what BSON keeps. Naive datetimes passed in are treated as UTC.
- `list_*` methods return the **newest first**: by `ts`, or by `version` for config versions. For a chart, reverse the list.
- `since` is inclusive (`ts >= since`).
- If a doc has no `ts`, it gets the current time. Monitor summaries fall back to `generated_utc`, and fills fall back to `created_time`. `ts` may be a datetime, an ISO string or epoch seconds.
- `save_user` and `save_cloud_settings` **merge** fields (`$set`). They refuse secret-looking keys such as `password`, `totp_secret`, `api_key`, `private_key`, `jwt_secret`, `cronjob_api_key`, `mongodb_uri` and `tick_token`.
- `ping()` never raises. `ensure_indexes()` is idempotent.
- `MemoryStore` doesn't apply the 180-day TTL to history. It does honour revoked-token expiry.

## Collections

| Collection | Document | Indexes / retention |
|---|---|---|
| `state` | `_id:"bot"`: `desired` (`running`\|`stopped`), `mode` (`demo`\|`live`), `arb_desired` (`running`\|`stopped`), `arb_mode` (`demo`\|`live`), `updated_utc`, `updated_by`. Defaults on first write: stopped, demo, stopped, demo. `get_state` fills in defaults for fields an older doc lacks. A single `set_state(desired="stopped", arb_desired="stopped")` stops both strategies in one atomic write. | none |
| `cloud_settings` | `_id:"cloud"`: `keepalive_minutes, pinger_minutes, pinger_enabled, silent_alert_minutes, cronjob_job_id, pinger_sync {ok, ts, error}, updated_utc, updated_by`. Defaults are in `schemas.DEFAULT_CLOUD_SETTINGS`. The store does not merge them in on read. | none |
| `config_versions` | `{version, ts, by, before, after}`. The current config is the `after` with the highest version. | unique `version` |
| `heartbeats` | `{ts, ok, error?, duration_s, mode, desired, quotes}` | TTL 180 d on `ts` |
| `journal_rows` | The CSV fields `utc, event, ticker, side, price, count, cost, cell, note` (stored as given, normally strings), plus `row_hash` and `ts`, which is parsed from `utc` | unique `row_hash`; TTL 180 d on `ts` (also serves `ts` desc sorts); `(event, ts desc)` |
| `account_snapshots` | `{ts, cash, exposure, headroom}` | TTL 180 d on `ts` |
| `fills` | The Kalshi fill as given, plus `fill_id` (str) and `ts` | unique `fill_id`; `ts` desc. `list_fills(since, until)` gives the d7/d30 P&L windows (`until` exclusive). |
| `settlements` | Kalshi `/portfolio/settlements` row as given (`market_result`, `yes/no_count`, `yes/no_total_cost`, `revenue` in cents, ...), plus `ticker` and `ts`, which comes from `settled_time` | unique `ticker`; `ts` desc |
| `arb_opportunities` | `{ts (first seen), last_seen, seen_count, event, kind NO\|YES, contracts, profit, cost, sum_best, legs, executed, mode?, sig}`. A log-only sighting with the same `sig` (event, kind, contracts, profit, cost, legs) within 120 s of the last one (`ARB_DEDUPE_SECONDS`) is folded into that doc instead of inserted. Executed ones are always inserted. | TTL **45 d** on `ts` (`ARB_RETENTION_DAYS`); `(sig, last_seen desc)` |
| `monitor_summaries` | The `maker_monitor` summary, plus `ts` | `ts` desc |
| `audit_log` | `{ts, user, action, ok, details, ip, ua}` | `ts` desc (no TTL) |
| `users` | `{username, password_hash, totp_secret_enc, failed_logins, locked_until, last_totp_step}` | unique `username` |
| `revoked_tokens` | `{jti, exp}` | unique `jti`; TTL on `exp` (`expireAfterSeconds=0`). `is_revoked` also checks `exp > now`, because the TTL monitor runs only about once a minute. |
| `runner_lease` | Single doc `_id:"leader"`: `holder, version, generation, expires_at, acquired_at, handover`. Changed only by filtered `find_one_and_update` (renew if holder matches; take over if expired with `$inc generation`) or a unique-`_id` insert, never read-modify-write. `generation` rises only when the holder changes or the lease is created. | none |
| `handover_nonces` | `{nonce, ts}`: a consumed signed-handover nonce (replay guard). `consume_handover` is one insert. | unique `nonce`; TTL 1 h on `ts` |
| `meta` | Single doc `_id:"schema"`: `version` (int, `SCHEMA_VERSION = 1`), `updated_utc`. | none |
| `releases` | `{id, version, commit, summary, ci, status pending\|approved\|rejected, created_at, expires_at (+30 min), by?, at?}`. `resolve_pending_release` is one filtered `find_one_and_update` (pending and unexpired only). | unique `id`; TTL 30 d on `created_at` |

`row_hash(row)` is the sha1 of the 9 CSV fields: each value is passed through `str()`, a missing field becomes `''`, and the values are joined with `\x1f` in CSV column order. A row read twice from the CSV always hashes the same, so `add_journal_rows` is idempotent and returns only the count of new rows. `upsert_fills` returns the count of **new** fills; fills that already exist are updated in place.

## Storage estimate (Atlas M0 = 512 MB)

These numbers are uncompressed and include indexes. WiredTiger compression makes the real numbers smaller.

| Collection | Size per doc | Rate (poll 10 min) | Over 180 d |
|---|---|---|---|
| journal_rows | ~450 B | ~5 rows/pass in live/demo mode (new orders, cancels, account) → 720/day | ~60 MB |
| heartbeats | ~200 B | 144/day | ~5 MB |
| account_snapshots | ~150 B | 144/day | ~4 MB |
| fills | ~400 B | tens/day | <3 MB |
| settlements | ~300 B | ~10/day | <1 MB |
| arb_opportunities | ~700 B | a few/day (sightings are collapsed, so a stale book that is re-seen every 15 s keeps one doc) | <2 MB; worst case, if the book changes every poll: 5,760/day, which is 4 MB/day, about 180 MB at the 45-day TTL |
| audit_log | ~300 B | <100/day (no TTL) | ~5 MB/yr |
| config_versions, monitor_summaries, others | KB | rare | <1 MB |

**Typical total: about 85 MB.** Worst case is `poll_minutes=5` with about 15 rows per pass, which comes to roughly 350 MB for the journal. That is still under 512 MB, but if `db.stats()` gets past about 300 MB, shorten `RETENTION_DAYS` in `schemas.py`, then drop and recreate the `ts_ttl` index (or use `collMod`).
