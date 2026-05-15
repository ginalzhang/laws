"""add review line low confidence fields

Revision ID: 6bd2f4c1a9e7
Revises: 494cd34bb43d
Create Date: 2026-05-14 17:25:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6bd2f4c1a9e7"
down_revision: Union[str, Sequence[str], None] = "494cd34bb43d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_packet_lines",
        sa.Column("low_confidence_fields", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_packet_lines", "low_confidence_fields")
