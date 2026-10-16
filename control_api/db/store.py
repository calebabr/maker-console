"""Async storage interface (DASHBOARD_PLAN.md "Interface contract -> Store") and the in-process MemoryStore.

Conventions (both implementations):
  * every method is async;
  * returned dicts are fresh copies, never contain `_id`, and hold tz-aware UTC datetimes (ms precision);
  * list_* methods return newest first (by `ts`, or `version` for config versions);
  * `since` filters are inclusive (ts >= since);
  * retention TTLs (180 d) are enforced by Atlas only; MemoryStore does not expire history
    (it does honour token-revocation expiry, since that affects behaviour).
"""
from __future__ import annotations

import abc
import asyncio
import copy
from datetime import datetime, timedelta
from typing import Any, Iterable

from . import schemas as S


class Store(abc.ABC):
    # ---- health
    @abc.abstractmethod
    async def ping(self) -> bool:
        """True if the backing database answers. Never raises."""

    @abc.abstractmethod
    async def ensure_indexes(self) -> None:
        """Create indexes/TTLs. Idempotent."""

    @abc.abstractmethod
    async def close(self) -> None: ...

    # ---- state
    @abc.abstractmethod
    async def get_state(self) -> dict | None:
        """{desired, mode, arb_desired, arb_mode, updated_utc, updated_by} or None if never set.
        Fields missing from an older stored doc are filled from schemas.DEFAULT_STATE."""

    @abc.abstractmethod
    async def set_state(self, *, by: str, desired: str | None = None, mode: str | None = None,
                        arb_desired: str | None = None, arb_mode: str | None = None) -> dict:
        """Update any of desired/mode (maker) and arb_desired/arb_mode (bracket arbitrage) in ONE atomic
        write (unspecified fields keep their value; defaults stopped/demo). Stop passes
        desired="stopped", arb_desired="stopped" together. Raises ValueError on an invalid value."""

    # ---- config
    @abc.abstractmethod
    async def get_config(self) -> dict | None:
        """`after` of the highest config version, or None."""

    @abc.abstractmethod
    async def save_config(self, after: dict, *, before: dict | None, by: str) -> int:
        """Append a config version; returns its number (1, 2, ...)."""

    @abc.abstractmethod
    async def list_config_versions(self, limit: int = 20) -> list[dict]:
        """[{version, ts, by, before, after}] newest first."""

    # ---- heartbeats
    @abc.abstractmethod
    async def add_heartbeat(self, doc: dict) -> None: ...

    @abc.abstractmethod
    async def last_heartbeat(self) -> dict | None: ...

    @abc.abstractmethod
    async def list_heartbeats(self, *, since: datetime | None = None, limit: int = 1000) -> list[dict]:
        """Heartbeats with ts >= since, newest first."""

    # ---- journal
    @abc.abstractmethod
    async def add_journal_rows(self, rows: Iterable[dict]) -> int:
        """Insert journal CSV rows; idempotent on row_hash. Returns the number newly inserted."""

    @abc.abstractmethod
    async def list_journal(self, *, since: datetime | None = None, limit: int = 200,
                           events: Iterable[str] | None = None, fields: Iterable[str] | None = None) -> list[dict]:
        """Journal rows with ts >= since (optionally only these events), newest first.
        `fields`: return only these keys (projection)."""

    # ---- account snapshots
    @abc.abstractmethod
    async def add_account_snapshot(self, doc: dict) -> None: ...

    @abc.abstractmethod
    async def list_account_snapshots(self, *, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        """Newest first (reverse for a chart)."""

    # ---- fills
    @abc.abstractmethod
    async def upsert_fills(self, fills: Iterable[dict]) -> int:
        """Upsert on fill_id (existing fills are overwritten field-by-field). Returns the number NEW."""

    @abc.abstractmethod
    async def set_fill_fields(self, fill_id: str, fields: dict) -> bool:
        """Merge extra fields (e.g. fill_quality) into ONE existing fill. No upsert; fill_id and ts never change.
        Returns True if the fill exists."""

    @abc.abstractmethod
    async def list_fills(self, *, since: datetime | None = None, until: datetime | None = None,
                         limit: int = 500, fields: Iterable[str] | None = None,
                         fill_ids: Iterable[str] | None = None) -> list[dict]:
        """Fills with since <= ts < until, newest first. For P&L windows pass a large limit.
        `fields`: return only these keys (projection). `fill_ids`: only these fills (fill_id $in)."""

    # ---- settlements (Kalshi /portfolio/settlements; realised P&L of held-to-settlement positions)
    @abc.abstractmethod
    async def upsert_settlements(self, rows: Iterable[dict]) -> int:
        """Upsert on ticker (fields merged). ts from `ts`, else `settled_time`. Returns the number NEW."""

    @abc.abstractmethod
    async def list_settlements(self, *, since: datetime | None = None, until: datetime | None = None,
                               limit: int = 500, fields: Iterable[str] | None = None) -> list[dict]:
        """Settlements with since <= ts < until, newest first. `fields`: return only these keys (projection)."""

    # ---- bracket-arbitrage opportunity log
    @abc.abstractmethod
    async def add_arb_opportunity(self, doc: Any) -> bool:
        """Record one opportunity (dict or arb_scanner.Opportunity). A log-only sighting identical
        (schemas.arb_signature) to one whose last_seen is within ARB_DEDUPE_SECONDS before this ts is
        collapsed into it (seen_count += 1, last_seen = ts) and False is returned. Executed
        opportunities are always inserted. Returns True when a new doc was inserted."""

    @abc.abstractmethod
    async def list_arb_opportunities(self, *, since: datetime | None = None, limit: int = 100,
                                     executed_only: bool = False) -> list[dict]:
        """Newest first by ts (first seen). executed_only drops 'log-only' docs."""

    # ---- queue-position telemetry (one doc per maker order: a "quote episode")
    @abc.abstractmethod
    async def upsert_quote_episodes(self, docs: Iterable[dict]) -> int:
        """Atomic per-doc upsert on order_id. Fields in schemas.QUOTE_EPISODE_IMMUTABLE are written only when
        the episode is created; every other given field is merged ($set). Returns the number NEW."""

    @abc.abstractmethod
    async def list_quote_episodes(self, *, since: datetime | None = None, env: str | None = None,
                                  status: str | None = None, limit: int = 5000,
                                  fields: Iterable[str] | None = None) -> list[dict]:
        """Episodes with ts >= since (optionally one env / status), newest first by ts (join time).
        `fields`: return only these keys (a projection; keeps the per-pass aggregate small in memory)."""

    # ---- market close time (services/closetime.py). Not abstract, so older test fakes keep working.
    async def set_quote_episode_close_time(self, order_id: str, close_time: datetime) -> bool:
        """Set close_time on ONE existing episode only while it has none (close_time is immutable once set).
        No upsert. Returns True if this call wrote it."""
        raise NotImplementedError

    async def set_settlement_fields(self, ticker: str, fields: dict) -> bool:
        """Merge extra fields (e.g. close_time) into ONE existing settlement. No upsert; ticker and ts never
        change. Returns True if the settlement exists."""
        raise NotImplementedError

    # ---- UX sprint additions. Not abstract, so older test fakes keep working.
    async def set_fills_fields(self, updates: dict[str, dict]) -> int:
        """Bulk set_fill_fields: {fill_id: fields}. No upsert; fill_id/ts never change. Returns the number matched."""
        n = 0
        for fid, fields in dict(updates).items():
            n += bool(await self.set_fill_fields(fid, fields))
        return n

    async def add_alert(self, doc: dict) -> dict:
        """Append one alert_history doc (schemas.prepare_alert); returns it."""
        raise NotImplementedError

    async def list_alerts(self, *, type: str | None = None, limit: int = 50,
                          before: datetime | None = None) -> list[dict]:
        """Alerts newest first, optionally one type and only ts < before (paging)."""
        raise NotImplementedError

    async def save_push_subscription(self, doc: dict) -> bool:
        """Upsert on sub_id (= sha256(endpoint)). Returns True if new. Raises ValueError when the
        PUSH_SUBSCRIPTIONS_MAX cap would be exceeded by a new one."""
        raise NotImplementedError

    async def list_push_subscriptions(self) -> list[dict]:
        """Every subscription (sensitive: callers never log or return endpoint/keys)."""
        raise NotImplementedError

    async def delete_push_subscription(self, sub_id: str) -> bool:
        raise NotImplementedError

    async def clear_push_subscriptions(self) -> int:
        """Delete every subscription (logout-all, owner 'clear all'). Returns how many were removed."""
        raise NotImplementedError

    # ---- monitor
    @abc.abstractmethod
    async def add_monitor_summary(self, doc: dict) -> None: ...

    @abc.abstractmethod
    async def last_monitor_summary(self) -> dict | None: ...

    async def list_monitor_summaries(self, *, since: datetime | None = None, limit: int = 100) -> list[dict]:
        """Monitor summaries with ts >= since, newest first. Default (for minimal subclasses): the last one only."""
        last = await self.last_monitor_summary()
        return [last] if last is not None and limit > 0 and _since_ok(last, since) else []

    # ---- audit
    @abc.abstractmethod
    async def add_audit(self, doc: dict) -> None: ...

    @abc.abstractmethod
    async def list_audit(self, limit: int = 100) -> list[dict]: ...

    # ---- users
    @abc.abstractmethod
    async def get_user(self, username: str) -> dict | None: ...

    @abc.abstractmethod
    async def save_user(self, doc: dict) -> dict:
        """Upsert on doc['username'], merging the given fields into any existing user. Returns the user.

        ``sessions_valid_after`` / ``last_totp_step`` (non-None) are applied as a max: they never
        move backwards (S-18)."""

    @abc.abstractmethod
    async def update_user_fields(self, username: str, *, set: dict | None = None,
                                 max: dict | None = None, inc: dict | None = None) -> dict | None:
        """Atomic field-level update of an EXISTING user (no upsert); returns the updated user or
        None if it doesn't exist. ``set`` overwrites, ``max`` only raises, ``inc`` adds. Monotonic
        fields passed in ``set`` are treated as ``max``. Use this instead of writing back a
        previously read doc (lost updates, S-18)."""

    # ---- token revocation
    @abc.abstractmethod
    async def revoke_jti(self, jti: str, exp: datetime | int | float) -> None:
        """Remember a revoked JWT id until `exp` (datetime or epoch seconds, as in the JWT `exp` claim)."""

    @abc.abstractmethod
    async def is_revoked(self, jti: str) -> bool: ...

    # ---- cloud settings
    @abc.abstractmethod
    async def get_cloud_settings(self) -> dict | None:
        """Stored cloud_settings doc, or None if never saved (callers merge schemas.DEFAULT_CLOUD_SETTINGS)."""

    @abc.abstractmethod
    async def save_cloud_settings(self, doc: dict, *, by: str) -> dict:
        """Merge the given top-level fields into the stored doc (e.g. {"pinger_minutes": 7} or
        {"cronjob_job_id": 123, "pinger_sync": {...}}); sets updated_utc/updated_by. Returns the full doc."""


# ------------------------------------------------------------------ shared validation
def _validate_state(desired: str | None, mode: str | None,
                    arb_desired: str | None = None, arb_mode: str | None = None) -> dict:
    """Validate and return the fields to set (only the non-None ones)."""
    upd = {}
    for name, val, allowed in (("desired", desired, S.DESIRED_VALUES), ("mode", mode, S.MODE_VALUES),
                               ("arb_desired", arb_desired, S.DESIRED_VALUES),
                               ("arb_mode", arb_mode, S.MODE_VALUES)):
        if val is not None:
            if val not in allowed:
                raise ValueError(f"{name} must be one of {allowed}")
            upd[name] = val
    return upd


def _state_out(doc: dict | None) -> dict | None:
    """Stored state doc -> contract shape (defaults filled for fields an older doc lacks)."""
    if doc is None:
        return None
    out = {**S.DEFAULT_STATE, **S.strip_id(doc)}
    return {k: copy.deepcopy(out.get(k)) for k in S.STATE_FIELDS}


def _window_ok(doc: dict, since: datetime | None, until: datetime | None) -> bool:
    return _since_ok(doc, since) and (until is None or doc["ts"] < S.norm_dt(until))


def _since_ok(doc: dict, since: datetime | None) -> bool:
    return since is None or doc["ts"] >= S.norm_dt(since)


def _project(docs: list[dict], fields: Iterable[str] | None) -> list[dict]:
    """Keep only `fields` (like a Mongo inclusion projection without _id); None = everything."""
    if fields is None:
        return docs
    keep = {str(f) for f in fields}
    return [{k: v for k, v in d.items() if k in keep} for d in docs]


def _newest(docs: list[dict], key: str = "ts") -> list[dict]:
    return sorted(docs, key=lambda d: d[key], reverse=True)


class MemoryStore(Store):
    """In-process store for dev and tests. Same semantics as MongoStore."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: dict | None = None
        self._cloud: dict | None = None
        self._config_versions: list[dict] = []
        self._heartbeats: list[dict] = []
        self._journal: dict[str, dict] = {}          # row_hash -> doc
        self._snapshots: list[dict] = []
        self._fills: dict[str, dict] = {}            # fill_id -> doc
        self._monitor: list[dict] = []
        self._audit: list[dict] = []
        self._users: dict[str, dict] = {}
        self._revoked: dict[str, datetime] = {}      # jti -> exp
        self._settlements: dict[str, dict] = {}      # ticker -> doc
        self._arb: list[dict] = []
        self._episodes: dict[str, dict] = {}         # order_id -> doc
        self._alerts: list[dict] = []
        self._push: dict[str, dict] = {}             # sub_id -> doc
        self._seq = 0                               # insertion order tiebreak for equal ts

    @staticmethod
    def _out(doc: dict | None) -> dict | None:
        return None if doc is None else copy.deepcopy(S.strip_id(doc))

    def _stamp(self, doc: dict) -> dict:
        self._seq += 1
        doc["\x00seq"] = self._seq
        return doc

    @staticmethod
    def _clean(doc: dict) -> dict:
        d = copy.deepcopy(doc)
        d.pop("\x00seq", None)
        d.pop("_id", None)
        return d

    @staticmethod
    def _newest_seq(docs: Iterable[dict], key: str = "ts") -> list[dict]:
        return sorted(docs, key=lambda d: (d[key], d.get("\x00seq", 0)), reverse=True)

    # ---- health
    async def ping(self) -> bool:
        return True

    async def ensure_indexes(self) -> None:
        return None

    async def close(self) -> None:
        return None

    # ---- state
    async def get_state(self) -> dict | None:
        return _state_out(self._state)

    async def set_state(self, *, by: str, desired: str | None = None, mode: str | None = None,
                        arb_desired: str | None = None, arb_mode: str | None = None) -> dict:
        upd = _validate_state(desired, mode, arb_desired, arb_mode)
        async with self._lock:
            cur = {**S.DEFAULT_STATE, **(self._state or {})}
            cur.update(upd)
            cur["updated_utc"] = S.utcnow()
            cur["updated_by"] = by
            self._state = {k: cur[k] for k in S.STATE_FIELDS}
            return _state_out(self._state)

    # ---- config
    async def get_config(self) -> dict | None:
        if not self._config_versions:
            return None
        latest = max(self._config_versions, key=lambda d: d["version"])
        return copy.deepcopy(latest["after"])

    async def save_config(self, after: dict, *, before: dict | None, by: str) -> int:
        async with self._lock:
            version = max((d["version"] for d in self._config_versions), default=0) + 1
            self._config_versions.append({"version": version, "ts": S.utcnow(), "by": by,
                                          "before": S.normalize(before) if before is not None else None,
                                          "after": S.normalize(after)})
            return version

    async def list_config_versions(self, limit: int = 20) -> list[dict]:
        docs = _newest(self._config_versions, "version")[:max(0, int(limit))]
        return [self._out(d) for d in docs]

    # ---- heartbeats
    async def add_heartbeat(self, doc: dict) -> None:
        self._heartbeats.append(self._stamp(S.prepare_ts_doc(doc)))

    async def last_heartbeat(self) -> dict | None:
        if not self._heartbeats:
            return None
        return self._clean(self._newest_seq(self._heartbeats)[0])

    async def list_heartbeats(self, *, since: datetime | None = None, limit: int = 1000) -> list[dict]:
        docs = [d for d in self._heartbeats if _since_ok(d, since)]
        return [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]

    # ---- journal
    async def add_journal_rows(self, rows: Iterable[dict]) -> int:
        n = 0
        async with self._lock:
            for row in rows:
                doc = S.prepare_journal_row(row)
                if doc["row_hash"] in self._journal:
                    continue
                self._journal[doc["row_hash"]] = self._stamp(doc)
                n += 1
        return n

    async def list_journal(self, *, since: datetime | None = None, limit: int = 200,
                           events: Iterable[str] | None = None, fields: Iterable[str] | None = None) -> list[dict]:
        ev = set(events) if events is not None else None
        docs = [d for d in self._journal.values()
                if _since_ok(d, since) and (ev is None or d.get("event") in ev)]
        return _project([self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]], fields)

    # ---- account snapshots
    async def add_account_snapshot(self, doc: dict) -> None:
        self._snapshots.append(self._stamp(S.prepare_ts_doc(doc)))

    async def list_account_snapshots(self, *, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        docs = [d for d in self._snapshots if _since_ok(d, since)]
        return [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]

    # ---- fills
    async def upsert_fills(self, fills: Iterable[dict]) -> int:
        n = 0
        async with self._lock:
            for f in fills:
                doc = S.prepare_fill(f)
                old = self._fills.get(doc["fill_id"])
                if old is None:
                    self._fills[doc["fill_id"]] = self._stamp(doc)
                    n += 1
                else:
                    old.update(doc)                      # $set semantics: merge fields
        return n

    async def set_fill_fields(self, fill_id: str, fields: dict) -> bool:
        clean = S.normalize({k: v for k, v in dict(fields).items() if k not in ("fill_id", "ts", "_id")})
        async with self._lock:
            old = self._fills.get(str(fill_id))
            if old is None:
                return False
            old.update(clean)
            return True

    async def list_fills(self, *, since: datetime | None = None, until: datetime | None = None,
                         limit: int = 500, fields: Iterable[str] | None = None,
                         fill_ids: Iterable[str] | None = None) -> list[dict]:
        ids = None if fill_ids is None else {str(i) for i in fill_ids}
        docs = [d for d in self._fills.values() if _window_ok(d, since, until)
                and (ids is None or d["fill_id"] in ids)]
        out = [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]
        if fields is not None:
            keep = set(fields)
            out = [{k: v for k, v in d.items() if k in keep} for d in out]
        return out

    # ---- settlements
    async def upsert_settlements(self, rows: Iterable[dict]) -> int:
        n = 0
        async with self._lock:
            for r in rows:
                doc = S.prepare_settlement(r)
                old = self._settlements.get(doc["ticker"])
                if old is None:
                    self._settlements[doc["ticker"]] = self._stamp(doc)
                    n += 1
                else:
                    old.update(doc)                      # $set semantics: merge fields
        return n

    async def list_settlements(self, *, since: datetime | None = None, until: datetime | None = None,
                               limit: int = 500, fields: Iterable[str] | None = None) -> list[dict]:
        docs = [d for d in self._settlements.values() if _window_ok(d, since, until)]
        return _project([self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]], fields)

    async def set_settlement_fields(self, ticker: str, fields: dict) -> bool:
        clean = S.normalize({k: v for k, v in dict(fields).items() if k not in ("ticker", "ts", "_id")})
        if S.CLOSE_TIME in clean and S._safe_to_utc(clean[S.CLOSE_TIME]) is None:
            clean.pop(S.CLOSE_TIME)                  # close_time is never stored as null
        async with self._lock:
            old = self._settlements.get(str(ticker))
            if old is None:
                return False
            old.update(clean)
            return True

    # ---- arbitrage log
    async def add_arb_opportunity(self, doc: Any) -> bool:
        new = S.prepare_arb_opportunity(doc)
        async with self._lock:
            if new["executed"] == S.ARB_LOG_ONLY:
                lo = new["ts"] - timedelta(seconds=S.ARB_DEDUPE_SECONDS)
                cands = [d for d in self._arb if d["sig"] == new["sig"] and d["executed"] == S.ARB_LOG_ONLY
                         and lo <= d["last_seen"] <= new["ts"]]
                if cands:
                    cur = max(cands, key=lambda d: (d["last_seen"], d["\x00seq"]))
                    cur["seen_count"] += 1
                    cur["last_seen"] = new["ts"]
                    return False
            self._arb.append(self._stamp(new))
            return True

    async def list_arb_opportunities(self, *, since: datetime | None = None, limit: int = 100,
                                     executed_only: bool = False) -> list[dict]:
        docs = [d for d in self._arb if _since_ok(d, since)
                and (not executed_only or d["executed"] != S.ARB_LOG_ONLY)]
        return [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]

    # ---- queue-position telemetry
    async def upsert_quote_episodes(self, docs: Iterable[dict]) -> int:
        n = 0
        async with self._lock:
            for d in docs:
                doc = S.prepare_quote_episode(d)
                old = self._episodes.get(doc["order_id"])
                if old is None:
                    self._episodes[doc["order_id"]] = self._stamp(doc)
                    n += 1
                else:
                    old.update({k: v for k, v in doc.items() if k not in S.QUOTE_EPISODE_IMMUTABLE})
        return n

    async def set_quote_episode_close_time(self, order_id: str, close_time: datetime) -> bool:
        ct = S._safe_to_utc(close_time)
        if ct is None:
            return False
        async with self._lock:
            old = self._episodes.get(str(order_id))
            if old is None or old.get(S.CLOSE_TIME) is not None:
                return False
            old[S.CLOSE_TIME] = ct
            return True

    async def list_quote_episodes(self, *, since: datetime | None = None, env: str | None = None,
                                  status: str | None = None, limit: int = 5000,
                                  fields: Iterable[str] | None = None) -> list[dict]:
        docs = [d for d in self._episodes.values() if _since_ok(d, since)
                and (env is None or d.get("env") == env) and (status is None or d.get("status") == status)]
        out = [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]
        if fields is not None:
            keep = set(fields)
            out = [{k: v for k, v in d.items() if k in keep} for d in out]
        return out

    # ---- UX sprint additions
    async def set_fills_fields(self, updates: dict[str, dict]) -> int:
        n = 0
        async with self._lock:
            for fid, fields in dict(updates).items():
                old = self._fills.get(str(fid))
                if old is None:
                    continue
                old.update(S.normalize({k: v for k, v in dict(fields).items() if k not in ("fill_id", "ts", "_id")}))
                n += 1
        return n

    async def add_alert(self, doc: dict) -> dict:
        d = S.prepare_alert(doc)
        async with self._lock:
            self._alerts.append(self._stamp(d))
            if len(self._alerts) > 5000:                 # memory guard (Atlas uses the 90-day TTL instead)
                self._alerts = self._alerts[-5000:]
        return self._clean(d)

    async def list_alerts(self, *, type: str | None = None, limit: int = 50,
                          before: datetime | None = None) -> list[dict]:
        b = S.norm_dt(before) if before is not None else None
        docs = [d for d in self._alerts if (type is None or d["type"] == type) and (b is None or d["ts"] < b)]
        return [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]

    async def save_push_subscription(self, doc: dict) -> bool:
        d = S.prepare_push_subscription(doc)
        async with self._lock:
            new = d["sub_id"] not in self._push
            if new and len(self._push) >= S.PUSH_SUBSCRIPTIONS_MAX:
                raise ValueError("too many push subscriptions")
            if not new:
                d["created_utc"] = self._push[d["sub_id"]]["created_utc"]
            self._push[d["sub_id"]] = d
            return new

    async def list_push_subscriptions(self) -> list[dict]:
        return [copy.deepcopy(d) for d in self._push.values()]

    async def delete_push_subscription(self, sub_id: str) -> bool:
        async with self._lock:
            return self._push.pop(str(sub_id), None) is not None

    async def clear_push_subscriptions(self) -> int:
        async with self._lock:
            n = len(self._push)
            self._push.clear()
            return n

    # ---- monitor
    async def add_monitor_summary(self, doc: dict) -> None:
        self._monitor.append(self._stamp(S.prepare_ts_doc(doc, ts_fallbacks=("generated_utc",))))

    async def last_monitor_summary(self) -> dict | None:
        if not self._monitor:
            return None
        return self._clean(self._newest_seq(self._monitor)[0])

    async def list_monitor_summaries(self, *, since: datetime | None = None, limit: int = 100) -> list[dict]:
        docs = [d for d in self._monitor if _since_ok(d, since)]
        return [self._clean(d) for d in self._newest_seq(docs)[:max(0, int(limit))]]

    # ---- audit
    async def add_audit(self, doc: dict) -> None:
        self._audit.append(self._stamp(S.prepare_ts_doc(doc)))

    async def list_audit(self, limit: int = 100) -> list[dict]:
        return [self._clean(d) for d in self._newest_seq(self._audit)[:max(0, int(limit))]]

    # ---- users
    async def get_user(self, username: str) -> dict | None:
        return self._out(self._users.get(username))

    async def save_user(self, doc: dict) -> dict:
        username = doc.get("username")
        if not username:
            raise ValueError("user doc needs a username")
        S.check_no_secrets(doc)
        plain, mono = S.split_monotonic(S.strip_id(S.normalize(doc)))
        async with self._lock:
            cur = self._users.get(username, {})
            cur.update(plain)
            self._apply_max(cur, mono)
            self._users[username] = cur
            return self._out(cur)

    @staticmethod
    def _apply_max(cur: dict, mono: dict) -> None:
        for k, v in mono.items():
            old = cur.get(k)
            try:
                cur[k] = v if old is None or v > old else old
            except TypeError:          # mismatched types: same as Mongo's BSON order is not worth emulating
                cur[k] = v

    async def update_user_fields(self, username: str, *, set: dict | None = None,
                                 max: dict | None = None, inc: dict | None = None) -> dict | None:
        set_ = dict(set or {})
        set_.pop("username", None)
        S.check_no_secrets(set_)
        plain, mono = S.split_monotonic(S.normalize(set_))
        mono.update({k: v for k, v in S.normalize(dict(max or {})).items() if v is not None})
        async with self._lock:
            cur = self._users.get(username)
            if cur is None:
                return None
            cur.update(plain)
            self._apply_max(cur, mono)
            for k, v in (inc or {}).items():
                cur[k] = (cur.get(k) or 0) + v
            return self._out(cur)

    # ---- token revocation
    async def revoke_jti(self, jti: str, exp: datetime | int | float) -> None:
        self._revoked[str(jti)] = S.to_utc(exp)

    async def is_revoked(self, jti: str) -> bool:
        exp = self._revoked.get(str(jti))
        if exp is None:
            return False
        if exp <= S.utcnow():                            # expired: the token is dead anyway; purge (TTL)
            self._revoked.pop(str(jti), None)
            return False
        return True

    # ---- cloud settings
    async def get_cloud_settings(self) -> dict | None:
        return self._out(self._cloud)

    async def save_cloud_settings(self, doc: dict, *, by: str) -> dict:
        S.check_no_secrets(doc)
        async with self._lock:
            cur = dict(self._cloud or {})
            cur.update(S.strip_id(S.normalize(doc)))
            cur["updated_utc"] = S.utcnow()
            cur["updated_by"] = by
            self._cloud = cur
            return self._out(cur)
