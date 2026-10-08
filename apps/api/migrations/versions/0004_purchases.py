"""Reviewed purchases and precise source-cost snapshots."""

import sqlalchemy as sa
from alembic import op

from supermarket.schema_v0004 import TABLE_NAMES, metadata

revision = "0004_purchases"
down_revision = "0003_catalog_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("stock_movement", "unit_cost", type_=sa.Numeric(18, 6))
    op.create_unique_constraint(
        "uq_stock_movement_business_id", "stock_movement", ["business_id", "id"]
    )
    op.create_unique_constraint(
        "uq_stock_movement_scope", "stock_movement", ["business_id", "store_id", "product_id", "id"]
    )
    for table in metadata.sorted_tables:
        if table.name in TABLE_NAMES:
            table.create(op.get_bind())
    for name in TABLE_NAMES:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        scope = "NULLIF(current_setting('app.business_id', true), '')::uuid"
        op.execute(
            f'CREATE POLICY tenant_scope ON "{name}" USING (business_id = {scope}) '
            f"WITH CHECK (business_id = {scope})"
        )
        op.execute(
            f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON "{name}" '
            f"FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )


def downgrade() -> None:
    # Refuse a precision-losing rollback; deployed financial corrections go forward.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM stock_movement WHERE unit_cost "
        "<> round(unit_cost, 2)) THEN RAISE EXCEPTION 'Cannot downgrade "
        "precise stock costs'; END IF; END $$"
    )
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
    op.drop_constraint("uq_stock_movement_scope", "stock_movement", type_="unique")
    op.drop_constraint("uq_stock_movement_business_id", "stock_movement", type_="unique")
    op.alter_column("stock_movement", "unit_cost", type_=sa.Numeric(14, 2))
