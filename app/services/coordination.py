"""Single-process demo mutation boundary. Deploy exactly one API worker/replica.

Mongo unique indexes protect identity; this lock also protects interval checks and
seed/tick/approval ordering. Multi-replica deployments require distributed fencing.
"""

import asyncio
from contextvars import ContextVar
from functools import wraps
from weakref import WeakKeyDictionary

_locks = WeakKeyDictionary()
_owner = ContextVar("mutation_owner", default=None)


def serialized(function):
    @wraps(function)
    async def guarded(*args, **kwargs):
        task = asyncio.current_task()
        if _owner.get() is task:
            return await function(*args, **kwargs)
        loop = asyncio.get_running_loop()
        lock = _locks.setdefault(loop, asyncio.Lock())
        async with lock:
            token = _owner.set(task)
            try:
                return await function(*args, **kwargs)
            finally:
                _owner.reset(token)

    return guarded
