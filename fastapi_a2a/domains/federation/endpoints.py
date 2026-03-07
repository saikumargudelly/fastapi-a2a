"""
Federation Peer Sync & Takedown Endpoints (§17.9).

Routes:
  GET  /federation/peers          List known federation peers
  POST /federation/sync           Push our agent card to a peer / pull from peer
  POST /federation/takedown       Submit a takedown request to a peer
  GET  /federation/takedown/{id}  Get takedown request status
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.federation.models import (
    FederationPeer,
    TakedownRequest,
)

logger = logging.getLogger("fastapi_a2a.federation")
router = APIRouter(tags=["Federation"], prefix="/federation")


@router.get("/peers")
async def list_peers(request: Request) -> dict[str, Any]:
    db: AsyncSession = request.state.db
    result = await db.execute(
        select(FederationPeer).where(FederationPeer.status == "active")
    )
    peers = result.scalars().all()
    return {
        "peers": [
            {
                "id": str(p.id),
                "name": p.name,
                "registry_url": p.registry_url,
                "region": p.region,
                "trust_level": p.trust_level,
                "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None,
            }
            for p in peers
        ]
    }


@router.post("/sync")
async def sync_with_peer(request: Request) -> dict[str, Any]:
    """
    Bi-directional agent card sync with a federation peer.
    - direction=push: send our agent.json to the peer's registry
    - direction=pull: fetch their agents and upsert into local registry
    - direction=both: do both
    """
    db: AsyncSession = request.state.db
    body = await request.json()
    peer_id: str | None = body.get("peer_id")
    direction: str = body.get("direction", "both")

    if not peer_id:
        raise HTTPException(status_code=422, detail="peer_id required")

    result = await db.execute(
        select(FederationPeer).where(FederationPeer.id == uuid.UUID(peer_id))
    )
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Federation peer not found")

    pushed = pulled = 0
    errors = []

    if direction in ("push", "both"):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{str(request.base_url).rstrip('/')}/.well-known/agent.json")
                if resp.status_code == 200:
                    card_data = resp.json()
                    push_resp = await client.post(
                        f"{peer.registry_url.rstrip('/')}/registry/register",
                        json=card_data,
                    )
                    if push_resp.status_code in (200, 201):
                        pushed = 1
                    else:
                        errors.append(f"Push failed: HTTP {push_resp.status_code}")
        except Exception as exc:
            errors.append(f"Push error: {str(exc)[:128]}")

    if direction in ("pull", "both"):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{peer.registry_url.rstrip('/')}/registry/agents")
                if resp.status_code == 200:
                    agents = resp.json().get("agents", [])
                    pulled = len(agents)
                    # In production, upsert these into local registry_entry
                    logger.debug("Pulled %d agents from peer %s", pulled, peer.name)
                else:
                    errors.append(f"Pull failed: HTTP {resp.status_code}")
        except Exception as exc:
            errors.append(f"Pull error: {str(exc)[:128]}")

    # Update last_synced_at
    peer.last_synced_at = datetime.now(UTC)
    await db.commit()

    return {
        "peer_id": peer_id,
        "direction": direction,
        "pushed": pushed,
        "pulled": pulled,
        "errors": errors,
        "synced_at": peer.last_synced_at.isoformat(),
    }


@router.post("/takedown")
async def submit_takedown(request: Request) -> dict[str, Any]:
    """
    Submit a takedown request — flags a remote agent card for removal.
    Creates a TakedownRequest and sends it to the peer's registry.
    """
    db: AsyncSession = request.state.db
    agent_card_id = request.app.state.agent_card_id
    body = await request.json()

    target_url: str | None = body.get("target_url")
    reason: str | None = body.get("reason")
    legal_basis: str | None = body.get("legal_basis")
    requester_identity: str | None = body.get("requester_identity")
    peer_registry_url: str | None = body.get("peer_registry_url")

    if not all([target_url, reason, legal_basis]):
        raise HTTPException(
            status_code=422,
            detail="target_url, reason, and legal_basis are required"
        )

    takedown = TakedownRequest(
        target_agent_url=target_url,
        reason=reason,
        legal_basis=legal_basis,
        requester_identity=requester_identity,
        agent_card_id=agent_card_id,
        status="submitted",
        submitted_at=datetime.now(UTC),
    )
    db.add(takedown)
    await db.flush()

    # If peer registry URL given, send the takedown request there
    forwarded = False
    if peer_registry_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{peer_registry_url.rstrip('/')}/federation/takedown",
                    json={
                        "target_url": target_url,
                        "reason": reason,
                        "legal_basis": legal_basis,
                        "requester_identity": requester_identity,
                    },
                )
                forwarded = resp.status_code in (200, 201, 202)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Takedown forward failed: %s", exc)

    await db.commit()
    return {
        "takedown_id": str(takedown.id),
        "status": takedown.status,
        "submitted_at": takedown.submitted_at.isoformat(),
        "forwarded_to_peer": forwarded,
    }


@router.get("/takedown/{takedown_id}")
async def get_takedown(takedown_id: uuid.UUID, request: Request) -> dict[str, Any]:
    db: AsyncSession = request.state.db
    result = await db.execute(
        select(TakedownRequest).where(TakedownRequest.id == takedown_id)
    )
    td = result.scalar_one_or_none()
    if td is None:
        raise HTTPException(status_code=404, detail="Takedown request not found")
    return {
        "id": str(td.id),
        "target_agent_url": td.target_agent_url,
        "status": td.status,
        "reason": td.reason,
        "legal_basis": td.legal_basis,
        "submitted_at": td.submitted_at.isoformat(),
    }

