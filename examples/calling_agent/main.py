"""
Call another A2A agent from inside an existing route.

Install and run:
    pip install fastapi-a2a uvicorn
    uvicorn main:app --reload
"""

from fastapi import FastAPI

from fastapi_a2a import A2AClient

app = FastAPI()


@app.post("/pipeline")
async def pipeline(req: dict) -> dict:
    """Summarise text locally, then translate via a remote A2A agent."""
    text = req.get("text", "")

    # Step 1 — your own local logic (no A2A needed)
    summary = text[:100] + ("..." if len(text) > 100 else "")

    # Step 2 — call a completely separate A2A agent.
    # send_task() blocks until the remote task reaches a terminal state.
    # Raises TimeoutError after poll_timeout_seconds (default 300s).
    async with A2AClient(
        "https://translation-agent.example.com",
        poll_timeout_seconds=30,
    ) as agent:
        task = await agent.send_task(
            text=summary,
            skill_id="translate",
            data={"target_lang": req.get("target_lang", "fr")},
        )

    translation = (
        task["artifacts"][0]["parts"][0]["data"].get("translated", "") if task["artifacts"] else ""
    )
    return {"summary": summary, "translation": translation}
