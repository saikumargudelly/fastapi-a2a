"""
Shared internal utilities.

FIX F1: utcnow() was previously private inside stores/memory.py.
Moving it here allows custom TaskStore implementations to produce
timestamps in the exact same ISO 8601 UTC format without re-implementing it,
preserving correct sort order in TaskStore.list() across implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> str:
    """
    Return current UTC time as an ISO 8601 string with millisecond precision.
    Format: ``2024-01-15T10:30:00.123Z``

    Custom TaskStore implementations MUST use this function (or produce the
    identical format) for all timestamp fields (createdAt, updatedAt,
    status.timestamp). Consistent formatting is required for correct
    lexicographic sort in TaskStore.list().
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
