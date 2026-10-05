"""Provider forwarding, Kafka state/history consumers and planning dispatch."""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import httpx
from aiokafka import TopicPartition
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.core.kafka import build_consumer, build_producer, ensure_topics
from app.domain import PlanRequest, Telemetry
from app.services.control import request_job
from app.services.ledger import Ledger
from app.services.optimizer import fingerprint
from app.services.plan_review import issue_message
from app.services.runner import Runner
from app.services.simulator import Simulator, caught_up, fresh
from app.services.telemetry import TelemetryProcessor

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, pool, history):
        self.pool, self.history = pool, history
        self.store, self.simulator = Ledger(pool), Simulator(pool)
        self.processor, self.planner = TelemetryProcessor(pool), Runner(pool)
        self.producer = build_producer()
        self.tasks, self.consumers = [], []
        self.status = {}
        self.http = httpx.AsyncClient(timeout=10)

    async def start(self):
        await ensure_topics()
        await self.producer.start()
        if settings.run_workers:
            for i in range(settings.telemetry_workers):
                name = f"state-{i}"
                self.tasks.append(
                    asyncio.create_task(
                        self.repeat(name, lambda name=name: self.consume("state", name))
                    )
                )
            self.tasks.append(
                asyncio.create_task(
                    self.repeat("history", lambda: self.consume("history", "history"))
                )
            )
            self.tasks.append(
                asyncio.create_task(
                    self.repeat(
                        "planning", lambda: self.consume("planning", "planning")
                    )
                )
            )
            self.tasks.append(
                asyncio.create_task(self.repeat("provider", self.forward))
            )
            self.tasks.append(
                asyncio.create_task(self.repeat("simulation", self.clock))
            )

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        await self.planner.stop()
        await self.producer.stop()
        await self.http.aclose()

    async def repeat(self, name, action):
        while True:
            try:
                await action()
                self.status[name] = "OK"
            except HTTPException as exc:
                self.status[name] = str(exc.detail)
            except Exception as exc:  # noqa: BLE001 - retry boundary for remote services
                self.status[name] = f"Retrying after {type(exc).__name__}"
                log.warning("%s waiting after %s", name, type(exc).__name__)
            await asyncio.sleep(1)

    async def publish(self, event):
        await asyncio.wait_for(
            self.producer.send_and_wait(
                settings.kafka_telemetry_topic,
                event.model_dump(mode="json"),
                key=event.vehicle_id.encode(),
            ),
            timeout=10,
        )

    async def forward(self):
        # One dispatcher lease prevents concurrent sends reordering this demo's
        # outbox. No transaction spans the HTTP call; duplicates are harmless.
        async with self.pool.acquire() as connection:
            locked = await connection.fetchval(
                "SELECT pg_try_advisory_lock(hashtext(current_schema()),72849103)"
            )
            if not locked:
                return
            try:
                rows = await connection.fetch(
                    "SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY created_at,id LIMIT 100"
                )
                for row in rows:
                    if row["kind"] == "TELEMETRY":
                        response = await self.http.post(
                            settings.provider_url.rstrip("/")
                            + settings.api_v1_prefix
                            + "/telemetry",
                            json=row["payload"],
                        )
                        if response.status_code == 409:
                            current = await connection.fetchval(
                                "SELECT run_id FROM fleet_run WHERE singleton"
                            )
                            if current != row["run_id"]:
                                continue
                        response.raise_for_status()
                    else:
                        await asyncio.wait_for(
                            self.producer.send_and_wait(
                                settings.kafka_planning_topic,
                                row["payload"],
                                key=row["message_key"].encode(),
                            ),
                            timeout=10,
                        )
                    await connection.execute(
                        "UPDATE outbox SET sent_at=now() WHERE id=$1", row["id"]
                    )
                # Processed duplicates are fenced by sequence even after checkpoint
                # cleanup. MongoDB's reader has its own Kafka position.
                await connection.execute(
                    "DELETE FROM outbox WHERE processed_at < now()-interval '1 hour' AND sent_at IS NOT NULL"
                )
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(current_schema()),72849103)"
                )

    async def consume(self, role, name):
        topic = (
            settings.kafka_planning_topic
            if role == "planning"
            else settings.kafka_telemetry_topic
        )
        consumer = build_consumer(topic, f"{settings.kafka_group_prefix}-{role}")
        self.consumers.append(consumer)
        try:
            await consumer.start()
            async for message in consumer:
                while True:
                    try:
                        if role == "planning":
                            body = json.loads(message.value)
                            if not isinstance(body, dict) or not all(
                                isinstance(body.get(k), str)
                                for k in ("run_id", "job_id")
                            ):
                                raise ValueError("Invalid planning request")
                            await self.planner.handle(body)
                            async with self.pool.acquire() as connection:
                                await connection.execute(
                                    "UPDATE outbox SET processed_at=now() WHERE id=$1",
                                    body["job_id"],
                                )
                        else:
                            event = Telemetry.model_validate_json(message.value)
                            if role == "state":
                                await self.processor.process(event)
                            else:
                                await self.save_history(event)
                        self.status[name] = "OK"
                        break
                    except (ValidationError, ValueError) as exc:
                        await self.reject(message, type(exc).__name__)
                        break
                    except HTTPException as exc:
                        if exc.status_code in (404, 422) or "Old-run" in str(
                            exc.detail
                        ):
                            await self.reject(message, str(exc.detail))
                            break
                        # Empty/changed run: old messages must not block the new run.
                        if role != "planning" and "Load / reset" in str(exc.detail):
                            break
                        self.status[name] = str(exc.detail)
                        await asyncio.sleep(1)
                    except Exception as exc:  # noqa: BLE001 - retain the offset and retry
                        self.status[name] = f"Retrying after {type(exc).__name__}"
                        log.warning(
                            "%s processing retry after %s", name, type(exc).__name__
                        )
                        await asyncio.sleep(1)
                # Acknowledge only after durable state/history/result or rejection.
                # Rebalances can replay this message; all handlers are idempotent.
                try:
                    await consumer.commit(
                        {
                            TopicPartition(
                                message.topic, message.partition
                            ): message.offset + 1
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - replay is safe after rebalance
                    self.status[name] = f"Checkpoint retry: {type(exc).__name__}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status[name] = f"Stopped: {type(exc).__name__}"
            log.exception("Consumer %s stopped", name)
            raise
        finally:
            await consumer.stop()

    async def reject(self, message, reason):
        async with self.pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO rejected_events(id,reason) VALUES($1,$2) ON CONFLICT DO NOTHING",
                f"{message.topic}:{message.partition}:{message.offset}",
                reason,
            )

    async def save_history(self, event):
        # _id is deterministic, so crashes after insert cannot duplicate a reading.
        await self.history.telemetry_history.create_index(
            "expires_at", expireAfterSeconds=0
        )
        body = event.model_dump(mode="json")
        body.update(
            observed_at=event.observed_at,
            received_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=settings.mongo_history_days),
        )
        await self.history.telemetry_history.update_one(
            {"_id": f"{event.run_id}:{event.event_id}"},
            {"$setOnInsert": body},
            upsert=True,
        )

    async def clock(self):
        # Same database lease across API replicas; physical simulation has one owner.
        async with self.pool.acquire() as connection:
            locked = await connection.fetchval(
                "SELECT pg_try_advisory_lock(hashtext(current_schema()),72849104)"
            )
            if not locked:
                return
            try:
                doc = await self.store.read()
                simulator = await connection.fetchval(
                    "SELECT data FROM simulator_state WHERE run_id=$1", doc["run_id"]
                )
                if not caught_up(doc, simulator):
                    return
                await self.auto_plan(doc)
                doc = await self.store.read()
                if doc["running"]:
                    await self.simulator.step(doc["run_id"], doc["speed"])
                elif any(
                    not v.get("received_at")
                    or (
                        datetime.now(UTC) - datetime.fromisoformat(v["received_at"])
                    ).total_seconds()
                    >= 10
                    for v in doc["vehicles"].values()
                ):
                    await self.simulator.heartbeat()
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtext(current_schema()),72849104)"
                )

    async def auto_plan(self, doc):
        if any(j["status"] in ("QUEUED", "RUNNING") for j in doc["jobs"].values()):
            return
        for vin, v in doc["vehicles"].items():
            if v.get("plan_id") or v["state"] in (
                "COMPLETED",
                "TRAVELLING",
                "CHARGING",
                "CONNECTING",
                "RELEASING",
            ):
                continue
            if not fresh(doc, v, settings.telemetry_max_age_seconds):
                continue
            options = [
                p
                for p in doc["plans"].values()
                if p["vin"] == vin and p["status"] == "PROPOSED"
            ]
            if any(not issue_message(doc, p) for p in options):
                continue
            if not options and v.get("auto_plan_marker") == fingerprint(doc, vin):
                continue
            # Pausing the demo makes fresh options reviewable; approval never happens here.
            await self.store.mutate(
                lambda d: request_job(
                    d,
                    vin,
                    PlanRequest(
                        run_id=d["run_id"],
                        compare_tradeoffs=True,
                        pause_for_review=True,
                    ),
                ),
                doc["run_id"],
            )
            return
