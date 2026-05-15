"""Baseline schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-14 17:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pdf_path", sa.String(), nullable=False),
        sa.Column("county", sa.String(), nullable=True),
        sa.Column("cause", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("total_lines", sa.Integer(), nullable=True),
        sa.Column("approved", sa.Integer(), nullable=True),
        sa.Column("review", sa.Integer(), nullable=True),
        sa.Column("rejected", sa.Integer(), nullable=True),
        sa.Column("duplicates", sa.Integer(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("fraud_flagged_lines", sa.Integer(), nullable=True),
        sa.Column("fraud_flags_json", sa.Text(), nullable=True),
        sa.Column("continuation_of", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["continuation_of"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("hourly_wage", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "pay_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_date", sa.String(), nullable=False),
        sa.Column("end_date", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "signatures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("raw_name", sa.String(), nullable=True),
        sa.Column("raw_address", sa.String(), nullable=True),
        sa.Column("raw_date", sa.String(), nullable=True),
        sa.Column("signature_present", sa.Boolean(), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("street", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("zip_code", sa.String(), nullable=True),
        sa.Column("voter_id", sa.String(), nullable=True),
        sa.Column("voter_name", sa.String(), nullable=True),
        sa.Column("voter_address", sa.String(), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("name_score", sa.Float(), nullable=True),
        sa.Column("address_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("duplicate_of_line", sa.Integer(), nullable=True),
        sa.Column("staff_override", sa.String(), nullable=True),
        sa.Column("staff_voter_id", sa.String(), nullable=True),
        sa.Column("staff_notes", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("clock_in", sa.DateTime(), nullable=False),
        sa.Column("clock_out", sa.DateTime(), nullable=True),
        sa.Column("is_weekend", sa.Boolean(), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "schedule_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("week_of", sa.String(), nullable=False),
        sa.Column("preferred_days", sa.Text(), nullable=True),
        sa.Column("preferred_hours", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "payment_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=True),
        sa.Column("details", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_id"),
    )
    op.create_table(
        "worker_projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=True),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("manual_sig_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "payroll_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("pay_period_id", sa.Integer(), nullable=False),
        sa.Column("total_hours", sa.Float(), nullable=True),
        sa.Column("total_signatures", sa.Integer(), nullable=True),
        sa.Column("valid_signatures", sa.Integer(), nullable=True),
        sa.Column("validity_rate", sa.Float(), nullable=True),
        sa.Column("hourly_wage_used", sa.Float(), nullable=True),
        sa.Column("base_pay_cents", sa.Integer(), nullable=True),
        sa.Column("bonus_cents", sa.Integer(), nullable=True),
        sa.Column("gross_cents", sa.Integer(), nullable=True),
        sa.Column("tax_cents", sa.Integer(), nullable=True),
        sa.Column("net_cents", sa.Integer(), nullable=True),
        sa.Column("earns_lunch", sa.Boolean(), nullable=True),
        sa.Column("breakdown_json", sa.Text(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pay_period_id"], ["pay_periods.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade the baseline migration because it would drop all "
        "application tables. Restore from backup instead."
    )
