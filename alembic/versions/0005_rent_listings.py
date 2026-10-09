"""deal type, house, rooms and area on offers

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("deal_type", sa.Text(), nullable=False, server_default="sale"))
    op.add_column("offers", sa.Column("house_id", sa.BigInteger(), nullable=True))
    op.add_column("offers", sa.Column("rooms", sa.Integer(), nullable=True))
    op.add_column("offers", sa.Column("total_area", sa.Float(), nullable=True))
    op.create_index("ix_offers_deal_status_raion", "offers", ["deal_type", "status", "raion_id"])


def downgrade() -> None:
    op.drop_index("ix_offers_deal_status_raion", table_name="offers")
    op.drop_column("offers", "total_area")
    op.drop_column("offers", "rooms")
    op.drop_column("offers", "house_id")
    op.drop_column("offers", "deal_type")
