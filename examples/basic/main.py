"""
Basic usage example.

Expose your existing FastAPI application as an A2A-compliant agent.
No changes to existing logic are required.

1. Add @a2a_skill to routes you want to expose.
2. Initialize FastApiA2A.
3. Call a2a.mount() at the end.

Install and run:
    pip install fastapi-a2a uvicorn
    uvicorn main:app --reload
"""
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi_a2a import FastApiA2A, a2a_skill

app = FastAPI()


class RequestData(BaseModel):
    text: str


@app.post("/summarise")
@a2a_skill(
    description="Summarise text to a few key sentences.",
    tags=["nlp", "summary"],
    examples=['{"text": "A very long document..."}'],
)
async def summarise(req: RequestData) -> dict:
    """Takes text, returns a summary."""
    return {"summary": req.text[:50] + "..."}


@app.post("/translate")
@a2a_skill(
    description="Translate text to French.",
    tags=["nlp", "translation"],
)
async def translate(req: RequestData) -> dict:
    """Takes text, returns identical text (mocked)."""
    return {"translated": req.text}


# ── Mount A2A ───────────────────────────────────────────────────────────────
# Do this AFTER all your routes are defined.
a2a = FastApiA2A(
    app,
    name="NLP Toolkit Agent",
    url="https://nlp-agent.example.com",
    version="1.0.0",
    description="A collection of NLP tools including summary and translation.",
)
a2a.mount()

# Check http://127.0.0.1:8000/.well-known/agent.json
