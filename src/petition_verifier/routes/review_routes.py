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

import csv
import difflib
import io
import json
import os
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import get_current_user
from ..ingestion.ca_counties import CALIFORNIA_COUNTIES, city_in_county
from ..storage import db
from ..storage.database import PacketLineRow

router = APIRouter(prefix="/review", tags=["review"])

UPLOAD_DIR = Path("packet_uploads")


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_packet(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    shift_id: int | None = None,
    current_user=Depends(get_current_user),
):
    import traceback as _tb

    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix   = Path(file.filename or "packet.jpg").suffix.lower() or ".jpg"
        filename = uuid.uuid4().hex + suffix
        raw_path = UPLOAD_DIR / filename

        data = await file.read()
        if not data:
            raise HTTPException(400, "Uploaded file is empty")
        raw_path.write_bytes(data)

        packet_id = db.create_packet(
            worker_id=current_user["user_id"],
            original_name=file.filename or filename,
            raw_path=str(raw_path),
            shift_id=shift_id,
        )
        bg.add_task(_process_packet, packet_id, raw_path)
        return {"packet_id": packet_id, "status": "processing"}
    except HTTPException:
        raise
    except Exception as exc:
        # Print full traceback to server logs (visible in Render/deployment logs)
        print("upload_packet failed:", _tb.format_exc(), flush=True)
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        )


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
        "voter_roll_text": packet.voter_roll_text or "",
        "county": packet.county or "",
        "lines": [
            {
                "id":                l.id,
                "line_no":           l.line_no,
                "row_status":        l.row_status,
                "raw_name":          l.raw_name,
                "norm_name":         l.norm_name,
                "raw_address":       l.raw_address,
                "norm_address":      l.norm_address,
                "raw_city":          l.raw_city,
                "raw_zip":           l.raw_zip,
                "valid_zip":         l.valid_zip,
                "raw_date":          l.raw_date,
                "has_signature":     l.has_signature,
                "ai_verdict":        l.ai_verdict,
                "flags":             json.loads(l.flags_json or "[]"),
                "voter_status":      l.voter_status,
                "voter_confidence":  l.voter_confidence,
                "voter_reason":      l.voter_reason,
                "fraud_flags":       json.loads(l.fraud_flags or "[]"),
                "fraud_score":       l.fraud_score or 0,
                "review_decision":   l.review_decision,
                "action":            l.action,
                "reviewed_at":       l.reviewed_at.isoformat() if l.reviewed_at else None,
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


# ── County list + save ────────────────────────────────────────────────────────

@router.get("/counties")
async def list_counties():
    return CALIFORNIA_COUNTIES


class CountyBody(BaseModel):
    county: str


@router.post("/packets/{packet_id}/county")
async def save_packet_county(
    packet_id: int, body: CountyBody, current_user=Depends(get_current_user)
):
    if body.county and body.county not in CALIFORNIA_COUNTIES:
        raise HTTPException(400, f"Unknown county: {body.county}")
    packet, _ = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")
    db.save_county(packet_id, body.county)
    return {"ok": True, "county": body.county}


# ── Voter roll ────────────────────────────────────────────────────────────────

class VoterRollBody(BaseModel):
    voter_roll_text: str


@router.post("/packets/{packet_id}/voter-roll")
async def save_voter_roll(
    packet_id: int, body: VoterRollBody, current_user=Depends(get_current_user)
):
    packet, _ = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")
    db.save_voter_roll(packet_id, body.voter_roll_text)
    return {"ok": True, "row_count": len(_parse_voter_roll(body.voter_roll_text))}


@router.post("/packets/{packet_id}/voter-match")
async def run_voter_match(packet_id: int, current_user=Depends(get_current_user)):
    packet, lines = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")
    if not packet.voter_roll_text:
        raise HTTPException(400, "No voter roll saved — POST /voter-roll first")

    voters = _parse_voter_roll(packet.voter_roll_text)
    if not voters:
        raise HTTPException(400, "Voter roll parsed 0 entries — check format")

    active = [l for l in lines if l.row_status != "blank" and l.raw_name]
    results = []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        # Claude Haiku: for each row, pass top-5 fuzzy candidates and let Claude decide
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        for l in active:
            # Pre-rank candidates with difflib to limit prompt size
            scored = sorted(
                voters,
                key=lambda v: _sim(l.raw_name, v["name"]) * 0.6 + _sim(l.raw_address or "", v["address"]) * 0.4,
                reverse=True,
            )[:5]
            candidates_text = "\n".join(
                f"  {i+1}. Name: {v['name']} | Address: {v['address']} | ZIP: {v['zip']}"
                for i, v in enumerate(scored)
            )
            prompt = (
                f"You are a petition voter-roll verifier. Determine if this petition signer matches any voter roll candidate.\n\n"
                f"Petition signer:\n"
                f"  Name: {l.raw_name}\n"
                f"  Address: {l.raw_address or '(blank)'}\n"
                f"  City: {l.raw_city or '(blank)'}\n"
                f"  ZIP: {l.raw_zip or '(blank)'}\n\n"
                f"Top voter roll candidates:\n{candidates_text}\n\n"
                f"Respond with JSON only, no explanation:\n"
                f'{{\"status\": \"valid\"|\"uncertain\"|\"invalid\", \"confidence\": 0-100, \"reason\": \"one sentence\"}}\n\n'
                f"Use:\n"
                f"  valid — clear match (name + address align, allowing for minor spelling variations)\n"
                f"  uncertain — partial match (name matches but address unclear, or vice versa)\n"
                f"  invalid — no reasonable match found"
            )
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=120,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                verdict = json.loads(raw)
                results.append({
                    "line_id": l.id,
                    "voter_status": verdict.get("status", "uncertain"),
                    "voter_confidence": int(verdict.get("confidence", 50)),
                    "voter_reason": verdict.get("reason", ""),
                })
            except Exception:
                # Fall back to difflib for this row
                results.append({"line_id": l.id, **_fuzzy_voter_match(l.raw_name, l.raw_address or "", l.raw_zip or "", voters)})
    else:
        # No API key — pure difflib
        for l in active:
            results.append({"line_id": l.id, **_fuzzy_voter_match(l.raw_name, l.raw_address or "", l.raw_zip or "", voters)})

    db.bulk_update_voter_match(results)
    valid    = sum(1 for r in results if r["voter_status"] == "valid")
    invalid  = sum(1 for r in results if r["voter_status"] == "invalid")
    uncertain = sum(1 for r in results if r["voter_status"] == "uncertain")
    return {"matched": len(results), "valid": valid, "invalid": invalid, "uncertain": uncertain}


@router.post("/packets/{packet_id}/fraud-analysis")
async def run_fraud_analysis(packet_id: int, current_user=Depends(get_current_user)):
    packet, lines = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")

    # Step 1: programmatic pattern detection (always runs), with county validation if set
    results = _detect_fraud_patterns(lines, county=packet.county or None)
    result_by_id = {r["line_id"]: r for r in results}

    # Step 2: Claude Sonnet holistic fraud pass (if API key available)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    active = [l for l in lines if l.row_status != "blank" and l.raw_name]
    sonnet_summary = None

    if api_key and active:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        rows_text = "\n".join(
            f"  Row {l.line_no}: name={l.raw_name!r}, address={l.raw_address!r}, city={l.raw_city!r}, "
            f"zip={l.raw_zip!r}, date={l.raw_date!r}, has_signature={l.has_signature}, "
            f"voter_status={l.voter_status or 'unmatched'}"
            for l in active
        )
        county_line = f"This petition is for {packet.county} County, California. Any signer listing a city outside {packet.county} County should be flagged.\n\n" if packet.county else ""
        prompt = (
            f"You are a petition fraud analyst. Review these extracted signature rows and identify fraud patterns.\n\n"
            f"{county_line}"
            "Known petition fraud patterns to check:\n"
            "- Same or nearly identical handwriting style across multiple entries\n"
            "- Suspiciously sequential addresses (101, 102, 103 same street)\n"
            "- Same city appearing in implausibly long unbroken runs\n"
            "- Multiple entries with identical or very similar names\n"
            "- All entries dated the same day across many rows\n"
            "- Missing signatures on filled rows\n"
            "- Names written in the same hand as address fields\n"
            "- Implausibly neat or uniform entries (real crowds vary)\n"
            "- Voter roll mismatches clustering on specific rows\n\n"
            f"Rows:\n{rows_text}\n\n"
            "Respond with JSON only:\n"
            '{"overall_risk": "low"|"medium"|"high", "validity_pct": 0-100, "summary": "2-3 sentence assessment", '
            '"row_flags": [{"row": <line_no>, "flags": ["flag1","flag2"], "score": 0-100}]}\n\n'
            "Only include rows in row_flags if they have at least one flag. "
            "Scores: 0=clean, 30-50=suspicious, 60-80=likely fraud, 90-100=almost certainly fraudulent."
        )
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            verdict = json.loads(raw)
            sonnet_summary = {
                "overall_risk": verdict.get("overall_risk", "unknown"),
                "validity_pct": verdict.get("validity_pct"),
                "summary": verdict.get("summary", ""),
            }
            # Merge Sonnet per-row flags on top of programmatic ones
            for rf in verdict.get("row_flags", []):
                line = next((l for l in active if l.line_no == rf["row"]), None)
                if not line:
                    continue
                r = result_by_id.get(line.id)
                if not r:
                    continue
                for flag in rf.get("flags", []):
                    if flag not in r["fraud_flags"]:
                        r["fraud_flags"].append(flag)
                # Take the higher of programmatic vs Sonnet score
                r["fraud_score"] = min(100, max(r["fraud_score"], rf.get("score", 0)))
        except Exception:
            pass  # fall through with programmatic results only

    db.bulk_update_fraud(results)
    flagged = sum(1 for r in results if r["fraud_flags"])
    response = {"lines_analyzed": len(results), "flagged": flagged}
    if sonnet_summary:
        response["ai_assessment"] = sonnet_summary
    return response


class DecisionBody(BaseModel):
    decision: str  # confirmed_fraud | cleared


@router.patch("/packets/{packet_id}/lines/{line_no}/decision")
async def set_review_decision(
    packet_id: int, line_no: int, body: DecisionBody, current_user=Depends(get_current_user)
):
    if body.decision not in ("confirmed_fraud", "cleared"):
        raise HTTPException(400, "decision must be confirmed_fraud or cleared")
    db.set_line_review_decision(packet_id, line_no, body.decision)
    return {"ok": True}


@router.get("/packets/{packet_id}/export")
async def export_packet(
    packet_id: int,
    filter: str = "all",  # all | valid | flagged
    token: str | None = None,
    current_user=Depends(get_current_user),
):
    packet, lines = db.get_packet_detail(packet_id)
    if not packet:
        raise HTTPException(404, "Packet not found")

    if filter == "valid":
        rows = [l for l in lines if l.voter_status == "valid" and l.review_decision != "confirmed_fraud"]
    elif filter == "flagged":
        rows = [l for l in lines if l.fraud_score and l.fraud_score > 30 or l.review_decision == "confirmed_fraud"]
    else:
        rows = [l for l in lines if l.row_status != "blank"]

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["line_no", "name", "address", "city", "zip", "date", "has_signature",
                "voter_status", "voter_confidence", "voter_reason",
                "fraud_score", "fraud_flags", "review_decision", "action"])
    for l in rows:
        w.writerow([
            l.line_no, l.raw_name, l.raw_address, l.raw_city, l.raw_zip, l.raw_date,
            l.has_signature, l.voter_status or "", l.voter_confidence or "",
            l.voter_reason or "", l.fraud_score or 0,
            "|".join(json.loads(l.fraud_flags or "[]")),
            l.review_decision or "", l.action or "",
        ])

    output.seek(0)
    filename = f"packet_{packet_id}_{filter}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Voter roll helpers ─────────────────────────────────────────────────────────

def _normalize_str(s: str) -> str:
    s = s.upper().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(ST|AVE|BLVD|DR|RD|LN|CT|PL|WAY)\b", lambda m: {
        "ST": "STREET", "AVE": "AVENUE", "BLVD": "BOULEVARD",
        "DR": "DRIVE", "RD": "ROAD", "LN": "LANE",
        "CT": "COURT", "PL": "PLACE", "WAY": "WAY",
    }.get(m.group(), m.group()), s)
    return re.sub(r"\s+", " ", s).strip()


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_str(a), _normalize_str(b)).ratio()


