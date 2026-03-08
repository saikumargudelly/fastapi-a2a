"""
TaskStore ABC.

Implementers guarantee:
  - All methods are coroutines (async def).
  - get() returns None if task_id is unknown. Never raises.
  - update_status() validates the transition via VALID_TRANSITIONS.
    Raises InvalidStateTransitionError on illegal transition.
    Raises TaskNotFoundError if task_id unknown.
    Appends message to task.history if message is not None.   [FIX B5]
  - create() assigns a uuid4 id and 'submitted' state. Never caller's job.
  - add_artifact() raises InvalidStateTransitionError if the task is in a
    terminal state. Artifacts must not be appended to completed tasks.   [FIX B6]
  - All writes are atomic per task_id. Concurrent updates to the same task
    are serialised (implementers must use a per-task lock or equivalent).
  - Timestamps are produced by _internal.utils.utcnow(). Custom stores
    MUST use the same function to preserve list() sort order.

Lifecycle (FIX B1):
  - start() is called by FastApiA2A on application startup.
  - stop()  is called by FastApiA2A on application shutdown.
  - Default implementations are no-ops. Override for eviction loops, pools, etc.
  - FastApiA2A calls start()/stop() unconditionally — no isinstance check.  [FIX B2]
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi_a2a._internal.schema import Artifact, Message, Task
from fastapi_a2a._internal.constants import TaskState


class TaskStore(ABC):
    """Abstract base class for all task persistence backends."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    # FIX B1: start/stop now declared on the ABC so any custom implementation
    # author sees them. Default: no-op.

    async def start(self) -> None:
        """
        Called once on application startup.
        Override to start background tasks, open connection pools, etc.
        Default: no-op.
        """

    async def stop(self) -> None:
        """
        Called once on application shutdown.
        Override to cancel background tasks, close connections, etc.
        Default: no-op.
        """

    # ── CRUD ──────────────────────────────────────────────────────────────────

    @abstractmethod
    async def create(self, context_id: str, message: Message) -> Task:
        """
        Create a new task in 'submitted' state.
        Assigns a uuid4 task id. Sets createdAt and updatedAt to utcnow().
        Stores message as the first entry in history.
        Returns the created Task.
        """

    @abstractmethod
    async def get(self, task_id: str) -> Task | None:
        """Return the Task or None. Never raises on missing id."""

    @abstractmethod
    async def update_status(
        self,
        task_id: str,
        state: TaskState,
        message: Message | None = None,
    ) -> Task:
        """
        Transition task to new state.
        Validates transition via VALID_TRANSITIONS; raises InvalidStateTransitionError.
        Raises TaskNotFoundError if task_id unknown.
        Appends message to task.history if message is not None.  [FIX B5]
        Sets updatedAt to utcnow().
        Returns updated Task.
        """

    @abstractmethod
    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """
        Append artifact to task.artifacts.
        Raises TaskNotFoundError if task_id unknown.
        Raises InvalidStateTransitionError if task is in a TERMINAL_STATE.  [FIX B6]
        Returns updated Task.
        """

    @abstractmethod
    async def list(
        self,
        *,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        """
        Paginated list. Returns (tasks, next_cursor).
        next_cursor is None when no further pages exist.
        Tasks are ordered by createdAt ascending.
        cursor is opaque — callers must not construct or parse it.
        """
