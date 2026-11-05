"""PostgreSQL migration checks, isolated in a random schema per test.

Set CABWATCH_TEST_DATABASE_URL to a disposable test database.
"""
import json
import os
from pathlib import Path
import unittest
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).parent
PLAN = json.dumps(dict(to_pickup=['A', 'G'], customer_ride=['G', 'K'], to_depot=['K', 'E']))


@unittest.skipUnless(os.getenv('CABWATCH_TEST_DATABASE_URL'), 'Requires a test PostgreSQL database')
class TripSchema(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.namespace = 'cw_schema_test_' + uuid4().hex
        self.db = await asyncpg.connect(os.environ['CABWATCH_TEST_DATABASE_URL'])

    async def asyncTearDown(self):
        try:
            await self.db.execute(f'DROP SCHEMA IF EXISTS {self.namespace} CASCADE')
        finally:
            await self.db.close()

    async def sql(self, query, *args):
        return await self.db.fetch(query.replace('cab_watch_accounts', self.namespace), *args)

    async def migrate(self, legacy=False):
        path = ROOT / ('tests/fixtures/pre_trip_idempotency.sql' if legacy else 'cab_watch/schema.sql')
        async with self.db.transaction():
            await self.db.execute(path.read_text().replace('cab_watch_accounts', self.namespace))

    async def seed(self):
        self.a, self.b = uuid4(), uuid4()
        for i, user in enumerate((self.a, self.b)):
            await self.sql("""INSERT INTO cab_watch_accounts.users(id,name,email,password_hash,jwt_key)
                VALUES($1,'Test',$2,'not-a-login',$3)""", user, f'{i}@test.invalid', b'x' * 32)
        self.cars = []
        for i, user in enumerate((self.a, self.a, self.b)):
            rows = await self.sql("""INSERT INTO cab_watch_accounts.vehicles(user_id,cab_number,parked_depot_id)
                VALUES($1,$2,(SELECT id FROM cab_watch_accounts.depots WHERE node='A')) RETURNING id""", user, i + 1)
            self.cars.append(rows[0]['id'])

    async def insert_trip(self, user, key, vehicle):
        return await self.sql("""INSERT INTO cab_watch_accounts.trips
            (id,user_id,idempotency_key,vehicle_id,planned_legs,completed_at)
            VALUES($1,$2,$3,$4,$5::jsonb,now()) RETURNING id""", uuid4(), user, key, vehicle, PLAN)

    async def test_fresh_schema_enforces_ownership_and_per_user_keys(self):
        await self.migrate()
        await self.seed()
        key = uuid4()
        trip = (await self.insert_trip(self.a, key, self.cars[0]))[0]['id']
        with self.assertRaises(asyncpg.UniqueViolationError):
            await self.insert_trip(self.a, key, self.cars[1])
        await self.insert_trip(self.b, key, self.cars[2])
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self.insert_trip(self.b, uuid4(), self.cars[0])
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self.sql('UPDATE cab_watch_accounts.trips SET vehicle_id=$2 WHERE id=$1', trip, self.cars[2])
        with self.assertRaises(asyncpg.NotNullViolationError):
            await self.insert_trip(self.a, None, self.cars[0])
        with self.assertRaises(asyncpg.NotNullViolationError):
            await self.insert_trip(None, uuid4(), self.cars[0])

    async def test_upgrade_preserves_history_keys_and_repeat_startup(self):
        await self.migrate(legacy=True)
        await self.seed()
        trip, old_put, key = uuid4(), uuid4(), uuid4()
        for tid, car in ((trip, self.cars[0]), (old_put, self.cars[1])):
            await self.sql("""INSERT INTO cab_watch_accounts.trips
                (id,vehicle_id,planned_legs,last_sequence,last_lat,last_lon,phase,completed_at)
                VALUES($1,$2,$3::jsonb,17,12.9,77.5,3,now())""", tid, car, PLAN)
        await self.sql('INSERT INTO cab_watch_accounts.dispatch_requests VALUES($1,$2,$3)', self.a, key, trip)
        await self.sql("""INSERT INTO cab_watch_accounts.alerts(trip_id,opened_sequence,peak_metres)
            VALUES($1,15,270)""", trip)
        before = await self.sql('SELECT * FROM cab_watch_accounts.trips ORDER BY id')
        alerts = await self.sql('SELECT * FROM cab_watch_accounts.alerts')
        await self.migrate()
        after = await self.sql('SELECT * FROM cab_watch_accounts.trips ORDER BY id')
        for original, updated in zip(before, after):
            self.assertEqual(dict(original), {k: updated[k] for k in original.keys()})
            self.assertEqual(updated['user_id'], self.a)
            self.assertIsNotNone(updated['idempotency_key'])
        mapped = next(r for r in after if r['id'] == trip)
        self.assertEqual(mapped['idempotency_key'], key)
        self.assertEqual(alerts, await self.sql('SELECT * FROM cab_watch_accounts.alerts'))
        self.assertIsNone((await self.sql("SELECT to_regclass('cab_watch_accounts.dispatch_requests') AS table_name"))[0]['table_name'])
        await self.migrate()
        self.assertEqual(after, await self.sql('SELECT * FROM cab_watch_accounts.trips ORDER BY id'))

    async def test_invalid_mapping_rolls_back_without_dropping_original(self):
        await self.migrate(legacy=True)
        await self.seed()
        tid, key = uuid4(), uuid4()
        await self.sql("""INSERT INTO cab_watch_accounts.trips(id,vehicle_id,planned_legs)
            VALUES($1,$2,$3::jsonb)""", tid, self.cars[0], PLAN)
        await self.sql('INSERT INTO cab_watch_accounts.dispatch_requests VALUES($1,$2,$3)', self.b, key, tid)
        with self.assertRaisesRegex(asyncpg.RaiseError, 'Dispatch mappings disagree'):
            await self.migrate()
        rows = await self.sql('SELECT * FROM cab_watch_accounts.dispatch_requests')
        self.assertEqual(rows[0]['request_key'], key)
        self.assertEqual(rows[0]['trip_id'], tid)
        self.assertNotIn('user_id', (await self.sql('SELECT * FROM cab_watch_accounts.trips'))[0].keys())
