"""Offline stock/value isolation and immutable terminal synchronization."""

from alembic import op
from sqlalchemy import text

from supermarket.schema_v0008 import TABLE_NAMES, metadata

revision = "0008_offline"
down_revision = "0007_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.execute("""
      CREATE FUNCTION offline_held(batch uuid) RETURNS TABLE(quantity numeric,value numeric)
      LANGUAGE sql STABLE AS $$
        SELECT COALESCE(sum(r.quantity-COALESCE(c.used,0)),0),
          COALESCE(sum((r.quantity-COALESCE(c.used,0))*r.unit_cost),0)
        FROM offline_reservation r LEFT JOIN
          (SELECT reservation_id,sum(quantity) AS used FROM offline_consumption GROUP BY
          reservation_id) c
          ON c.reservation_id=r.id WHERE r.batch_id=batch AND NOT EXISTS
          (SELECT 1 FROM offline_seal s WHERE s.target_id=r.lease_id AND
          s.business_id=r.business_id)
      $$;
      CREATE OR REPLACE FUNCTION guard_sale_stock() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE available numeric; stock_value numeric; held_quantity numeric; held_value numeric;
      BEGIN
        IF NEW.quantity >= 0 THEN RETURN NEW; END IF;
        PERFORM id FROM product WHERE id=NEW.product_id AND business_id=NEW.business_id FOR UPDATE;
        SELECT COALESCE(sum(quantity),0),COALESCE(sum(quantity*unit_cost),0)
          INTO available,stock_value FROM stock_movement
          WHERE business_id=NEW.business_id AND batch_id=NEW.batch_id;
        SELECT quantity,value INTO held_quantity,held_value FROM offline_held(NEW.batch_id);
        IF available-held_quantity+NEW.quantity < 0
          OR stock_value-held_value+NEW.quantity*NEW.unit_cost < -0.000001 THEN
          RAISE EXCEPTION 'Insufficient unreserved stock/value; reconcile offline till first'
            USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE FUNCTION guard_offline_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE reserved numeric; used numeric;
      BEGIN
        SELECT quantity INTO reserved FROM offline_reservation WHERE id=NEW.reservation_id
          AND business_id=NEW.business_id AND store_id=NEW.store_id AND product_id=NEW.product_id;
        PERFORM
          pg_advisory_xact_lock(hashtextextended('offline-reservation:'||NEW.reservation_id::text,0));
        SELECT COALESCE(sum(quantity),0) INTO used FROM offline_consumption
          WHERE reservation_id=NEW.reservation_id AND business_id=NEW.business_id;
        IF reserved IS NULL OR used+NEW.quantity > reserved THEN
          RAISE EXCEPTION 'Offline quota exceeded' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_offline_consumption BEFORE INSERT ON offline_consumption
        FOR EACH ROW EXECUTE FUNCTION guard_offline_consumption();
      CREATE FUNCTION guard_offline_cash_closing() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF EXISTS(SELECT 1 FROM offline_lease l WHERE l.cash_session_id=NEW.cash_session_id
          AND l.business_id=NEW.business_id AND NOT EXISTS
          (SELECT 1 FROM offline_seal s WHERE s.target_id=l.id AND s.business_id=l.business_id))
          THEN
          RAISE EXCEPTION 'Offline till must be synchronized and finalized first' USING
          ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_offline_closing BEFORE INSERT ON cash_closing
        FOR EACH ROW EXECUTE FUNCTION guard_offline_cash_closing();
    """)

    op.execute("""
      CREATE FUNCTION check_offline_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM offline_reservation r
          JOIN stock_movement m ON m.id=NEW.movement_id AND m.business_id=r.business_id
          JOIN sales_stock_allocation a ON a.movement_id=m.id AND a.business_id=m.business_id
          JOIN sales_item i ON i.id=a.sale_item_id AND i.business_id=a.business_id
          JOIN offline_sale j ON j.sale_id=i.sale_id AND j.business_id=i.business_id
          WHERE r.id=NEW.reservation_id AND r.business_id=NEW.business_id
            AND m.batch_id=r.batch_id AND m.product_id=r.product_id
            AND m.quantity=-NEW.quantity AND m.unit_cost=r.unit_cost AND m.kind='sale'
            AND j.lease_id=r.lease_id AND a.quantity=NEW.quantity AND a.unit_cost=r.unit_cost
        ) THEN
          RAISE EXCEPTION 'Offline stock source mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE CONSTRAINT TRIGGER complete_offline_consumption AFTER INSERT ON offline_consumption
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_offline_consumption();
      CREATE FUNCTION check_offline_seal() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'offline-sync:' || NEW.business_id::text || NEW.target_id::text,0));
        IF NEW.last_sequence <> (SELECT count(*) FROM offline_sale
            WHERE lease_id=NEW.target_id AND business_id=NEW.business_id) THEN
          RAISE EXCEPTION 'Offline sequence mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_offline_seal BEFORE INSERT ON offline_seal
        FOR EACH ROW EXECUTE FUNCTION check_offline_seal();
    """)


def downgrade() -> None:
    if any(
        op.get_bind()
        .execute(
            text('SELECT EXISTS(SELECT 1 FROM "' + name + '")')  # noqa: S608
        )
        .scalar()
        for name in TABLE_NAMES
    ):
        raise RuntimeError("Offline journals require forward recovery")
    op.execute("DROP TRIGGER checked_offline_closing ON cash_closing")
    op.execute("DROP FUNCTION guard_offline_cash_closing()")
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
    op.execute("DROP FUNCTION guard_offline_consumption()")
    op.execute("DROP FUNCTION check_offline_consumption()")
    op.execute("DROP FUNCTION check_offline_seal()")
    op.execute("DROP FUNCTION offline_held(uuid)")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_sale_stock() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE available numeric;
      BEGIN
        IF NEW.kind <> 'sale' THEN RETURN NEW; END IF;
        PERFORM id FROM product WHERE id=NEW.product_id AND business_id=NEW.business_id FOR UPDATE;
        SELECT COALESCE(sum(quantity),0) INTO available FROM stock_movement
          WHERE business_id=NEW.business_id AND batch_id=NEW.batch_id;
        IF NEW.quantity >=0 OR available+NEW.quantity <0 THEN
          RAISE EXCEPTION 'Insufficient stock' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
    """)
