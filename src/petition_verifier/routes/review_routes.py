"""
AI-assisted Signature Review Center — field-level OCR with page versioning.

Pipeline (background task):
  1. Preprocess image with OpenCV
  2. Detect signature table bbox (ignore petition header)
  3. Split into row strips
  4. Per field: crop → Vision OCR → normalise
  5. Signature detection via ink density
  6. Page fingerprinting + versioning (compare against prior uploads)
  7. Classify rows: new_signature / already_counted / changed_needs_review / blank
  8. Claude resolves 'changed_needs_review' rows only

Endpoints:
  POST /review/upload                         Upload + trigger processing
  GET  /review/packets                        List all packets
  GET  /review/packets/{id}                   Packet detail + rows
  GET  /review/packets/{id}/image             Serve cleaned/raw image
  POST /review/packets/{id}/lines/{n}/action  Approve / reject / escalate a row
  POST /review/packets/{id}/approve-all       Approve all new_signature rows
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth import get_current_user
from ..storage import db
from ..storage.database import PacketLineRow

router = APIRouter(prefix="/review", tags=["review"])

UPLOAD_DIR = Path("packet_uploads")


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_packet(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix   = Path(file.filename or "packet.jpg").suffix.lower() or ".jpg"
    filename = uuid.uuid4().hex + suffix
    raw_path = UPLOAD_DIR / filename

    data = await file.read()
    raw_path.write_bytes(data)

    packet_id = db.create_packet(
        worker_id=current_user["user_id"],
        original_name=file.filename or filename,
        raw_path=str(raw_path),
    )
    bg.add_task(_process_packet, packet_id, raw_path)
    return {"packet_id": packet_id, "status": "processing"}


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/packets")
async def list_packets(current_user=Depends(get_current_user)):
    packets = db.list_packets()
    return [
        {
            "id":               p.id,
            "original_name":    p.original_name,
            "uploaded_at":      p.uploaded_at.isoformat() if p.uploaded_at else None,
            "status":           p.status,
            "total_lines":      p.total_lines,
            "new_sigs":         p.new_sigs,
            "already_counted":  p.already_counted,
            "needs_review":     p.needs_review,
            "worker_id":        p.worker_id,
        }
        for p in packets
    ]


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/packets/{packet_id}")
async def get_packet(packet_id: int, current_user=Depends(get_current_user)):
    packet, lines = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")

    # Prefer the stored rich result_json if available
    try:
        rich = json.loads(packet.result_json or "{}")
    except Exception:
        rich = {}

    return {
        "id":              packet.id,
        "original_name":   packet.original_name,
        "uploaded_at":     packet.uploaded_at.isoformat() if packet.uploaded_at else None,
        "status":          packet.status,
        "error_msg":       packet.error_msg,
        "total_lines":     packet.total_lines,
        "new_sigs":        packet.new_sigs,
        "already_counted": packet.already_counted,
        "needs_review":    packet.needs_review,
        "worker_id":       packet.worker_id,
        "has_cleaned":     bool(packet.cleaned_path),
        "summary":         rich.get("summary", {}),
        "lines": [
            {
                "id":               l.id,
                "line_no":          l.line_no,
                "row_status":       l.row_status,
                "raw_name":         l.raw_name,
                "norm_name":        l.norm_name,
                "raw_address":      l.raw_address,
                "norm_address":     l.norm_address,
                "raw_city":         l.raw_city,
                "raw_zip":          l.raw_zip,
                "valid_zip":        l.valid_zip,
                "raw_date":         l.raw_date,
                "has_signature":    l.has_signature,
                "ai_verdict":       l.ai_verdict,
                "flags":            json.loads(l.flags_json or "[]"),
                "action":           l.action,
                "reviewed_at":      l.reviewed_at.isoformat() if l.reviewed_at else None,
            }
            for l in lines
        ],
    }


# ── Image ─────────────────────────────────────────────────────────────────────

@router.get("/packets/{packet_id}/image")
async def get_packet_image(
    packet_id: int,
    type: str = "cleaned",
    current_user=Depends(get_current_user),
):
    packet, _ = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")
    path_str = (
        packet.cleaned_path if type == "cleaned" and packet.cleaned_path else packet.raw_path
    )
    path = Path(path_str)
    if not path.exists():
        raise HTTPException(404, "Image file not found")
    return FileResponse(str(path))


# ── Row action ────────────────────────────────────────────────────────────────

class ActionBody(BaseModel):
    action: str   # approved | rejected | escalated


@router.post("/packets/{packet_id}/lines/{line_no}/action")
async def set_line_action(
    packet_id: int,
    line_no: int,
    body: ActionBody,
    current_user=Depends(get_current_user),
):
    if body.action not in ("approved", "rejected", "escalated"):
        raise HTTPException(400, "action must be approved, rejected, or escalated")
    db.set_packet_line_action(packet_id, line_no, body.action, current_user["user_id"])
    return {"ok": True}


# ── Approve all new signatures ────────────────────────────────────────────────

@router.post("/packets/{packet_id}/approve-all")
async def approve_all_new(packet_id: int, current_user=Depends(get_current_user)):
    n = db.approve_all_new_sigs(packet_id, current_user["user_id"])
    return {"approved": n}


# ── Background processing ─────────────────────────────────────────────────────

def _process_packet(packet_id: int, raw_path: Path) -> None:
    try:
        _do_process(packet_id, raw_path)
    except Exception as exc:
        db.fail_packet(packet_id, str(exc))


def _do_process(packet_id: int, raw_path: Path) -> None:
    import re as _re
    import anthropic
    from PIL import Image

    # ── Load image ────────────────────────────────────────────────────────────
    import pillow_heif
    pillow_heif.register_heif_opener()
    pil_img = Image.open(raw_path).convert("RGB")

    # ── Preprocess + save cleaned copy ────────────────────────────────────────
    from ..ingestion.field_vision import (
        preprocess_image, page_fingerprint,
        _apply_versioning, claude_resolve_ambiguous,
    )

    preprocessed = preprocess_image(pil_img)
    cleaned_path = raw_path.parent / f"clean_{raw_path.stem}.jpg"
    preprocessed.save(str(cleaned_path), "JPEG", quality=92)

    fp = page_fingerprint(preprocessed)

    # ── Fetch previous rows for the same page fingerprint ────────────────────
    prev_rows = db.get_prev_rows_for_fingerprint(fp, exclude_packet_id=packet_id)

    # ── Claude Vision extraction (single call, no coordinate math) ────────────
    from ..ingestion.claude_extractor import _to_base64_jpeg, _PROMPT

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_rows: list[dict] = []
    if api_key:
        claude_client = anthropic.Anthropic(api_key=api_key)
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": _to_base64_jpeg(preprocessed),
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        raw_text = resp.content[0].text.strip()
        raw_text = _re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = _re.sub(r"\s*```$", "", raw_text)
        if raw_text.startswith("["):
            try:
                claude_rows = json.loads(raw_text)
            except Exception:
                claude_rows = []

    # ── Convert Claude rows to internal page_rows format ─────────────────────
    page_rows: list[dict] = []
    for row in claude_rows:
        if not isinstance(row, dict):
            continue
        name    = str(row.get("name",    "")).strip()
        address = str(row.get("address", "")).strip()
        city    = str(row.get("city",    "")).strip()
        zip_    = str(row.get("zip",     "")).strip()
        date    = str(row.get("date",    "")).strip()
        has_sig = bool(row.get("has_signature", False))
        try:
            line_no = int(row.get("line", 0))
        except (TypeError, ValueError):
            line_no = 0
        if not (1 <= line_no <= 8):
            line_no = len(page_rows) + 1

        valid_zip = bool(_re.match(r"^\d{5}$", zip_))
        status = "blank" if (not name and not address) else "new_signature"

        page_rows.append({
            "row_number":     line_no,
            "name":           {"raw": name,    "normalized": name.upper(),    "ocr_confidence": "high"},
            "street_address": {"raw": address, "normalized": address.upper(), "ocr_confidence": "high"},
            "city":           {"raw": city,    "normalized": city.upper(),    "ocr_confidence": "high"},
            "zip":            {"raw": zip_,    "normalized": zip_,            "valid_format": valid_zip},
            "date":           {"raw": date,    "normalized": date,            "ocr_confidence": "high"},
            "signature_present": has_sig,
            "flags":          [],
            "status":         status,
            "row_fingerprint": "",
        })

    # Fill any missing rows 1–8 as blank
    filled = {r["row_number"] for r in page_rows}
    for i in range(1, 9):
        if i not in filled:
            page_rows.append(_blank_row(i))
    page_rows.sort(key=lambda r: r["row_number"])

    # ── Versioning ────────────────────────────────────────────────────────────
    if prev_rows:
        _apply_versioning(page_rows, prev_rows)
        page_rows = claude_resolve_ambiguous(page_rows, prev_rows)

    # ── Summary counts ────────────────────────────────────────────────────────
    new_sigs    = sum(1 for r in page_rows if r["status"] == "new_signature")
    already_cnt = sum(1 for r in page_rows if r["status"] == "already_counted")
    needs_rev   = sum(1 for r in page_rows if r["status"] == "changed_needs_review")

    summary = {
        "total_rows_detected": len(page_rows),
        "new_signatures":      new_sigs,
        "previously_counted":  already_cnt,
        "needs_review":        needs_rev,
        "blank":               sum(1 for r in page_rows if r["status"] == "blank"),
    }
    page_result = {
        "page_id":          f"pkt{packet_id}",
        "page_fingerprint": fp,
        "summary":          summary,
        "rows":             page_rows,
    }

    # ── Build DB line rows ────────────────────────────────────────────────────
    line_rows: list[PacketLineRow] = []
    for r in page_rows:
        flags = r.get("flags", [])
        verdict = (
            "likely_valid"   if r["status"] == "new_signature" and r["signature_present"]
            else "likely_invalid" if not r["signature_present"] and r["status"] != "blank"
            else "needs_review"
        )
        line_rows.append(PacketLineRow(
            packet_id       = packet_id,
            line_no         = r["row_number"],
            row_fingerprint = r.get("row_fingerprint", ""),
            row_status      = r["status"],
            raw_name        = r["name"]["raw"],
            norm_name       = r["name"]["normalized"],
            raw_address     = r["street_address"]["raw"],
            norm_address    = r["street_address"]["normalized"],
            raw_city        = r["city"]["raw"],
            raw_zip         = r["zip"]["raw"],
            valid_zip       = r["zip"]["valid_format"],
            raw_date        = r["date"]["raw"],
            has_signature   = r["signature_present"],
            ai_verdict      = verdict,
            ai_reason       = "",
            flags_json      = json.dumps(flags),
        ))

    # ── Persist ───────────────────────────────────────────────────────────────
    db.finish_packet(
        packet_id        = packet_id,
        cleaned_path     = str(cleaned_path),
        lines            = line_rows,
        page_fingerprint = fp,
        new_sigs         = new_sigs,
        already_counted  = already_cnt,
        needs_review     = needs_rev,
        result_json      = json.dumps(page_result),
    )


def _blank_row(row_number: int) -> dict:
    empty_field = {"raw": "", "normalized": "", "ocr_confidence": "none"}
    return {
        "row_number":      row_number,
        "name":            empty_field,
        "street_address":  empty_field,
        "city":            {"raw": "", "normalized": "", "ocr_confidence": "none"},
        "zip":             {"raw": "", "normalized": "", "valid_format": False},
        "date":            {"raw": "", "normalized": "", "ocr_confidence": "none"},
        "signature_present": False,
        "flags":           [],
        "status":          "blank",
        "row_fingerprint": "",
    }
