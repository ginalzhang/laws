"""Worker location pin routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user
from ..storage import db

router = APIRouter()


class PinPayload(BaseModel):
    lat: float
    lng: float
    note: str = ""


@router.post("/pin")
async def drop_pin(payload: PinPayload, user: dict = Depends(get_current_user)):
    if not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(400, "Invalid coordinates")
    pin = db.drop_pin(user["user_id"], payload.lat, payload.lng, payload.note)
    return {
        "id":        pin.id,
        "worker_id": pin.worker_id,
        "lat":       pin.lat,
        "lng":       pin.lng,
        "note":      pin.note,
        "pinned_at": pin.pinned_at.isoformat(),
    }


@router.get("/pins")
async def get_pins(user: dict = Depends(get_current_user)):
    if user["role"] not in ("boss", "admin", "field_manager"):
        raise HTTPException(403, "Not authorized")
    pins = db.get_all_pins()
    result = []
    for p in pins:
        worker = db.get_user_by_id(p.worker_id)
        result.append({
            "id":          p.id,
            "worker_id":   p.worker_id,
            "worker_name": worker.full_name if worker else f"Worker {p.worker_id}",
            "lat":         p.lat,
            "lng":         p.lng,
            "note":        p.note,
            "pinned_at":   p.pinned_at.isoformat(),
        })
    return result
