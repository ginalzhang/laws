"""Seed script for petition verifier workforce management."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from src.petition_verifier.auth import hash_password
from src.petition_verifier.storage.database import Database


def main():
    db = Database()

    existing = db.list_users()
    if existing:
        print(f"Users already exist ({len(existing)} users). Skipping seed.")
        print("To re-seed, delete the database file: petition_verifier.db")
        return

    demo_password = os.getenv("PVFY_DEMO_PASSWORD", "")
    if len(demo_password) < 8:
        print("Set PVFY_DEMO_PASSWORD to at least 8 characters before seeding.")
        sys.exit(1)
    pw = hash_password(demo_password)

    print("Creating users...")
    boss = db.create_user(
        "boss@petition.co", pw, "boss", "Jordan Boss", "+1-555-0100", 35.0
    )
    print(f"  Boss: {boss.email} (id={boss.id})")

    admin1 = db.create_user(
        "admin1@petition.co", pw, "admin", "Alex Admin", "+1-555-0101", 28.0
    )
    print(f"  Admin: {admin1.email} (id={admin1.id})")

    admin2 = db.create_user(
        "admin2@petition.co", pw, "admin", "Morgan Admin", "+1-555-0102", 28.0
    )
    print(f"  Admin: {admin2.email} (id={admin2.id})")

    wages = [22.0, 24.0, 25.0, 26.0, 28.0]
    for i in range(1, 6):
        w = db.create_user(
            f"worker{i}@petition.co",
            pw,
            "worker",
            f"Worker {i}",
            f"+1-555-010{i + 2}",
            wages[i - 1],
        )
        print(f"  Worker: {w.email} (id={w.id}, wage=${w.hourly_wage}/hr)")

    # Create open pay period for current week
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    pp = db.create_pay_period(monday.isoformat(), sunday.isoformat())
    print(f"\nPay period created: {pp.start_date} to {pp.end_date} (id={pp.id})")

    print("\n" + "=" * 50)
    print("Seeded successfully!")
    print("=" * 50)
    print("\nDemo users created with the password from PVFY_DEMO_PASSWORD:")
    print("  Boss:    boss@petition.co")
    print("  Admin 1: admin1@petition.co")
    print("  Admin 2: admin2@petition.co")
    for i in range(1, 6):
        print(f"  Worker {i}: worker{i}@petition.co")
    print("\nLogin at: http://localhost:8000/static/login.html")
    print("Admin UI: http://localhost:8000/static/dashboard.html")
    print("Worker UI: http://localhost:8000/static/worker.html")
    print("Petition review: http://localhost:8000/")


if __name__ == "__main__":
    main()
