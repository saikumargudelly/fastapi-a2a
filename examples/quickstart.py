"""
Quick-start example: wrap a FastAPI app with FastApiA2A.
Run with: uvicorn examples.quickstart:app --reload
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_a2a import FastApiA2A, RegistryConfig


# ── Domain models ──────────────────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    text: str
    max_sentences: int = 3


class SummarizeResponse(BaseModel):
    summary: str
    sentence_count: int


# ── Create your existing FastAPI app ──────────────────────────────────────────

app = FastAPI(
    title="My Summariser Agent",
    description="Summarises documents using extractive summarisation.",
    version="1.0.0",
)

# ── Attach FastApiA2A (before lifespan) ───────────────────────────────────────

a2a = FastApiA2A(
    app,
    name="My Summariser Agent",
    description="Summarises documents using extractive summarisation. Accepts plain text; returns a concise summary.",
    version="1.0.0",
    url="http://localhost:8000",
    registry=RegistryConfig(
        url="https://registry.fastapi-a2a.dev",  # or None for local-only
        heartbeat_interval_seconds=60,
    ),
    streaming=True,
    provider_org="Acme Corp",
    provider_url="https://acme.example.com",
)


# ── Register a skill handler (optional — maps skill_id → your function) ───────

@a2a.skill("summarize")
async def handle_summarize(task_id, message: dict, db) -> str:
    """Called when the agent receives a tasks/send for the 'summarize' skill."""
    parts = message.get("parts", [])
    text = next((p["text"] for p in parts if p.get("type") == "text"), "")
    # Replace with real summariser logic
    sentences = [s.strip() for s in text.split(".") if s.strip()][:3]
    return ". ".join(sentences) + "." if sentences else "No text provided."


# ── FastAPI lifespan wraps FastApiA2A startup/shutdown ───────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await a2a.startup()
    try:
        yield
    finally:
        await a2a.shutdown()


app.router.lifespan_context = lifespan


# ── Your existing routes (auto-discovered as skills) ─────────────────────────

@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(body: SummarizeRequest) -> SummarizeResponse:
    """Summarise a document into bullet points."""
    sentences = [s.strip() for s in body.text.split(".") if s.strip()]
    top = sentences[: body.max_sentences]
    return SummarizeResponse(summary=". ".join(top) + "." if top else "", sentence_count=len(top))


# ── Run hint ──────────────────────────────────────────────────────────────────
# uvicorn examples.quickstart:app --reload
# Then visit: http://localhost:8000/.well-known/agent.json
