from __future__ import annotations

import json

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
        }
