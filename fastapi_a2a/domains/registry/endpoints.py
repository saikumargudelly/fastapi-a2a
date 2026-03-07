"""
Registry Domain Endpoints:
  - POST /registry/register
  - GET  /registry/agents
  - GET  /registry/agents/{agent_id}
  - POST /registry/deregister
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.core_a2a.models import AgentCard
from fastapi_a2a.domains.core_a2a.schemas import RegisterRequest, RegisterResponse
from fastapi_a2a.domains.registry.models import Heartbeat, RegistryEntry

router = APIRouter(tags=["Registry"])


@router.post("/register", response_model=RegisterResponse)
async def register_agent(body: RegisterRequest, request: Request) -> RegisterResponse:
    """
    Self-register an agent card with the discovery registry.
    Priority 1 in the agent's own registry (other registries call GET /.well-known/agent.json).
    """
    db: AsyncSession = request.state.db

    # Look up or create agent card by URL
    result = await db.execute(
        select(AgentCard).where(AgentCard.url == body.card_url)
    )
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(
            status_code=404,
            detail=f"No agent card found at URL: {body.card_url}. "
                   f"Ensure /.well-known/agent.json is served first.",
        )
    if card.quarantine_status == "quarantined":
        raise HTTPException(status_code=403, detail="Agent card is quarantined")

    # Upsert registry entry
    entry_result = await db.execute(
        select(RegistryEntry).where(RegistryEntry.agent_card_id == card.id)
    )
    entry = entry_result.scalar_one_or_none()

    if entry is None:
        entry = RegistryEntry(
            agent_card_id=card.id,
            org_namespace=body.org_namespace,
            visibility=body.visibility,
            primary_region=body.region,
            import_source_type="self_registered",
        )
        db.add(entry)
        await db.flush()

        # Create heartbeat record
        hb = Heartbeat(
            agent_card_id=card.id,
            check_interval_seconds=60,
            is_reachable=True,
            last_seen_at=datetime.now(UTC),
        )
        db.add(hb)
    else:
        entry.visibility = body.visibility
        if body.org_namespace:
            entry.org_namespace = body.org_namespace

    await db.commit()
    return RegisterResponse(
        registry_entry_id=entry.id,
        agent_card_id=card.id,
        status="registered",
        message="Agent successfully registered. Heartbeat monitoring started.",
    )


@router.get("/agents")
async def list_agents(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    visibility: str | None = None,
    org_namespace: str | None = None,
) -> dict:
    """List all discovered agents in the registry."""
    db: AsyncSession = request.state.db

    stmt = (
        select(RegistryEntry, AgentCard, Heartbeat)
        .join(AgentCard, RegistryEntry.agent_card_id == AgentCard.id)
        .outerjoin(Heartbeat, Heartbeat.agent_card_id == AgentCard.id)
        .where(
            AgentCard.is_active.is_(True),
            RegistryEntry.approval_status == "active",
        )
    )
    if visibility:
        stmt = stmt.where(RegistryEntry.visibility == visibility)
    if org_namespace:
        stmt = stmt.where(RegistryEntry.org_namespace == org_namespace)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()

    agents = []
    for entry, card, hb in rows:
        agents.append(
            {
                "registry_entry_id": str(entry.id),
                "agent_card_id": str(card.id),
                "name": card.name,
                "description": card.description,
                "url": card.url,
                "version": card.version,
                "visibility": entry.visibility,
                "org_namespace": entry.org_namespace,
                "primary_region": entry.primary_region,
                "is_reachable": hb.is_reachable if hb else None,
                "last_seen_at": hb.last_seen_at.isoformat() if (hb and hb.last_seen_at) else None,
                "registered_at": entry.registered_at.isoformat(),
            }
        )
    return {"agents": agents, "total": len(agents), "page": page, "page_size": page_size}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: uuid.UUID, request: Request) -> dict:
    """Get a single agent registry entry by registry_entry_id."""
    db: AsyncSession = request.state.db

    result = await db.execute(
        select(RegistryEntry, AgentCard, Heartbeat)
        .join(AgentCard, RegistryEntry.agent_card_id == AgentCard.id)
        .outerjoin(Heartbeat, Heartbeat.agent_card_id == AgentCard.id)
        .where(RegistryEntry.id == agent_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    entry, card, hb = row
    return {
        "registry_entry_id": str(entry.id),
        "agent_card_id": str(card.id),
        "name": card.name,
        "description": card.description,
        "url": card.url,
        "version": card.version,
        "visibility": entry.visibility,
        "org_namespace": entry.org_namespace,
        "primary_region": entry.primary_region,
        "is_reachable": hb.is_reachable if hb else None,
        "last_seen_at": hb.last_seen_at.isoformat() if (hb and hb.last_seen_at) else None,
        "registered_at": entry.registered_at.isoformat(),
    }


@router.post("/deregister")
async def deregister_agent(request: Request) -> dict:
    """
    Deregister an agent from the discovery registry.
    Marks registry_entry.approval_status = 'suspended'.
    """
    db: AsyncSession = request.state.db
    body = await request.json()
    agent_card_id: str | None = body.get("agent_card_id")
    if not agent_card_id:
        raise HTTPException(status_code=422, detail="Missing agent_card_id")

    result = await db.execute(
        select(RegistryEntry).where(RegistryEntry.agent_card_id == uuid.UUID(agent_card_id))
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Registry entry not found")

    entry.approval_status = "suspended"
    await db.commit()
    return {"status": "deregistered", "agent_card_id": agent_card_id}
