"""Append-only reviewed purchase documents; changes require a new migration."""

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid

metadata = sa.MetaData()
for name in ("business", "store", "supplier", "product", "stock_batch", "stock_movement"):
    sa.Table(name, metadata, uid(primary_key=True), uid("business_id"))
sa.Table("user_account", metadata, uid(primary_key=True))
for name in ("stock_batch", "stock_movement"):
    metadata.tables[name].append_column(uid("store_id"))
    metadata.tables[name].append_column(uid("product_id"))

purchases = sa.Table(
    "purchase",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("supplier_id"),
    sa.Column("supplier_name", sa.String(150), nullable=False),
    sa.Column("supplier_gstin", sa.String(15)),
    sa.Column("invoice_number", sa.String(100), nullable=False),
    sa.Column("invoice_identity", sa.String(100), nullable=False),
    sa.Column("invoice_date", sa.Date, nullable=False),
    sa.Column("financial_year", sa.Integer, nullable=False),
    sa.Column("tax_mode", sa.String(16), nullable=False),
    sa.Column("tax_kind", sa.String(16), nullable=False),
    sa.Column("taxable_total", sa.Numeric(14, 2), nullable=False),
    sa.Column("cgst", sa.Numeric(14, 2), nullable=False),
    sa.Column("sgst", sa.Numeric(14, 2), nullable=False),
    sa.Column("igst", sa.Numeric(14, 2), nullable=False),
    sa.Column("round_off", sa.Numeric(4, 2), nullable=False),
    sa.Column("invoice_total", sa.Numeric(14, 2), nullable=False),
    sa.Column("review_reason", sa.String(500), nullable=False),
    sa.Column(
        "actor_user_id", sa.Uuid(as_uuid=False), sa.ForeignKey("user_account.id"), nullable=False
    ),
    sa.Column("human_approved", sa.Boolean, nullable=False),
    sa.Column("source", sa.String(16), nullable=False),
    sa.Column("idempotency_key", sa.Uuid(as_uuid=False), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    timestamp(),
    sa.UniqueConstraint("business_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    sa.UniqueConstraint("business_id", "idempotency_key"),
    sa.UniqueConstraint("business_id", "supplier_id", "financial_year", "invoice_identity"),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "supplier_id"], ["supplier.business_id", "supplier.id"]
    ),
    sa.CheckConstraint("human_approved AND source = 'human'"),
    sa.CheckConstraint("tax_mode IN ('exclusive', 'inclusive')"),
    sa.CheckConstraint("tax_kind IN ('intra', 'inter')"),
    sa.CheckConstraint("taxable_total >= 0 AND cgst >= 0 AND sgst >= 0 AND igst >= 0"),
    sa.CheckConstraint("abs(round_off) <= 1 AND invoice_total > 0"),
    sa.CheckConstraint("invoice_total = taxable_total + cgst + sgst + igst + round_off"),
    sa.CheckConstraint(
        "(tax_kind = 'intra' AND igst = 0) OR (tax_kind = 'inter' AND cgst = 0 AND sgst = 0)"
    ),
    sa.Index("ix_purchase_store_date", "business_id", "store_id", "invoice_date", "id"),
    sa.Index("ix_purchase_supplier_date", "business_id", "supplier_id", "invoice_date"),
)
items = sa.Table(
    "purchase_item",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("purchase_id"),
    uid("store_id"),
    uid("product_id"),
    uid("batch_id"),
    uid("movement_id"),
    sa.Column("line_number", sa.Integer, nullable=False),
    sa.Column("product_name", sa.String(180), nullable=False),
    sa.Column("sku", sa.String(64), nullable=False),
    sa.Column("stock_unit", sa.String(16), nullable=False),
    sa.Column("supplier_description", sa.String(250), nullable=False),
    sa.Column("purchase_unit", sa.String(16), nullable=False),
    sa.Column("purchase_quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("units_per_purchase", sa.Numeric(15, 3), nullable=False),
    sa.Column("free_stock_quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("stock_quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("conversion_evidence", sa.String(500), nullable=False),
    sa.Column("unit_rate", sa.Numeric(18, 6), nullable=False),
    sa.Column("discount", sa.Numeric(14, 2), nullable=False),
    sa.Column("gst_rate", sa.Numeric(5, 2), nullable=False),
    sa.Column("hsn", sa.String(8)),
    sa.Column("taxable_value", sa.Numeric(14, 2), nullable=False),
    sa.Column("cgst", sa.Numeric(14, 2), nullable=False),
    sa.Column("sgst", sa.Numeric(14, 2), nullable=False),
    sa.Column("igst", sa.Numeric(14, 2), nullable=False),
    sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
    sa.Column("unit_cost", sa.Numeric(18, 6), nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "purchase_id"],
        ["purchase.business_id", "purchase.store_id", "purchase.id"],
    ),
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
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "product_id", "movement_id"],
        [
            "stock_movement.business_id",
            "stock_movement.store_id",
            "stock_movement.product_id",
            "stock_movement.id",
        ],
    ),
    sa.UniqueConstraint("business_id", "purchase_id", "line_number"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint(
        "purchase_quantity > 0 AND units_per_purchase > 0 AND free_stock_quantity >= 0"
    ),
    sa.CheckConstraint(
        "stock_quantity = purchase_quantity * units_per_purchase + free_stock_quantity"
    ),
    sa.CheckConstraint(
        "unit_rate >= 0 AND discount >= 0 AND unit_cost >= 0 AND gst_rate BETWEEN 0 AND 100"
    ),
    sa.CheckConstraint("taxable_value >= 0 AND cgst >= 0 AND sgst >= 0 AND igst >= 0"),
    sa.CheckConstraint("line_total = taxable_value + cgst + sgst + igst"),
    sa.Index("ix_purchase_item_product", "business_id", "product_id"),
    sa.Index("ix_purchase_item_batch", "business_id", "batch_id"),
)
TABLE_NAMES = ("purchase", "purchase_item")
