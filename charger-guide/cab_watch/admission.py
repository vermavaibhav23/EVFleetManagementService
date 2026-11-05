"""Bound telemetry work and give waiting accounts FIFO admission per worker."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager

from fastapi import HTTPException


class TelemetryAdmission:
    def __init__(self, concurrency=2, max_waiting=32, wait_seconds=2):
        self.concurrency = concurrency
        self.max_waiting = max_waiting
        self.wait_seconds = wait_seconds
        self.accounts = set()
        self.active = set()
        self.waiting = deque()

    def admit_waiters(self):
        while self.waiting and len(self.active) < self.concurrency:
            user, ready = self.waiting.popleft()
            self.active.add(user)
            ready.set_result(None)

    @asynccontextmanager
    async def slot(self, user):
        if user in self.accounts or len(self.waiting) >= self.max_waiting:
            raise HTTPException(429, "Telemetry capacity busy. Retry this batch.",
                                headers={"Retry-After": "1"})
        ready = asyncio.get_running_loop().create_future()
        ticket = (user, ready)
        self.accounts.add(user)
        self.waiting.append(ticket)
        self.admit_waiters()
        try:
            try:
                await asyncio.wait_for(asyncio.shield(ready), self.wait_seconds)
            except TimeoutError:
                raise HTTPException(429, "Telemetry capacity busy. Retry this batch.",
                                    headers={"Retry-After": "1"}) from None
            yield
        finally:
            if ticket in self.waiting:
                self.waiting.remove(ticket)
            self.active.discard(user)
            self.accounts.discard(user)
            self.admit_waiters()
