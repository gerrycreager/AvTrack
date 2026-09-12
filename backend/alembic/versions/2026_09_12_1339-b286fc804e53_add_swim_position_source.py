"""add swim position source

Revision ID: b286fc804e53
Revises: 52d975c226ce
Create Date: 2026-09-12 13:39:44.038388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b286fc804e53'
down_revision: Union[str, Sequence[str], None] = '52d975c226ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written -- autogenerate doesn't detect native Postgres ENUM value changes
    # (a known SQLAlchemy/Alembic limitation, confirmed empty on --autogenerate here).
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction, as long as
    # the new value isn't *used* in the same transaction -- fine here, nothing else
    # in this migration references 'swim'.
    op.execute("ALTER TYPE positionsource ADD VALUE IF NOT EXISTS 'swim'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- removing an enum value requires
    # rebuilding the type (create new type, migrate column, drop old type), which
    # isn't worth the risk/complexity for a downgrade path here. Not implemented.
    raise NotImplementedError("Cannot drop an enum value in Postgres without rebuilding the type")
