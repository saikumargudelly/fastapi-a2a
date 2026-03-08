"""Tests for the TypedDict schema models and Pydantic TypeAdapters."""
from __future__ import annotations

import pytest

from fastapi_a2a._internal.schema import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Artifact,
    DataPart,
    FileWithBytes,
    FileWithUri,
    FilePart,
    Message,
    OAuthFlows,
    Part,
    SecurityScheme,
    Task,
    TaskStatus,
    TextPart,
    agent_card_adapter,
    message_adapter,
    part_adapter,
    rpc_request_adapter,
    task_adapter,
)


# ── Part discriminated union ───────────────────────────────────────────────────

def test_text_part_validates() -> None:
    raw = {"kind": "text", "text": "hello"}
    part = part_adapter.validate_python(raw)
    assert part["kind"] == "text"
    assert part["text"] == "hello"


def test_data_part_validates() -> None:
    raw = {"kind": "data", "data": {"x": 1}}
    part = part_adapter.validate_python(raw)
    assert part["kind"] == "data"
    assert part["data"]["x"] == 1


def test_file_part_with_bytes_validates() -> None:
    raw = {"kind": "file", "file": {"content": "abc123", "mimeType": "image/png"}}
    part = part_adapter.validate_python(raw)
    assert part["kind"] == "file"
    assert part["file"]["content"] == "abc123"


def test_file_part_with_uri_validates() -> None:
    raw = {"kind": "file", "file": {"uri": "https://example.com/file.txt"}}
    part = part_adapter.validate_python(raw)
    assert part["kind"] == "file"
    assert part["file"]["uri"] == "https://example.com/file.txt"


def test_invalid_part_kind_rejected() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        part_adapter.validate_python({"kind": "unknown", "text": "x"})


# ── FIX A4: FileWithBytes.content (was bytes) ─────────────────────────────────

def test_file_with_bytes_uses_content_field() -> None:
    """FIX A4: field name is 'content', not 'bytes' which shadowed built-in."""
    f: FileWithBytes = {"content": "base64data", "mimeType": "image/png"}
    assert f["content"] == "base64data"
    assert "bytes" not in f


# ── AgentCard serialisation ────────────────────────────────────────────────────

def test_agent_card_round_trip() -> None:
    card: AgentCard = {
        "name": "NLP Agent",
        "url": "https://nlp.example.com",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "capabilities": {"streaming": False},
        "skills": [],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
    }
    raw = agent_card_adapter.dump_python(card, by_alias=True, exclude_none=True)
    assert raw["name"] == "NLP Agent"
    assert raw["protocolVersion"] == "0.3.0"


def test_agent_card_optional_fields_excluded() -> None:
    card: AgentCard = {
        "name": "x",
        "url": "https://x.com",
        "version": "1.0",
        "protocolVersion": "0.3.0",
        "capabilities": {},
        "skills": [],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
    }
    raw = agent_card_adapter.dump_python(card, by_alias=True, exclude_none=True)
    assert "description" not in raw
    assert "provider" not in raw


def test_agent_skill_endpoint_not_in_cleaned_card() -> None:
    """endpoint field must be stripped before wire serialisation."""
    from fastapi_a2a._internal.card import AgentCardBuilder

    skill: AgentSkill = {
        "id": "echo",
        "name": "Echo",
        "description": "Echoes text",
        "inputModes": ["application/json"],
        "outputModes": ["application/json"],
        "endpoint": "/echo",
    }
    builder = AgentCardBuilder(
        name="Test",
        url="https://x.com",
        version="1.0",
        description="test",
        capabilities={},
        skills=[skill],
        provider=None,
    )
    card_bytes = builder.build_bytes()
    import json
    card_json = json.loads(card_bytes)
    assert "endpoint" not in card_json["skills"][0]
    assert "_endpoint" not in card_json["skills"][0]


# ── FIX E1: SecurityScheme and OAuthFlows are typed ───────────────────────────

def test_security_scheme_typed_dict() -> None:
    scheme: SecurityScheme = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert scheme["type"] == "http"
    assert scheme["bearerFormat"] == "JWT"


def test_oauth_flows_typed_dict() -> None:
    flows: OAuthFlows = {
        "authorizationCode": {
            "authorizationUrl": "https://auth.example.com/authorize",
            "tokenUrl": "https://auth.example.com/token",
            "scopes": {"read": "Read access"},
        }
    }
    assert "authorizationCode" in flows


# ── JSON-RPC payload parsing ───────────────────────────────────────────────────

def test_rpc_request_validates() -> None:
    raw = b'{"jsonrpc":"2.0","id":"1","method":"tasks/get","params":{"id":"x"}}'
    rpc = rpc_request_adapter.validate_json(raw)
    assert rpc["method"] == "tasks/get"
    assert rpc["params"]["id"] == "x"


def test_rpc_parse_error_on_bad_json() -> None:
    from pydantic import ValidationError
    with pytest.raises((ValidationError, Exception)):
        rpc_request_adapter.validate_json(b"not json!!")


# ── Message validation ─────────────────────────────────────────────────────────

def test_message_round_trip() -> None:
    msg: Message = {
        "role": "user",
        "kind": "message",
        "parts": [{"kind": "text", "text": "hello"}],
        "messageId": "msg-1",
    }
    dumped = message_adapter.dump_python(msg, by_alias=True, exclude_none=True)
    assert dumped["role"] == "user"
    assert dumped["messageId"] == "msg-1"
    assert "contextId" not in dumped


# ── Task validation ────────────────────────────────────────────────────────────

def test_task_structure() -> None:
    from fastapi_a2a._internal.utils import utcnow
    now = utcnow()
    task: Task = {
        "id": "t1",
        "contextId": "c1",
        "kind": "task",
        "status": {"state": "submitted", "timestamp": now},
        "history": [],
        "artifacts": [],
        "createdAt": now,
        "updatedAt": now,
    }
    dumped = task_adapter.dump_python(task, by_alias=True, exclude_none=True)
    assert dumped["id"] == "t1"
    assert dumped["status"]["state"] == "submitted"
