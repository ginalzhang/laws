"""Schedule request routes."""
from __future__ import annotations

import json
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_admin, require_worker
from ..storage import Database

router = APIRouter()
db = Database()


def _req_to_dict(req) -> dict:
    try:
        days = json.loads(req.preferred_days) if req.preferred_days else []
    except Exception:
        days = []
    return {
        "id": req.id,
        "worker_id": req.worker_id,
        "week_of": req.week_of,
        "preferred_days": days,
        "preferred_hours": req.preferred_hours,
        "notes": req.notes,
        "status": req.status,
    }


class ScheduleRequestCreate(BaseModel):
    week_of: str
    preferred_days: List[str] = []
    preferred_hours: str = ""
    notes: str = ""


class ScheduleStatusUpdate(BaseModel):
    status: str  # pending|approved|rejected


@router.post("")
async def create_schedule_request(
    payload: ScheduleRequestCreate,
    user: dict = Depends(require_worker),
):
    req = db.create_schedule_request(
        worker_id=user["user_id"],
        week_of=payload.week_of,
        preferred_days=payload.preferred_days,
        preferred_hours=payload.preferred_hours,
        notes=payload.notes,
    )
    return _req_to_dict(req)


@router.get("")
async def list_schedule_requests(
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if user["role"] == "worker":
        reqs = db.list_schedule_requests(status=status, worker_id=user["user_id"])
    else:
        reqs = db.list_schedule_requests(status=status)
    return [_req_to_dict(r) for r in reqs]


@router.patch("/{req_id}")
async def update_schedule_request(
    req_id: int,
    payload: ScheduleStatusUpdate,
    user: dict = Depends(require_admin),
):
    if payload.status not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "Status must be pending, approved, or rejected")
    db.update_schedule_request(req_id, payload.status)
    return {"ok": True}
