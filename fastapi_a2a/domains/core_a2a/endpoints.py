"""
Core A2A Protocol endpoints:
  - GET  /.well-known/agent.json
  - GET  /.well-known/agent-extended.json
  - POST /rpc  (A2A JSON-RPC: tasks/send, tasks/get, tasks/cancel, tasks/sendSubscribe)
  - GET  /rpc/health
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.core_a2a.models import AgentCard
from fastapi_a2a.domains.core_a2a.schemas import (
    AgentCapabilitiesOut,
    AgentCardOut,
    AgentSkillOut,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    TaskSendParams,
)
from fastapi_a2a.domains.task_lifecycle.models import (
    Message,
    MessagePart,
    Task,
    TaskSession,
)

router = APIRouter(tags=["A2A Protocol"])


# ── Agent Card endpoints ───────────────────────────────────────────────────────

def _build_card_response(card: AgentCard) -> dict[str, Any]:
    skills_out = [
        AgentSkillOut(
            id=skill.id,
            skill_id=skill.skill_id,
            name=skill.name,
            description=skill.description,
            tags=skill.tags,
            examples=skill.examples,
            input_modes=skill.input_modes,
            output_modes=skill.output_modes,
            input_schema=skill.input_schema.json_schema if skill.input_schema else None,
            output_schema=skill.output_schema.json_schema if skill.output_schema else None,
        ).model_dump()
        for skill in (card.skills or [])
    ]

    caps = None
    if card.capabilities:
        caps = AgentCapabilitiesOut(
            streaming=card.capabilities.streaming,
            push_notifications=card.capabilities.push_notifications,
            state_transition_history=card.capabilities.state_transition_history,
            default_input_modes=card.capabilities.default_input_modes,
            default_output_modes=card.capabilities.default_output_modes,
            supports_auth_schemes=card.capabilities.supports_auth_schemes,
        ).model_dump()

    out = AgentCardOut(
        name=card.name,
        description=card.description,
        url=card.url,
        version=card.version,
        documentation_url=card.documentation_url,
        provider={
            "organization": card.provider_org,
            "url": card.provider_url,
        } if card.provider_org or card.provider_url else None,
        capabilities=AgentCapabilitiesOut.model_validate(caps) if caps else None,
        skills=[AgentSkillOut.model_validate(s) for s in skills_out],
        default_input_modes=card.capabilities.default_input_modes if card.capabilities else [],
        default_output_modes=card.capabilities.default_output_modes if card.capabilities else [],
    )
    return out.model_dump(exclude_none=True)


@router.get("/.well-known/agent.json")
async def get_agent_card(request: Request) -> JSONResponse:
    """
    A2A Protocol: serves the Agent Card discovery document.
    """
    db: AsyncSession = request.state.db
    # Find the primary card for this deployment
    card_id: uuid.UUID | None = getattr(request.app.state, "agent_card_id", None)
    if card_id is None:
        raise HTTPException(status_code=503, detail="Agent card not yet initialised")

    result = await db.execute(
        select(AgentCard).where(AgentCard.id == card_id, AgentCard.is_active.is_(True))
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="Agent card not found")

    return JSONResponse(
        content=_build_card_response(card),
        headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
    )


@router.get("/.well-known/agent-extended.json")
async def get_agent_card_extended(request: Request) -> JSONResponse:
    """
    Extended agent card with full skill schemas, executor policies, and SLO refs.
    """
    db: AsyncSession = request.state.db
    card_id: uuid.UUID | None = getattr(request.app.state, "agent_card_id", None)
    if card_id is None:
        raise HTTPException(status_code=503, detail="Agent card not yet initialised")

    result = await db.execute(
        select(AgentCard).where(AgentCard.id == card_id, AgentCard.is_active.is_(True))
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="Agent card not found")

    base = _build_card_response(card)
    # Extended adds quarantine status info (useful for admin tooling)
    base["quarantine_status"] = card.quarantine_status
    base["approval_status"] = card.approval_status
    return JSONResponse(content=base, headers={"Cache-Control": "no-store"})


# ── JSON-RPC Dispatcher ────────────────────────────────────────────────────────

RPC_ERROR_CODES = {
    "method_not_found": -32601,
    "invalid_params": -32602,
    "task_not_found": 404,
    "task_not_cancellable": 409,
    "parse_error": -32700,
}


@router.post("/rpc")
async def rpc_endpoint(request: Request) -> Response:
    """
    A2A JSON-RPC dispatcher.
    Handles: tasks/send, tasks/get, tasks/cancel, tasks/sendSubscribe
    """
    db: AsyncSession = request.state.db

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            JsonRpcResponse(
                error=JsonRpcError(code=-32700, message="Parse error")
            ).model_dump(exclude_none=True),
            status_code=400,
        )

    rpc_req = JsonRpcRequest.model_validate(body)

    if rpc_req.method == "tasks/send":
        return await _handle_tasks_send(rpc_req, db, request)
    elif rpc_req.method == "tasks/get":
        return await _handle_tasks_get(rpc_req, db)
    elif rpc_req.method == "tasks/cancel":
        return await _handle_tasks_cancel(rpc_req, db)
    elif rpc_req.method == "tasks/sendSubscribe":
        return await _handle_tasks_send_subscribe(rpc_req, db, request)
    else:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=-32601, message=f"Method '{rpc_req.method}' not found"),
            ).model_dump(exclude_none=True),
            status_code=404,
        )


async def _handle_tasks_send(rpc_req: JsonRpcRequest, db: AsyncSession, request: Request) -> JSONResponse:
    """Execute tasks/send — creates and synchronously processes a task."""
    try:
        params = TaskSendParams.model_validate(rpc_req.params or {})
    except Exception as exc:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=-32602, message=f"Invalid params: {exc}"),
            ).model_dump(exclude_none=True),
            status_code=422,
        )

    card_id: uuid.UUID = request.app.state.agent_card_id

    # Create or join session
    session_id = uuid.UUID(params.session_id) if params.session_id else None
    if session_id is None:
        session_obj = TaskSession(agent_card_id=card_id)
        db.add(session_obj)
        await db.flush()
        session_id = session_obj.id

    # Create task
    task = Task(
        session_id=session_id,
        agent_card_id=card_id,
        skill_id=params.skill_id,
        status="working",
        started_at=datetime.now(UTC),
        metadata_=params.metadata,
    )
    db.add(task)
    await db.flush()

    # Create message
    msg = Message(task_id=task.id, role="user", sequence_number=1)
    db.add(msg)
    await db.flush()

    for idx, part in enumerate(params.message.parts):
        part_obj = MessagePart(
            message_id=msg.id,
            part_type=part.type,
            content_text=part.text,
            file_url=part.url,
            content_data=part.data,
            file_mime_type=part.mime_type,
            metadata_=part.metadata,
            sort_order=idx,
        )
        db.add(part_obj)

    # Dispatch to skill handler if registered
    handler = getattr(request.app.state, "skill_handlers", {}).get(params.skill_id)
    response_text = "Task received."
    if handler:
        try:
            result = await handler(task.id, params.message.model_dump(), db)
            response_text = result if isinstance(result, str) else json.dumps(result)
            task.status = "completed"
        except Exception as exc:
            task.status = "failed"
            task.error_code = "handler_error"
            task.error_message = str(exc)
    else:
        task.status = "completed"

    task.completed_at = datetime.now(UTC)
    await db.commit()

    return JSONResponse(
        JsonRpcResponse(
            id=rpc_req.id,
            result={
                "id": str(task.id),
                "session_id": str(session_id),
                "status": task.status,
                "messages": [
                    {
                        "role": "agent",
                        "parts": [{"type": "text", "text": response_text}],
                    }
                ],
            },
        ).model_dump(exclude_none=True)
    )


async def _handle_tasks_get(rpc_req: JsonRpcRequest, db: AsyncSession) -> JSONResponse:
    """Execute tasks/get — returns current task state."""
    task_id: str | None = (rpc_req.params or {}).get("id")
    if not task_id:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=-32602, message="Missing 'id' param"),
            ).model_dump(exclude_none=True),
            status_code=422,
        )
    result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
    task = result.scalar_one_or_none()
    if task is None:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=404, message="Task not found"),
            ).model_dump(exclude_none=True),
            status_code=404,
        )
    return JSONResponse(
        JsonRpcResponse(
            id=rpc_req.id,
            result={
                "id": str(task.id),
                "session_id": str(task.session_id) if task.session_id else None,
                "status": task.status,
                "error_code": task.error_code,
                "error_message": task.error_message,
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            },
        ).model_dump(exclude_none=True)
    )


async def _handle_tasks_cancel(rpc_req: JsonRpcRequest, db: AsyncSession) -> JSONResponse:
    """Execute tasks/cancel — cancels a task if cancellable."""
    task_id: str | None = (rpc_req.params or {}).get("id")
    if not task_id:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=-32602, message="Missing 'id' param"),
            ).model_dump(exclude_none=True),
            status_code=422,
        )
    result = await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))
    task = result.scalar_one_or_none()
    if task is None:
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=404, message="Task not found"),
            ).model_dump(exclude_none=True),
            status_code=404,
        )
    if task.status not in ("submitted", "working", "input_required"):
        return JSONResponse(
            JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=409, message=f"Task in status '{task.status}' cannot be cancelled"),
            ).model_dump(exclude_none=True),
            status_code=409,
        )
    task.status = "cancelled"
    task.completed_at = datetime.now(UTC)
    await db.commit()
    return JSONResponse(
        JsonRpcResponse(id=rpc_req.id, result={"id": task_id, "status": "cancelled"}).model_dump(
            exclude_none=True
        )
    )


async def _handle_tasks_send_subscribe(
    rpc_req: JsonRpcRequest, db: AsyncSession, request: Request
) -> StreamingResponse:
    """Execute tasks/sendSubscribe — tasks/send with Server-Sent Events streaming."""

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            params = TaskSendParams.model_validate(rpc_req.params or {})
        except Exception as exc:
            error_event = JsonRpcResponse(
                id=rpc_req.id,
                error=JsonRpcError(code=-32602, message=str(exc)),
            ).model_dump(exclude_none=True)
            yield f"data: {json.dumps(error_event)}\n\n"
            return

        card_id: uuid.UUID = request.app.state.agent_card_id
        task = Task(
            agent_card_id=card_id,
            skill_id=params.skill_id,
            status="working",
            started_at=datetime.now(UTC),
            metadata_=params.metadata,
        )
        db.add(task)
        await db.flush()

        yield f"data: {json.dumps({'status': 'working', 'id': str(task.id)})}\n\n"

        # Emit processing event
        await anyio.sleep(0.05)
        task.status = "completed"
        task.completed_at = datetime.now(UTC)
        await db.commit()

        yield f"data: {json.dumps({'status': 'completed', 'id': str(task.id)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/rpc/health")
async def rpc_health() -> dict[str, str]:
    return {"status": "ok", "protocol": "A2A/1.0"}
