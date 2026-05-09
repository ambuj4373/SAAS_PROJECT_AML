"""
core/cache.py — Streamlit-independent TTL cache.

Drop-in replacement for ``st.cache_data`` for the API/search wrapper
pattern in app.py:

    fetch_charity_data = cached(ttl=3600)(fetch_charity_data)

Or as a decorator:

    @cached(ttl=3600)
    def search_adverse_media(query): ...

Behaviour
---------
- Process-local TTL cache (cachetools.TTLCache) shared across all calls.
- Pickle-based key hashing so dict / list / bytes args work the same way
  ``st.cache_data`` accepts them.
- ``invalidate()`` clears all entries; ``stats()`` returns hit/miss counts.

This module has no Streamlit dependency and no I/O. It is safe to import
from any layer (api_clients, pipeline, reports, tests).
"""

from __future__ import annotations

import functools
import pickle
import threading
from typing import Any, Callable, TypeVar

from cachetools import TTLCache

F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_TTL_SECONDS = 3600  # 1 hour, matches the previous st.cache_data TTL
DEFAULT_MAX_ENTRIES = 4096  # bounded — typical report uses < 50 entries

_lock = threading.RLock()
_cache: TTLCache = TTLCache(maxsize=DEFAULT_MAX_ENTRIES, ttl=DEFAULT_TTL_SECONDS)
_stats = {"hits": 0, "misses": 0, "errors": 0}


def _make_key(func: Callable, args: tuple, kwargs: dict) -> bytes:
    """Build a stable cache key from the function identity + args.

    Uses pickle so dicts / lists / bytes are supported the same way
    Streamlit's cache_data accepts them. Falls back to repr() if a value
    is not picklable (e.g. open file handles, callables) — caller is
    responsible for not relying on cache hits in those cases.
    """
    try:
        payload = pickle.dumps(
            (func.__module__, func.__qualname__, args, kwargs),
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    except (pickle.PicklingError, TypeError):
        # Fall back to repr — collision-prone but never crashes
        payload = repr(
            (func.__module__, func.__qualname__, args, sorted(kwargs.items()))
        ).encode("utf-8")
    return payload


def cached(
    ttl: int = DEFAULT_TTL_SECONDS,
    *,
    show_spinner: bool = False,  # accepted for st.cache_data API parity; ignored
    maxsize: int | None = None,  # ignored (shared cache); accepted for parity
) -> Callable[[F], F]:
    """TTL-cached decorator. Drop-in for ``st.cache_data`` in this codebase.

    The shared module-level TTLCache is used regardless of ``ttl`` — the
    parameter is currently advisory; per-key TTL is not supported because
    cachetools' TTLCache uses a single TTL. If a finer-grained TTL is
    needed later, add per-decorator caches keyed by ttl.
    """
    del show_spinner, maxsize  # accepted-but-unused (API parity)

    def _decorator(func: F) -> F:
        @functools.wraps(func)
        def _wrapper(*args, **kwargs):
            key = _make_key(func, args, kwargs)
            with _lock:
                hit = _cache.get(key, _MISS)
                if hit is not _MISS:
                    _stats["hits"] += 1
                    return hit
                _stats["misses"] += 1

            try:
                result = func(*args, **kwargs)
            except Exception:
                with _lock:
                    _stats["errors"] += 1
                raise

            with _lock:
                _cache[key] = result
            return result

        _wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return _wrapper  # type: ignore[return-value]

    return _decorator


# Sentinel distinct from any return value (including None)
_MISS = object()


def invalidate() -> None:
    """Drop every cached entry. Useful in tests."""
    with _lock:
        _cache.clear()


def stats() -> dict[str, int]:
    """Return a snapshot of {hits, misses, errors, size}."""
    with _lock:
        return {**_stats, "size": len(_cache)}


def configure(*, ttl: int | None = None, maxsize: int | None = None) -> None:
    """Reconfigure the shared cache (clears existing entries).

    Intended for application startup or tests; not for hot paths.
    """
    global _cache
    with _lock:
        new_ttl = ttl if ttl is not None else _cache.ttl
        new_max = maxsize if maxsize is not None else _cache.maxsize
        _cache = TTLCache(maxsize=new_max, ttl=new_ttl)
