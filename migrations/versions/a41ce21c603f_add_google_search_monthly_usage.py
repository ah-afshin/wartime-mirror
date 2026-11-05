"""add Google Search monthly usage ledger

Revision ID: a41ce21c603f
Revises: 63d32953b3fe
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a41ce21c603f"
down_revision: Union[str, None] = "63d32953b3fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "google_search_monthly_usage",
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "requests_used >= 0",
            name="ck_google_search_usage_nonnegative",
        ),
        sa.PrimaryKeyConstraint("month"),
    )


def downgrade() -> None:
    op.drop_table("google_search_monthly_usage")
