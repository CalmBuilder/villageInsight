from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select, text

from village_insight.api.dependencies import Database
from village_insight.config import get_settings
from village_insight.db.models import Job, JobStatus
from village_insight.resources import read_memory_snapshot

router = APIRouter(tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(database: Database) -> dict[str, str]:
    try:
        database.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@router.get("/capacity")
def capacity(database: Database) -> dict[str, object]:
    settings = get_settings()
    memory = read_memory_snapshot()
    rows = database.execute(
        select(Job.kind, Job.status, func.count())
        .where(Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
        .group_by(Job.kind, Job.status)
    ).all()
    counts = {
        f"{kind}:{status}": count
        for kind, status, count in rows
    }
    return {
        "lanes": {
            "parse": settings.parse_workers,
            "hermes": settings.hermes_workers,
            "materialize": settings.materialize_workers,
        },
        "queued": {
            "parse": counts.get("PROFILE_FILE:pending", 0)
            + counts.get("MATCH_TEMPLATE:pending", 0),
            "hermes": counts.get("RECOGNIZE_TEMPLATE_DIFF:pending", 0),
            "materialize": counts.get("MATERIALIZE_FILE:pending", 0),
        },
        "running": {
            "parse": counts.get("PROFILE_FILE:running", 0)
            + counts.get("MATCH_TEMPLATE:running", 0),
            "hermes": counts.get("RECOGNIZE_TEMPLATE_DIFF:running", 0),
            "materialize": counts.get("MATERIALIZE_FILE:running", 0),
        },
        "resources": {
            "available_memory_mb": memory.available_mb if memory is not None else None,
            "total_memory_mb": memory.total_mb if memory is not None else None,
            "admission_floor_mb": settings.worker_min_available_memory_mb,
            "admission_paused": (
                memory is not None
                and memory.available_mb < settings.worker_min_available_memory_mb
            ),
        },
    }
