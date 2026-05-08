"""Live stats: sig counts (DB-persisted) and locations (in-memory)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_manager
from ..storage import db

router = APIRouter()

# worker_id → {full_name, lat, lng, updated_at}  — locations only, in-memory
_locations: dict[int, dict] = {}


class SigCountPayload(BaseModel):
    count: int


class LocationPayload(BaseModel):
    lat: float
    lng: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_name(user: dict) -> str:
    name = user.get("full_name", "")
    if not name:
        u = db.get_user_by_id(user["user_id"])
        name = u.full_name if u else ""
    return name


@router.post("/sig-count")
async def submit_sig_count(payload: SigCountPayload, user: dict = Depends(get_current_user)):
    if payload.count < 0:
        raise HTTPException(400, "count must be non-negative")
    # Persist to DB so count survives restarts and sign-outs
    db.upsert_live_sig_count(user["user_id"], payload.count)
    # Keep location entry in sync
    loc = _locations.setdefault(user["user_id"], {"full_name": _resolve_name(user), "lat": None, "lng": None})
    loc["updated_at"] = _now_iso()
    return {"ok": True, "sig_count": payload.count}


@router.post("/sig-count/{worker_id}")
async def set_worker_sig_count(worker_id: int, payload: SigCountPayload, user: dict = Depends(require_manager)):
    if payload.count < 0:
        raise HTTPException(400, "count must be non-negative")
    target = db.get_user_by_id(worker_id)
    if not target:
        raise HTTPException(404, "Worker not found")
    db.upsert_live_sig_count(worker_id, payload.count)
    return {"ok": True, "worker_id": worker_id, "sig_count": payload.count}


@router.post("/location")
async def submit_location(payload: LocationPayload, user: dict = Depends(get_current_user)):
    if not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(400, "Invalid coordinates")
    entry = _locations.setdefault(user["user_id"], {"full_name": _resolve_name(user), "lat": None, "lng": None})
    entry["lat"] = payload.lat
    entry["lng"] = payload.lng
    entry["updated_at"] = _now_iso()
    db.drop_pin(user["user_id"], payload.lat, payload.lng)
    return {"ok": True}


@router.delete("/location")
async def delete_location(user: dict = Depends(get_current_user)):
    entry = _locations.get(user["user_id"])
    if entry:
        entry["lat"] = None
        entry["lng"] = None
        entry["updated_at"] = _now_iso()
    db.delete_worker_pins(user["user_id"])
    return {"ok": True}


@router.get("/live")
async def live_stats(user: dict = Depends(get_current_user)):
    # Load all persisted sig counts from DB
    db_counts = {row["worker_id"]: row["sig_count"] for row in db.get_all_live_sig_counts()}

    # Build result: one entry per worker who has a sig count or a location
    result = {}

    # Add DB sig counts
    for worker_id, count in db_counts.items():
        u = db.get_user_by_id(worker_id)
        if not u:
            continue
        result[worker_id] = {
            "worker_id": worker_id,
            "full_name": u.full_name,
            "sig_count": count,
            "lat": None,
            "lng": None,
        }

    # Overlay in-memory locations
    for worker_id, loc in _locations.items():
        if worker_id in result:
            result[worker_id]["lat"] = loc.get("lat")
            result[worker_id]["lng"] = loc.get("lng")
        elif loc.get("lat") is not None:
            result[worker_id] = {
                "worker_id": worker_id,
                "full_name": loc.get("full_name", ""),
                "sig_count": db_counts.get(worker_id, 0),
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
            }

    return list(result.values())


@router.get("/my-count")
async def my_count(user: dict = Depends(get_current_user)):
    """Return the current user's persisted sig count."""
    count = db.get_live_sig_count(user["user_id"])
    return {"sig_count": count}
