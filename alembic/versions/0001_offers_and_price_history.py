"""offers and price history

Revision ID: 0001
Revises:
Create Date: 2026-10-08

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("info", sa.Text(), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("cian_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "offer_id",
            sa.BigInteger(),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_price_history_offer_seen", "price_history", ["offer_id", "seen_at"])


def downgrade() -> None:
    op.drop_index("ix_price_history_offer_seen", table_name="price_history")
    op.drop_table("price_history")
    op.drop_table("offers")
