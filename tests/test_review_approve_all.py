from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from petition_verifier.routes import review_routes
from petition_verifier.storage.database import Database, PacketLineRow, PacketRow


def test_approve_all_only_approves_clean_voter_valid_signed_rows(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review.db'}")

    def line(line_no: int, **overrides):
        data = {
            "packet_id": 1,
            "line_no": line_no,
            "row_status": "new_signature",
            "raw_name": f"Signer {line_no}",
            "has_signature": True,
            "ai_verdict": "likely_valid",
            "flags_json": "[]",
            "voter_status": "valid",
            "fraud_flags": "[]",
            "fraud_score": 0,
        }
        data.update(overrides)
        return PacketLineRow(**data)

    with db._Session() as session:
        session.add(PacketRow(id=1, original_name="packet.jpg", raw_path="packet.jpg"))
        session.add_all([
            line(1),
            line(2, has_signature=False),
            line(3, voter_status="uncertain"),
            line(4, fraud_flags=json.dumps(["consecutive_addresses"]), fraud_score=40),
            line(5, flags_json=json.dumps(["low_confidence"])),
            line(6, action="rejected"),
            line(7, row_status="already_counted"),
            line(8, low_confidence_fields=json.dumps(["name"])),
        ])
        session.commit()

    approved = db.approve_all_new_sigs(packet_id=1, reviewer_id=99)

    assert approved == 1
    with db._Session() as session:
        actions = {
            row.line_no: row.action
            for row in session.query(PacketLineRow).order_by(PacketLineRow.line_no)
        }
        assert actions == {
            1: "approved",
            2: None,
            3: None,
            4: None,
            5: None,
            6: "rejected",
            7: None,
            8: None,
        }


def test_low_confidence_line_approval_requires_explicit_override(monkeypatch, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review-action.db'}")
    with db._Session() as session:
        session.add(PacketRow(id=1, worker_id=1, original_name="packet.jpg", raw_path="packet.jpg"))
        session.add(PacketLineRow(
            packet_id=1,
            line_no=1,
            row_status="new_signature",
            raw_name="Reggie Ellison",
            raw_address="123 Main St",
            has_signature=True,
            low_confidence_fields=json.dumps(["name"]),
        ))
        session.commit()

    app = FastAPI()
    app.include_router(review_routes.router)
    app.dependency_overrides[review_routes.get_current_user] = lambda: {
        "user_id": 99,
        "role": "boss",
    }
    monkeypatch.setattr(review_routes, "db", db)

    with TestClient(app) as client:
        blocked = client.post(
            "/review/packets/1/lines/1/action",
            json={"action": "approved"},
        )
        approved = client.post(
            "/review/packets/1/lines/1/action",
            json={"action": "approved", "override": True},
        )

    assert blocked.status_code == 409
    assert approved.status_code == 200
    with db._Session() as session:
        line = session.query(PacketLineRow).filter_by(packet_id=1, line_no=1).one()
        assert line.action == "approved"


def test_packet_detail_includes_low_confidence_fields_and_raw_values(monkeypatch, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review-detail.db'}")
    with db._Session() as session:
        session.add(PacketRow(id=1, worker_id=1, original_name="packet.jpg", raw_path="packet.jpg"))
        session.add(PacketLineRow(
            packet_id=1,
            line_no=1,
            row_status="new_signature",
            raw_name="Reggie Ellison",
            norm_name="REGGIE ELLISON",
            raw_address="123 Main St",
            norm_address="123 MAIN ST",
            has_signature=True,
            low_confidence_fields=json.dumps(["name", "address"]),
        ))
        session.commit()

    app = FastAPI()
    app.include_router(review_routes.router)
    app.dependency_overrides[review_routes.get_current_user] = lambda: {
        "user_id": 99,
        "role": "boss",
    }
    monkeypatch.setattr(review_routes, "db", db)

    with TestClient(app) as client:
        response = client.get("/review/packets/1")

    assert response.status_code == 200
    line = response.json()["lines"][0]
    assert line["low_confidence_fields"] == ["name", "address"]
    assert line["raw_name"] == "Reggie Ellison"
    assert line["raw_address"] == "123 Main St"
