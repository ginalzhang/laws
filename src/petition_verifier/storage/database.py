"""
SQLite (default) or PostgreSQL storage via SQLAlchemy.

Schema:
  projects       — one row per processed PDF
  signatures     — one row per signature line; FK → projects
  voter_roll     — loaded from CSV at startup (or kept external)
  users          — workforce management users
  shifts         — clock in/out records
  schedule_requests — worker schedule preferences
  payment_preferences — worker payment methods
  worker_projects — assignments of workers to projects
  pay_periods    — payroll pay periods
  payroll_records — calculated payroll per worker per period
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    ForeignKey, create_engine, text, func, case,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from ..models import ProjectResult, VerificationResult, VerificationStatus


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./petition_verifier.db")
# Render gives postgres:// but SQLAlchemy 2.x requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id           = Column(String, primary_key=True)
    pdf_path     = Column(String, nullable=False)
    county       = Column(String, default="")
    cause        = Column(String, default="")   # petition initiative/campaign name
    created_at   = Column(DateTime, default=datetime.utcnow)
    total_lines  = Column(Integer, default=0)
    approved     = Column(Integer, default=0)
    review       = Column(Integer, default=0)
    rejected     = Column(Integer, default=0)
    duplicates   = Column(Integer, default=0)
    summary_json = Column(Text, default="{}")
    # Fraud scan results (populated for worker-uploaded projects)
    fraud_flagged_lines = Column(Integer, nullable=True)
    fraud_flags_json    = Column(Text, nullable=True)
    # Links this sheet back to the original if it's a continuation
    continuation_of     = Column(String, ForeignKey("projects.id"), nullable=True)

    signatures  = relationship("SignatureRow", back_populates="project",
                               cascade="all, delete-orphan")


class SignatureRow(Base):
    __tablename__ = "signatures"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    project_id        = Column(String, ForeignKey("projects.id"), nullable=False)
    line_number       = Column(Integer, nullable=False)
    page              = Column(Integer, nullable=False)

    # Extracted
    raw_name          = Column(String, default="")
    raw_address       = Column(String, default="")
    raw_date          = Column(String, default="")
    signature_present = Column(Boolean, default=False)
    ocr_confidence    = Column(Float, nullable=True)

    # Normalized
    first_name        = Column(String, default="")
    last_name         = Column(String, default="")
    street            = Column(String, default="")
    city              = Column(String, default="")
    state             = Column(String, default="")
    zip_code          = Column(String, default="")

    # Match
    voter_id          = Column(String, nullable=True)
    voter_name        = Column(String, nullable=True)
    voter_address     = Column(String, nullable=True)
    match_confidence  = Column(Float, nullable=True)
    name_score        = Column(Float, nullable=True)
    address_score     = Column(Float, nullable=True)

    # Outcome
    status            = Column(String, nullable=False)
    duplicate_of_line = Column(Integer, nullable=True)

    # Staff review
    staff_override    = Column(String, nullable=True)
    staff_voter_id    = Column(String, nullable=True)
    staff_notes       = Column(String, default="")
    reviewed_at       = Column(DateTime, nullable=True)

    project           = relationship("ProjectRow", back_populates="signatures")


# ── Workforce Management Models ───────────────────────────────────────────────

class UserRow(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    email         = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role          = Column(String, nullable=False)  # "boss" | "admin" | "worker"
    full_name     = Column(String, nullable=False)
    phone         = Column(String, default="")
    hourly_wage   = Column(Float, default=25.0)  # customizable per worker
    is_active     = Column(Boolean, default=True, server_default="true")
    created_at    = Column(DateTime, default=datetime.utcnow)
    team_id       = Column(Integer, ForeignKey("teams.id"), nullable=True)


class ShiftRow(Base):
    __tablename__ = "shifts"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    worker_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    clock_in      = Column(DateTime, nullable=False)
    clock_out     = Column(DateTime, nullable=True)
    is_weekend    = Column(Boolean, default=False)  # determines bonus doubling
    approved      = Column(Boolean, default=False)
    approved_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes         = Column(String, default="")


class ScheduleRequestRow(Base):
    __tablename__ = "schedule_requests"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    worker_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    week_of         = Column(String, nullable=False)  # ISO date of Monday
    preferred_days  = Column(Text, default="[]")  # JSON list of day names
    preferred_hours = Column(String, default="")
    notes           = Column(String, default="")
    status          = Column(String, default="pending")  # pending|approved|rejected


class PaymentPreferenceRow(Base):
    __tablename__ = "payment_preferences"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    worker_id  = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    method     = Column(String, default="check")  # check|zelle|venmo|direct_deposit
    details    = Column(String, default="")  # e.g. phone number for zelle
    updated_at = Column(DateTime, default=datetime.utcnow)


class WorkerProjectRow(Base):
    __tablename__ = "worker_projects"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    worker_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id       = Column(String, ForeignKey("projects.id"), nullable=False)
    assigned_at      = Column(DateTime, default=datetime.utcnow)
    assigned_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    manual_sig_count = Column(Integer, nullable=True)   # worker's own hand-count


class PayPeriodRow(Base):
    __tablename__ = "pay_periods"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    start_date = Column(String, nullable=False)  # ISO date
    end_date   = Column(String, nullable=False)
    status     = Column(String, default="open")  # open|closed|paid


class WorkerLocationRow(Base):
    __tablename__ = "worker_locations"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    worker_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    lat        = Column(Float, nullable=False)
    lng        = Column(Float, nullable=False)
    note       = Column(String, default="")
    pinned_at  = Column(DateTime, default=datetime.utcnow)


class PayrollRecordRow(Base):
    __tablename__ = "payroll_records"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    worker_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    pay_period_id    = Column(Integer, ForeignKey("pay_periods.id"), nullable=False)
    total_hours      = Column(Float, default=0.0)
    total_signatures = Column(Integer, default=0)
    valid_signatures = Column(Integer, default=0)
    validity_rate    = Column(Float, default=0.0)
    hourly_wage_used = Column(Float, default=25.0)
    base_pay_cents   = Column(Integer, default=0)
    bonus_cents      = Column(Integer, default=0)
    gross_cents      = Column(Integer, default=0)
    tax_cents        = Column(Integer, default=0)
    net_cents        = Column(Integer, default=0)
    earns_lunch      = Column(Boolean, default=False)
    breakdown_json   = Column(Text, default="{}")
    calculated_at    = Column(DateTime, default=datetime.utcnow)


class LiveSigCountRow(Base):
    __tablename__ = "live_sig_counts"
    worker_id  = Column(Integer, ForeignKey("users.id"), primary_key=True)
    sig_count  = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AppSettingRow(Base):
    __tablename__ = "app_settings"
    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False, default="")


class TeamRow(Base):
    __tablename__ = "teams"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String, nullable=False)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Signature Review Center ───────────────────────────────────────────────────

class PacketRow(Base):
    __tablename__ = "review_packets"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    worker_id        = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at      = Column(DateTime, default=datetime.utcnow)
    original_name    = Column(String, default="")
    raw_path         = Column(String, default="")
    cleaned_path     = Column(String, default="")
    status           = Column(String, default="pending")   # pending|processing|done|failed
    error_msg        = Column(String, default="")
    total_lines      = Column(Integer, default=0)
    # Versioning / duplicate detection
    page_fingerprint = Column(String, default="")          # dHash of cleaned image
    new_sigs         = Column(Integer, default=0)          # count of new_signature rows
    already_counted  = Column(Integer, default=0)          # count of already_counted rows
    needs_review     = Column(Integer, default=0)          # count of changed_needs_review rows
    result_json      = Column(Text, default="{}")          # full page_result JSON
    shift_id         = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    voter_roll_text  = Column(Text, nullable=True)         # pasted voter roll for this packet
    county           = Column(String, nullable=True)       # CA county selected for validation
    lines            = relationship("PacketLineRow", back_populates="packet",
                                    cascade="all, delete-orphan")


class ShiftReflectionRow(Base):
    __tablename__ = "shift_reflections"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    worker_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    shift_id      = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    sigs_reported = Column(Integer, nullable=False, default=0)
    hours_worked  = Column(Float, nullable=False, default=0.0)
    hit_goal      = Column(Boolean, nullable=False, default=False)
    reflection    = Column(Text, default="")   # why not / what they did well
    notes         = Column(Text, default="")
    created_at    = Column(DateTime, default=datetime.utcnow)


class PacketLineRow(Base):
    __tablename__ = "review_packet_lines"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    packet_id        = Column(Integer, ForeignKey("review_packets.id"), nullable=False)
    line_no          = Column(Integer, nullable=False)
    row_fingerprint  = Column(String, default="")          # composite fingerprint for versioning
    # Row status from versioning
    row_status       = Column(String, default="blank")     # blank|new_signature|already_counted|changed_needs_review
    # OCR fields
    raw_name         = Column(String, default="")
    norm_name        = Column(String, default="")
    raw_address      = Column(String, default="")
    norm_address     = Column(String, default="")
    raw_city         = Column(String, default="")
    raw_zip          = Column(String, default="")
    valid_zip        = Column(Boolean, default=False)
    raw_date         = Column(String, default="")
    has_signature    = Column(Boolean, default=False)
    # Legacy AI verdict fields (kept for backward compat with basic review flow)
    ai_verdict       = Column(String, default="needs_review")
    ai_reason        = Column(String, default="")
    flags_json       = Column(Text, default="[]")
    # Voter roll matching
    voter_status     = Column(String, nullable=True)       # valid|invalid|uncertain
    voter_confidence = Column(Integer, nullable=True)       # 0-100
    voter_reason     = Column(String, nullable=True)
    # Fraud detection
    fraud_flags      = Column(Text, default="[]")          # JSON array of fraud pattern strings
    fraud_score      = Column(Integer, default=0)          # 0-100 risk score
    # Human review decision
    review_decision  = Column(String, nullable=True)       # confirmed_fraud|cleared
    # Reviewer action
    action           = Column(String, nullable=True)       # approved|rejected|escalated
    reviewed_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at      = Column(DateTime, nullable=True)
    packet           = relationship("PacketRow", back_populates="lines")


def init_db(url: str = DATABASE_URL) -> sessionmaker:
    is_postgres = url.startswith("postgresql")
    engine = create_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=3,
        pool_pre_ping=True,
        connect_args={"options": "-c statement_timeout=30000"},
        execution_options={"prepared_statement_cache_size": 0},
    ) if is_postgres else create_engine(url, echo=False)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE users ADD COLUMN team_id INTEGER",
            "ALTER TABLE review_packets ADD COLUMN shift_id INTEGER",
            "ALTER TABLE review_packets ADD COLUMN voter_roll_text TEXT",
            "ALTER TABLE review_packets ADD COLUMN county VARCHAR",
            "ALTER TABLE review_packet_lines ADD COLUMN voter_status VARCHAR",
            "ALTER TABLE review_packet_lines ADD COLUMN voter_confidence INTEGER",
            "ALTER TABLE review_packet_lines ADD COLUMN voter_reason TEXT",
            "ALTER TABLE review_packet_lines ADD COLUMN fraud_flags TEXT",
            "ALTER TABLE review_packet_lines ADD COLUMN fraud_score INTEGER",
            "ALTER TABLE review_packet_lines ADD COLUMN review_decision VARCHAR",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass
    return sessionmaker(bind=engine)


class Database:
    def __init__(self, url: str = DATABASE_URL):
        self._Session = init_db(url)

    # ── Existing petition methods ─────────────────────────────────────────────

    def save_project(
        self,
        result: ProjectResult,
        county: str = "",
        cause: str = "",
        continuation_of: Optional[str] = None,
    ) -> None:
        with self._Session() as session:
            proj = ProjectRow(
                id=result.project_id,
                pdf_path=result.pdf_path,
                county=county,
                cause=cause,
                total_lines=result.total_lines,
                approved=result.approved,
                review=result.review,
                rejected=result.rejected,
                duplicates=result.duplicates,
                summary_json=json.dumps(result.summary()),
                continuation_of=continuation_of,
            )
            session.merge(proj)

            for vr in result.signatures:
                row = self._vr_to_row(vr, result.project_id)
                session.merge(row)

            session.commit()

    def save_fraud_scan(self, project_id: str, flagged_lines: int, flag_counts: dict) -> None:
        """Attach fraud scan results to an existing project row."""
        with self._Session() as session:
            proj = session.query(ProjectRow).filter_by(id=project_id).first()
            if proj:
                proj.fraud_flagged_lines = flagged_lines
                proj.fraud_flags_json = json.dumps(flag_counts)
                session.commit()

    def get_fraud_alerts(self, threshold_pct: float = 30.0) -> list:
        """
        Return workers whose projects have a suspicious fraud rate.
        A project is flagged if fraud_flagged_lines / filled_lines >= threshold_pct.
        Returns list of dicts with worker info + offending projects.
        """
        with self._Session() as session:
            # Join projects to their worker assignments
            from sqlalchemy import and_
            rows = (
                session.query(ProjectRow, WorkerProjectRow, UserRow)
                .join(WorkerProjectRow, WorkerProjectRow.project_id == ProjectRow.id)
                .join(UserRow, UserRow.id == WorkerProjectRow.worker_id)
                .filter(ProjectRow.fraud_flagged_lines.isnot(None))
                .all()
            )

        alerts_by_worker: dict = {}
        for proj, wp, user in rows:
            filled = proj.total_lines or 1
            pct = (proj.fraud_flagged_lines / filled) * 100
            if pct < threshold_pct:
                continue
            wid = user.id
            if wid not in alerts_by_worker:
                alerts_by_worker[wid] = {
                    "worker_id":   user.id,
                    "worker_name": user.full_name,
                    "email":       user.email,
                    "projects":    [],
                }
            try:
                flags = json.loads(proj.fraud_flags_json or "{}")
            except Exception:
                flags = {}
            alerts_by_worker[wid]["projects"].append({
                "project_id":    proj.id,
                "county":        proj.county,
                "cause":         proj.cause,
                "total_lines":   proj.total_lines,
                "flagged_lines": proj.fraud_flagged_lines,
                "flagged_pct":   round(pct, 1),
                "flag_counts":   flags,
                "uploaded_at":   proj.created_at.isoformat() if proj.created_at else None,
            })

        return list(alerts_by_worker.values())

    def vr_to_row(self, vr: VerificationResult, project_id: str) -> SignatureRow:
        return self._vr_to_row(vr, project_id)

    def _vr_to_row(self, vr: VerificationResult, project_id: str) -> SignatureRow:
        m = vr.best_match
        return SignatureRow(
            id=None,
            project_id=project_id,
            line_number=vr.line_number,
            page=vr.page,
            raw_name=vr.extracted.raw_name,
            raw_address=vr.extracted.raw_address,
            raw_date=vr.extracted.raw_date,
            signature_present=vr.extracted.signature_present,
            ocr_confidence=vr.extracted.ocr_confidence,
            first_name=vr.normalized.first_name,
            last_name=vr.normalized.last_name,
            street=vr.normalized.street,
            city=vr.normalized.city,
            state=vr.normalized.state,
            zip_code=vr.normalized.zip_code,
            voter_id=m.voter_id if m else None,
            voter_name=m.voter_name if m else None,
            voter_address=m.voter_address if m else None,
            match_confidence=m.confidence if m else None,
            name_score=m.name_score if m else None,
            address_score=m.address_score if m else None,
            status=vr.status.value,
            duplicate_of_line=vr.duplicate_of_line,
            staff_override=vr.staff_override.value if vr.staff_override else None,
            staff_voter_id=vr.staff_voter_id,
            staff_notes=vr.staff_notes,
        )

    def get_project_signatures(self, project_id: str) -> list:
        with self._Session() as session:
            return (
                session.query(SignatureRow)
                .filter(SignatureRow.project_id == project_id)
                .order_by(SignatureRow.line_number)
                .all()
            )

    def update_staff_review(
        self,
        project_id: str,
        line_number: int,
        override: Optional[str],
        voter_id: Optional[str],
        notes: str = "",
    ) -> None:
        with self._Session() as session:
            row = (
                session.query(SignatureRow)
                .filter_by(project_id=project_id, line_number=line_number)
                .first()
            )
            if row:
                row.staff_override = override
                row.staff_voter_id = voter_id
                row.staff_notes    = notes
                row.reviewed_at    = datetime.utcnow()
                session.commit()

    def list_projects(self) -> list:
        with self._Session() as session:
            return session.query(ProjectRow).order_by(ProjectRow.created_at.desc()).all()

    def update_project_cause(self, project_id: str, cause: str) -> None:
        with self._Session() as session:
            proj = session.query(ProjectRow).filter_by(id=project_id).first()
            if proj:
                proj.cause = cause
                session.commit()

    def stats_by_cause(self) -> list:
        """Aggregate approved/total signatures grouped by cause + county."""
        with self._Session() as session:
            projects = session.query(ProjectRow).all()
            groups: dict = {}
            for p in projects:
                key = (p.cause or "Unknown", p.county or "Unknown")
                if key not in groups:
                    groups[key] = {"cause": key[0], "county": key[1],
                                   "projects": 0, "total_sigs": 0, "approved_sigs": 0}
                groups[key]["projects"] += 1
                groups[key]["total_sigs"] += p.total_lines or 0
                groups[key]["approved_sigs"] += p.approved or 0
            return list(groups.values())

    def get_project_sig_counts(self, project_id: str) -> dict:
        with self._Session() as session:
            proj = session.query(ProjectRow).filter_by(id=project_id).first()
            if not proj:
                return {"total_sigs": 0, "valid_sigs": 0}
            return {"total_sigs": proj.total_lines or 0, "valid_sigs": proj.approved or 0}

    # ── User methods ──────────────────────────────────────────────────────────

    def create_user(
        self,
        email: str,
        password_hash: str,
        role: str,
        full_name: str,
        phone: str = "",
        hourly_wage: float = 25.0,
    ) -> UserRow:
        with self._Session() as session:
            user = UserRow(
                email=email,
                password_hash=password_hash,
                role=role,
                full_name=full_name,
                phone=phone,
                hourly_wage=hourly_wage,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user

    def get_user_by_email(self, email: str) -> Optional[UserRow]:
        with self._Session() as session:
            user = session.query(UserRow).filter_by(email=email).first()
            if user:
                session.expunge(user)
            return user

    def get_user_by_id(self, user_id: int) -> Optional[UserRow]:
        with self._Session() as session:
            user = session.query(UserRow).filter_by(id=user_id).first()
            if user:
                session.expunge(user)
            return user

    def get_user_by_name(self, full_name: str) -> Optional[UserRow]:
        with self._Session() as session:
            role_priority = case(
                (UserRow.role == 'boss', 0),
                (UserRow.role == 'admin', 1),
                (UserRow.role == 'field_manager', 2),
                (UserRow.role == 'evan', 2),
                (UserRow.role == 'office_worker', 3),
                (UserRow.role == 'worker', 4),
                else_=9,
            )
            user = (
                session.query(UserRow)
                .filter(
                    func.lower(UserRow.full_name) == full_name.lower().strip(),
                    UserRow.is_active == True,
                )
                .order_by(role_priority, UserRow.id)
                .first()
            )
            if user:
                session.expunge(user)
            return user

    def list_users(self, role: Optional[str] = None) -> list:
        with self._Session() as session:
            q = session.query(UserRow)
            if role:
                q = q.filter_by(role=role)
            users = q.order_by(UserRow.full_name).all()
            for u in users:
                session.expunge(u)
            return users

    def update_user_wage(self, user_id: int, wage: float) -> None:
        with self._Session() as session:
            user = session.query(UserRow).filter_by(id=user_id).first()
            if user:
                user.hourly_wage = wage
                session.commit()

    def update_user(self, user_id: int, **kwargs) -> None:
        with self._Session() as session:
            user = session.query(UserRow).filter_by(id=user_id).first()
            if user:
                for k, v in kwargs.items():
                    setattr(user, k, v)
                session.commit()

    # ── Shift methods ─────────────────────────────────────────────────────────

    def clock_in(self, worker_id: int) -> ShiftRow:
        with self._Session() as session:
            now = datetime.utcnow()
            is_weekend = now.weekday() >= 5
            shift = ShiftRow(
                worker_id=worker_id,
                clock_in=now,
                is_weekend=is_weekend,
            )
            session.add(shift)
            session.commit()
            session.refresh(shift)
            session.expunge(shift)
            return shift

    def clock_out(self, worker_id: int) -> ShiftRow:
        with self._Session() as session:
            shift = (
                session.query(ShiftRow)
                .filter_by(worker_id=worker_id, clock_out=None)
                .order_by(ShiftRow.clock_in.desc())
                .first()
            )
            if not shift:
                raise ValueError("No active shift found for worker")
            shift.clock_out = datetime.utcnow()
            session.commit()
            session.refresh(shift)
            session.expunge(shift)
            return shift

    def clock_out_at(self, worker_id: int, clock_out: datetime) -> ShiftRow:
        with self._Session() as session:
            shift = (
                session.query(ShiftRow)
                .filter_by(worker_id=worker_id, clock_out=None)
                .order_by(ShiftRow.clock_in.desc())
                .first()
            )
            if not shift:
                raise ValueError("No active shift found for worker")
            if clock_out <= shift.clock_in:
                raise ValueError("Clock-out time must be after clock-in time")
            shift.clock_out = clock_out
            session.commit()
            session.refresh(shift)
            session.expunge(shift)
            return shift

    def add_manual_shift(self, worker_id: int, clock_in: datetime, clock_out: datetime) -> ShiftRow:
        with self._Session() as session:
            shift = ShiftRow(
                worker_id=worker_id,
                clock_in=clock_in,
                clock_out=clock_out,
                is_weekend=clock_in.weekday() >= 5,
                approved=True,
            )
            session.add(shift)
            session.commit()
            session.refresh(shift)
            session.expunge(shift)
            return shift

    def create_manual_sig_entry(self, worker_id: int, sig_count: int, notes: str = "", sig_date: Optional[datetime] = None) -> str:
        import uuid as _uuid
        project_id = "m-" + str(_uuid.uuid4())[:6]
        entry_time = sig_date or datetime.utcnow()
        with self._Session() as session:
            proj = ProjectRow(
                id=project_id,
                pdf_path="[manual entry]",
                county="",
                cause="",
                total_lines=sig_count,
                approved=sig_count,
                review=0,
                rejected=0,
                duplicates=0,
                summary_json="{}",
                created_at=entry_time,
            )
            session.add(proj)
            for i in range(1, sig_count + 1):
                sig = SignatureRow(
                    project_id=project_id,
                    line_number=i,
                    page=1,
                    raw_name=f"Manual entry {i}",
                    raw_address="",
                    raw_date="",
                    signature_present=True,
                    status="approved",
                    staff_notes=notes,
                )
                session.add(sig)
            wp = WorkerProjectRow(
                worker_id=worker_id,
                project_id=project_id,
                manual_sig_count=sig_count,
            )
            session.add(wp)
            session.commit()
        return project_id

    def get_worker_daily_sigs(
        self,
        worker_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """Return {"YYYY-MM-DD": total_approved_sigs} for manual sig entries for this worker."""
        with self._Session() as session:
            q = (
                session.query(WorkerProjectRow, ProjectRow)
                .join(ProjectRow, ProjectRow.id == WorkerProjectRow.project_id)
                .filter(
                    WorkerProjectRow.worker_id == worker_id,
                    ProjectRow.pdf_path == "[manual entry]",
                )
            )
            if date_from:
                q = q.filter(ProjectRow.created_at >= date_from)
            if date_to:
                q = q.filter(ProjectRow.created_at <= date_to)
            rows = q.all()

        daily: dict = {}
        for _wp, proj in rows:
            day = proj.created_at.strftime("%Y-%m-%d") if proj.created_at else "1970-01-01"
            daily[day] = daily.get(day, 0) + (proj.approved or 0)
        return daily

    def get_active_shift(self, worker_id: int) -> Optional[ShiftRow]:
        with self._Session() as session:
            shift = (
                session.query(ShiftRow)
                .filter_by(worker_id=worker_id, clock_out=None)
                .order_by(ShiftRow.clock_in.desc())
                .first()
            )
            if shift:
                session.expunge(shift)
            return shift

    def list_shifts(
        self,
        worker_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> list:
        with self._Session() as session:
            q = session.query(ShiftRow)
            if worker_id is not None:
                q = q.filter_by(worker_id=worker_id)
            if date_from:
                q = q.filter(ShiftRow.clock_in >= date_from)
            if date_to:
                q = q.filter(ShiftRow.clock_in <= date_to)
            shifts = q.order_by(ShiftRow.clock_in.desc()).all()
            for s in shifts:
                session.expunge(s)
            return shifts

    def approve_shift(self, shift_id: int, approved_by_id: int) -> None:
        with self._Session() as session:
            shift = session.query(ShiftRow).filter_by(id=shift_id).first()
            if shift:
                shift.approved = True
                shift.approved_by = approved_by_id
                session.commit()

    def update_shift(self, shift_id: int, **kwargs) -> None:
        with self._Session() as session:
            shift = session.query(ShiftRow).filter_by(id=shift_id).first()
            if shift:
                for k, v in kwargs.items():
                    setattr(shift, k, v)
                session.commit()

    # ── Schedule request methods ──────────────────────────────────────────────

    def create_schedule_request(
        self,
        worker_id: int,
        week_of: str,
        preferred_days: list,
        preferred_hours: str,
        notes: str,
    ) -> ScheduleRequestRow:
        with self._Session() as session:
            req = ScheduleRequestRow(
                worker_id=worker_id,
                week_of=week_of,
                preferred_days=json.dumps(preferred_days),
                preferred_hours=preferred_hours,
                notes=notes,
            )
            session.add(req)
            session.commit()
            session.refresh(req)
            session.expunge(req)
            return req

    def list_schedule_requests(self, status: Optional[str] = None, worker_id: Optional[int] = None) -> list:
        with self._Session() as session:
            q = session.query(ScheduleRequestRow)
            if status:
                q = q.filter_by(status=status)
            if worker_id is not None:
                q = q.filter_by(worker_id=worker_id)
            reqs = q.order_by(ScheduleRequestRow.id.desc()).all()
            for r in reqs:
                session.expunge(r)
            return reqs

    def update_schedule_request(self, req_id: int, status: str) -> None:
        with self._Session() as session:
            req = session.query(ScheduleRequestRow).filter_by(id=req_id).first()
            if req:
                req.status = status
                session.commit()

    # ── Payment preference methods ────────────────────────────────────────────

    def set_payment_preference(
        self, worker_id: int, method: str, details: str
    ) -> PaymentPreferenceRow:
        with self._Session() as session:
            pref = session.query(PaymentPreferenceRow).filter_by(worker_id=worker_id).first()
            if pref:
                pref.method = method
                pref.details = details
                pref.updated_at = datetime.utcnow()
            else:
                pref = PaymentPreferenceRow(
                    worker_id=worker_id,
                    method=method,
                    details=details,
                )
                session.add(pref)
            session.commit()
            session.refresh(pref)
            session.expunge(pref)
            return pref

    def get_payment_preference(self, worker_id: int) -> Optional[PaymentPreferenceRow]:
        with self._Session() as session:
            pref = session.query(PaymentPreferenceRow).filter_by(worker_id=worker_id).first()
            if pref:
                session.expunge(pref)
            return pref

    # ── Worker project methods ────────────────────────────────────────────────

    def assign_project_to_worker(
        self,
        worker_id: int,
        project_id: str,
        assigned_by_id: Optional[int] = None,
        manual_sig_count: Optional[int] = None,
    ) -> WorkerProjectRow:
        with self._Session() as session:
            # Remove any existing assignment for this project
            existing = session.query(WorkerProjectRow).filter_by(project_id=project_id).first()
            if existing:
                session.delete(existing)
            wp = WorkerProjectRow(
                worker_id=worker_id,
                project_id=project_id,
                assigned_by=assigned_by_id,
                manual_sig_count=manual_sig_count,
            )
            session.add(wp)
            session.commit()
            session.refresh(wp)
            session.expunge(wp)
            return wp

    def get_all_sheets_by_cause(self, cause: str, county: str) -> list:
        """
        Return (ProjectRow, [SignatureRow]) pairs for ALL uploaded sheets
        (across all workers) matching the given cause + county.
        Used for same-sheet detection — sheets can be mixed up between workers.
        """
        with self._Session() as session:
            query = session.query(ProjectRow)
            # Loose cause/county match — empty values match anything
            projects = query.all()
            results = []
            for proj in projects:
                cause_match = (not cause or not proj.cause or
                               proj.cause.lower() == cause.lower())
                county_match = (not county or not proj.county or
                                proj.county.lower() == county.lower())
                if not cause_match or not county_match:
                    continue
                sigs = (
                    session.query(SignatureRow)
                    .filter_by(project_id=proj.id)
                    .order_by(SignatureRow.line_number)
                    .all()
                )
                session.expunge(proj)
                for s in sigs:
                    session.expunge(s)
                results.append((proj, sigs))
            return results

    def update_manual_sig_count(self, worker_id: int, project_id: str, count: int) -> None:
        with self._Session() as session:
            wp = session.query(WorkerProjectRow).filter_by(
                project_id=project_id, worker_id=worker_id
            ).first()
            if not wp:
                raise ValueError("Assignment not found")
            wp.manual_sig_count = count
            session.commit()

    def get_worker_projects(self, worker_id: int) -> list:
        with self._Session() as session:
            wps = (
                session.query(WorkerProjectRow)
                .filter_by(worker_id=worker_id)
                .order_by(WorkerProjectRow.assigned_at.desc())
                .all()
            )
            for wp in wps:
                session.expunge(wp)
            return wps

    def get_project_worker(self, project_id: str) -> Optional[WorkerProjectRow]:
        with self._Session() as session:
            wp = session.query(WorkerProjectRow).filter_by(project_id=project_id).first()
            if wp:
                session.expunge(wp)
            return wp

    # ── Pay period methods ────────────────────────────────────────────────────

    def create_pay_period(self, start_date: str, end_date: str) -> PayPeriodRow:
        with self._Session() as session:
            pp = PayPeriodRow(start_date=start_date, end_date=end_date)
            session.add(pp)
            session.commit()
            session.refresh(pp)
            session.expunge(pp)
            return pp

    def list_pay_periods(self) -> list:
        with self._Session() as session:
            pps = session.query(PayPeriodRow).order_by(PayPeriodRow.id.desc()).all()
            for pp in pps:
                session.expunge(pp)
            return pps

    def get_pay_period(self, pay_period_id: int) -> Optional[PayPeriodRow]:
        with self._Session() as session:
            pp = session.query(PayPeriodRow).filter_by(id=pay_period_id).first()
            if pp:
                session.expunge(pp)
            return pp

    def update_pay_period_status(self, pay_period_id: int, status: str) -> None:
        with self._Session() as session:
            pp = session.query(PayPeriodRow).filter_by(id=pay_period_id).first()
            if pp:
                pp.status = status
                session.commit()

    # ── Payroll record methods ────────────────────────────────────────────────

    def save_payroll_record(self, record: PayrollRecordRow) -> PayrollRecordRow:
        with self._Session() as session:
            # Check for existing record for same worker+period
            existing = (
                session.query(PayrollRecordRow)
                .filter_by(worker_id=record.worker_id, pay_period_id=record.pay_period_id)
                .first()
            )
            if existing:
                existing.total_hours      = record.total_hours
                existing.total_signatures = record.total_signatures
                existing.valid_signatures = record.valid_signatures
                existing.validity_rate    = record.validity_rate
                existing.hourly_wage_used = record.hourly_wage_used
                existing.base_pay_cents   = record.base_pay_cents
                existing.bonus_cents      = record.bonus_cents
                existing.gross_cents      = record.gross_cents
                existing.tax_cents        = record.tax_cents
                existing.net_cents        = record.net_cents
                existing.earns_lunch      = record.earns_lunch
                existing.breakdown_json   = record.breakdown_json
                existing.calculated_at    = datetime.utcnow()
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            else:
                session.add(record)
                session.commit()
                session.refresh(record)
                session.expunge(record)
                return record

    def get_payroll_records(
        self,
        worker_id: Optional[int] = None,
        pay_period_id: Optional[int] = None,
    ) -> list:
        with self._Session() as session:
            q = session.query(PayrollRecordRow)
            if worker_id is not None:
                q = q.filter_by(worker_id=worker_id)
            if pay_period_id is not None:
                q = q.filter_by(pay_period_id=pay_period_id)
            records = q.order_by(PayrollRecordRow.calculated_at.desc()).all()
            for r in records:
                session.expunge(r)
            return records

    # ── Signature count helpers for payroll ──────────────────────────────────

    def get_project_sig_counts(self, project_id: str) -> dict:
        """Return total, valid, and ink-detected signature counts for a project."""
        with self._Session() as session:
            sigs = (
                session.query(SignatureRow)
                .filter_by(project_id=project_id)
                .all()
            )
            total = len(sigs)
            valid = sum(
                1 for s in sigs
                if (s.staff_override or s.status) == "approved"
            )
            signed = sum(1 for s in sigs if s.signature_present)
            return {"total_sigs": total, "valid_sigs": valid, "signed_sigs": signed}

    def get_all_worker_sig_counts(self) -> dict:
        """Return sig counts for ALL workers in 1 query using pre-computed ProjectRow totals.
        Returns {worker_id: {"total_sigs": int, "valid_sigs": int}}
        """
        with self._Session() as session:
            # Single join: worker_projects → projects (uses pre-computed approved/total_lines)
            rows = (
                session.query(
                    WorkerProjectRow.worker_id,
                    func.sum(ProjectRow.total_lines).label("total"),
                    func.sum(ProjectRow.approved).label("valid"),
                )
                .join(ProjectRow, ProjectRow.id == WorkerProjectRow.project_id)
                .group_by(WorkerProjectRow.worker_id)
                .all()
            )
            return {
                r.worker_id: {"total_sigs": int(r.total or 0), "valid_sigs": int(r.valid or 0)}
                for r in rows
            }

    def get_all_active_shifts(self) -> dict:
        """Return active shifts for ALL workers in 1 query.
        Returns {worker_id: ShiftRow}
        """
        with self._Session() as session:
            shifts = (
                session.query(ShiftRow)
                .filter(ShiftRow.clock_out.is_(None))
                .all()
            )
            result = {}
            for s in shifts:
                # Keep most recent if duplicates
                if s.worker_id not in result or s.clock_in > result[s.worker_id].clock_in:
                    session.expunge(s)
                    result[s.worker_id] = s
            return result

    # ── Location pin methods ──────────────────────────────────────────────────

    def drop_pin(self, worker_id: int, lat: float, lng: float, note: str = "") -> WorkerLocationRow:
        with self._Session() as session:
            pin = WorkerLocationRow(worker_id=worker_id, lat=lat, lng=lng, note=note)
            session.add(pin)
            session.commit()
            session.refresh(pin)
            session.expunge(pin)
            return pin

    def get_all_pins(self, limit: int = 200) -> list:
        with self._Session() as session:
            pins = (
                session.query(WorkerLocationRow)
                .order_by(WorkerLocationRow.pinned_at.desc())
                .limit(limit)
                .all()
            )
            for p in pins:
                session.expunge(p)
            return pins

    def upsert_live_sig_count(self, worker_id: int, count: int) -> None:
        with self._Session() as session:
            row = session.query(LiveSigCountRow).filter(LiveSigCountRow.worker_id == worker_id).first()
            if row:
                row.sig_count = count
                row.updated_at = datetime.utcnow()
            else:
                session.add(LiveSigCountRow(worker_id=worker_id, sig_count=count))
            session.commit()

    def get_all_live_sig_counts(self) -> list:
        with self._Session() as session:
            rows = session.query(LiveSigCountRow).all()
            result = [{"worker_id": r.worker_id, "sig_count": r.sig_count} for r in rows]
            return result

    def get_live_sig_count(self, worker_id: int) -> int:
        with self._Session() as session:
            row = session.query(LiveSigCountRow).filter(LiveSigCountRow.worker_id == worker_id).first()
            return row.sig_count if row else 0

    def get_setting(self, key: str, default: str = "") -> str:
        try:
            with self._Session() as session:
                row = session.query(AppSettingRow).filter(AppSettingRow.key == key).first()
                return row.value if row else default
        except Exception:
            return default

    def set_setting(self, key: str, value: str) -> None:
        try:
            with self._Session() as session:
                row = session.query(AppSettingRow).filter(AppSettingRow.key == key).first()
                if row:
                    row.value = value
                else:
                    session.add(AppSettingRow(key=key, value=value))
                session.commit()
        except Exception:
            pass

    def delete_worker_pins(self, worker_id: int) -> int:
        with self._Session() as session:
            deleted = (
                session.query(WorkerLocationRow)
                .filter(WorkerLocationRow.worker_id == worker_id)
                .delete()
            )
            session.commit()
            return deleted

    # ── Review packet methods ─────────────────────────────────────────────────

    def create_packet(self, worker_id: int, original_name: str, raw_path: str, shift_id: int | None = None) -> int:
        with self._Session() as session:
            pkt = PacketRow(
                worker_id=worker_id,
                original_name=original_name,
                raw_path=raw_path,
                status="processing",
            )
            try:
                pkt.shift_id = shift_id
            except Exception:
                pass
            session.add(pkt)
            try:
                session.commit()
            except Exception:
                session.rollback()
                # Fallback: insert without shift_id if column doesn't exist yet
                pkt2 = PacketRow(
                    worker_id=worker_id,
                    original_name=original_name,
                    raw_path=raw_path,
                    status="processing",
                )
                session.add(pkt2)
                session.commit()
                session.refresh(pkt2)
                return pkt2.id
            session.refresh(pkt)
            return pkt.id

    def list_packets(self) -> list:
        with self._Session() as session:
            pkts = session.query(PacketRow).order_by(PacketRow.uploaded_at.desc()).all()
            for p in pkts:
                session.expunge(p)
            return pkts

    def get_packet_detail(self, packet_id: int) -> tuple:
        with self._Session() as session:
            pkt = session.query(PacketRow).filter_by(id=packet_id).first()
            if not pkt:
                return None, []
            lines = (
                session.query(PacketLineRow)
                .filter_by(packet_id=packet_id)
                .order_by(PacketLineRow.line_no)
                .all()
            )
            session.expunge(pkt)
            for l in lines:
                session.expunge(l)
            return pkt, lines

    def finish_packet(
        self,
        packet_id: int,
        cleaned_path: str,
        lines: list,
        page_fingerprint: str = "",
        new_sigs: int = 0,
        already_counted: int = 0,
        needs_review: int = 0,
        result_json: str = "{}",
    ) -> None:
        with self._Session() as session:
            pkt = session.query(PacketRow).filter_by(id=packet_id).first()
            if pkt:
                pkt.cleaned_path     = cleaned_path
                pkt.status           = "done"
                pkt.total_lines      = len(lines)
                pkt.page_fingerprint = page_fingerprint
                pkt.new_sigs         = new_sigs
                pkt.already_counted  = already_counted
                pkt.needs_review     = needs_review
                pkt.result_json      = result_json
            for l in lines:
                session.add(l)
            session.commit()

    def get_prev_rows_for_fingerprint(
        self,
        page_fingerprint: str,
        exclude_packet_id: Optional[int] = None,
        hamming_threshold: int = 10,
    ) -> list[dict]:
        """
        Return rows from previous uploads of the same physical page.
        Matches using Hamming distance on page_fingerprint dHash.
        """
        try:
            fp_int = int(page_fingerprint, 16)
        except ValueError:
            return []

        with self._Session() as session:
            packets = session.query(PacketRow).filter(
                PacketRow.status == "done",
                PacketRow.page_fingerprint != "",
            ).all()
            matching_ids = []
            for p in packets:
                if exclude_packet_id and p.id == exclude_packet_id:
                    continue
                try:
                    diff = bin(fp_int ^ int(p.page_fingerprint, 16)).count("1")
                    if diff <= hamming_threshold:
                        matching_ids.append(p.id)
                except ValueError:
                    continue

            if not matching_ids:
                return []

            rows = (
                session.query(PacketLineRow)
                .filter(
                    PacketLineRow.packet_id.in_(matching_ids),
                    PacketLineRow.row_status.in_(["new_signature", "already_counted"]),
                )
                .all()
            )
            result = []
            for r in rows:
                result.append({
                    "row_number":      r.line_no,
                    "row_fingerprint": r.row_fingerprint,
                    "status":          r.row_status,
                    "name":            {"normalized": r.norm_name, "raw": r.raw_name},
                    "street_address":  {"normalized": r.norm_address, "raw": r.raw_address},
                })
            return result

    def fail_packet(self, packet_id: int, error: str) -> None:
        with self._Session() as session:
            pkt = session.query(PacketRow).filter_by(id=packet_id).first()
            if pkt:
                pkt.status = "failed"
                pkt.error_msg = str(error)[:500]
                session.commit()

    def set_packet_line_action(
        self, packet_id: int, line_no: int, action: str, reviewer_id: int
    ) -> None:
        with self._Session() as session:
            line = (
                session.query(PacketLineRow)
                .filter_by(packet_id=packet_id, line_no=line_no)
                .first()
            )
            if line:
                line.action = action
                line.reviewed_by = reviewer_id
                line.reviewed_at = datetime.utcnow()
                session.commit()

    def approve_all_valid_lines(self, packet_id: int, reviewer_id: int) -> int:
        return self.approve_all_new_sigs(packet_id, reviewer_id)

    def approve_all_new_sigs(self, packet_id: int, reviewer_id: int) -> int:
        """Approve all rows classified as new_signature."""
        with self._Session() as session:
            lines = (
                session.query(PacketLineRow)
                .filter_by(packet_id=packet_id, row_status="new_signature")
                .all()
            )
            n = 0
            for l in lines:
                if not l.action:
                    l.action      = "approved"
                    l.reviewed_by = reviewer_id
                    l.reviewed_at = datetime.utcnow()
                    n += 1
            session.commit()
            return n

    def save_county(self, packet_id: int, county: str) -> None:
        with self._Session() as session:
            pkt = session.query(PacketRow).filter_by(id=packet_id).first()
            if pkt:
                pkt.county = county
                session.commit()

    def save_voter_roll(self, packet_id: int, text: str) -> None:
        with self._Session() as session:
            pkt = session.query(PacketRow).filter_by(id=packet_id).first()
            if pkt:
                pkt.voter_roll_text = text
                session.commit()

    def bulk_update_voter_match(self, results: list[dict]) -> None:
        """results: list of {line_id, voter_status, voter_confidence, voter_reason}"""
        with self._Session() as session:
            for r in results:
                line = session.query(PacketLineRow).filter_by(id=r["line_id"]).first()
                if line:
                    line.voter_status     = r["voter_status"]
                    line.voter_confidence = r["voter_confidence"]
                    line.voter_reason     = r.get("voter_reason", "")
            session.commit()

    def bulk_update_fraud(self, results: list[dict]) -> None:
        """results: list of {line_id, fraud_flags (list), fraud_score}"""
        with self._Session() as session:
            for r in results:
                line = session.query(PacketLineRow).filter_by(id=r["line_id"]).first()
                if line:
                    line.fraud_flags = json.dumps(r.get("fraud_flags", []))
                    line.fraud_score = r.get("fraud_score", 0)
            session.commit()

    def set_line_review_decision(self, packet_id: int, line_no: int, decision: str) -> None:
        with self._Session() as session:
            line = session.query(PacketLineRow).filter_by(
                packet_id=packet_id, line_no=line_no
            ).first()
            if line:
                line.review_decision = decision
                session.commit()

    # ── Teams ─────────────────────────────────────────────────────────────────

    def create_team(self, name: str, manager_id: int) -> int:
        with self._Session() as session:
            team = TeamRow(name=name, manager_id=manager_id)
            session.add(team)
            session.commit()
            session.refresh(team)
            return team.id

    def get_team(self, team_id: int):
        with self._Session() as session:
            t = session.query(TeamRow).filter_by(id=team_id).first()
            if t:
                session.expunge(t)
            return t

    def get_team_by_manager(self, manager_id: int):
        with self._Session() as session:
            t = session.query(TeamRow).filter_by(manager_id=manager_id).first()
            if t:
                session.expunge(t)
            return t

    def set_user_team(self, user_id: int, team_id) -> None:
        with self._Session() as session:
            u = session.query(UserRow).filter_by(id=user_id).first()
            if u:
                u.team_id = team_id
                session.commit()

    def get_team_detail(self, team_id: int) -> dict | None:
        with self._Session() as session:
            team = session.query(TeamRow).filter_by(id=team_id).first()
            if not team:
                return None
            members = session.query(UserRow).filter_by(team_id=team_id).all()
            sig_counts = {r.worker_id: r.sig_count for r in session.query(LiveSigCountRow).all()}
            manager = session.query(UserRow).filter_by(id=team.manager_id).first() if team.manager_id else None
            return {
                "id":           team.id,
                "name":         team.name,
                "manager_id":   team.manager_id,
                "manager_name": manager.full_name if manager else "",
                "total_sigs":   sum(sig_counts.get(m.id, 0) for m in members),
                "members": [
                    {"id": m.id, "full_name": m.full_name, "role": m.role,
                     "sig_count": sig_counts.get(m.id, 0)}
                    for m in members
                ],
            }

    def get_team_leaderboard(self) -> list[dict]:
        with self._Session() as session:
            teams    = session.query(TeamRow).all()
            sig_counts = {r.worker_id: r.sig_count for r in session.query(LiveSigCountRow).all()}
            result = []
            for team in teams:
                members = session.query(UserRow).filter_by(team_id=team.id).all()
                total   = sum(sig_counts.get(m.id, 0) for m in members)
                manager = session.query(UserRow).filter_by(id=team.manager_id).first() if team.manager_id else None
                result.append({
                    "id":           team.id,
                    "name":         team.name,
                    "manager_name": manager.full_name if manager else "",
                    "manager_id":   team.manager_id,
                    "member_count": len(members),
                    "total_sigs":   total,
                    "members": [
                        {"id": m.id, "full_name": m.full_name, "role": m.role,
                         "sig_count": sig_counts.get(m.id, 0)}
                        for m in members
                    ],
                })
            result.sort(key=lambda t: t["total_sigs"], reverse=True)
            return result

    def get_unassigned_workers(self) -> list:
        with self._Session() as session:
            users = (
                session.query(UserRow)
                .filter(
                    UserRow.team_id.is_(None),
                    UserRow.is_active == True,
                    UserRow.role.in_(["petitioner", "worker", "office_worker"]),
                )
                .order_by(UserRow.full_name)
                .all()
            )
            for u in users:
                session.expunge(u)
            return users

    def get_total_valid_sigs(self) -> int:
        with self._Session() as session:
            result = session.query(func.sum(ProjectRow.approved)).scalar()
            return int(result or 0)

    # ── Shift reflections ─────────────────────────────────────────────────────

    def save_shift_reflection(
        self, worker_id: int, shift_id: int | None,
        sigs_reported: int, hours_worked: float,
        hit_goal: bool, reflection: str, notes: str,
    ) -> int:
        with self._Session() as session:
            row = ShiftReflectionRow(
                worker_id=worker_id, shift_id=shift_id,
                sigs_reported=sigs_reported, hours_worked=hours_worked,
                hit_goal=hit_goal, reflection=reflection, notes=notes,
            )
            session.add(row)
            session.commit()
            return row.id

    def get_worker_reflections(self, worker_id: int, limit: int = 30) -> list:
        with self._Session() as session:
            rows = (
                session.query(ShiftReflectionRow)
                .filter_by(worker_id=worker_id)
                .order_by(ShiftReflectionRow.created_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for r in rows:
                session.expunge(r)
                result.append(r)
            return result

    def get_all_reflections(self, limit: int = 200) -> list:
        with self._Session() as session:
            rows = (
                session.query(ShiftReflectionRow)
                .order_by(ShiftReflectionRow.created_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for r in rows:
                session.expunge(r)
                result.append(r)
            return result

    def delete_reflection(self, reflection_id: int) -> bool:
        with self._Session() as session:
            row = session.query(ShiftReflectionRow).filter_by(id=reflection_id).first()
            if not row:
                return False
            session.delete(row)
            session.commit()
            return True

    def get_all_today_shifts(self, today_start: datetime) -> dict:
        """Return today's shifts for ALL workers in 1 query.
        Returns {worker_id: [ShiftRow, ...]}
        """
        with self._Session() as session:
            shifts = (
                session.query(ShiftRow)
                .filter(ShiftRow.clock_in >= today_start)
                .order_by(ShiftRow.clock_in)
                .all()
            )
            result: dict = {}
            for s in shifts:
                session.expunge(s)
                result.setdefault(s.worker_id, []).append(s)
            return result

