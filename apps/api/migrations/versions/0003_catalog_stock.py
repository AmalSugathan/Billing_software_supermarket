"""Catalog, suppliers and immutable opening-stock evidence."""

from alembic import op

from supermarket.schema_v0003 import TABLE_NAMES, metadata

revision = "0003_catalog_stock"
down_revision = "0002_identity_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in metadata.sorted_tables:
        if table.name in TABLE_NAMES:
            table.create(bind)
    for name in TABLE_NAMES:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        scope = "NULLIF(current_setting('app.business_id', true), '')::uuid"
        op.execute(
            f'CREATE POLICY tenant_scope ON "{name}" USING (business_id = {scope}) '
            f"WITH CHECK (business_id = {scope})"
        )
    for name in ("stock_movement", "stock_batch"):
        op.execute(
            f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON "{name}" '
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    op.execute(
        "CREATE UNIQUE INDEX one_opening_per_batch ON stock_movement "
        "(business_id, batch_id) WHERE kind = 'opening'"
    )
    op.execute(
        "CREATE UNIQUE INDEX unique_stock_lot ON stock_batch (business_id, store_id, product_id, "
        "COALESCE(batch_number, ''), COALESCE(expiry_date, DATE '0001-01-01'))"
    )


def downgrade() -> None:
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
