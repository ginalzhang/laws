"""Worker CRUD and stats routes."""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional  # noqa: F401 already used

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_admin, require_boss, require_manager
from ..storage import db
from ..storage.database import UserRow

router = APIRouter()


def _worker_stats_from_bulk(worker: UserRow, sig_counts: dict, active_shifts: dict, today_shifts: dict) -> dict:
    """Compute worker stats from pre-fetched bulk data (no extra DB queries)."""
    active_shift = active_shifts.get(worker.id)
    is_clocked_in = active_shift is not None

    counts = sig_counts.get(worker.id, {"total_sigs": 0, "valid_sigs": 0})
    total_sigs = counts["total_sigs"]
    total_valid_sigs = counts["valid_sigs"]
    validity_rate = (total_valid_sigs / total_sigs * 100.0) if total_sigs > 0 else 0.0

    shifts_today = today_shifts.get(worker.id, [])
    today_hours = 0.0
    for s in shifts_today:
        if s.clock_out:
            today_hours += (s.clock_out - s.clock_in).total_seconds() / 3600.0
        elif is_clocked_in and active_shift and s.id == active_shift.id:
            today_hours += (datetime.utcnow() - s.clock_in).total_seconds() / 3600.0

    estimated_pay_cents = int(round(today_hours * worker.hourly_wage * 100))

    return {
        "is_clocked_in": is_clocked_in,
        "today_hours": round(today_hours, 2),
        "total_valid_sigs": total_valid_sigs,
        "total_sigs": total_sigs,
        "validity_rate": round(validity_rate, 1),
        "estimated_pay_cents": estimated_pay_cents,
        "clock_in_time": active_shift.clock_in.isoformat() if active_shift else None,
    }


def _worker_stats(worker: UserRow) -> dict:
    """Compute basic worker stats from DB (single-worker fallback)."""
    active_shift = db.get_active_shift(worker.id)
    is_clocked_in = active_shift is not None
    wps = db.get_worker_projects(worker.id)
    total_valid_sigs = 0
    total_sigs = 0
    for wp in wps:
        counts = db.get_project_sig_counts(wp.project_id)
        total_valid_sigs += counts["valid_sigs"]
        total_sigs += counts["total_sigs"]
    validity_rate = (total_valid_sigs / total_sigs * 100.0) if total_sigs > 0 else 0.0
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_shifts = db.list_shifts(worker_id=worker.id, date_from=today_start)
    today_hours = 0.0
    for s in today_shifts:
        if s.clock_out:
            today_hours += (s.clock_out - s.clock_in).total_seconds() / 3600.0
        elif is_clocked_in and active_shift and s.id == active_shift.id:
            today_hours += (datetime.utcnow() - s.clock_in).total_seconds() / 3600.0
    estimated_pay_cents = int(round(today_hours * worker.hourly_wage * 100))
    return {
        "is_clocked_in": is_clocked_in,
        "today_hours": round(today_hours, 2),
        "total_valid_sigs": total_valid_sigs,
        "total_sigs": total_sigs,
        "validity_rate": round(validity_rate, 1),
        "estimated_pay_cents": estimated_pay_cents,
        "clock_in_time": active_shift.clock_in.isoformat() if active_shift else None,
    }


