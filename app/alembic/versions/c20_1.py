"""Migración sintética c20_1: crea la tabla tesis_c20_1

Revision ID: c20_1
Revises: e3acbde9bd2f
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c20_1'
down_revision = 'e3acbde9bd2f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tesis_c20_1',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nota', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('tesis_c20_1')
