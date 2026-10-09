"""offer status, sold_at, district, sweep misses

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("status", sa.Text(), nullable=False, server_default="active"))
    op.add_column("offers", sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offers", sa.Column("raion_id", sa.Integer(), nullable=True))
    op.add_column("offers", sa.Column("raion_name", sa.Text(), nullable=True))
    op.add_column("offers", sa.Column("sweep_misses", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_offers_status_raion", "offers", ["status", "raion_id"])


def downgrade() -> None:
    op.drop_index("ix_offers_status_raion", table_name="offers")
    op.drop_column("offers", "sweep_misses")
    op.drop_column("offers", "raion_name")
    op.drop_column("offers", "raion_id")
    op.drop_column("offers", "sold_at")
    op.drop_column("offers", "status")
