"""domclick estimates

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "domclick_estimates",
        sa.Column(
            "offer_id",
            sa.BigInteger(),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("house_guid", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("market_price", sa.BigInteger(), nullable=True),
        sa.Column("price_m2", sa.BigInteger(), nullable=True),
        sa.Column("min_price", sa.BigInteger(), nullable=True),
        sa.Column("max_price", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("estimated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("domclick_estimates")
