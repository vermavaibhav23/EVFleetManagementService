import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.services.explanations import (
    ExplanationRequest,
    PlanExplainer,
    explain_current,
    facts_for,
)
from app.services.optimizer import optimize
from tests.test_v2 import fixture


@pytest.fixture(scope="module")
def snapshot():
    doc = fixture()
    plan = optimize(doc, "SIM-001")["plan"]
    doc["plans"][plan["plan_id"]] = plan
    request = ExplanationRequest(run_id=doc["run_id"], plan_id=plan["plan_id"])
    return doc, request


def test_facts_are_server_selected_read_only_and_exclude_locations(snapshot):
    doc, request = deepcopy(snapshot)
    before = deepcopy(doc)
    facts = facts_for(doc, "SIM-001", request)
    assert doc == before
    assert facts["charging_cost_inr"] == round(
        doc["plans"][request.plan_id]["total_cost"], 2
    )
    assert len(facts["slots"]) == 2
    assert "lat" not in json.dumps(facts, default=str).split('"')
    assert "lon" not in json.dumps(facts, default=str).split('"')
    with pytest.raises(HTTPException) as exc:
        facts_for(doc, "SIM-002", request)
    assert exc.value.status_code == 404


def test_old_run_and_stale_plan_are_rejected(snapshot):
    doc, request = deepcopy(snapshot)
    doc["run_id"] = "new-run"
    with pytest.raises(HTTPException) as exc:
        facts_for(doc, "SIM-001", request)
    assert exc.value.status_code == 409
    doc, request = deepcopy(snapshot)
    doc["vehicles"]["SIM-001"]["energy_kwh"] = 0
    with pytest.raises(HTTPException) as exc:
        facts_for(doc, "SIM-001", request)
    assert exc.value.status_code == 409


def test_missing_key_gives_honest_factual_fallback(snapshot):
    facts = facts_for(snapshot[0], "SIM-001", snapshot[1])
    result = asyncio.run(PlanExplainer(Settings(gemini_api_key=None)).explain(facts))
    assert result["source"] == "facts"
    assert "not configured" in result["notice"]
    assert "INR" in result["text"]


def test_provider_request_cache_and_rate_limit(snapshot):
    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "test-key" not in str(request.url)
        assert "tools" not in body
        assert body["generationConfig"]["maxOutputTokens"] == 350
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "text": "Charge at the two listed stations. Manager approval is required."
                                }
                            ]
                        },
                    }
                ]
            },
        )

    async def run():
        facts = facts_for(snapshot[0], "SIM-001", snapshot[1])
        service = PlanExplainer(
            Settings(gemini_api_key="test-key", ai_requests_per_minute=1),
            httpx.MockTransport(respond),
        )
        first = await service.explain(facts)
        second = await service.explain(facts)
        limited = await service.explain({**facts, "version": 2})
        assert first["source"] == "gemini" and not first["cached"]
        assert second["cached"] and len(calls) == 1
        assert limited["source"] == "facts" and "limit" in limited["notice"]
        assert "test-key" not in json.dumps(first)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode", ["timeout", "quota", "malformed", "blocked", "truncated"]
)
def test_provider_failures_do_not_break_planning_or_leak_errors(snapshot, mode):
    def respond(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-provider-details", request=request)
        if mode == "quota":
            return httpx.Response(429, json={"error": "secret-provider-details"})
        if mode == "malformed":
            return httpx.Response(200, text="not json")
        if mode == "blocked":
            return httpx.Response(200, json={"candidates": []})
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {"parts": [{"text": "partial advice"}]},
                    }
                ]
            },
        )

    facts = facts_for(snapshot[0], "SIM-001", snapshot[1])
    result = asyncio.run(
        PlanExplainer(
            Settings(gemini_api_key="test-key"), httpx.MockTransport(respond)
        ).explain(facts)
    )
    assert result["source"] == "facts"
    assert "secret-provider-details" not in json.dumps(result)
    assert "partial advice" not in result["text"]


def test_reset_during_generation_rejects_result(snapshot):
    doc, request = deepcopy(snapshot)

    class Store:
        async def read(self, run_id):
            return deepcopy(doc)

    class Service:
        async def explain(self, facts):
            doc["run_id"] = "reset-during-inference"
            return {"source": "gemini", "text": "An obsolete answer"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(explain_current(Store(), Service(), "SIM-001", request))
    assert exc.value.status_code == 409


def test_timeout_explanation_does_not_claim_infeasibility(snapshot):
    doc, _ = deepcopy(snapshot)
    doc["jobs"] = {
        "job": {
            "job_id": "job",
            "vin": "SIM-001",
            "status": "COMPLETED",
            "results": [{"vin": "SIM-001", "status": "LIMIT_NO_INCUMBENT"}],
        }
    }
    facts = facts_for(
        doc, "SIM-001", ExplanationRequest(run_id=doc["run_id"], job_id="job")
    )
    assert "does not prove" in facts["reason"]
    doc["jobs"]["new"] = {"job_id": "new", "vin": "SIM-001", "status": "RUNNING"}
    with pytest.raises(HTTPException):
        facts_for(
            doc, "SIM-001", ExplanationRequest(run_id=doc["run_id"], job_id="job")
        )


def test_explanation_requires_exactly_one_target():
    with pytest.raises(ValidationError):
        ExplanationRequest(run_id="r")
    with pytest.raises(ValidationError):
        ExplanationRequest(run_id="r", plan_id="p", job_id="j")
