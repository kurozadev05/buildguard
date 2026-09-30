"""AI code runs on the event loop; database work runs in the threadpool with its own short-lived session.
(Streaming responses outlive the request-scoped session, so they must never share it.)"""
import threading
from collections.abc import Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from ..database import SessionLocal, is_sqlite

# SQLite allows one writer, and a transaction that reads and then writes cannot wait for the lock ("database is locked").
# So on SQLite the AI layer's writes are serialised in-process (each holds it for milliseconds). PostgreSQL needs no lock.
_sqlite_write_lock = threading.Lock()


def _run(fn: Callable, args: tuple, kw: dict) -> Any:
    with SessionLocal() as db:
        return fn(db, *args, **kw)


def _run_write(fn: Callable, args: tuple, kw: dict) -> Any:
    if is_sqlite:
        with _sqlite_write_lock:
            return _run(fn, args, kw)
    return _run(fn, args, kw)


async def in_db(fn: Callable, *args, **kw) -> Any:
    """Read-only work in a threadpool thread with its own short-lived session."""
    return await run_in_threadpool(_run, fn, args, kw)


async def in_db_write(fn: Callable, *args, **kw) -> Any:
    """Work that commits. Same as in_db, plus the SQLite single-writer guard."""
    return await run_in_threadpool(_run_write, fn, args, kw)
