"""
Custom TaskStore example.

Shows how to plug in your own storage backend (Postgres, Redis, DynamoDB, …)
by implementing the TaskStore ABC.

Key implementation rules:
  - Use fastapi_a2a._internal.utils.utcnow() for ALL timestamps so that
    list() sort order is consistent across implementations.
  - Implement start() / stop() if you open connections or background tasks.
  - add_artifact() MUST raise InvalidStateTransitionError on terminal tasks.
  - update_status() MUST append message to task["history"] when not None.

Install and run:
    pip install fastapi-a2a uvicorn
    uvicorn main:app --reload
"""
from fastapi import FastAPI

from fastapi_a2a import FastApiA2A, InvalidStateTransitionError, TaskStore, a2a_skill
from fastapi_a2a._internal.schema import Artifact, Message, Task, TaskState
from fastapi_a2a._internal.utils import utcnow  # always use this for timestamps

app = FastAPI()


class MyPostgresTaskStore(TaskStore):
    """
    Skeleton — replace method bodies with real DB calls.
    Uses asyncpg / SQLAlchemy async / tortoise-orm, etc.
    """

    async def start(self) -> None:
        """Open connection pool on startup (called by FastApiA2A)."""
        # self._pool = await asyncpg.create_pool(dsn=DATABASE_URL)
        ...

    async def stop(self) -> None:
        """Close pool on shutdown (called by FastApiA2A)."""
        # await self._pool.close()
        ...

    async def create(self, context_id: str, message: Message) -> Task:
        now = utcnow()
        import uuid
        task: Task = {
            "id": str(uuid.uuid4()),
            "contextId": context_id,
            "kind": "task",
            "status": {"state": "submitted", "timestamp": now},
            "history": [message],
            "artifacts": [],
            "createdAt": now,
            "updatedAt": now,
        }
        # INSERT INTO tasks …
        return task

    async def get(self, task_id: str) -> Task | None:
        # SELECT * FROM tasks WHERE id = $1
        return None

    async def update_status(
        self,
        task_id: str,
        state: TaskState,
        message: Message | None = None,
    ) -> Task:
        # UPDATE tasks SET status = $1, updated_at = $2 WHERE id = $3
        # If message is not None, also INSERT INTO task_history …
        raise NotImplementedError

    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        # Must raise InvalidStateTransitionError if task is in terminal state.
        raise NotImplementedError

    async def list(
        self,
        *,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        # SELECT * FROM tasks WHERE … ORDER BY created_at LIMIT $1 …
        return [], None


@app.post("/classify")
@a2a_skill(description="Classify text into categories.", tags=["ml"])
async def classify(req: dict) -> dict:
    """Example skill — replace with your real model."""
    return {"category": "sports", "confidence": 0.91}


# ── Mount A2A with the custom store ───────────────────────────────────────────
a2a = FastApiA2A(
    app,
    name="Classifier Agent",
    url="https://classifier.example.com",
    store=MyPostgresTaskStore(),   # ← plug in your own store
)
a2a.mount()

# Run: uvicorn main:app --reload
