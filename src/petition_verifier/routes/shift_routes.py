"""Shift / clock-in / clock-out routes — admin manages worker time."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_admin, require_manager, require_worker
from ..storage import Database

router = APIRouter()
db = Database()


def _shift_to_dict(shift) -> dict:
    hours = None
    if shift.clock_out:
        delta = shift.clock_out - shift.clock_in
        hours = round(delta.total_seconds() / 3600.0, 2)
    worker = db.get_user_by_id(shift.worker_id)
    return {
        "id":          shift.id,
        "worker_id":   shift.worker_id,
        "worker_name": worker.full_name if worker else "",
        "clock_in":    shift.clock_in.isoformat(),
        "clock_out":   shift.clock_out.isoformat() if shift.clock_out else None,
        "hours":       hours,
        "is_weekend":  shift.is_weekend,
        "approved":    shift.approved,
        "approved_by": shift.approved_by,
        "notes":       shift.notes,
    }


class ManualShiftRequest(BaseModel):
    worker_id: int
    clock_in: str   # ISO datetime, e.g. "2024-04-24T09:00:00"
    clock_out: str  # ISO datetime


@router.post("/manual")
async def add_manual_shift(payload: ManualShiftRequest, user: dict = Depends(require_manager)):
    """Admin logs a completed shift with explicit start/end times."""
    worker = db.get_user_by_id(payload.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    try:
        clock_in  = datetime.fromisoformat(payload.clock_in)
        clock_out = datetime.fromisoformat(payload.clock_out)
    except ValueError:
        raise HTTPException(400, "Invalid datetime format — use ISO 8601 (e.g. 2024-04-24T09:00:00)")
    if clock_out <= clock_in:
        raise HTTPException(400, "clock_out must be after clock_in")
    shift = db.add_manual_shift(payload.worker_id, clock_in, clock_out)
    return _shift_to_dict(shift)


class ClockAtRequest(BaseModel):
    worker_id: int
    clock_in: str  # ISO datetime


@router.post("/clock-in-at")
async def clock_in_at(payload: ClockAtRequest, user: dict = Depends(require_manager)):
    """Schedule a worker to start at a specific time (no clock-out yet)."""
    worker = db.get_user_by_id(payload.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    try:
        clock_in_dt = datetime.fromisoformat(payload.clock_in)
    except ValueError:
        raise HTTPException(400, "Invalid datetime format")
    existing = db.get_active_shift(payload.worker_id)
    if existing:
        raise HTTPException(400, f"{worker.full_name} is already clocked in")
    with db._Session() as session:
        from ..storage.database import ShiftRow
        shift = ShiftRow(
            worker_id=payload.worker_id,
            clock_in=clock_in_dt,
            is_weekend=clock_in_dt.weekday() >= 5,
        )
        session.add(shift)
        session.commit()
        session.refresh(shift)
        session.expunge(shift)
    return _shift_to_dict(shift)


class ClockOutAtRequest(BaseModel):
    worker_id: int
    clock_out: str  # ISO datetime, e.g. "2024-04-24T17:30:00"


@router.post("/clock-out-at")
async def clock_out_at(payload: ClockOutAtRequest, user: dict = Depends(require_manager)):
    """Admin clocks a worker out at a specific time (not necessarily now)."""
    worker = db.get_user_by_id(payload.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    try:
        clock_out_dt = datetime.fromisoformat(payload.clock_out)
    except ValueError:
        raise HTTPException(400, "Invalid datetime format — use ISO 8601 (e.g. 2024-04-24T17:30:00)")
    try:
        shift = db.clock_out_at(payload.worker_id, clock_out_dt)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _shift_to_dict(shift)


class ClockRequest(BaseModel):
    worker_id: int


@router.post("/clock-in")
async def clock_in(payload: ClockRequest, user: dict = Depends(require_manager)):
    """Admin clocks a worker in."""
    worker = db.get_user_by_id(payload.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    existing = db.get_active_shift(payload.worker_id)
    if existing:
        raise HTTPException(400, f"{worker.full_name} is already clocked in")
    shift = db.clock_in(payload.worker_id)
    return _shift_to_dict(shift)


@router.post("/clock-out")
async def clock_out(payload: ClockRequest, user: dict = Depends(require_manager)):
    """Admin clocks a worker out."""
    worker = db.get_user_by_id(payload.worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    try:
        shift = db.clock_out(payload.worker_id)
    except ValueError:
        raise HTTPException(400, f"{worker.full_name} is not currently clocked in")
    return _shift_to_dict(shift)


@router.get("/active")
async def get_active_shifts(user: dict = Depends(require_manager)):
    """All currently clocked-in workers."""
    workers = db.list_users()
    active = []
    for w in workers:
        shift = db.get_active_shift(w.id)
        if shift:
            active.append(_shift_to_dict(shift))
    return active


@router.get("/active/{worker_id}")
async def get_active_shift_for_worker(
    worker_id: int, user: dict = Depends(require_worker)
):
    """Check clock-in status for one worker. Workers can only see themselves."""
    if user["role"] == "worker" and user["user_id"] != worker_id:
        raise HTTPException(403, "Cannot view other workers' shifts")
    shift = db.get_active_shift(worker_id)
    if not shift:
        return {"active": False, "shift": None}
    return {"active": True, "shift": _shift_to_dict(shift)}


@router.get("")
async def list_shifts(
    worker_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if user["role"] == "worker":
        worker_id = user["user_id"]
    dt_from = datetime.fromisoformat(date_from) if date_from else None
    dt_to   = datetime.fromisoformat(date_to)   if date_to   else None
    shifts  = db.list_shifts(worker_id=worker_id, date_from=dt_from, date_to=dt_to)
    return [_shift_to_dict(s) for s in shifts]


@router.post("/{shift_id}/approve")
async def approve_shift(shift_id: int, user: dict = Depends(require_manager)):
    db.approve_shift(shift_id, user["user_id"])
    return {"ok": True}


class WeekendUpdate(BaseModel):
    is_weekend: bool


@router.patch("/{shift_id}/weekend")
async def set_weekend(shift_id: int, payload: WeekendUpdate, user: dict = Depends(require_manager)):
    db.update_shift(shift_id, is_weekend=payload.is_weekend)
    return {"ok": True}


class NotesUpdate(BaseModel):
    notes: str


@router.patch("/{shift_id}/notes")
async def update_notes(shift_id: int, payload: NotesUpdate, user: dict = Depends(require_manager)):
    db.update_shift(shift_id, notes=payload.notes)
    return {"ok": True}


@router.delete("/{shift_id}")
async def delete_shift(shift_id: int):
    with db._Session() as session:
        from ..storage.database import ShiftRow
        shift = session.query(ShiftRow).filter_by(id=shift_id).first()
        if not shift:
            raise HTTPException(404, "Shift not found")
        session.delete(shift)
        session.commit()
    return {"ok": True}
