"""add adsb_lol position source

Revision ID: 8db027860662
Revises: a5d0ac8d87b6
Create Date: 2026-09-12 17:30:22.554107

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8db027860662'
down_revision: Union[str, Sequence[str], None] = 'a5d0ac8d87b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written -- same as b286fc804e53 (add swim position source): autogenerate
    # doesn't detect native Postgres ENUM value changes.
    op.execute("ALTER TYPE positionsource ADD VALUE IF NOT EXISTS 'adsb_lol'")


def downgrade() -> None:
    raise NotImplementedError("Cannot drop an enum value in Postgres without rebuilding the type")
