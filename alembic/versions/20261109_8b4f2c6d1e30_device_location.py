"""device location

Where an IoT device stands (FarmFuture reports each device's installed position), so devices can be shown on the farm
map and checked against their plot. A precise location – shown only with farmer.read_pii.

Revision ID: 8b4f2c6d1e30
Revises: 7d3e9a1c4b20
Create Date: 2026-11-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '8b4f2c6d1e30'
down_revision: Union[str, Sequence[str], None] = '7d3e9a1c4b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('devices', sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('devices', sa.Column('location_source', sa.Unicode(length=40), nullable=True))
    op.add_column('devices', sa.Column('location_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'location_updated_at')
    op.drop_column('devices', 'location_source')
    op.drop_column('devices', 'longitude')
    op.drop_column('devices', 'latitude')
