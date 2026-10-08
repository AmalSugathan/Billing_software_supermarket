"""Atomic approved invoice-to-purchase provenance; no silent financial posting."""

from alembic import op
from sqlalchemy import text

from supermarket.schema_v0010 import TABLE_NAMES, metadata

revision = "0010_ocr_purchase"
down_revision = "0009_ocr_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_ocr_attempt_document_source",
        "ocr_attempt",
        ["business_id", "store_id", "document_id", "id"],
    )
    for table in metadata.sorted_tables:
        if table.name in TABLE_NAMES:
            table.create(op.get_bind())
    for name in TABLE_NAMES:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        scope = "NULLIF(current_setting('app.business_id',true),'')::uuid"
        op.execute(
            f'CREATE POLICY tenant_scope ON "{name}" USING (business_id={scope}) '
            f"WITH CHECK (business_id={scope})"
        )
        op.execute(
            f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON "{name}" '
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )


def downgrade():
    for name in TABLE_NAMES:
        if op.get_bind().execute(text('SELECT EXISTS(SELECT 1 FROM "' + name + '")')).scalar():  # noqa: S608 -- frozen table names
            raise RuntimeError("Stored invoice evidence requires forward recovery")
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())

    op.drop_constraint("uq_ocr_attempt_document_source", "ocr_attempt", type_="unique")
