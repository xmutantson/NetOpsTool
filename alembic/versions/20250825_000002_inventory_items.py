"""add inventory_items table

Revision ID: 000002_inventory
Revises: 000001_init
Create Date: 2025-08-25 00:00:02
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "000002_inventory"
down_revision = "000001_init"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("item", sa.String(128), nullable=False),
        sa.Column("qty", sa.Float(), nullable=True),
        sa.Column("weight_lbs", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("station_id", "item", name="uq_inventory_station_item"),
    )
    op.create_index(
        "ix_inventory_station_item", "inventory_items", ["station_id", "item"]
    )

def downgrade():
    op.drop_index("ix_inventory_station_item", table_name="inventory_items")
    op.drop_table("inventory_items")
