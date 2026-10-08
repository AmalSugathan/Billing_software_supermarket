"""Identity and tenancy schema shared with the reviewed migration."""

from datetime import datetime

import sqlalchemy as sa

metadata = sa.MetaData()


def identifier(name: str = "id", *, primary_key: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.Uuid(as_uuid=False), primary_key=primary_key, nullable=False)


def created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


users = sa.Table(
    "user_account",
    metadata,
    identifier(primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),
    sa.Column("display_name", sa.String(100), nullable=False),
    sa.Column("password_hash", sa.String(180), nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    created_at(),
)
sessions = sa.Table(
    "auth_session",
    metadata,
    sa.Column("token_digest", sa.String(64), primary_key=True),
    sa.Column(
        "user_id",
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("user_account.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
    created_at(),
)
rate_limits = sa.Table(
    "auth_rate_limit",
    metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.CheckConstraint("attempts > 0"),
)
security_events = sa.Table(
    "security_event",
    metadata,
    identifier(primary_key=True),
    sa.Column("user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("user_account.id")),
    sa.Column("action", sa.String(64), nullable=False),
    created_at(),
)
businesses = sa.Table(
    "business",
    metadata,
    identifier(primary_key=True),
    sa.Column("name", sa.String(150), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("timezone", sa.String(50), nullable=False),
    created_at(),
    sa.CheckConstraint("currency = 'INR'"),
)
stores = sa.Table(
    "store",
    metadata,
    identifier(primary_key=True),
    sa.Column(
        "business_id",
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("business.id"),
        nullable=False,
        index=True,
    ),
    sa.Column("name", sa.String(150), nullable=False),
    sa.Column("address", sa.String(500), nullable=False),
    created_at(),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "name"),
)
terminals = sa.Table(
    "terminal",
    metadata,
    identifier(primary_key=True),
    identifier("business_id"),
    identifier("store_id"),
    sa.Column("name", sa.String(100), nullable=False),
    created_at(),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "name"),
    sa.Index("ix_terminal_business_store", "business_id", "store_id"),
    sa.UniqueConstraint("business_id", "store_id", "id", name="uq_terminal_store_scope"),
)
permissions = sa.Table("permission", metadata, sa.Column("code", sa.String(64), primary_key=True))
roles = sa.Table(
    "role",
    metadata,
    identifier(primary_key=True),
    sa.Column("business_id", sa.Uuid(as_uuid=False), sa.ForeignKey("business.id"), nullable=False),
    sa.Column("name", sa.String(32), nullable=False),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "name"),
)
role_permissions = sa.Table(
    "role_permission",
    metadata,
    identifier("business_id", primary_key=True),
    identifier("role_id", primary_key=True),
    sa.Column("permission_code", sa.String(64), sa.ForeignKey("permission.code"), primary_key=True),
    sa.ForeignKeyConstraint(["business_id", "role_id"], ["role.business_id", "role.id"]),
)
memberships = sa.Table(
    "membership",
    metadata,
    identifier(primary_key=True),
    identifier("business_id"),
    sa.Column(
        "user_id",
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("user_account.id"),
        nullable=False,
        index=True,
    ),
    identifier("role_id"),
    sa.Column("all_stores", sa.Boolean, nullable=False),
    created_at(),
    sa.ForeignKeyConstraint(["business_id", "role_id"], ["role.business_id", "role.id"]),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "user_id"),
)
membership_stores = sa.Table(
    "membership_store",
    metadata,
    identifier("business_id", primary_key=True),
    identifier("membership_id", primary_key=True),
    identifier("store_id", primary_key=True),
    sa.ForeignKeyConstraint(
        ["business_id", "membership_id"], ["membership.business_id", "membership.id"]
    ),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
)
audit_logs = sa.Table(
    "audit_log",
    metadata,
    identifier(primary_key=True),
    sa.Column("business_id", sa.Uuid(as_uuid=False), sa.ForeignKey("business.id"), nullable=False),
    sa.Column(
        "actor_user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("user_account.id"), nullable=False
    ),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("resource_id", sa.String(64), nullable=False),
    sa.Column("source", sa.String(16), nullable=False),
    sa.Column("before_value", sa.JSON),
    sa.Column("after_value", sa.JSON, nullable=False),
    created_at(),
    sa.CheckConstraint("source IN ('human', 'ai', 'background')"),
    sa.Index("ix_audit_business_created", "business_id", "created_at"),
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "OWNER": frozenset(
        {
            "business.read",
            "business.manage",
            "stores.manage",
            "staff.manage",
            "audit.read",
            "catalog.manage",
            "inventory.read",
            "purchases.manage",
            "expenses.manage",
            "sales.create",
            "cash.sessions",
            "finance.read",
            "actions.approve",
        }
    ),
    "STORE_MANAGER": frozenset(
        {
            "business.read",
            "stores.manage",
            "audit.read",
            "catalog.manage",
            "inventory.read",
            "purchases.manage",
            "expenses.manage",
            "sales.create",
            "cash.sessions",
        }
    ),
    "CASHIER": frozenset({"business.read", "sales.create", "cash.sessions"}),
    "INVENTORY_MANAGER": frozenset({"business.read", "catalog.manage", "inventory.read"}),
    "PURCHASE_MANAGER": frozenset({"business.read", "purchases.manage", "inventory.read"}),
    "ACCOUNTANT": frozenset({"business.read", "expenses.manage", "finance.read", "audit.read"}),
    "ADMIN": frozenset(
        {"business.read", "business.manage", "stores.manage", "staff.manage", "audit.read"}
    ),
}
