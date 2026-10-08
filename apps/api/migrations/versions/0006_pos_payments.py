"""Atomic online checkout and owner-confirmed supplier payments."""

from alembic import op
from sqlalchemy import text

from supermarket.schema_v0006 import TABLE_NAMES, metadata

revision = "0006_pos_payments"
down_revision = "0005_finance_cash"
branch_labels = None
depends_on = None

OLD = (
    "(kind = 'opening' AND amount >= 0 AND cash_session_id IS NULL) "
    "OR (kind = 'opening_variance' AND amount <> 0 AND "
    "cash_session_id IS NULL) OR (kind IN ('expense', 'withdrawal') "
    "AND amount < 0) OR (kind IN ('receipt', 'expense_reversal') AND "
    "amount > 0) OR (kind = 'cash_variance' AND amount <> 0 AND "
    "cash_session_id IS NOT NULL)"
)
NEW = OLD + " OR (kind = 'sale' AND amount > 0) OR (kind = 'supplier_payment' AND amount < 0)"


def upgrade() -> None:
    names = (
        op.get_bind()
        .execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                "'financial_movement'::regclass AND contype = 'c' AND "
                "pg_get_constraintdef(oid) LIKE '%kind%'"
            )
        )
        .scalars()
        .all()
    )
    if len(names) != 1:
        raise RuntimeError("Expected one financial movement kind constraint")
    op.drop_constraint(names[0], "financial_movement", type_="check")
    op.create_check_constraint("ck_money_kind", "financial_movement", NEW)
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
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )

    op.execute("""
      CREATE FUNCTION guard_supplier_payment() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE invoice_amount numeric; supplier uuid; paid numeric;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'purchase-payment:' || NEW.business_id::text || NEW.purchase_id::text, 0));
        SELECT invoice_total, supplier_id INTO invoice_amount, supplier FROM purchase
          WHERE id = NEW.purchase_id AND business_id = NEW.business_id AND store_id = NEW.store_id;
        SELECT COALESCE(sum(amount), 0) INTO paid FROM supplier_payment
          WHERE business_id = NEW.business_id AND purchase_id = NEW.purchase_id;
        IF supplier IS DISTINCT FROM NEW.supplier_id OR NEW.amount > invoice_amount - paid THEN
          RAISE EXCEPTION 'Invalid invoice allocation' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM financial_movement WHERE id = NEW.movement_id
          AND business_id = NEW.business_id AND store_id = NEW.store_id
          AND account_id = NEW.account_id AND amount = -NEW.amount
          AND kind = 'supplier_payment' AND resource_id = NEW.id) THEN
          RAISE EXCEPTION 'Supplier payment ledger mismatch' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_supplier_payment BEFORE INSERT ON supplier_payment
        FOR EACH ROW EXECUTE FUNCTION guard_supplier_payment();
      CREATE FUNCTION guard_sale_stock() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE available numeric;
      BEGIN
        IF NEW.kind <> 'sale' THEN RETURN NEW; END IF;
        PERFORM id FROM product WHERE id = NEW.product_id
          AND business_id = NEW.business_id FOR UPDATE;
        SELECT COALESCE(sum(quantity), 0) INTO available FROM stock_movement
          WHERE business_id = NEW.business_id AND batch_id = NEW.batch_id;
        IF NEW.quantity >= 0 OR available + NEW.quantity < 0 THEN
          RAISE EXCEPTION 'Insufficient stock' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_sale_stock BEFORE INSERT ON stock_movement
        FOR EACH ROW EXECUTE FUNCTION guard_sale_stock();
      CREATE FUNCTION check_complete_sale() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.total <> (SELECT COALESCE(sum(amount), 0) FROM sales_payment
          WHERE business_id = NEW.business_id AND sale_id = NEW.id)
          OR NEW.total <> (SELECT COALESCE(sum(total), 0) FROM sales_item
          WHERE business_id = NEW.business_id AND sale_id = NEW.id)
          OR EXISTS (SELECT 1 FROM sales_item i WHERE i.business_id = NEW.business_id
          AND i.sale_id = NEW.id AND i.quantity <> (SELECT COALESCE(sum(quantity), 0)
            FROM sales_stock_allocation a WHERE a.business_id = i.business_id
              AND a.sale_item_id = i.id))
          OR EXISTS (SELECT 1 FROM sales_payment p JOIN financial_movement m
            ON m.id = p.movement_id AND m.business_id = p.business_id
            JOIN financial_account a ON a.id = p.account_id AND a.business_id = p.business_id
            WHERE p.sale_id = NEW.id AND p.business_id = NEW.business_id
            AND (p.amount <> m.amount OR m.kind <> 'sale' OR m.resource_id <> p.id
              OR a.kind <> p.method))
          OR EXISTS (SELECT 1 FROM sales_stock_allocation a JOIN sales_item i
            ON i.id = a.sale_item_id AND i.business_id = a.business_id
            JOIN stock_movement m ON m.id = a.movement_id AND m.business_id = a.business_id
            WHERE i.sale_id = NEW.id AND i.business_id = NEW.business_id
            AND (m.kind <> 'sale' OR m.quantity <> -a.quantity OR m.unit_cost <> a.unit_cost
              OR m.batch_id <> a.batch_id)) THEN
          RAISE EXCEPTION 'Incomplete sale ledger' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE CONSTRAINT TRIGGER complete_sale AFTER INSERT ON sales_invoice
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_complete_sale();
    """)


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM financial_movement WHERE kind IN "
                "('sale', 'supplier_payment'))"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Cannot downgrade posted commerce transactions")
    op.execute("DROP TRIGGER checked_sale_stock ON stock_movement")
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
    op.drop_constraint("ck_money_kind", "financial_movement", type_="check")
    op.create_check_constraint("ck_money_kind", "financial_movement", OLD)

    op.execute("DROP FUNCTION check_complete_sale()")
    op.execute("DROP FUNCTION guard_sale_stock()")
    op.execute("DROP FUNCTION guard_supplier_payment()")
