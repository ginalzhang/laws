"""
Payroll calculator for petition workforce management.

Incentive tier system (PER SHIFT):
  Weekday 4-hour shifts:
    < 40 sigs  → base pay only
    40-49 sigs → base pay only
    50-59 sigs → free lunch (earns_lunch=True)
    60-69 sigs → $20 cash bonus
    70-79 sigs → $40 cash bonus
    80+  sigs  → $60 cash bonus + lunch

  Weekend 8-hour shifts (double thresholds AND bonuses):
    < 80  sigs → base pay only
    80-99 sigs → base pay only
    100-119    → free lunch
    120-139    → $40 cash bonus
    140-159    → $80 cash bonus
    160+       → $120 cash bonus + lunch

Wage tiers per pay period:
  >= 10 valid sigs/hour → use worker.hourly_wage (default $25)
  <  10 valid sigs/hour → use min(worker.hourly_wage, $20.00)

Tax: Federal 22% + CA state 9.3% = 31.3% total withheld from gross
All money as cents (integers).
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.database import UserRow, ShiftRow

TAX_RATE = 0.313  # 22% federal + 9.3% CA state
MIN_WAGE_CAP = 20.0  # $/hr if below performance threshold
SIGS_PER_HOUR_THRESHOLD = 10.0


# ── Weekday tier table (valid_sigs: (bonus_cents, earns_lunch)) ───────────────
_WEEKDAY_TIERS = [
    (80, 6000, True),
    (70, 4000, False),
    (60, 2000, False),
    (50,    0, True),
    (40,    0, False),
    ( 0,    0, False),
]

# ── Weekend tier table (doubled thresholds and bonuses) ──────────────────────
_WEEKEND_TIERS = [
    (160, 12000, True),
    (140,  8000, False),
    (120,  4000, False),
    (100,     0, True),
    ( 80,     0, False),
    (  0,     0, False),
]


def calculate_shift_bonus(valid_sigs: int, is_weekend: bool) -> dict:
    """Returns {"bonus_cents": int, "earns_lunch": bool}"""
    tiers = _WEEKEND_TIERS if is_weekend else _WEEKDAY_TIERS
    for threshold, bonus_cents, earns_lunch in tiers:
        if valid_sigs >= threshold:
            return {"bonus_cents": bonus_cents, "earns_lunch": earns_lunch}
    return {"bonus_cents": 0, "earns_lunch": False}


def _shift_hours(shift) -> float:
    """Calculate hours worked for a shift."""
    if shift.clock_out is None:
        return 0.0
    delta = shift.clock_out - shift.clock_in
    return delta.total_seconds() / 3600.0


def calculate_pay_for_period(
    worker,
    shifts: list,
    project_stats: dict,
    daily_sigs: Optional[dict] = None,
) -> dict:
    """
    Returns full payroll breakdown.

    Args:
        worker: UserRow instance
        shifts: list of ShiftRow (completed shifts in this period)
        project_stats: {"total_sigs": int, "valid_sigs": int}
        daily_sigs: optional {"YYYY-MM-DD": int} mapping of sig counts per day.
                    If provided, each shift's wage is evaluated per-day:
                    < 10 sigs that day → $20/hr cap, else worker.hourly_wage.
    """
    completed_shifts = [s for s in shifts if s.clock_out is not None]

    total_hours = sum(_shift_hours(s) for s in completed_shifts)
    total_sigs = project_stats.get("total_sigs", 0)
    valid_sigs = project_stats.get("valid_sigs", 0)

    validity_rate = (valid_sigs / total_sigs * 100.0) if total_sigs > 0 else 0.0
    sigs_per_hour = (valid_sigs / total_hours) if total_hours > 0 else 0.0

    shift_breakdown = []
    total_base_cents = 0
    total_bonus_cents = 0
    any_lunch = False

    for shift in completed_shifts:
        hours = _shift_hours(shift)
        shift_date = shift.clock_in.strftime("%Y-%m-%d")

        # Per-day wage: if we have daily sig data use it, else fall back to period-wide rate
        if daily_sigs is not None:
            day_sigs = daily_sigs.get(shift_date, 0)
            wage = worker.hourly_wage if day_sigs >= 10 else min(worker.hourly_wage, MIN_WAGE_CAP)
        else:
            wage = worker.hourly_wage if sigs_per_hour >= SIGS_PER_HOUR_THRESHOLD else min(worker.hourly_wage, MIN_WAGE_CAP)
            day_sigs = 0

        shift_base = int(round(hours * wage * 100))
        total_base_cents += shift_base

        # Attribute sigs to this shift for bonus calc
        if daily_sigs is not None:
            shift_valid_sigs = day_sigs
        elif total_hours > 0:
            shift_valid_sigs = int(round(valid_sigs * hours / total_hours))
        else:
            shift_valid_sigs = 0

        bonus_info = calculate_shift_bonus(shift_valid_sigs, shift.is_weekend)
        shift_bonus = bonus_info["bonus_cents"]
        shift_lunch = bonus_info["earns_lunch"]
        total_bonus_cents += shift_bonus
        if shift_lunch:
            any_lunch = True

        shift_breakdown.append({
            "shift_id": shift.id,
            "date": shift_date,
            "clock_in": shift.clock_in.isoformat(),
            "clock_out": shift.clock_out.isoformat() if shift.clock_out else None,
            "hours": round(hours, 2),
            "is_weekend": shift.is_weekend,
            "day_sigs": day_sigs,
            "wage_used": wage,
            "base_pay_cents": shift_base,
            "valid_sigs_attributed": shift_valid_sigs,
            "bonus_cents": shift_bonus,
            "earns_lunch": shift_lunch,
            "approved": shift.approved,
        })

    # Overall effective wage for summary (weighted average)
    hourly_wage = (total_base_cents / 100 / total_hours) if total_hours > 0 else worker.hourly_wage

    gross_cents = total_base_cents + total_bonus_cents
    tax_cents = int(round(gross_cents * TAX_RATE))
    net_cents = gross_cents - tax_cents

    return {
        "total_hours": round(total_hours, 2),
        "total_signatures": total_sigs,
        "valid_signatures": valid_sigs,
        "validity_rate": round(validity_rate, 1),
        "sigs_per_hour": round(sigs_per_hour, 2),
        "hourly_wage_used": round(hourly_wage, 2),
        "base_pay_cents": total_base_cents,
        "bonus_cents": total_bonus_cents,
        "earns_lunch": any_lunch,
        "gross_cents": gross_cents,
        "tax_cents": tax_cents,
        "net_cents": net_cents,
        "shift_breakdown": shift_breakdown,
    }
