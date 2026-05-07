"""Live in-memory stats: sig counts and locations submitted by canvassers."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user
from ..storage import db

router = APIRouter()

# worker_id → {full_name, sig_count, lat, lng, updated_at}
_live: dict[int, dict] = {}


class SigCountPayload(BaseModel):
    count: int


class LocationPayload(BaseModel):
    lat: float
    lng: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/sig-count")
async def submit_sig_count(payload: SigCountPayload, user: dict = Depends(get_current_user)):
    if payload.count < 0:
        raise HTTPException(400, "count must be non-negative")
    entry = _live.setdefault(user["user_id"], {"full_name": user.get("full_name", ""), "sig_count": 0, "lat": None, "lng": None})
    entry["sig_count"] = payload.count
    entry["updated_at"] = _now_iso()
    # Resolve name from DB if not in token
    if not entry["full_name"]:
        u = db.get_user_by_id(user["user_id"])
        entry["full_name"] = u.full_name if u else ""
    return {"ok": True, "sig_count": entry["sig_count"]}


@router.post("/location")
async def submit_location(payload: LocationPayload, user: dict = Depends(get_current_user)):
    if not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(400, "Invalid coordinates")
    entry = _live.setdefault(user["user_id"], {"full_name": user.get("full_name", ""), "sig_count": 0, "lat": None, "lng": None})
    entry["lat"] = payload.lat
    entry["lng"] = payload.lng
    entry["updated_at"] = _now_iso()
    if not entry["full_name"]:
        u = db.get_user_by_id(user["user_id"])
        entry["full_name"] = u.full_name if u else ""
    # Also persist to DB pin log
    db.drop_pin(user["user_id"], payload.lat, payload.lng)
    return {"ok": True}


@router.get("/live")
async def live_stats(user: dict = Depends(get_current_user)):
    return list(_live.values()) if _live else []
