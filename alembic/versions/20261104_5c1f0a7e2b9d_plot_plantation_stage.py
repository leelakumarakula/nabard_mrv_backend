"""plot plantation stage

Proposal §5.2: interventions are selected by verified plantation maturity (productive, young, underperforming).

Revision ID: 5c1f0a7e2b9d
Revises: 69f0d87a2d80
Create Date: 2026-11-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '5c1f0a7e2b9d'
down_revision: Union[str, Sequence[str], None] = '69f0d87a2d80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('farm_plots', sa.Column('plantation_stage', sa.Unicode(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column('farm_plots', 'plantation_stage')
