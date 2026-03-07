"""
QuerySkill JSON-RPC Method Handler (§16.1).

Handles: a2a.querySkill RPC method
  - Checks if a skill can handle a given input (schema + free-text intent)
  - Returns confidence score + detailed match reasoning
  - Logs to skill_query_log for analytics

Registered in the main /rpc dispatcher by adding this to the method map.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.core_a2a.models import AgentSkill
from fastapi_a2a.domains.safety.models import SkillQueryLog


async def handle_query_skill(
    rpc_req,
    db: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    """
    Handle a2a.querySkill JSON-RPC method.

    Expected params:
      skill_id (str): The skill's string identifier
      input_sample (dict|None): Sample input to validate against schema
      free_text_intent (str|None): Natural-language description of the intent
      required_output_fields (list[str]|None): Fields caller needs in response

    Returns:
      can_handle (bool): Whether the skill can handle this
      confidence_score (float 0–1): Estimated confidence
      match_score (float 0–1): Schema/intent match score
      reasoning (str): Human-readable explanation
      skill (dict): Skill metadata
    """
    params = rpc_req.params or {}
    skill_id_str: str | None = params.get("skill_id")
    input_sample: dict | None = params.get("input_sample")
    free_text_intent: str | None = params.get("free_text_intent")
    required_output_fields: list[str] | None = params.get("required_output_fields")

    card_id: uuid.UUID = request.app.state.agent_card_id

    # Look up the skill
    stmt = select(AgentSkill).where(AgentSkill.agent_card_id == card_id)
    if skill_id_str:
        stmt = stmt.where(AgentSkill.skill_id == skill_id_str)
    result = await db.execute(stmt)
    skills = result.scalars().all()

    if not skills:
        return {
            "can_handle": False,
            "confidence_score": 0.0,
            "match_score": 0.0,
            "reasoning": f"No skill found with id '{skill_id_str}'",
            "skill": None,
        }

    skill = skills[0]

    # Schema validation check
    schema_match_score = 0.0
    schema_reasoning = "No input schema defined."

    if input_sample and skill.input_schema:
        # Simple field presence check (full JSON Schema validation would use jsonschema)
        input_schema = skill.input_schema
        required_fields = input_schema.get("required", [])
        provided_fields = set(input_sample.keys())
        required_set = set(required_fields)
        matched = required_set & provided_fields
        schema_match_score = len(matched) / max(len(required_set), 1)
        schema_reasoning = (
            f"Schema match: {len(matched)}/{len(required_set)} required fields present."
        )
    elif not input_sample:
        schema_match_score = 1.0
        schema_reasoning = "No input sample provided — assuming compatible."

    # Free-text intent matching: naive keyword overlap with description + tags
    intent_match_score = 0.0
    intent_reasoning = "No intent provided."
    if free_text_intent:
        tokens = set(free_text_intent.lower().split())
        desc_tokens = set((skill.description or "").lower().split())
        tag_tokens = set(t.lower() for t in (skill.tags or []))
        combined = desc_tokens | tag_tokens
        overlap = tokens & combined
        intent_match_score = min(1.0, len(overlap) / max(len(tokens), 1))
        intent_reasoning = (
            f"Intent match: {len(overlap)} of {len(tokens)} intent tokens matched "
            f"skill description/tags."
        )

    # Output field check
    output_field_score = 1.0
    if required_output_fields and skill.output_schema:
        output_props = skill.output_schema.get("properties", {})
        available = set(output_props.keys())
        required = set(required_output_fields)
        matched_out = required & available
        output_field_score = len(matched_out) / max(len(required), 1)

    # Aggregate confidence
    confidence = (schema_match_score * 0.4 + intent_match_score * 0.4 + output_field_score * 0.2)
    can_handle = confidence >= 0.5

    # Log to skill_query_log
    caller = getattr(request.scope.get("auth_claims", {}), "get", lambda k, d=None: d)("sub")
    log_entry = SkillQueryLog(
        agent_card_id=card_id,
        skill_id=skill.id,
        caller_identity=caller,
        input_sample=input_sample,
        free_text_intent=free_text_intent,
        required_output_fields=required_output_fields,
        can_handle=can_handle,
        confidence_score=round(confidence, 4),
        match_score=round(schema_match_score, 4),
        response_details={
            "schema_reasoning": schema_reasoning,
            "intent_reasoning": intent_reasoning,
        },
        queried_at=datetime.now(UTC),
    )
    db.add(log_entry)
    await db.commit()

    return {
        "can_handle": can_handle,
        "confidence_score": round(confidence, 4),
        "match_score": round(schema_match_score, 4),
        "reasoning": f"{schema_reasoning} {intent_reasoning}".strip(),
        "skill": {
            "id": str(skill.id),
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "tags": skill.tags,
            "input_modes": skill.input_modes,
            "output_modes": skill.output_modes,
        },
    }
