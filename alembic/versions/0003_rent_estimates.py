"""rent estimates

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "rent_estimates",
        sa.Column(
            "offer_id",
            sa.BigInteger(),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("median_rent", sa.BigInteger(), nullable=False),
        sa.Column("min_rent", sa.BigInteger(), nullable=False),
        sa.Column("max_rent", sa.BigInteger(), nullable=False),
        sa.Column("estimated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rent_estimates")
