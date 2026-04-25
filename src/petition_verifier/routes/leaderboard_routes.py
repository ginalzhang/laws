"""Leaderboard routes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends

from ..auth import require_worker
from ..storage import Database
from ..payroll.calculator import calculate_shift_bonus

router = APIRouter()
db = Database()


def _get_bonus_tier_label(valid_sigs: int, hours: float) -> str:
    """Get a human-readable tier label based on sigs/hour."""
    if hours <= 0:
        return "No shifts"
    sph = valid_sigs / hours
    if sph >= 20:
        return "Top Performer"
    elif sph >= 15:
        return "Gold"
    elif sph >= 10:
        return "Silver"
    elif sph >= 5:
        return "Bronze"
    else:
        return "Starting"


@router.get("/leaderboard")
async def leaderboard(
    pay_period_id: Optional[int] = None,
    user: dict = Depends(require_worker),
):
    workers = db.list_users()
    entries = []

    for worker in workers:
        if not worker.is_active:
            continue

        # Get all signature stats
        wps = db.get_worker_projects(worker.id)
        total_sigs = 0
        valid_sigs = 0
        for wp in wps:
            counts = db.get_project_sig_counts(wp.project_id)
            total_sigs += counts["total_sigs"]
            valid_sigs += counts["valid_sigs"]

        # Get total hours
        shifts = db.list_shifts(worker_id=worker.id)
        completed = [s for s in shifts if s.clock_out]
        total_hours = sum(
            (s.clock_out - s.clock_in).total_seconds() / 3600.0
            for s in completed
        )

        validity_rate = (valid_sigs / total_sigs * 100.0) if total_sigs > 0 else 0.0
        sigs_per_hour = (valid_sigs / total_hours) if total_hours > 0 else 0.0

        # Cost per sig estimate
        gross_cents = int(round(total_hours * worker.hourly_wage * 100))
        cost_per_sig_cents = (gross_cents / valid_sigs) if valid_sigs > 0 else 0.0

        entries.append({
            "worker_id": worker.id,
            "full_name": worker.full_name,
            "valid_sigs": valid_sigs,
            "total_sigs": total_sigs,
            "validity_rate": round(validity_rate, 1),
            "total_hours": round(total_hours, 2),
            "sigs_per_hour": round(sigs_per_hour, 2),
            "cost_per_sig_cents": round(cost_per_sig_cents, 2),
            "tier_label": _get_bonus_tier_label(valid_sigs, total_hours),
            "is_clocked_in": db.get_active_shift(worker.id) is not None,
        })

    # Sort by valid_sigs desc
    entries.sort(key=lambda x: x["valid_sigs"], reverse=True)

    # Add rank
    for i, entry in enumerate(entries):
        entry["rank"] = i + 1

    # Find requesting user's rank
    my_rank = next(
        (e["rank"] for e in entries if e["worker_id"] == user["user_id"]), None
    )

    return {
        "leaderboard": entries,
        "my_rank": my_rank,
        "total_workers": len(entries),
    }
