"""Deployment marker only; business migrations follow identity/tenancy design."""

import sqlalchemy as sa
from alembic import op

revision = "0001_pipeline_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "deployment_metadata",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.bulk_insert(table, [{"key": "schema_baseline", "value": "development-pipeline"}])


def downgrade() -> None:
    op.drop_table("deployment_metadata")