def _parse_voter_roll(text: str) -> list[dict]:
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return []
    first = lines[0].lower()
    has_header = any(kw in first for kw in ["name", "first", "last", "address", "city", "zip"])
    if has_header:
        headers = [h.strip().lower() for h in re.split(r"[\t,]", lines[0])]
        data = lines[1:]
    else:
        headers = None
        data = lines

    voters = []
    for line in data:
        cols = [c.strip() for c in re.split(r"[\t,]", line)]
        if not cols or not any(cols):
            continue
        if headers:
            row = dict(zip(headers, cols + [""] * max(0, len(headers) - len(cols))))
            fname = row.get("first name", row.get("firstname", row.get("first", "")))
            lname = row.get("last name", row.get("lastname", row.get("last", "")))
            full = row.get("name", row.get("full name", f"{fname} {lname}".strip()))
            addr = row.get("address", row.get("residence address", row.get("street", "")))
            city = row.get("city", "")
            zip_ = row.get("zip", row.get("zip code", row.get("zipcode", "")))
        else:
            if len(cols) >= 5:
                full = f"{cols[0]} {cols[1]}".strip(); addr = cols[2]; city = cols[3]; zip_ = cols[4]
            elif len(cols) >= 3:
                full = cols[0]; addr = cols[1]; zip_ = cols[2]; city = ""
            elif len(cols) >= 2:
                full = cols[0]; addr = cols[1]; zip_ = ""; city = ""
            else:
                continue
        zip5 = re.sub(r"\D", "", zip_)[:5]
        voters.append({"name": full, "address": f"{addr} {city}".strip(), "zip": zip5})
    return voters


