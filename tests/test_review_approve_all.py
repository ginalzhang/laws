from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

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
    crop_path = tmp_path / "row-1.jpg"
    crop_path.write_bytes(b"fake-crop")
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
            crop_path=str(crop_path),
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
    assert line["has_crop"] is True


def test_packet_line_crop_route_serves_stored_crop_with_packet_access(monkeypatch, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review-crop.db'}")
    crop_path = tmp_path / "row-1.jpg"
    crop_path.write_bytes(b"fake-crop")
    with db._Session() as session:
        session.add(PacketRow(id=1, worker_id=1, original_name="packet.jpg", raw_path="packet.jpg"))
        session.add(PacketLineRow(
            packet_id=1,
            line_no=1,
            row_status="new_signature",
            raw_name="Reggie Ellison",
            has_signature=True,
            crop_path=str(crop_path),
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
        response = client.get("/review/packets/1/lines/1/crop")

    assert response.status_code == 200
    assert response.content == b"fake-crop"
    assert response.headers["content-type"] == "image/jpeg"


def test_voter_match_persists_top_suggestions(monkeypatch, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review-voters.db'}")
    voter_roll = "\n".join([
        "first,last,address,city,zip",
        "Reggie,Ellison,123 Oak Ave,Pasadena,91101",
        "Boyce,Shelton,999 Pine St,Pasadena,91101",
        "Rachel,Ellis,123 Oak Avenue,Pasadena,91101",
    ])
    with db._Session() as session:
        session.add(PacketRow(
            id=1,
            worker_id=1,
            original_name="packet.jpg",
            raw_path="packet.jpg",
            voter_roll_text=voter_roll,
        ))
        session.add(PacketLineRow(
            packet_id=1,
            line_no=1,
            row_status="new_signature",
            raw_name="Reggie Elison",
            raw_address="123 Oak Ave",
            raw_zip="91101",
            has_signature=True,
        ))
        session.commit()

    app = FastAPI()
    app.include_router(review_routes.router)
    app.dependency_overrides[review_routes.get_current_user] = lambda: {
        "user_id": 99,
        "role": "boss",
    }
    monkeypatch.setattr(review_routes, "db", db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with TestClient(app) as client:
        match_response = client.post("/review/packets/1/voter-match")
        detail_response = client.get("/review/packets/1")

    assert match_response.status_code == 200
    line = detail_response.json()["lines"][0]
    suggestions = line["voter_suggestions"]
    assert len(suggestions) == 3
    assert suggestions[0]["name"] == "Reggie Ellison"
    assert suggestions[0]["score"] >= suggestions[1]["score"]


def test_fraud_analysis_flags_similar_handwriting_from_row_crops(monkeypatch, tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'review-handwriting.db'}")

    def crop(path):
        img = Image.new("RGB", (320, 56), "white")
        draw = ImageDraw.Draw(img)
        draw.line((0, 0, 319, 0), fill="black", width=1)
        draw.line((0, 55, 319, 55), fill="black", width=1)
        draw.line((22, 0, 22, 55), fill="black", width=1)
        draw.line((138, 0, 138, 55), fill="black", width=1)
        draw.line((35, 16, 78, 36), fill="black", width=3)
        draw.line((78, 36, 112, 15), fill="black", width=3)
        draw.line((42, 42, 120, 42), fill="black", width=3)
        img.save(path)

    crop1 = tmp_path / "row-1.jpg"
    crop2 = tmp_path / "row-2.jpg"
    crop(crop1)
    crop(crop2)

    with db._Session() as session:
        session.add(PacketRow(id=1, worker_id=1, original_name="packet.jpg", raw_path="packet.jpg"))
        session.add_all([
            PacketLineRow(
                packet_id=1,
                line_no=1,
                row_status="new_signature",
                raw_name="Jane Smith",
                raw_address="123 Oak Ave",
                has_signature=True,
                crop_path=str(crop1),
            ),
            PacketLineRow(
                packet_id=1,
                line_no=2,
                row_status="new_signature",
                raw_name="Maria Garcia",
                raw_address="456 Pine St",
                has_signature=True,
                crop_path=str(crop2),
            ),
        ])
        session.commit()

    app = FastAPI()
    app.include_router(review_routes.router)
    app.dependency_overrides[review_routes.get_current_user] = lambda: {
        "user_id": 99,
        "role": "boss",
    }
    monkeypatch.setattr(review_routes, "db", db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with TestClient(app) as client:
        response = client.post("/review/packets/1/fraud-analysis")
        detail = client.get("/review/packets/1")

    assert response.status_code == 200
    flags_by_line = {
        line["line_no"]: line["fraud_flags"]
        for line in detail.json()["lines"]
    }
    assert "same_handwriting" in flags_by_line[1]
    assert "same_handwriting" in flags_by_line[2]
