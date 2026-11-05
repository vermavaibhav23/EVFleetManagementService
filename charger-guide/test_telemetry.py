import asyncio
import unittest
from fastapi import HTTPException
from cab_watch.admission import TelemetryAdmission


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_accounts_wait_in_order_and_duplicate_cannot_jump_queue(self):
        gate = TelemetryAdmission(concurrency=1)
        order = []

        async def request(user):
            async with gate.slot(user):
                order.append(user)

        async with gate.slot('a'):
            second = asyncio.create_task(request('b'))
            third = asyncio.create_task(request('c'))
            await asyncio.sleep(0)
            with self.assertRaises(HTTPException) as error:
                async with gate.slot('a'):
                    self.fail('Concurrent work from same account admitted')
            self.assertEqual(error.exception.status_code, 429)
        await asyncio.gather(second, third)
        self.assertEqual(order, ['b', 'c'])
        self.assertFalse(gate.accounts)

    async def test_cancelled_waiter_does_not_leak_capacity(self):
        gate = TelemetryAdmission(concurrency=1)

        async def request():
            async with gate.slot('b'):
                self.fail('Cancelled waiter ran')

        async with gate.slot('a'):
            task = asyncio.create_task(request())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        async with gate.slot('b'):
            self.assertEqual(gate.active, {'b'})

    async def test_full_queue_and_wait_timeout_return_retry_guidance(self):
        gate = TelemetryAdmission(concurrency=1, max_waiting=1, wait_seconds=.01)

        async def request():
            with self.assertRaises(HTTPException) as error:
                async with gate.slot('b'):
                    self.fail('Blocked request admitted')
            self.assertEqual(error.exception.headers['Retry-After'], '1')

        async with gate.slot('a'):
            task = asyncio.create_task(request())
            await asyncio.sleep(0)
            with self.assertRaises(HTTPException):
                async with gate.slot('c'):
                    self.fail('Full queue accepted work')
            await task
        self.assertFalse(gate.accounts)
