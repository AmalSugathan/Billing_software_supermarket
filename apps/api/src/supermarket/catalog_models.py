"""Live catalog and inventory tables; schema changes require new migrations."""

from datetime import datetime

import sqlalchemy as sa

metadata = sa.MetaData()


def uid(name: str = "id", *, primary_key: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.Uuid(as_uuid=False), nullable=False, primary_key=primary_key)


def timestamp() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


# Reference-only tables already installed by 0002. Never create/drop these here.
sa.Table("business", metadata, uid(primary_key=True))
sa.Table("store", metadata, uid(primary_key=True), uid("business_id"))
sa.Table("user_account", metadata, uid(primary_key=True))


def named_table(name: str) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        uid(primary_key=True),
        sa.Column(
            "business_id", sa.Uuid(as_uuid=False), sa.ForeignKey("business.id"), nullable=False
        ),
        sa.Column("name", sa.String(150), nullable=False),
        timestamp(),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "name"),
    )


categories = named_table("category")
brands = named_table("brand")
suppliers = sa.Table(
    "supplier",
    metadata,
    uid(primary_key=True),
    sa.Column("business_id", sa.Uuid(as_uuid=False), sa.ForeignKey("business.id"), nullable=False),
    sa.Column("name", sa.String(150), nullable=False),
    sa.Column("gstin", sa.String(15)),
    sa.Column("phone", sa.String(20), nullable=False),
    sa.Column("address", sa.String(500), nullable=False),
    sa.Column("payment_terms_days", sa.Integer, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    timestamp(),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "name"),
    sa.UniqueConstraint("business_id", "gstin"),
    sa.CheckConstraint("payment_terms_days BETWEEN 0 AND 365"),
)
products = sa.Table(
    "product",
    metadata,
    uid(primary_key=True),
    sa.Column("business_id", sa.Uuid(as_uuid=False), sa.ForeignKey("business.id"), nullable=False),
    sa.Column("sku", sa.String(64), nullable=False),
    sa.Column("name", sa.String(180), nullable=False),
    sa.Column("normalized_name", sa.String(180), nullable=False),
    sa.Column("unit", sa.String(16), nullable=False),
    sa.Column("category_id", sa.Uuid(as_uuid=False)),
    sa.Column("brand_id", sa.Uuid(as_uuid=False)),
    sa.Column("supplier_id", sa.Uuid(as_uuid=False)),
    sa.Column("purchase_price", sa.Numeric(14, 2), nullable=False),
    sa.Column("landed_cost", sa.Numeric(14, 2), nullable=False),
    sa.Column("selling_price", sa.Numeric(14, 2), nullable=False),
    sa.Column("mrp", sa.Numeric(14, 2), nullable=False),
    sa.Column("minimum_selling_price", sa.Numeric(14, 2), nullable=False),
    sa.Column("target_margin", sa.Numeric(5, 2), nullable=False),
    sa.Column("gst_rate", sa.Numeric(5, 2), nullable=False),
    sa.Column("hsn", sa.String(8)),
    sa.Column("reorder_level", sa.Numeric(15, 3), nullable=False),
    sa.Column("reorder_quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("batch_tracking", sa.Boolean, nullable=False),
    sa.Column("expiry_tracking", sa.Boolean, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    timestamp(),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "sku"),
    sa.UniqueConstraint("business_id", "normalized_name", "unit"),
    sa.ForeignKeyConstraint(
        ["business_id", "category_id"], ["category.business_id", "category.id"]
    ),
    sa.ForeignKeyConstraint(["business_id", "brand_id"], ["brand.business_id", "brand.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "supplier_id"], ["supplier.business_id", "supplier.id"]
    ),
    sa.CheckConstraint(
        "purchase_price >= 0 AND landed_cost >= 0 AND selling_price >= 0 AND mrp >= 0"
    ),
    sa.CheckConstraint(
        "minimum_selling_price >= 0 AND minimum_selling_price <= selling_price "
        "AND selling_price <= mrp"
    ),
    sa.CheckConstraint("gst_rate BETWEEN 0 AND 100 AND target_margin BETWEEN 0 AND 100"),
    sa.CheckConstraint("reorder_level >= 0 AND reorder_quantity >= 0"),
    sa.CheckConstraint("NOT expiry_tracking OR batch_tracking"),
    sa.CheckConstraint("unit IN ('pcs', 'pack', 'kg', 'g', 'l', 'ml')"),
    sa.Index("ix_product_business_name", "business_id", "normalized_name"),
)
barcodes = sa.Table(
    "barcode",
    metadata,
    uid("business_id", primary_key=True),
    sa.Column("value", sa.String(64), primary_key=True),
    uid("product_id"),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
    sa.Index("ix_barcode_product", "business_id", "product_id"),
)
aliases = sa.Table(
    "product_alias",
    metadata,
    uid("business_id", primary_key=True),
    uid("product_id", primary_key=True),
    sa.Column("name", sa.String(180), primary_key=True),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
)
batches = sa.Table(
    "stock_batch",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    sa.Column("batch_number", sa.String(100)),
    sa.Column("expiry_date", sa.Date),
    timestamp(),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "product_id", "id"),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
    sa.Index("ix_batch_expiry", "business_id", "store_id", "expiry_date"),
)
movements = sa.Table(
    "stock_movement",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    uid("batch_id"),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("unit_cost", sa.Numeric(18, 6), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    sa.Column(
        "actor_user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("user_account.id"), nullable=False
    ),
    sa.Column("source", sa.String(16), nullable=False),
    sa.Column("human_approved", sa.Boolean, nullable=False),
    sa.Column("idempotency_key", sa.Uuid(as_uuid=False), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "product_id", "batch_id"],
        [
            "stock_batch.business_id",
            "stock_batch.store_id",
            "stock_batch.product_id",
            "stock_batch.id",
        ],
    ),
    sa.UniqueConstraint("business_id", "idempotency_key"),
    sa.UniqueConstraint("business_id", "id", name="uq_stock_movement_business_id"),
    sa.UniqueConstraint(
        "business_id", "store_id", "product_id", "id", name="uq_stock_movement_scope"
    ),
    sa.CheckConstraint("quantity <> 0 AND unit_cost >= 0"),
    sa.CheckConstraint(
        "kind <> 'opening' OR (quantity > 0 AND human_approved AND source = 'human')"
    ),
    sa.CheckConstraint(
        "kind IN ('opening', 'purchase', 'sale', 'sales_return', 'purchase_return', "
        "'wastage', 'damage', 'adjustment', 'transfer')"
    ),
    sa.CheckConstraint("source IN ('human', 'ai', 'background')"),
    sa.CheckConstraint("length(trim(reason)) > 0"),
    sa.Index("ix_movement_stock", "business_id", "store_id", "product_id", "created_at"),
)

TABLE_NAMES = (
    "category",
    "brand",
    "supplier",
    "product",
    "barcode",
    "product_alias",
    "stock_batch",
    "stock_movement",
)
