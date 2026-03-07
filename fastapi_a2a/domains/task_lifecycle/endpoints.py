"""
Task Lifecycle Endpoints (§16.2).

Routes:
  GET  /tasks                List tasks for the current agent card
  GET  /tasks/{task_id}      Get a specific task with its messages and artifacts
  POST /tasks/{task_id}/cancel   Request cancellation of a running task
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.task_lifecycle.models import Artifact, Message, Task, TaskSession

router = APIRouter(tags=["Task Lifecycle"], prefix="/tasks")


@router.get("")
async def list_tasks(request: Request) -> dict[str, Any]:
    """List all tasks for the current session / agent card."""
    db: AsyncSession = request.state.db
    agent_card_id: uuid.UUID | None = getattr(request.app.state, "agent_card_id", None)

    stmt = select(Task).order_by(Task.created_at.desc()).limit(100)
    if agent_card_id:
        stmt = (
            select(Task)
            .join(TaskSession, Task.session_id == TaskSession.id)
            .where(TaskSession.agent_card_id == agent_card_id)
            .order_by(Task.created_at.desc())
            .limit(100)
        )

    result = await db.execute(stmt)
    tasks = result.scalars().all()
    return {
        "tasks": [
            {
                "id": str(t.id),
                "status": t.status,
                "skill_id": t.skill_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ],
        "count": len(tasks),
    }


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Get a task with its full message history and artifacts."""
    db: AsyncSession = request.state.db

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.task_id == task_id)
        .order_by(Message.created_at)
    )
    messages = msgs_result.scalars().all()

    arts_result = await db.execute(
        select(Artifact).where(Artifact.task_id == task_id)
    )
    artifacts = arts_result.scalars().all()

    return {
        "id": str(task.id),
        "status": task.status,
        "skill_id": task.skill_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "artifacts": [
            {
                "id": str(a.id),
                "name": a.name,
                "media_type": a.media_type,
            }
            for a in artifacts
        ],
    }


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Request cancellation of a running or submitted task."""
    db: AsyncSession = request.state.db

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Task is already in terminal state: {task.status}",
        )

    task.status = "cancelled"
    await db.commit()
    return {"id": str(task.id), "status": task.status}
