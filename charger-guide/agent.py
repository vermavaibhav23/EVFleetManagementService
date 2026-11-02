"""A bounded Gemini tool loop. All tools operate on the same alert snapshot."""
import os
from collections import deque
from time import monotonic

import httpx

# One web process serves the public demo. Limit actual model calls, including tool turns.
model_calls = deque()


def tool(name, description):
    return {"name": name, "description": description,
            "parameters": {"type": "OBJECT", "properties": {}}}


async def investigate(van, facts, ask):
    tools = {
        "read_battery": {"battery_kwh": van["battery_kwh"], "safe_range_km": facts["safe_km"]},
        "compare_chargers": {"stations": facts["options"], "selected": facts["charger"]},
    }
    declarations = [tool("read_battery", "Read battery and safe range for this alert."),
                    tool("compare_chargers", "Inspect every candidate and its eligibility.")]
    contents = [{"role": "user", "parts": [{"text": "Investigate this simulated van's alert. "
                 "Use both tools, in any order. Give one short explanation of the selected "
                 "charger or why assistance is needed. Use only returned facts."}]}]
    trace = []
    for _ in range(3):
        turn = await ask(contents, declarations)
        parts = turn["parts"]
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if not calls:
            summary = " ".join(p["text"] for p in parts if p.get("text") and not p.get("thought"))
            if set(trace) == set(tools) and summary.strip():
                return {"mode": "Gemini review", "trace": trace, "summary": summary[:1200]}
            break
        replies = []
        for call in calls:
            name = call["name"]
            if name not in tools or name in trace or call.get("args"):
                raise ValueError("Invalid tool request")
            trace.append(name)
            response = {"name": name, "response": tools[name]}
            if call.get("id"):
                response["id"] = call["id"]
            replies.append({"functionResponse": response})
        contents.extend([turn, {"role": "user", "parts": replies}])
    raise ValueError("Incomplete investigation")


async def generate(contents, declarations, mode="AUTO"):
    key = os.environ["GEMINI_API_KEY"]
    now = monotonic()
    while model_calls and now - model_calls[0] >= 86400:
        model_calls.popleft()
    if len(model_calls) >= 60 or sum(now - t < 60 for t in model_calls) >= 9:
        raise ValueError("Demo model-call limit reached")
    model_calls.append(now)
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    async with httpx.AsyncClient(timeout=12) as client:
        result = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key}, json={"contents": contents,
            "tools": [{"functionDeclarations": declarations}],
            "toolConfig": {"functionCallingConfig": {"mode": mode}}})
        result.raise_for_status()
        return result.json()["candidates"][0]["content"]


async def review(van, facts):
    key = os.getenv("GEMINI_API_KEY")
    if key:
        try:
            return await investigate(van, facts, generate)
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
            pass
    return {"mode": "Rule-based summary", "trace": [], "summary": facts["reason"]}

