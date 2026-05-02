"""Payroll routes: preview, calculate, records, P&L."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, require_admin, require_boss, require_worker
from ..storage import db
from ..storage.database import PayrollRecordRow
from ..payroll.calculator import calculate_pay_for_period

router = APIRouter()


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _record_to_dict(rec) -> dict:
    try:
        breakdown = json.loads(rec.breakdown_json) if rec.breakdown_json else {}
    except Exception:
        breakdown = {}
    return {
        "id": rec.id,
        "worker_id": rec.worker_id,
        "pay_period_id": rec.pay_period_id,
        "total_hours": rec.total_hours,
        "total_signatures": rec.total_signatures,
        "valid_signatures": rec.valid_signatures,
        "validity_rate": rec.validity_rate,
        "hourly_wage_used": rec.hourly_wage_used,
        "base_pay_cents": rec.base_pay_cents,
        "bonus_cents": rec.bonus_cents,
        "gross_cents": rec.gross_cents,
        "tax_cents": rec.tax_cents,
        "net_cents": rec.net_cents,
        "earns_lunch": rec.earns_lunch,
        "calculated_at": rec.calculated_at.isoformat() if rec.calculated_at else None,
        "breakdown": breakdown,
    }


def _period_to_dict(pp) -> dict:
    return {
        "id": pp.id,
        "start_date": pp.start_date,
        "end_date": pp.end_date,
        "status": pp.status,
    }


def _compute_worker_pay(worker, pay_period) -> dict:
    """Compute payroll for a single worker in a pay period."""
    start_dt = datetime.fromisoformat(pay_period.start_date)
    end_dt = datetime.fromisoformat(pay_period.end_date)
    # Set end to end of day
    end_dt = end_dt.replace(hour=23, minute=59, second=59)

    shifts = db.list_shifts(worker_id=worker.id, date_from=start_dt, date_to=end_dt)

    # Get all projects assigned to worker and sum their signature counts
    wps = db.get_worker_projects(worker.id)
    total_sigs = 0
    valid_sigs = 0
    for wp in wps:
        counts = db.get_project_sig_counts(wp.project_id)
        total_sigs += counts["total_sigs"]
        valid_sigs += counts["valid_sigs"]

    project_stats = {"total_sigs": total_sigs, "valid_sigs": valid_sigs}
    daily_sigs = db.get_worker_daily_sigs(worker.id, date_from=start_dt, date_to=end_dt)
    result = calculate_pay_for_period(worker, shifts, project_stats, daily_sigs=daily_sigs or None)
    return result


@router.get("/preview")
async def payroll_preview(
    worker_id: Optional[int] = None,
    pay_period_id: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    """Preview estimated pay for a worker."""
    # Workers can only see their own preview
    if user["role"] == "worker":
        target_worker_id = user["user_id"]
    else:
        target_worker_id = worker_id or user["user_id"]

    worker = db.get_user_by_id(target_worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")

    # Use most recent open pay period if none specified
    if pay_period_id:
        pay_period = db.get_pay_period(pay_period_id)
    else:
        periods = db.list_pay_periods()
        open_periods = [p for p in periods if p.status == "open"]
        pay_period = open_periods[0] if open_periods else (periods[0] if periods else None)

    if not pay_period:
        # No pay period: compute against all time
        class FakePeriod:
            start_date = "2000-01-01"
            end_date = datetime.utcnow().date().isoformat()
            status = "open"
            id = None
        pay_period = FakePeriod()

    result = _compute_worker_pay(worker, pay_period)
    result["worker_id"] = worker.id
    result["worker_name"] = worker.full_name
    result["pay_period"] = {
        "id": getattr(pay_period, "id", None),
        "start_date": pay_period.start_date,
        "end_date": pay_period.end_date,
    }
    return result


@router.get("/records")
async def list_payroll_records(
    worker_id: Optional[int] = None,
    pay_period_id: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    if user["role"] == "worker":
        worker_id = user["user_id"]
    records = db.get_payroll_records(worker_id=worker_id, pay_period_id=pay_period_id)
    return [_record_to_dict(r) for r in records]


class CreatePayPeriodRequest(BaseModel):
    start_date: str  # ISO date YYYY-MM-DD
    end_date: str


@router.post("/periods")
async def create_pay_period(
    payload: CreatePayPeriodRequest,
    user: dict = Depends(require_boss),
):
    pp = db.create_pay_period(payload.start_date, payload.end_date)
    return _period_to_dict(pp)


@router.get("/periods")
async def list_pay_periods(user: dict = Depends(require_worker)):
    periods = db.list_pay_periods()
    return [_period_to_dict(p) for p in periods]


@router.post("/run/{pay_period_id}")
async def run_payroll(pay_period_id: int, user: dict = Depends(require_boss)):
    """Calculate payroll for all workers in a pay period."""
    pay_period = db.get_pay_period(pay_period_id)
    if not pay_period:
        raise HTTPException(404, "Pay period not found")

    workers = db.list_users()
    results = []
    for worker in workers:
        if not worker.is_active:
            continue
        calc = _compute_worker_pay(worker, pay_period)

        record = PayrollRecordRow(
            worker_id=worker.id,
            pay_period_id=pay_period_id,
            total_hours=calc["total_hours"],
            total_signatures=calc["total_signatures"],
            valid_signatures=calc["valid_signatures"],
            validity_rate=calc["validity_rate"],
            hourly_wage_used=calc["hourly_wage_used"],
            base_pay_cents=calc["base_pay_cents"],
            bonus_cents=calc["bonus_cents"],
            gross_cents=calc["gross_cents"],
            tax_cents=calc["tax_cents"],
            net_cents=calc["net_cents"],
            earns_lunch=calc["earns_lunch"],
            breakdown_json=json.dumps(calc),
        )
        saved = db.save_payroll_record(record)
        results.append({
            "worker_id": worker.id,
            "worker_name": worker.full_name,
            **_record_to_dict(saved),
        })

    db.update_pay_period_status(pay_period_id, "closed")
    return {
        "pay_period_id": pay_period_id,
        "workers_processed": len(results),
        "total_gross_cents": sum(r["gross_cents"] for r in results),
        "total_net_cents": sum(r["net_cents"] for r in results),
        "records": results,
    }


@router.get("/p-and-l")
async def profit_and_loss(
    pay_period_id: Optional[int] = None,
    revenue_cents: Optional[int] = None,
    user: dict = Depends(require_boss),
):
    """Boss-only P&L view."""
    records = db.get_payroll_records(pay_period_id=pay_period_id)
    total_labor_gross = sum(r.gross_cents for r in records)
    total_labor_net = sum(r.net_cents for r in records)
    total_valid_sigs = sum(r.valid_signatures for r in records)
    total_hours = sum(r.total_hours for r in records)

    cost_per_sig = (total_labor_gross / total_valid_sigs) if total_valid_sigs > 0 else 0
    cost_per_hour = (total_labor_gross / total_hours) if total_hours > 0 else 0

    revenue = revenue_cents or 0
    margin_cents = revenue - total_labor_gross
    margin_pct = (margin_cents / revenue * 100.0) if revenue > 0 else None

    return {
        "pay_period_id": pay_period_id,
        "total_labor_gross_cents": total_labor_gross,
        "total_labor_net_cents": total_labor_net,
        "total_valid_signatures": total_valid_sigs,
        "total_hours": round(total_hours, 2),
        "cost_per_sig_cents": round(cost_per_sig, 2),
        "cost_per_hour_cents": round(cost_per_hour, 2),
        "revenue_cents": revenue,
        "margin_cents": margin_cents,
        "margin_pct": round(margin_pct, 1) if margin_pct is not None else None,
        "worker_count": len(set(r.worker_id for r in records)),
    }
