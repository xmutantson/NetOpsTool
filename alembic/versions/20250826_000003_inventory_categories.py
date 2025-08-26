"""add category fields to inventory_items and adjust unique constraint

Revision ID: 000003_inventory_categories
Revises: 000002_inventory
Create Date: 2025-08-26 00:00:03
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "000003_inventory_categories"
down_revision = "000002_inventory"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("inventory_items") as batch:
        batch.add_column(sa.Column("category", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        # drop old unique on (station_id, item)
        batch.drop_constraint("uq_inventory_station_item", type_="unique")
        # new unique on (station_id, category, item)
        batch.create_unique_constraint(
            "uq_inventory_station_cat_item",
            ["station_id", "category", "item"]
        )
    op.create_index(
        "ix_inventory_station_category",
        "inventory_items",
        ["station_id", "category"]
    )

def downgrade():
    op.drop_index("ix_inventory_station_category", table_name="inventory_items")
    with op.batch_alter_table("inventory_items") as batch:
        batch.drop_constraint("uq_inventory_station_cat_item", type_="unique")
        batch.create_unique_constraint(
            "uq_inventory_station_item",
            ["station_id", "item"]
        )
        batch.drop_column("category_id")
        batch.drop_column("category")