def _fuzzy_voter_match(name: str, address: str, zip_: str, voters: list[dict]) -> dict:
    best_score, best_voter = 0.0, None
    for v in voters:
        name_sim = _sim(name, v["name"])
        addr_sim = _sim(address, v["address"]) if address and v["address"] else 0.0
        zip_ok = 1.0 if zip_ and v["zip"] and zip_.strip()[:5] == v["zip"] else 0.0
        score = name_sim * 0.55 + addr_sim * 0.30 + zip_ok * 0.15
        if score > best_score:
            best_score = score; best_voter = v
    confidence = int(best_score * 100)
    if confidence >= 72:
        return {"voter_status": "valid", "voter_confidence": confidence,
                "voter_reason": f"Matched: {best_voter['name']} — {best_voter['address']}"}
    elif confidence >= 45:
        match_str = f"{best_voter['name']} — {best_voter['address']}" if best_voter else "none"
        return {"voter_status": "uncertain", "voter_confidence": confidence,
                "voter_reason": f"Partial match ({confidence}%): {match_str}"}
    return {"voter_status": "invalid", "voter_confidence": confidence,
            "voter_reason": "No voter roll match found"}


# ── Fraud detection helpers ────────────────────────────────────────────────────

def _detect_fraud_patterns(lines: list, county: str | None = None) -> list[dict]:
    results = [{"line_id": l.id, "line_no": l.line_no, "fraud_flags": [], "fraud_score": 0}
               for l in lines]
    id_to_idx = {l.id: i for i, l in enumerate(lines)}
    active = [l for l in lines if l.row_status != "blank" and l.raw_name]

    def add_flag(line_id: int, flag: str, score: int):
        idx = id_to_idx.get(line_id)
        if idx is not None and flag not in results[idx]["fraud_flags"]:
            results[idx]["fraud_flags"].append(flag)
            results[idx]["fraud_score"] += score

    # Missing signature on a filled row
    for l in lines:
        if l.row_status == "new_signature" and not l.has_signature:
            add_flag(l.id, "no_signature", 30)
        if l.raw_zip and not l.valid_zip:
            add_flag(l.id, "invalid_zip", 15)

    # County / city validation
    if county:
        for l in active:
            if l.raw_city and not city_in_county(l.raw_city, county):
                add_flag(l.id, f"city_not_in_{county.lower().replace(' ', '_')}_county", 80)

    # Consecutive house numbers (sequential runs of 3+ in same city)
    def _house_num(addr: str):
        m = re.match(r"^(\d+)", (addr or "").strip())
        return int(m.group(1)) if m else None

    city_groups: dict[str, list] = defaultdict(list)
    for l in active:
        city_groups[(l.raw_city or "").lower().strip()].append(l)

    for city_lines in city_groups.values():
        numbered = [(l, _house_num(l.raw_address)) for l in city_lines]
        numbered = sorted([(l, n) for l, n in numbered if n is not None], key=lambda x: x[1])
        for j in range(len(numbered) - 2):
            l1, n1 = numbered[j]; l2, n2 = numbered[j + 1]; l3, n3 = numbered[j + 2]
            if n2 - n1 <= 6 and n3 - n2 <= 6 and n3 - n1 <= 12:
                for l in [l1, l2, l3]:
                    add_flag(l.id, "consecutive_addresses", 40)

    # Long same-city runs (5+ in a row)
    ordered = sorted(active, key=lambda l: l.line_no)
    run: list = []; cur_city = None
    def _flush_run(run):
        if len(run) >= 5:
            for l in run:
                add_flag(l.id, "long_city_run", 20)
    for l in ordered:
        c = (l.raw_city or "").lower().strip()
        if c == cur_city:
            run.append(l)
        else:
            _flush_run(run); cur_city = c; run = [l]
    _flush_run(run)

    # Similar names across lines
    names = [(l, (l.norm_name or l.raw_name or "").upper()) for l in active]
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            la, na = names[a]; lb, nb = names[b]
            if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.88:
                add_flag(la.id, "similar_name", 35)
                add_flag(lb.id, "similar_name", 35)

    # Same date dominance (>70% share a date)
    dates = [l.raw_date for l in active if l.raw_date]
    if len(dates) >= 4:
        most_common, cnt = Counter(dates).most_common(1)[0]
        if cnt / len(dates) >= 0.70:
            for l in active:
                if l.raw_date == most_common:
                    add_flag(l.id, "uniform_date", 15)

    # Cap at 100
    for r in results:
        r["fraud_score"] = min(r["fraud_score"], 100)
    return results


