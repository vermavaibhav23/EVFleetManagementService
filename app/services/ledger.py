"""Single-document, versioned MongoDB scheduling authority (also on standalone)."""

import copy
from datetime import UTC, datetime

from bson import BSON
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError


class Ledger:
    def __init__(self, db):
        self.collection = db.fleet_ledger

    async def read(self):
        doc = await self.collection.find_one({"_id": "active"})
        if doc is None:
            raise HTTPException(409, "Choose a scenario and select Load / reset first")
        return doc

    async def replace_run(self, document):
        previous = await self.collection.find_one({"_id": "active"})
        document = copy.deepcopy(document)
        document.update(_id="active", revision=(previous or {}).get("revision", 0) + 1)
        self.check_size(document)
        if previous:
            result = await self.collection.replace_one(
                {"_id": "active", "revision": previous["revision"]}, document
            )
            if not result.modified_count:
                raise HTTPException(409, "Fleet changed during reset; retry load")
        else:
            try:
                await self.collection.insert_one(document)
            except DuplicateKeyError as exc:
                raise HTTPException(409, "Another reset finished first") from exc
        return document

    @staticmethod
    def check_size(doc):
        if len(BSON.encode(doc)) > 12 * 1024 * 1024:
            raise HTTPException(
                413, "Simulation history limit reached; load a fresh run"
            )

    async def commit(self, previous, updated):
        updated = copy.deepcopy(updated)
        updated["revision"] = previous["revision"] + 1
        updated["updated_at"] = datetime.now(UTC)
        self.check_size(updated)
        result = await self.collection.replace_one(
            {
                "_id": "active",
                "run_id": previous["run_id"],
                "revision": previous["revision"],
            },
            updated,
        )
        if not result.modified_count:
            raise HTTPException(
                409,
                "Fleet changed; refresh and retry. No partial changes were committed.",
            )
        return updated

    async def mutate(self, action, run_id=None):
        # Actions are pure edits to a private document. Recheck on CAS contention,
        # including clock-lease renewals; never retry a failed business validation.
        for attempt in range(4):
            previous = await self.read()
            if run_id is not None and previous["run_id"] != run_id:
                raise HTTPException(409, "This action belongs to an old simulation run")
            updated = copy.deepcopy(previous)
            result = action(updated)
            try:
                await self.commit(previous, updated)
                return result
            except HTTPException as exc:
                if exc.status_code != 409 or attempt == 3:
                    raise


def bookings(doc, exclude_plan=None):
    result = []
    for plan in doc["plans"].values():
        if plan["plan_id"] == exclude_plan or plan["status"] not in {
            "APPROVED",
            "EXECUTING",
            "INTERRUPTED",
        }:
            continue
        for op in plan["operations"]:
            if op["kind"] == "CHARGE" and op["status"] in {
                "PLANNED",
                "ACTIVE",
                "RELEASING",
            }:
                result.append(
                    {
                        **op,
                        "plan_id": plan["plan_id"],
                        "vin": plan["vin"],
                        "run_id": plan["run_id"],
                    }
                )
    return result
