"""Storage layer for the control API. Use get_store(settings) to build the configured Store."""
from __future__ import annotations

from typing import Any

from .schemas import DB_NAME_DEFAULT, row_hash
from .store import MemoryStore, Store


def get_store(settings: Any) -> Store:
    """MongoStore when settings.store == "mongo", else MemoryStore.

    `settings` is any object with attributes `store`, `mongodb_uri`, `mongodb_db`.
    Does not connect: call `await store.ensure_indexes()` / `await store.ping()` at startup.
    """
    kind = (getattr(settings, "store", None) or "memory").strip().lower()
    if kind == "mongo":
        from .mongo import MongoStore                       # imported lazily: memory mode needs no pymongo
        uri = getattr(settings, "mongodb_uri", None)
        if not uri:
            raise ValueError("STORE=mongo but MONGODB_URI is not set")
        return MongoStore(uri, getattr(settings, "mongodb_db", None) or DB_NAME_DEFAULT)
    if kind != "memory":
        raise ValueError(f"unknown STORE {kind!r} (expected 'mongo' or 'memory')")
    return MemoryStore()


__all__ = ["Store", "MemoryStore", "get_store", "row_hash"]