# ── Background processing ─────────────────────────────────────────────────────

def _process_packet(packet_id: int, raw_path: Path) -> None:
    import traceback
    try:
        _do_process(packet_id, raw_path)
    except Exception as exc:
        tb = traceback.format_exc()
        db.fail_packet(packet_id, f"{type(exc).__name__}: {exc}\n\n{tb}")


def _do_process(packet_id: int, raw_path: Path) -> None:
    import re as _re
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

    # ── Google Vision extraction — single call, 4-level fallback ─────────────
    from ..ingestion.vision import (
        _vision_words, _find_grid_top,
        _extract_by_header_columns, _is_vision_block_format, _extract_vision_block,
        _extract_by_line_numbers, _extract_vision_columns,
    )

    extracted_sigs = []
    try:
        words = _vision_words(preprocessed)

        # Clip to signature grid: skip ballot header, stop at Declaration
        grid_top = _find_grid_top(words)
        decl_top = next(
            (w.top for w in words if _re.match(r"^declaration$", w.text, _re.I)),
            None,
        )
        if grid_top is not None:
            words = [w for w in words if w.top >= grid_top]
        if decl_top is not None:
            words = [w for w in words if w.top < decl_top]

        # 4-level cascade (mirrors VisionProcessor.extract)
        sigs = _extract_by_header_columns(words, preprocessed, 1, 1)
        if not sigs and _is_vision_block_format(words):
            sigs = _extract_vision_block(words, preprocessed, 1, 1)
        if not sigs:
            sigs = _extract_by_line_numbers(words, preprocessed, 1, 1)
        if not sigs:
            sigs = _extract_vision_columns(words, preprocessed, 1, 1)
        extracted_sigs = sigs
    except Exception:
        extracted_sigs = []

    # ── Convert ExtractedSignature → page_rows ────────────────────────────────
    _zip_re = _re.compile(r"^\d{5}$")

    def _split_addr(full: str):
        """Split 'street, city, zip' into (street, city, zip)."""
        parts = [p.strip() for p in full.split(",") if p.strip()]
        zip_  = parts.pop() if parts and _zip_re.match(parts[-1]) else ""
        city  = parts.pop() if len(parts) >= 2 else ""
        return ", ".join(parts), city, zip_

    page_rows: list[dict] = []
    for sig in extracted_sigs:
        street, city, zip_ = _split_addr(sig.raw_address or "")
        valid_zip = bool(_zip_re.match(zip_))
        name      = sig.raw_name or ""
        status    = "blank" if (not name and not street) else "new_signature"
        line_no   = max(1, len(page_rows) + 1)

        page_rows.append({
            "row_number":     line_no,
            "name":           {"raw": name,   "normalized": name.upper(),   "ocr_confidence": "high"},
            "street_address": {"raw": street, "normalized": street.upper(), "ocr_confidence": "high"},
            "city":           {"raw": city,   "normalized": city.upper(),   "ocr_confidence": "high"},
            "zip":            {"raw": zip_,   "normalized": zip_,           "valid_format": valid_zip},
            "date":           {"raw": sig.raw_date or "", "normalized": sig.raw_date or "", "ocr_confidence": "high"},
            "signature_present": bool(sig.signature_present),
            "flags":          [],
            "status":         status,
            "row_fingerprint": "",
        })

    # Fill any missing rows 1–7 as blank
    filled = {r["row_number"] for r in page_rows}
    for i in range(1, 8):
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
