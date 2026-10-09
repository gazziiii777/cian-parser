"""hidden: offers a user does not want to see

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "hidden",
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
        sa.UniqueConstraint("telegram_chat_id", "offer_id", name="uq_hidden_chat_offer"),
    )


def downgrade() -> None:
    op.drop_table("hidden")
