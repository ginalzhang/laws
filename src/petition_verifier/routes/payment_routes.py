"""Payment preference routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_worker
from ..storage import Database

router = APIRouter()
db = Database()


class PaymentPrefRequest(BaseModel):
    method: str  # check|zelle|venmo|direct_deposit
    details: str = ""


def _pref_to_dict(pref) -> dict:
    return {
        "id": pref.id,
        "worker_id": pref.worker_id,
        "method": pref.method,
        "details": pref.details,
        "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
    }


@router.get("")
async def get_payment_preference(user: dict = Depends(require_worker)):
    pref = db.get_payment_preference(user["user_id"])
    if not pref:
        return {"worker_id": user["user_id"], "method": "check", "details": "", "id": None}
    return _pref_to_dict(pref)


@router.put("")
async def set_payment_preference(
    payload: PaymentPrefRequest,
    user: dict = Depends(require_worker),
):
    valid_methods = ("check", "zelle", "venmo", "direct_deposit")
    if payload.method not in valid_methods:
        raise HTTPException(400, f"Method must be one of: {', '.join(valid_methods)}")
    pref = db.set_payment_preference(user["user_id"], payload.method, payload.details)
    return _pref_to_dict(pref)
