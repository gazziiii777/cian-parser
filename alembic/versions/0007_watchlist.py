"""watchlist: which user tracks which offer

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "telegram_chat_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "offer_id", sa.BigInteger(), sa.ForeignKey("offers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_chat_id", "offer_id", name="uq_watchlist_chat_offer"),
    )
    op.create_index("ix_watchlist_offer", "watchlist", ["offer_id"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_offer", table_name="watchlist")
    op.drop_table("watchlist")
