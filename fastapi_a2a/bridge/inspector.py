"""
FastAPI Bridge — Route Inspector.
Introspects FastAPI routes and derives AgentSkill + SkillSchema objects automatically.
"""
from __future__ import annotations

import hashlib
import inspect
from typing import Any, get_args, get_origin

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel


def _pydantic_to_jsonschema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic v2 model to a JSON Schema dict."""
    return model.model_json_schema()


def _get_type_schema(annotation: Any) -> dict[str, Any] | None:
    """Resolve a route parameter/return annotation to a JSON Schema dict."""
    if annotation is inspect.Parameter.empty or annotation is None:
        return None
    origin = get_origin(annotation)
    if origin is not None:
        # Handle Optional[X], List[X] etc — generate a basic schema
        args = get_args(annotation)
        if args and hasattr(args[0], "model_json_schema"):
            return args[0].model_json_schema()
        return None
    if hasattr(annotation, "model_json_schema"):
        return annotation.model_json_schema()
    return None


def inspect_routes(app: FastAPI) -> list[dict[str, Any]]:
    """
    Introspect all APIRoutes in a FastAPI app and return a list of skill dicts.
    Each dict has: skill_id, name, description, tags, input_schema, output_schema,
    input_modes, output_modes, examples.
    """
    skills: list[dict[str, Any]] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        # Skip A2A internal endpoints (added by FastApiA2A.mount())
        if any(
            route.path.startswith(prefix)
            for prefix in ["/.well-known", "/registry", "/rpc", "/admin"]
        ):
            continue

        endpoint = route.endpoint
        sig = inspect.signature(endpoint)

        # Derive input schema from first non-standard parameter with a Pydantic annotation
        input_schema = None
        for param in sig.parameters.values():
            if param.name in ("request", "response", "background_tasks"):
                continue
            schema = _get_type_schema(param.annotation)
            if schema:
                input_schema = schema
                break

        # Derive output schema from return annotation
        output_schema = _get_type_schema(sig.return_annotation)

        # Build skill_id from path + methods (deterministic)
        methods = sorted(route.methods or {"GET"})
        raw = f"{','.join(methods)}:{route.path}"
        skill_id = hashlib.sha256(raw.encode()).hexdigest()[:32]

        name = (
            route.name.replace("_", " ").title()
            if route.name
            else route.path.strip("/").replace("/", " ").title()
        )
        description = (
            inspect.getdoc(endpoint) or f"Auto-discovered from {','.join(methods)} {route.path}"
        )

        skills.append(
            {
                "skill_id": skill_id,
                "name": name,
                "description": description,
                "tags": list(route.tags or []),
                "examples": [],
                "input_modes": ["application/json"],
                "output_modes": ["application/json"],
                "input_schema": input_schema,
                "output_schema": output_schema,
                "path": route.path,
                "methods": methods,
            }
        )
    return skills
