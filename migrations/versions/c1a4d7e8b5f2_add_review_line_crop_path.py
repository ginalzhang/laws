"""add review line crop path

Revision ID: c1a4d7e8b5f2
Revises: 6bd2f4c1a9e7
Create Date: 2026-05-14 17:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a4d7e8b5f2"
down_revision: Union[str, Sequence[str], None] = "6bd2f4c1a9e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_packet_lines",
        sa.Column("crop_path", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_packet_lines", "crop_path")
