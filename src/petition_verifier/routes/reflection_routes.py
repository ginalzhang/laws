from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_manager
from ..storage import db

router = APIRouter(prefix="/reflections", tags=["reflections"])


class ReflectionPayload(BaseModel):
    shift_id: int | None = None
    sigs_reported: int
    hours_worked: float
    hit_goal: bool
    reflection: str = ""
    notes: str = ""


@router.post("")
async def submit_reflection(payload: ReflectionPayload, user=Depends(get_current_user)):
    if payload.sigs_reported < 0:
        raise HTTPException(400, "sigs_reported must be non-negative")
    if payload.hours_worked <= 0:
        raise HTTPException(400, "hours_worked must be positive")
    rid = db.save_shift_reflection(
        worker_id=user["user_id"],
        shift_id=payload.shift_id,
        sigs_reported=payload.sigs_reported,
        hours_worked=payload.hours_worked,
        hit_goal=payload.hit_goal,
        reflection=payload.reflection,
        notes=payload.notes,
    )
    return {"id": rid, "ok": True}


@router.get("/mine")
async def my_reflections(user=Depends(get_current_user)):
    rows = db.get_worker_reflections(user["user_id"])
    return [_fmt(r) for r in rows]


@router.get("")
async def all_reflections(user=Depends(require_manager)):
    rows = db.get_all_reflections()
    workers = {u.id: u.full_name for u in db.list_users()}
    result = []
    for r in rows:
        d = _fmt(r)
        d["worker_name"] = workers.get(r.worker_id, f"user {r.worker_id}")
        result.append(d)
    return result


def _fmt(r) -> dict:
    return {
        "id":            r.id,
        "worker_id":     r.worker_id,
        "shift_id":      r.shift_id,
        "sigs_reported": r.sigs_reported,
        "hours_worked":  r.hours_worked,
        "hit_goal":      r.hit_goal,
        "reflection":    r.reflection,
        "notes":         r.notes,
        "created_at":    r.created_at.isoformat() if r.created_at else None,
    }
