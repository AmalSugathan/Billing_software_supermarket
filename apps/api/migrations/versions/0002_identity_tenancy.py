"""Identity, store scopes, audit immutability and PostgreSQL tenant isolation."""

from alembic import op

from supermarket.schema_v0002 import ROLE_CAPABILITIES, metadata, permissions

revision = "0002_identity_tenancy"
down_revision = "0001_pipeline_baseline"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "business",
    "store",
    "terminal",
    "role",
    "role_permission",
    "membership",
    "membership_store",
    "audit_log",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Identity migrations require PostgreSQL; SQLite cannot verify RLS")
    # Frozen snapshot: do not import the live application models into historical migrations.
    for table in metadata.sorted_tables:
        table.create(bind)
    op.bulk_insert(
        permissions, [{"code": code} for code in sorted(set().union(*ROLE_CAPABILITIES.values()))]
    )
    scope = "NULLIF(current_setting('app.business_id', true), '')::uuid"
    actor = "NULLIF(current_setting('app.user_id', true), '')::uuid"
    for name in TENANT_TABLES:
        column = "id" if name == "business" else "business_id"
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{name}" USING ({column} = {scope}) '
            f"WITH CHECK ({column} = {scope})"
        )
    op.execute(f"CREATE POLICY own_memberships ON membership FOR SELECT USING (user_id = {actor})")
    op.execute(
        "CREATE POLICY own_businesses ON business FOR SELECT USING (EXISTS "
        "(SELECT 1 FROM membership WHERE membership.business_id = business.id "
        "AND membership.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))"
    )
    op.execute("""
      CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Immutable record: append a correction instead'; END;
      $$
    """)
    for name in ("audit_log", "security_event"):
        op.execute(
            f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON "{name}" '
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )


def downgrade() -> None:
    op.execute("DROP POLICY own_businesses ON business")
    op.execute("DROP POLICY own_memberships ON membership")
    for name in TENANT_TABLES:
        op.execute(f'DROP POLICY tenant_scope ON "{name}"')
    for table in reversed(metadata.sorted_tables):
        table.drop(op.get_bind())
    op.execute("DROP FUNCTION reject_immutable_change()")