def _user_to_dict(user: UserRow, include_stats: bool = False) -> dict:
    d = {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name,
        "phone": user.phone,
        "hourly_wage": user.hourly_wage,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
    if include_stats:
        d.update(_worker_stats(user))
    return d


VALID_ROLES = ("boss", "admin", "worker", "field_manager", "petitioner", "office_worker")

class CreateWorkerRequest(BaseModel):
    email: str = ""
    password: str = ""
    role: str = "worker"
    full_name: str
    phone: str = ""
    hourly_wage: float = 25.0


class UpdateWageRequest(BaseModel):
    hourly_wage: float


@router.get("")
async def list_workers(user: dict = Depends(require_manager)):
    users = db.list_users()
    if user["role"] == "field_manager":
        users = [u for u in users if u.role not in ("boss", "admin", "office_worker")]
    today_start   = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sig_counts    = db.get_all_worker_sig_counts()
    active_shifts = db.get_all_active_shifts()
    today_shifts  = db.get_all_today_shifts(today_start)
    result = []
    for u in users:
        d = _user_to_dict(u)
        d.update(_worker_stats_from_bulk(u, sig_counts, active_shifts, today_shifts))
        result.append(d)
    return result


@router.post("")
async def create_worker(payload: CreateWorkerRequest, user: dict = Depends(require_manager)):
    import uuid as _uuid
    from ..auth import hash_password
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")
    # Auto-generate email and password if not provided
    email = payload.email.strip() or f"worker_{_uuid.uuid4().hex[:8]}@local"
    existing = db.get_user_by_email(email)
    if existing:
        raise HTTPException(409, "Email already registered")
    password_hash = hash_password(payload.password or _uuid.uuid4().hex)
    new_user = db.create_user(
        email=email,
        password_hash=password_hash,
        role=payload.role,
        full_name=payload.full_name,
        phone=payload.phone,
        hourly_wage=payload.hourly_wage,
    )
    return _user_to_dict(new_user)


@router.get("/{worker_id}")
async def get_worker(worker_id: int, user: dict = Depends(get_current_user)):
    # Workers can only see their own detail
    if user["role"] == "worker" and user["user_id"] != worker_id:
        raise HTTPException(403, "Cannot view other workers")
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    return _user_to_dict(worker, include_stats=True)


@router.patch("/{worker_id}/wage")
async def update_wage(
    worker_id: int,
    payload: UpdateWageRequest,
    user: dict = Depends(require_boss),
):
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    if payload.hourly_wage <= 0:
        raise HTTPException(400, "Wage must be positive")
    db.update_user_wage(worker_id, payload.hourly_wage)
    return {"ok": True, "hourly_wage": payload.hourly_wage}


@router.get("/{worker_id}/projects")
async def get_worker_projects(worker_id: int, user: dict = Depends(get_current_user)):
    if user["role"] == "worker" and user["user_id"] != worker_id:
        raise HTTPException(403, "Cannot view other workers")
    wps = db.get_worker_projects(worker_id)
    result = []
    for wp in wps:
        entry = {
            "worker_project_id": wp.id,
            "project_id": wp.project_id,
            "assigned_at": wp.assigned_at.isoformat() if wp.assigned_at else None,
            "assigned_by": wp.assigned_by,
            "manual_sig_count": wp.manual_sig_count,
        }
        counts = db.get_project_sig_counts(wp.project_id)
        entry.update(counts)
        result.append(entry)
    return result


class ManualSigRequest(BaseModel):
    sig_count: int
    notes: str = ""
    date: str = ""  # optional ISO date "YYYY-MM-DD"; defaults to today


@router.post("/{worker_id}/manual-sigs")
async def add_manual_sigs(
    worker_id: int,
    payload: ManualSigRequest,
    user: dict = Depends(require_admin),
):
    """Manually record a signature count for a worker without uploading a petition."""
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    if payload.sig_count < 1:
        raise HTTPException(400, "sig_count must be at least 1")
    sig_date = None
    if payload.date:
        try:
            sig_date = datetime.fromisoformat(payload.date)
        except ValueError:
            raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")
    project_id = db.create_manual_sig_entry(worker_id, payload.sig_count, payload.notes, sig_date=sig_date)
    return {"ok": True, "project_id": project_id, "sig_count": payload.sig_count, "date": payload.date or datetime.utcnow().date().isoformat()}


class UpdateWorkerRequest(BaseModel):
    full_name: str = ""
    phone: str = ""
    email: str = ""
    role: str = ""
    hourly_wage: Optional[float] = None


@router.patch("/{worker_id}")
async def update_worker(worker_id: int, payload: UpdateWorkerRequest):
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    updates = {}
    if payload.full_name.strip(): updates["full_name"] = payload.full_name.strip()
    if payload.phone.strip() or payload.phone == "": updates["phone"] = payload.phone.strip()
    if payload.email.strip(): updates["email"] = payload.email.strip()
    if payload.role and payload.role in VALID_ROLES: updates["role"] = payload.role
    if payload.hourly_wage is not None and payload.hourly_wage > 0: updates["hourly_wage"] = payload.hourly_wage
    if updates:
        db.update_user(worker_id, **updates)
    return _user_to_dict(db.get_user_by_id(worker_id))


@router.delete("/{worker_id}")
async def delete_worker(worker_id: int):
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    db.update_user(worker_id, is_active=False)
    return {"ok": True}


@router.patch("/{worker_id}/deactivate")
async def deactivate_worker(worker_id: int, user: dict = Depends(require_manager)):
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    db.update_user(worker_id, is_active=False)
    return {"ok": True}


@router.patch("/{worker_id}/activate")
async def activate_worker(worker_id: int, user: dict = Depends(require_admin)):
    worker = db.get_user_by_id(worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    db.update_user(worker_id, is_active=True)
    return {"ok": True}
