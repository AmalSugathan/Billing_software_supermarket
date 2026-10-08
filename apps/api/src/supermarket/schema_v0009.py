"""Frozen OCR intake schema; do not edit after publication."""

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid

metadata = sa.MetaData()
for name, columns in {
    "business": ["id"],
    "store": ["id", "business_id"],
    "user_account": ["id"],
    "supplier": ["id", "business_id"],
    "product": ["id", "business_id"],
}.items():
    sa.Table(name, metadata, *(uid(c, primary_key=c == "id") for c in columns))


def command():
    return (
        uid("idempotency_key"),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("business_id", "idempotency_key"),
    )


def scope():
    return (
        uid("business_id"),
        uid("store_id"),
        uid("actor_user_id"),
        sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user_account.id"]),
        timestamp(),
    )


documents = sa.Table(
    "ocr_document",
    metadata,
    uid(primary_key=True),
    *scope(),
    sa.Column("filename", sa.String(120), nullable=False),
    sa.Column("mime_type", sa.String(40), nullable=False),
    sa.Column("data_origin", sa.String(20), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("byte_size", sa.Integer, nullable=False),
    sa.Column("page_count", sa.Integer, nullable=False),
    sa.Column("original_ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("processing_ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("processing_mime", sa.String(40), nullable=False),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "sha256"),
    sa.CheckConstraint("byte_size > 0 AND byte_size <= 10485760 AND page_count BETWEEN 1 AND 10"),
    sa.CheckConstraint("mime_type IN ('image/jpeg', 'image/png', 'application/pdf')"),
    sa.CheckConstraint("data_origin IN ('authentic', 'synthetic', 'unknown')"),
    *command(),
    sa.Index("ix_ocr_document_store_date", "business_id", "store_id", "created_at"),
)
attempts = sa.Table(
    "ocr_attempt",
    metadata,
    uid(primary_key=True),
    *scope(),
    uid("document_id"),
    sa.Column("provider_model", sa.String(100), nullable=False),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "document_id"],
        ["ocr_document.business_id", "ocr_document.store_id", "ocr_document.id"],
    ),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    *command(),
    sa.Index("ix_ocr_attempt_document_date", "business_id", "document_id", "created_at"),
)
results = sa.Table(
    "ocr_result",
    metadata,
    uid(primary_key=True),
    *scope(),
    uid("attempt_id"),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("error_code", sa.String(40)),
    sa.Column("evidence_ciphertext", sa.LargeBinary),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "attempt_id"],
        ["ocr_attempt.business_id", "ocr_attempt.store_id", "ocr_attempt.id"],
    ),
    sa.UniqueConstraint("business_id", "attempt_id"),
    sa.CheckConstraint(
        "(status = 'review_required' AND evidence_ciphertext IS NOT NULL AND "
        "error_code IS NULL) OR (status = 'failed' AND error_code IS NOT NULL "
        "AND evidence_ciphertext IS NULL)"
    ),
)
mappings = sa.Table(
    "supplier_product_mapping",
    metadata,
    uid(primary_key=True),
    *scope(),
    uid("supplier_id"),
    uid("product_id"),
    uid("document_id"),
    sa.Column("supplier_description", sa.String(250), nullable=False),
    sa.Column("normalized_description", sa.String(250), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    sa.ForeignKeyConstraint(
        ["business_id", "supplier_id"], ["supplier.business_id", "supplier.id"]
    ),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "document_id"],
        ["ocr_document.business_id", "ocr_document.store_id", "ocr_document.id"],
    ),
    sa.UniqueConstraint("business_id", "supplier_id", "normalized_description"),
    *command(),
)
TABLE_NAMES = ("ocr_document", "ocr_attempt", "ocr_result", "supplier_product_mapping")
