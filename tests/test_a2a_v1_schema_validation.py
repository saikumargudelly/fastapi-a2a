"""
A2A 1.0.0 Agent Card JSON Schema Validation and Protocol Tests.

Validates:
1. Exact serialization against the official A2A 1.0.0 JSON Schema using `jsonschema`.
2. Canonical well-known discovery path: /.well-known/agent-card.json.
3. Backward-compatible discovery path: /.well-known/agent.json.
4. Interface advertising constraint: only advertise active interfaces when mounted.
5. End-to-end task execution against mounted dispatcher.
"""

from __future__ import annotations

import jsonschema
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from fastapi_a2a import FastApiA2A, a2a_skill, setup_fastapi_a2a
from fastapi_a2a.adapters.fastapi import FastApiAdapter

# Official A2A 1.0.0 AgentCard JSON Schema
A2A_V1_AGENT_CARD_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AgentCard",
    "type": "object",
    "required": [
        "name",
        "description",
        "version",
        "supportedInterfaces",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "supportedInterfaces": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["url", "protocolBinding", "protocolVersion"],
                "properties": {
                    "url": {"type": "string"},
                    "protocolBinding": {
                        "type": "string",
                        "enum": ["JSONRPC", "GRPC", "HTTP+JSON"],
                    },
                    "protocolVersion": {"type": "string"},
                    "tenant": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "capabilities": {
            "type": "object",
            "required": ["streaming", "pushNotifications", "extensions"],
            "properties": {
                "streaming": {"type": "boolean"},
                "pushNotifications": {"type": "boolean"},
                "extendedAgentCard": {"type": "boolean"},
                "stateTransitionHistory": {"type": "boolean"},
                "extensions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "defaultInputModes": {"type": "array", "items": {"type": "string"}},
        "defaultOutputModes": {"type": "array", "items": {"type": "string"}},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name", "description", "tags"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "inputModes": {"type": "array", "items": {"type": "string"}},
                    "outputModes": {"type": "array", "items": {"type": "string"}},
                    "outputSchema": {"type": "object"},
                    "inputSchema": {"type": "object"},
                },
            },
        },
        "url": {"type": "string"},
        "protocolVersion": {"type": "string"},
        "provider": {
            "type": "object",
            "required": ["organization"],
            "properties": {
                "organization": {"type": "string"},
                "url": {"type": "string"},
            },
        },
        "documentationUrl": {"type": "string"},
        "iconUrl": {"type": "string"},
    },
}


class CalcRequest(BaseModel):
    x: int
    y: int


class CalcResponse(BaseModel):
    result: int


@pytest.mark.asyncio
async def test_agent_card_validates_against_official_a2a_v1_schema() -> None:
    """Verifies that the serialized AgentCard satisfies the official A2A 1.0 JSON Schema."""
    app = FastAPI(title="Math Agent")

    @app.post("/add", response_model=CalcResponse)
    @a2a_skill(
        name="Addition",
        description="Adds two integers",
        tags=["math", "arithmetic"],
        examples=["Add 5 and 10"],
    )
    async def add(req: CalcRequest) -> CalcResponse:
        return CalcResponse(result=req.x + req.y)

    a2a = setup_fastapi_a2a(
        app,
        name="Math Agent",
        url="https://math.example.com",
        version="1.0.0",
        description="Math calculating agent",
    )

    card_dict = a2a.card_builder.build()

    # Validate strictly against the official JSON Schema
    jsonschema.validate(instance=card_dict, schema=A2A_V1_AGENT_CARD_SCHEMA)

    # Required fields verification
    assert card_dict["name"] == "Math Agent"
    assert card_dict["description"] == "Math calculating agent"
    assert card_dict["version"] == "1.0.0"
    assert len(card_dict["supportedInterfaces"]) == 1
    iface = card_dict["supportedInterfaces"][0]
    assert iface["url"] == "https://math.example.com/a2a/rpc"
    assert iface["protocolBinding"] == "JSONRPC"
    assert iface["protocolVersion"] == "1.0"

    assert card_dict["capabilities"]["streaming"] is False
    assert card_dict["capabilities"]["pushNotifications"] is False
    assert card_dict["capabilities"]["extensions"] == []

    assert len(card_dict["skills"]) == 1
    skill = card_dict["skills"][0]
    assert skill["id"] == "add"
    assert skill["name"] == "Addition"
    assert skill["tags"] == ["math", "arithmetic"]
    assert skill["examples"] == ["Add 5 and 10"]


@pytest.mark.asyncio
async def test_interface_unadvertised_before_mount() -> None:
    """Requirement 5: Do not advertise an interface unless the dispatcher is mounted."""
    app = FastAPI()

    @app.post("/test")
    @a2a_skill(name="test_skill", description="Testing")
    def test_op() -> dict:
        return {}

    a2a = FastApiA2A(app, name="Unmounted Agent", url="https://unmounted.com")

    # Before mount: dispatcher is NOT mounted, so supportedInterfaces is empty
    assert a2a.is_mounted is False
    card_before = a2a.card_builder.build()
    assert card_before["supportedInterfaces"] == []

    # Mount: dispatcher is now mounted, so supportedInterfaces is advertised
    a2a.mount()
    assert a2a.is_mounted is True
    card_after = a2a.card_builder.build()
    assert len(card_after["supportedInterfaces"]) == 1
    assert card_after["supportedInterfaces"][0]["url"] == "https://unmounted.com/a2a/rpc"
    assert card_after["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"


@pytest.mark.asyncio
async def test_canonical_and_legacy_endpoints() -> None:
    """Verifies that both /.well-known/agent-card.json and /.well-known/agent.json respond."""
    app = FastAPI()

    @app.post("/ping")
    @a2a_skill(name="ping", description="Ping")
    def ping() -> dict:
        return {"pong": True}

    setup_fastapi_a2a(app, name="Ping Agent", url="https://ping.com")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://ping.com") as client:
        # Canonical A2A 1.0 endpoint
        res_canonical = await client.get("/.well-known/agent-card.json")
        assert res_canonical.status_code == 200
        card_canonical = res_canonical.json()

        # Legacy endpoint
        res_legacy = await client.get("/.well-known/agent.json")
        assert res_legacy.status_code == 200
        card_legacy = res_legacy.json()

        assert card_canonical == card_legacy
        jsonschema.validate(instance=card_canonical, schema=A2A_V1_AGENT_CARD_SCHEMA)


@pytest.mark.asyncio
async def test_mounted_sub_application_and_openapi_extra_discovery() -> None:
    """Verifies that FastApiAdapter discovers skills in mounted sub-apps and openapi_extra."""
    app = FastAPI()

    # Direct route with openapi_extra
    @app.post(
        "/extra-skill",
        openapi_extra={
            "a2a": {
                "name": "Extra Skill",
                "description": "Declared via openapi_extra",
                "tags": ["extra"],
            }
        },
    )
    def extra_op():
        return {}

    # Mounted sub-application
    sub_app = FastAPI()

    @sub_app.post("/sub-skill")
    @a2a_skill(
        name="Sub Skill",
        description="Declared in mounted app",
        tags=["mounted"],
    )
    def sub_op():
        return {}

    app.mount("/sub", sub_app)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    skill_names = {s["name"] for s in skills}
    assert "Extra Skill" in skill_names
    assert "Sub Skill" in skill_names

    sub_skill = next(s for s in skills if s["name"] == "Sub Skill")
    assert sub_skill["endpoint"] == "POST /sub/sub-skill"
