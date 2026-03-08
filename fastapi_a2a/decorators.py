"""
The entry point for exposing skills.

This decorator (`@a2a_skill`) is the literal mechanism that turns a standard
FastAPI route into an A2A-discoverable skill.

We deliberately designed this to be completely passive. It doesn't wrap the
execution of your function or alter its inputs/outputs. It simply tags the
function object with a `_a2a_skill` dictionary containing the metadata.

Later, when `a2a.mount()` is called, our internal scanner hunts down these tags
and wires them up without disturbing your original routes.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def a2a_skill(
    *,
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    # FIX A1: mutable default arguments replaced with None sentinels.
    # list[str] = [] would share a single list object across all call sites.
    tags: list[str] | None = None,
    examples: list[str] | None = None,
) -> Callable[[F], F]:
    """
    Mark an existing FastAPI route as an A2A skill.

    Args:
        id:          Skill identifier. Defaults to slugified function name.
        name:        Human-readable name. Defaults to function name.
        description: What this skill does. Shown in AgentCard.
        tags:        Categorisation tags. E.g. ["nlp", "translation"].
        examples:    Example inputs. Shown in AgentCard for discoverability.

    Usage::

        @app.post("/summarise")
        @a2a_skill(description="Summarise any document", tags=["nlp"])
        async def summarise(req: SummariseRequest) -> SummariseResponse:
            ...

    Decorator order:
        ``@a2a_skill`` must be closer to the function definition than ``@app.post``.
        Python applies decorators bottom-up: ``@a2a_skill`` runs first (attaching
        metadata), then ``@app.post`` runs on the already-decorated function and
        registers the route. ``route.endpoint._a2a_skill`` is therefore always set.

    FIX F2: previous docstring incorrectly stated "FastAPI registers the route
    first" — registration happens second, after ``@a2a_skill``.
    The example code was always correct; only the explanation was wrong.
    """

    def decorator(func: F) -> F:
        func._a2a_skill = {  # type: ignore[attr-defined]
            "id": id,
            "name": name,
            "description": description,
            # FIX A1: copy the list so callers sharing None get fresh copies
            "tags": list(tags) if tags is not None else [],
            "examples": list(examples) if examples is not None else [],
        }
        # We do NOT call functools.wraps / update_wrapper here because we return
        # the exact same function object — no wrapper function is introduced.
        # FastAPI's signature inspection therefore works unchanged.
        return func

    return decorator
