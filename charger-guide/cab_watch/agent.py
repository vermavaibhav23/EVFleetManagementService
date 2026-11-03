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

