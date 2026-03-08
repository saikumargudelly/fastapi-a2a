"""
A2A protocol constants.

These are facts about the protocol spec — not configuration.
Never make these user-configurable.
"""
from __future__ import annotations

from typing import Final, Literal, get_args

# ── Protocol version ──────────────────────────────────────────────────────────

PROTOCOL_VERSION: Final = "0.3.0"
SUPPORTED_VERSIONS: Final = frozenset({"0.3.0"})

# ── JSON-RPC 2.0 standard error codes ────────────────────────────────────────

PARSE_ERROR: Final      = -32700
INVALID_REQUEST: Final  = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final   = -32602
INTERNAL_ERROR: Final   = -32603

# ── A2A protocol extension codes ─────────────────────────────────────────────

TASK_NOT_FOUND: Final           = -32001
TASK_NOT_CANCELABLE: Final      = -32002
AUTH_REQUIRED: Final            = -32003
UNSUPPORTED_OPERATION: Final    = -32004
INVALID_STATE_TRANSITION: Final = -32005
PUSH_NOT_SUPPORTED: Final       = -32006
VERSION_NOT_SUPPORTED: Final    = -32007

# ── Task state machine ────────────────────────────────────────────────────────

TaskState = Literal[
    "submitted",
    "working",
    "input-required",
    "artifact-updated",
    "completed",
    "canceled",
    "failed",
    "rejected",
]

ALL_STATES: Final      = frozenset(get_args(TaskState))
TERMINAL_STATES: Final = frozenset({"completed", "canceled", "failed", "rejected"})

VALID_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "submitted":        frozenset({"working", "canceled", "rejected"}),
    "working":          frozenset({"input-required", "artifact-updated",
                                   "completed", "failed", "canceled"}),
    "input-required":   frozenset({"working", "canceled"}),
    "artifact-updated": frozenset({"artifact-updated", "completed",
                                   "failed", "canceled"}),
    "completed":        frozenset(),
    "canceled":         frozenset(),
    "failed":           frozenset(),
    "rejected":         frozenset(),
}
