"""offers: coordinates and floor for background estimates

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("offers", sa.Column("lng", sa.Float(), nullable=True))
    op.add_column("offers", sa.Column("floor", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "floor")
    op.drop_column("offers", "lng")
    op.drop_column("offers", "lat")
