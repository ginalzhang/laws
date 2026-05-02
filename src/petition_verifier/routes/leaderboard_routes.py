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

    # Bulk fetch everything in a few queries instead of N×M
    sig_counts    = db.get_all_worker_sig_counts()
    active_shifts = db.get_all_active_shifts()
    all_shifts    = db.list_shifts()  # all shifts, filter per worker below

    # Pre-group completed shifts by worker
    from collections import defaultdict
    shifts_by_worker: dict = defaultdict(list)
    for s in all_shifts:
        if s.clock_out:
            shifts_by_worker[s.worker_id].append(s)

    for worker in workers:
        if not worker.is_active:
            continue

        counts = sig_counts.get(worker.id, {"total_sigs": 0, "valid_sigs": 0})
        total_sigs = counts["total_sigs"]
        valid_sigs = counts["valid_sigs"]

        completed = shifts_by_worker[worker.id]
        total_hours = sum(
            (s.clock_out - s.clock_in).total_seconds() / 3600.0
            for s in completed
        )

        validity_rate = (valid_sigs / total_sigs * 100.0) if total_sigs > 0 else 0.0
        sigs_per_hour = (valid_sigs / total_hours) if total_hours > 0 else 0.0

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
            "is_clocked_in": worker.id in active_shifts,
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
