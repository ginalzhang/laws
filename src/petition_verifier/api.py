"""
FastAPI backend for the review UI.

Endpoints:
  GET  /projects                           List all projects
  GET  /projects/{id}/signatures           All sigs for a project (paginated)
  GET  /projects/{id}/signatures/{line}    One sig detail
  POST /projects/{id}/signatures/{line}/review  Staff correction
  POST /process                            Process a petition photo/PDF (with voter roll upload)
  POST /projects/{id}/process             Process a new PDF (legacy, server-path voter roll)
  GET  /projects/{id}/export              Download CSV of results
  GET  /                                   Serve the review UI (index.html)
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .storage import db
from .auth import get_current_user
from .routes.auth_routes import router as auth_router
from .routes.worker_routes import router as worker_router
from .routes.shift_routes import router as shift_router
from .routes.schedule_routes import router as schedule_router
from .routes.payroll_routes import router as payroll_router
from .routes.leaderboard_routes import router as leaderboard_router
from .routes.payment_routes import router as payment_router
from .routes.location_routes import router as location_router
from .routes.stats_routes import router as stats_router
from .routes.review_routes import router as review_router
from .routes.team_routes import router as team_router
from .routes.reflection_routes import router as reflection_router

app  = FastAPI(title="Petition Verifier", version="0.2.0")

# ── Hardcoded permanent accounts ─────────────────────────────────────────────
# Permanent accounts — recreated/updated on every startup so role/password stay correct.
_PERMANENT_USERS = [
    {"email": "arianafan2000@app.local", "password": "arianafan2000", "role": "boss", "full_name": "arianafan2000"},
    {"email": "evan@app.local",          "password": "evan",          "role": "evan", "full_name": "evann"},
]

@app.on_event("startup")
async def ensure_permanent_users():
    from .auth import hash_password
    for u in _PERMANENT_USERS:
        existing = db.get_user_by_email(u["email"])
        if not existing:
            db.create_user(u["email"], hash_password(u["password"]), u["role"], u["full_name"])
        else:
            # Keep password and role in sync on every deploy
            db.update_user(existing.id, password_hash=hash_password(u["password"]), role=u["role"], full_name=u["full_name"], is_active=True)
    # Seed default FM team password if not already set
    if not db.get_setting("fm_password"):
        db.set_setting("fm_password", "seals")
    # Remove old hardcoded Kay Kay entry left over from previous deploys
    old_kaykay = db.get_user_by_email("kaykay@app.local")
    if old_kaykay:
        db.update_user(old_kaykay.id, is_active=False)

_UI_DIR = Path(__file__).parent.parent.parent / "ui"
if _UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(worker_router, prefix="/workers", tags=["workers"])
app.include_router(shift_router, prefix="/shifts", tags=["shifts"])
app.include_router(schedule_router, prefix="/schedule", tags=["schedule"])
app.include_router(payroll_router, prefix="/payroll", tags=["payroll"])
app.include_router(leaderboard_router, tags=["leaderboard"])
app.include_router(payment_router, prefix="/payment-preferences", tags=["payment"])
app.include_router(location_router, prefix="/locations", tags=["locations"])
app.include_router(stats_router, prefix="/stats", tags=["stats"])
app.include_router(review_router, tags=["review"])
app.include_router(team_router)
app.include_router(reflection_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/stats/live-count")
async def live_sig_count():
    """Total approved signatures across all projects — poll for live updates."""
    return {"total_valid_sigs": db.get_total_valid_sigs()}


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_same_sheet(extracted, cause: str, county: str) -> tuple:
    """
    Search ALL previously uploaded sheets (across all workers) for the same
    physical paper.  Sheets can get mixed up between workers, so we don't
    restrict by worker_id.

    Returns (original_project_id, new_extractions, already_counted_count)
    or (None, extracted, 0) if no match is found.

    A sheet is considered "the same paper" when ≥50% of its previously-filled
    lines fuzzy-match a filled line at the same line number in this upload.
    New signatures are lines that were blank in the previous upload but now
    have content.
    """
    from rapidfuzz import fuzz as _fuzz

    previous = db.get_all_sheets_by_cause(cause, county)
    if not previous:
        return None, extracted, 0

    current_by_line = {e.line_number: e for e in extracted}

    best_match_id   = None
    best_new_sigs   = extracted
    best_already    = 0
    best_score      = 0.0

    for proj, prev_sigs in previous:
        prev_filled = [s for s in prev_sigs if s.raw_name.strip() or s.raw_address.strip()]
        if not prev_filled:
            continue

        prev_by_line = {s.line_number: s for s in prev_sigs}
        matches = 0
        for ps in prev_filled:
            ce = current_by_line.get(ps.line_number)
            if not ce:
                continue
            name_sim = _fuzz.token_sort_ratio(
                ps.raw_name.strip().lower(), ce.raw_name.strip().lower()
            ) if ps.raw_name.strip() and ce.raw_name.strip() else 0
            addr_sim = _fuzz.token_sort_ratio(
                ps.raw_address.strip().lower(), ce.raw_address.strip().lower()
            ) if ps.raw_address.strip() and ce.raw_address.strip() else 0
            if max(name_sim, addr_sim) >= 75:
                matches += 1

        score = matches / len(prev_filled)
        if score >= 0.50 and score > best_score:
            best_score = score
            new_sigs = []
            already  = 0
            for e in extracted:
                ps = prev_by_line.get(e.line_number)
                if not ps:
                    if e.raw_name.strip() or e.raw_address.strip():
                        new_sigs.append(e)
                    continue
                was_blank   = not ps.raw_name.strip() and not ps.raw_address.strip()
                has_content = bool(e.raw_name.strip() or e.raw_address.strip())
                if was_blank and has_content:
                    new_sigs.append(e)
                elif has_content:
                    already += 1
            best_match_id = proj.id
            best_new_sigs = new_sigs
            best_already  = already

    return best_match_id, best_new_sigs, best_already


def _detect_cause(petition_path: Path) -> str:
    """
    Extract the petition initiative/cause name from the top of the page.
    Vision already reads the full page — we grab the first meaningful text
    before the signer rows begin (top ~15% of image height).
    Returns a short title string, or "" if nothing detected.
    """
    try:
        from .ingestion.vision import _load_images_pil, _vision_words
        images = _load_images_pil(petition_path)
        if not images:
            return ""
        image = images[0]
        words = _vision_words(image)
        # Grab words in the top 15% of the image
        cutoff_y = image.height * 0.15
        top_words = [w for w in words if w.top < cutoff_y]
        # Filter out pure numbers and very short tokens
        import re as _re
        content = [w.text for w in sorted(top_words, key=lambda w: (w.top, w.left))
                   if not _re.match(r"^[\d\.\-\:\,]+$", w.text) and len(w.text) > 1]
        if not content:
            return ""
        # Take first 8 meaningful words as the cause title
        title = " ".join(content[:8]).strip()
        return title
    except Exception:
        return ""


def _row_to_dict(row) -> dict:
    return {
        "line_number":       row.line_number,
        "page":              row.page,
        "raw_name":          row.raw_name,
        "raw_address":       row.raw_address,
        "raw_date":          row.raw_date,
        "signature_present": row.signature_present,
        "first_name":        row.first_name,
        "last_name":         row.last_name,
        "street":            row.street,
        "city":              row.city,
        "state":             row.state,
        "zip_code":          row.zip_code,
        "voter_id":          row.voter_id,
        "voter_name":        row.voter_name,
        "voter_address":     row.voter_address,
        "match_confidence":  row.match_confidence,
        "name_score":        row.name_score,
        "address_score":     row.address_score,
        "status":            row.staff_override or row.status,
        "auto_status":       row.status,
        "duplicate_of_line": row.duplicate_of_line,
        "staff_notes":       row.staff_notes or "",
    }


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    login = _UI_DIR / "login.html"
    if login.exists():
        return HTMLResponse(login.read_text())
    return HTMLResponse("<h1>Petition Verifier API</h1><p>UI not found.</p>")


@app.get("/canvasser", response_class=HTMLResponse)
async def canvasser_page():
    page = _UI_DIR / "canvasser.html"
    if page.exists():
        return HTMLResponse(page.read_text())
    return HTMLResponse("<h1>Canvasser page not found</h1>", status_code=404)


@app.get("/field-manager", response_class=HTMLResponse)
async def field_manager_page():
    page = _UI_DIR / "field-manager.html"
    if page.exists():
        return HTMLResponse(page.read_text())
    return HTMLResponse("<h1>Field manager page not found</h1>", status_code=404)


@app.get("/evann", response_class=HTMLResponse)
async def evann_page():
    page = _UI_DIR / "evann.html"
    if page.exists():
        return HTMLResponse(page.read_text())
    return HTMLResponse("<h1>Page not found</h1>", status_code=404)


@app.get("/projects")
async def list_projects():
    rows = db.list_projects()
    return [
        {
            "id":          r.id,
            "pdf_path":    r.pdf_path,
            "county":      r.county or "",
            "created_at":  r.created_at.isoformat() if r.created_at else None,
            "total_lines": r.total_lines,
            "approved":    r.approved,
            "review":      r.review,
            "rejected":    r.rejected,
            "duplicates":  r.duplicates,
        }
        for r in rows
    ]


@app.get("/projects/{project_id}/signatures")
async def list_signatures(
    project_id: str,
    status: Optional[str] = None,   # filter: approved|review|rejected|duplicate
    page: int = 1,
    page_size: int = 50,
):
    rows = db.get_project_signatures(project_id)
    if status:
        rows = [r for r in rows if (r.staff_override or r.status) == status]
    total  = len(rows)
    offset = (page - 1) * page_size
    rows   = rows[offset: offset + page_size]
    return {
        "total":    total,
        "page":     page,
        "page_size": page_size,
        "items":    [_row_to_dict(r) for r in rows],
    }


@app.get("/projects/{project_id}/signatures/{line_number}")
async def get_signature(project_id: str, line_number: int):
    rows = db.get_project_signatures(project_id)
    for r in rows:
        if r.line_number == line_number:
            return _row_to_dict(r)
    raise HTTPException(404, "Signature not found")


class ReviewPayload(BaseModel):
    override: Optional[str] = None      # approved|review|rejected|duplicate
    voter_id: Optional[str] = None
    notes: str = ""


@app.post("/projects/{project_id}/signatures/{line_number}/review")
async def review_signature(
    project_id: str,
    line_number: int,
    payload: ReviewPayload,
):
    db.update_staff_review(
        project_id=project_id,
        line_number=line_number,
        override=payload.override,
        voter_id=payload.voter_id,
        notes=payload.notes,
    )
    return {"ok": True}


@app.post("/process")
async def process_petition(
    project_id:      Optional[str]      = Form(None),
    county:          str                = Form("", description="County name for this petition"),
    voter_roll:      Optional[str]      = Form(None, description="Path to voter roll CSV on server"),
    voter_roll_file: Optional[UploadFile] = File(None, description="Voter roll CSV file upload"),
    petition:        UploadFile         = File(..., description="Petition photo or PDF"),
):
    """Accept a petition photo/PDF and optional voter roll, run the pipeline, save to DB."""
    from .pipeline import Pipeline

    project_id = project_id or str(uuid.uuid4())[:8]

    # ── Resolve voter roll ────────────────────────────────────────────────────
    voter_roll_tmp: Optional[Path] = None
    if voter_roll_file and voter_roll_file.filename:
        suffix = Path(voter_roll_file.filename).suffix or ".csv"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as vr:
            vr.write(await voter_roll_file.read())
            voter_roll_tmp = Path(vr.name)
        voter_roll_path: Optional[Path] = voter_roll_tmp
    elif voter_roll:
        voter_roll_path = Path(voter_roll)
        if not voter_roll_path.exists():
            raise HTTPException(400, f"Voter roll not found: {voter_roll}")
    else:
        raise HTTPException(400, "Provide voter_roll (server path) or voter_roll_file (upload)")

    # ── Save petition file ────────────────────────────────────────────────────
    orig_name = petition.filename or "petition"
    suffix    = Path(orig_name).suffix.lower() or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await petition.read())
        tmp_path = Path(tmp.name)

    try:
        pipeline = Pipeline(voter_roll_csv=voter_roll_path)
        result   = pipeline.process(tmp_path, project_id=project_id)
        cause    = _detect_cause(tmp_path)
        db.save_project(result, county=county, cause=cause)
    finally:
        tmp_path.unlink(missing_ok=True)
        if voter_roll_tmp:
            voter_roll_tmp.unlink(missing_ok=True)

    summary = result.summary()
    summary["cause"] = cause
    return summary


# Keep old endpoint as alias for backwards compatibility
@app.post("/projects/{project_id}/process")
async def process_pdf(
    project_id: str,
    voter_roll: str     = Form(..., description="Path to voter roll CSV on server"),
    pdf: UploadFile     = File(...),
):
    """Accept a PDF upload, run the pipeline, save to DB."""
    from .pipeline import Pipeline

    voter_roll_path = Path(voter_roll)
    if not voter_roll_path.exists():
        raise HTTPException(400, f"Voter roll not found: {voter_roll}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await pdf.read())
        tmp_path = Path(tmp.name)

    try:
        pipeline = Pipeline(voter_roll_csv=voter_roll_path)
        result   = pipeline.process(tmp_path, project_id=project_id)
        db.save_project(result, county="")
    finally:
        tmp_path.unlink(missing_ok=True)

    return result.summary()


@app.post("/fraud-scan")
async def fraud_scan(
    petition: UploadFile = File(..., description="Petition photo or PDF to scan for fraud"),
):
    """
    Scan a petition for fraud indicators without a voter roll.
    Checks for: missing signatures, duplicate names/addresses,
    sequential house numbers, name clustering, and printed entries.
    """
    from .ingestion import get_processor
    from .matching import normalize_signature
    from .matching.fraud_detector import FraudAnalyzer

    orig_name = petition.filename or "petition"
    suffix    = Path(orig_name).suffix.lower() or ".pdf"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await petition.read())
        tmp_path = Path(tmp.name)

    try:
        processor  = get_processor()
        extracted  = processor.extract(tmp_path)
        normalized = [normalize_signature(e) for e in extracted]
        analyzer   = FraudAnalyzer()
        result     = analyzer.analyze(
            extracted, normalized,
            project_id=str(uuid.uuid4())[:8],
            source_path=orig_name,
        )
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "summary": result.summary(),
        "lines": [
            {
                "line_number":       r.line_number,
                "page":              r.page,
                "raw_name":          r.raw_name,
                "raw_address":       r.raw_address,
                "raw_date":          r.raw_date,
                "signature_present": r.signature_present,
                "ocr_confidence":    r.ocr_confidence,
                "flagged":           r.is_flagged,
                "flags": [
                    {
                        "code":          f.code,
                        "description":   f.description,
                        "related_lines": f.related_lines,
                    }
                    for f in r.flags
                ],
            }
            for r in result.lines
        ],
    }


@app.post("/worker/upload")
async def worker_upload(
    county:          str             = Form("", description="County name for this petition"),
    manual_sig_count: Optional[int]  = Form(None, description="Worker's own hand-count (overrides OCR if provided)"),
    petition:        UploadFile      = File(..., description="Petition photo or PDF"),
    user: dict = Depends(get_current_user),
):
    """
    Worker-facing petition upload.  Runs OCR extraction only — no voter roll
    matching, no match scores, no voter IDs returned.  Auto-assigns the
    resulting project to the authenticated worker.
    Returns: {project_id, total_lines, signed_lines, cause, county, filename}
    """
    from .ingestion import get_processor
    from .matching import normalize_signature
    from .matching.fraud_detector import FraudAnalyzer
    from .models import ProjectResult, VerificationResult, VerificationStatus

    worker_id = user["user_id"]

    orig_name = petition.filename or "petition"
    suffix    = Path(orig_name).suffix.lower() or ".pdf"
    project_id = str(uuid.uuid4())[:8]

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await petition.read())
        tmp_path = Path(tmp.name)

    try:
        processor = get_processor()
        extracted = processor.extract(tmp_path)
        normalized = [normalize_signature(e) for e in extracted]
        cause = _detect_cause(tmp_path)   # must run before unlink
    finally:
        tmp_path.unlink(missing_ok=True)

    # Build a ProjectResult without voter matching — all filled lines are "review"
    signatures = []
    for e, n in zip(extracted, normalized):
        has_content = bool(e.raw_name.strip() or e.raw_address.strip())
        status = VerificationStatus.REVIEW if has_content else VerificationStatus.REJECTED
        signatures.append(VerificationResult(
            line_number=e.line_number,
            page=e.page,
            extracted=e,
            normalized=n,
            best_match=None,
            status=status,
        ))

    total_lines  = len(signatures)
    review_count = sum(1 for s in signatures if s.status == VerificationStatus.REVIEW)

    result = ProjectResult(
        project_id=project_id,
        pdf_path=orig_name,
        total_lines=total_lines,
        approved=0,
        review=review_count,
        rejected=total_lines - review_count,
        duplicates=0,
        signatures=signatures,
    )
    # ── Same-sheet detection (searches all workers' sheets) ──────────────────
    orig_project_id, new_extracted, already_counted = _find_same_sheet(
        extracted, cause, county
    )

    is_continuation = orig_project_id is not None

    if is_continuation:
        # Build a NEW project for this worker containing only the new sigs.
        # The original sheet stays untouched so the original worker keeps credit.
        new_normalized = [normalize_signature(e) for e in new_extracted]
        new_signatures = []
        for e, n in zip(new_extracted, new_normalized):
            has_content = bool(e.raw_name.strip() or e.raw_address.strip())
            status = VerificationStatus.REVIEW if has_content else VerificationStatus.REJECTED
            new_signatures.append(VerificationResult(
                line_number=e.line_number,
                page=e.page,
                extracted=e,
                normalized=n,
                best_match=None,
                status=status,
            ))

        new_review  = sum(1 for s in new_signatures if s.status == VerificationStatus.REVIEW)
        new_signed  = sum(1 for s in new_signatures if s.extracted.signature_present)
        cont_result = ProjectResult(
            project_id=project_id,
            pdf_path=orig_name,
            total_lines=len(new_signatures),
            approved=0,
            review=new_review,
            rejected=len(new_signatures) - new_review,
            duplicates=0,
            signatures=new_signatures,
        )
        db.save_project(
            cont_result, county=county, cause=cause,
            continuation_of=orig_project_id,
        )
        db.assign_project_to_worker(
            worker_id=worker_id,
            project_id=project_id,
            assigned_by_id=None,
            manual_sig_count=manual_sig_count,
        )

        # Fraud scan on the new sigs only
        fraud = FraudAnalyzer().analyze(
            new_extracted, new_normalized,
            project_id=project_id, source_path=orig_name,
        )
        fraud_summary = fraud.summary()
        db.save_fraud_scan(
            project_id=project_id,
            flagged_lines=fraud_summary["suspicious_lines"],
            flag_counts=fraud_summary["flag_counts"],
        )

        return {
            "project_id":       project_id,
            "original_sheet_id": orig_project_id,
            "is_continuation":  True,
            "new_signatures":   new_signed,
            "already_counted":  already_counted,
            "total_on_sheet":   new_signed + already_counted,
            "cause":            cause,
            "county":           county,
            "filename":         orig_name,
        }

    # ── Brand-new sheet ───────────────────────────────────────────────────────
    db.save_project(result, county=county, cause=cause)
    db.assign_project_to_worker(
        worker_id=worker_id,
        project_id=project_id,
        assigned_by_id=None,
        manual_sig_count=manual_sig_count,
    )

    # Run fraud analysis and store results so boss can be alerted
    fraud = FraudAnalyzer().analyze(
        extracted, normalized,
        project_id=project_id,
        source_path=orig_name,
    )
    fraud_summary = fraud.summary()
    db.save_fraud_scan(
        project_id=project_id,
        flagged_lines=fraud_summary["suspicious_lines"],
        flag_counts=fraud_summary["flag_counts"],
    )

    signed_lines = sum(1 for s in signatures if s.extracted.signature_present)

    return {
        "project_id":      project_id,
        "is_continuation": False,
        "new_signatures":  signed_lines,
        "already_counted": 0,
        "total_signed":    signed_lines,
        "manual_sig_count": manual_sig_count,
        "cause":           cause,
        "county":          county,
        "filename":        orig_name,
    }


@app.get("/fraud-alerts")
async def fraud_alerts(
    threshold: float = 30.0,
    user: dict = Depends(get_current_user),
):
    """
    Return workers with suspiciously high fraud rates on their uploaded petitions.
    Boss/admin only. threshold = minimum % of flagged lines to trigger an alert.
    """
    if user["role"] not in ("boss", "admin"):
        raise HTTPException(403, "Not authorized")
    return db.get_fraud_alerts(threshold_pct=threshold)


class ManualCountPayload(BaseModel):
    count: int


@app.patch("/worker/projects/{project_id}/count")
async def update_manual_count(
    project_id: str,
    payload: ManualCountPayload,
    user: dict = Depends(get_current_user),
):
    """Worker corrects their own hand-count for a project after the fact."""
    if payload.count < 0:
        raise HTTPException(400, "Count cannot be negative")
    try:
        db.update_manual_sig_count(
            worker_id=user["user_id"],
            project_id=project_id,
            count=payload.count,
        )
    except ValueError:
        raise HTTPException(404, "Project not assigned to you")
    return {"ok": True, "project_id": project_id, "manual_sig_count": payload.count}


@app.get("/projects/{project_id}/export")
async def export_csv(project_id: str):
    """Download all signatures for a project as CSV."""
    rows = db.get_project_signatures(project_id)
    if not rows:
        raise HTTPException(404, "Project not found or empty")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_row_to_dict(rows[0]).keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(_row_to_dict(r))

    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project_id}_results.csv"'},
    )


# ── Project worker assignment ─────────────────────────────────────────────────

class AssignWorkerPayload(BaseModel):
    worker_id: int


@app.post("/projects/{project_id}/assign")
async def assign_project_to_worker(project_id: str, payload: AssignWorkerPayload):
    """Assign a project to a worker (admin+)."""
    from .auth import get_current_user
    wp = db.assign_project_to_worker(
        worker_id=payload.worker_id,
        project_id=project_id,
        assigned_by_id=None,
    )
    return {
        "ok": True,
        "worker_id": wp.worker_id,
        "project_id": wp.project_id,
        "assigned_at": wp.assigned_at.isoformat() if wp.assigned_at else None,
    }


# ── Dev seed endpoint ─────────────────────────────────────────────────────────

@app.post("/seed-demo-data")
async def seed_demo_data():
    """Create demo users for development. Only works if no users exist."""
    from .auth import hash_password
    from datetime import date, timedelta

    existing = db.list_users()
    if existing:
        return {"ok": False, "message": f"Users already exist ({len(existing)} users). Skipping seed."}

    pw = hash_password("password123")

    boss = db.create_user("boss@petition.co", pw, "boss", "Jordan Boss", "+1-555-0100", 35.0)
    admin1 = db.create_user("admin1@petition.co", pw, "admin", "Alex Admin", "+1-555-0101", 28.0)
    admin2 = db.create_user("admin2@petition.co", pw, "admin", "Morgan Admin", "+1-555-0102", 28.0)

    wages = [22.0, 24.0, 25.0, 26.0, 28.0]
    workers = []
    for i in range(1, 6):
        w = db.create_user(
            f"worker{i}@petition.co",
            pw,
            "worker",
            f"Worker {i}",
            f"+1-555-010{i+2}",
            wages[i - 1],
        )
        workers.append(w)

    # Create open pay period for current week
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    db.create_pay_period(monday.isoformat(), sunday.isoformat())

    return {
        "ok": True,
        "message": "Seeded demo data",
        "credentials": {
            "boss": "boss@petition.co / password123",
            "admin1": "admin1@petition.co / password123",
            "admin2": "admin2@petition.co / password123",
            "workers": [f"worker{i}@petition.co / password123" for i in range(1, 6)],
        },
    }


@app.post("/fix-dedup-users")
async def fix_dedup_users():
    """Delete duplicate users — keeps the lowest-id copy of each (full_name, role) pair."""
    from .storage.database import ShiftRow, WorkerProjectRow
    users = db.list_users()
    seen: dict[tuple, int] = {}
    deleted = []
    for u in sorted(users, key=lambda u: u.id):
        key = (u.full_name.strip().lower(), u.role)
        if key in seen:
            # Delete the duplicate (higher id)
            with db._Session() as session:
                session.query(ShiftRow).filter(ShiftRow.worker_id == u.id).delete()
                session.query(WorkerProjectRow).filter(WorkerProjectRow.worker_id == u.id).delete()
                from .storage.database import UserRow
                session.query(UserRow).filter(UserRow.id == u.id).delete()
                session.commit()
            deleted.append(f"{u.full_name} (id={u.id})")
        else:
            seen[key] = u.id
    return {"ok": True, "deleted": deleted, "kept": len(seen)}


@app.post("/fix-activate-users")
async def fix_activate_users():
    """Temporary: activate all users. Remove after use."""
    users = db.list_users()
    deactivated = [u for u in users if not u.is_active]
    for u in deactivated:
        db.update_user(u.id, is_active=True)
    return {"ok": True, "activated": [u.email for u in deactivated]}


@app.post("/fix-reset-permanent")
async def fix_reset_permanent():
    """Force-reset permanent account passwords to match hardcoded values."""
    from .auth import hash_password
    results = []
    for u in _PERMANENT_USERS:
        user = db.get_user_by_email(u["email"])
        if not user:
            db.create_user(u["email"], hash_password(u["password"]), u["role"], u["full_name"])
            results.append(f"created {u['email']}")
        else:
            db.update_user(user.id, password_hash=hash_password(u["password"]), is_active=True)
            results.append(f"reset {u['email']}")
    return {"ok": True, "results": results}


@app.post("/fix-reset-boss")
async def fix_reset_boss():
    """Temporary: reset boss password to password123. Remove after use."""
    from .auth import hash_password
    users = db.list_users()
    boss = next((u for u in users if u.email == "boss@petition.co"), None)
    if not boss:
        return {"ok": False, "message": "boss user not found"}
    new_hash = hash_password("password123")
    db.update_user(boss.id, password_hash=new_hash)
    return {"ok": True, "message": "boss password reset to password123", "hash_preview": new_hash[:20]}
