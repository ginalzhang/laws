"""add review line voter suggestions

Revision ID: f39b8d2a61c4
Revises: c1a4d7e8b5f2
Create Date: 2026-05-14 19:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f39b8d2a61c4"
down_revision: Union[str, Sequence[str], None] = "c1a4d7e8b5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_packet_lines",
        sa.Column("voter_suggestions_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_packet_lines", "voter_suggestions_json")
