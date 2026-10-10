"""Read-only owner reporting over immutable posted ledgers; no generated numbers."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, text

from supermarket.config import Settings
from supermarket.identity import IdentityAccess, permitted_store_ids, scope_for

TIMEZONE = ZoneInfo("Asia/Kolkata")
ZERO = Decimal("0")


class Activity(BaseModel):
    revenue: Decimal = ZERO
    billed: Decimal = ZERO
    cogs: Decimal = ZERO
    expenses: Decimal = ZERO
    stock_loss: Decimal = ZERO
    money_in: Decimal = ZERO
    money_out: Decimal = ZERO
    opening_funds: Decimal = ZERO
    cash_variance: Decimal = ZERO
    variance_absolute: Decimal = ZERO
    sale_count: int = 0
    credit_count: int = 0
    variance_count: int = 0


class Metrics(Activity):
    estimated_gross_profit: Decimal
    after_recorded_expenses: Decimal
    net_cash_flow: Decimal


class Snapshot(BaseModel):
    recorded_balance: Decimal
    supplier_outstanding: Decimal
    inventory_value: Decimal
    low_stock_count: int
    expiring_batch_count: int
    expired_batch_count: int
    stocked_product_count: int


class DailyPoint(BaseModel):
    date: date
    revenue: Decimal
    money_in: Decimal
    money_out: Decimal
    net_cash_flow: Decimal


class BriefingItem(BaseModel):
    priority: str
    title: str
    detail: str
    module: str | None = None


class InsightView(BaseModel):
    store_id: str
    start: date
    end: date
    timezone: str = "Asia/Kolkata"
    basis: str = "Server posting date; late offline sales appear after synchronization."
    generated_at: datetime
    metrics: Metrics
    snapshot: Snapshot
    daily: list[DailyPoint]
    briefing: list[BriefingItem]
    briefing_method: str = "rules_based"
    limitations: list[str]


def reporting_window(start: date, end: date, today: date) -> tuple[datetime, datetime]:
    if start > end or (end - start).days > 365 or end > today:
        raise HTTPException(422, "Choose up to 366 days, ending no later than today in India.")
    return (
        datetime.combine(start, time.min, TIMEZONE).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, TIMEZONE).astimezone(UTC),
    )


def rounded(value: Decimal) -> Decimal:
    result = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return abs(result) if result == 0 else result


def summarize(activity: list[Activity]) -> Metrics:
    totals = Activity()
    for row in activity:
        for name in Activity.model_fields:
            setattr(totals, name, getattr(totals, name) + getattr(row, name))
    gross = totals.revenue - totals.cogs
    values = {
        key: rounded(value) if isinstance(value, Decimal) else value
        for key, value in totals.model_dump().items()
    }
    values.update(
        estimated_gross_profit=rounded(gross),
        after_recorded_expenses=rounded(gross - totals.expenses - totals.stock_loss),
        net_cash_flow=rounded(totals.money_in - totals.money_out),
    )
    return Metrics.model_validate(values)


def briefing(metrics: Metrics, snapshot: Snapshot) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    if metrics.variance_count:
        items.append(
            BriefingItem(
                priority="high",
                title="Review cash count differences",
                detail=f"{metrics.variance_count} recorded differences; absolute differences total "
                f"INR {metrics.variance_absolute}. Net difference: INR {metrics.cash_variance}.",
                module="cash",
            )
        )
    if snapshot.expired_batch_count:
        items.append(
            BriefingItem(
                priority="critical",
                title="Review expired stock",
                detail=f"{snapshot.expired_batch_count} batches still have recorded stock "
                "after expiry.",
                module="inventory",
            )
        )
    if snapshot.expiring_batch_count:
        items.append(
            BriefingItem(
                priority="high",
                title="Check approaching expiry",
                detail=f"{snapshot.expiring_batch_count} stocked batches expire within 30 days "
                "of the selected end date.",
                module="inventory",
            )
        )
    if snapshot.low_stock_count:
        items.append(
            BriefingItem(
                priority="high",
                title="Review reorder levels",
                detail=f"{snapshot.low_stock_count} active products with stock history are at or "
                "below their configured reorder level.",
                module="inventory",
            )
        )
    if snapshot.supplier_outstanding > 0:
        items.append(
            BriefingItem(
                priority="medium",
                title="Plan supplier payments",
                detail=f"INR {snapshot.supplier_outstanding} is outstanding on recorded purchases "
                "through the selected end date. This is not an overdue-payment calculation.",
                module="payments",
            )
        )
    if metrics.estimated_gross_profit < 0:
        items.append(
            BriefingItem(
                priority="high",
                title="Review negative estimated gross profit",
                detail="Net sales excluding GST are below recorded net cost of goods sold. "
                "Check returns, selling prices and source costs before deciding on action.",
                module="products",
            )
        )
    if not metrics.sale_count and not metrics.credit_count:
        items.append(
            BriefingItem(
                priority="informational",
                title="No posted sales or credits in this period",
                detail="Check the store/date selection and synchronize offline tills. "
                "Unposted activity is not included.",
            )
        )
    items.append(
        BriefingItem(
            priority="informational",
            title="Your recorded business",
            detail=f"Net sales excluding GST: INR {metrics.revenue}. Money in: INR "
            f"{metrics.money_in}; money out: INR {metrics.money_out}. Estimated gross profit: "
            f"INR {metrics.estimated_gross_profit}, before paid expenses and stock losses.",
        )
    )
    order = {"critical": 0, "high": 1, "medium": 2, "informational": 3}
    return sorted(items, key=lambda item: order[item.priority])


DAILY_SQL = text("""
WITH activity AS (
 SELECT created_at, 'invoice' AS kind, taxable_total AS revenue, total AS billed,
        0::numeric AS cogs, 0::numeric AS expenses, 0::numeric AS stock_loss,
        0::numeric AS amount
 FROM sales_invoice WHERE business_id=:business AND store_id=:store
   AND created_at>=:start_at AND created_at<:end_at
 UNION ALL
 SELECT created_at, 'credit', -taxable_total, -total, 0, 0, 0, 0
 FROM credit_note WHERE business_id=:business AND store_id=:store
   AND created_at>=:start_at AND created_at<:end_at
 UNION ALL
 SELECT created_at, 'stock', 0, 0,
        CASE WHEN kind IN ('sale','sales_return') THEN -quantity*unit_cost ELSE 0 END,
        0, CASE WHEN kind IN ('damage','wastage') THEN -quantity*unit_cost ELSE 0 END, 0
 FROM stock_movement WHERE business_id=:business AND store_id=:store
   AND created_at>=:start_at AND created_at<:end_at
 UNION ALL
 SELECT created_at, kind, 0, 0, 0,
        CASE WHEN kind IN ('expense','expense_reversal') THEN -amount ELSE 0 END, 0, amount
 FROM financial_movement WHERE business_id=:business AND store_id=:store
   AND created_at>=:start_at AND created_at<:end_at
)
SELECT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
 sum(revenue) AS revenue, sum(billed) AS billed, sum(cogs) AS cogs,
 sum(expenses) AS expenses, sum(stock_loss) AS stock_loss,
 sum(CASE WHEN kind NOT IN ('opening','cash_variance','opening_variance')
          AND amount>0 THEN amount ELSE 0 END) AS money_in,
 sum(CASE WHEN kind NOT IN ('opening','cash_variance','opening_variance')
          AND amount<0 THEN -amount ELSE 0 END) AS money_out,
 sum(CASE WHEN kind='opening' THEN amount ELSE 0 END) AS opening_funds,
 sum(CASE WHEN kind IN ('cash_variance','opening_variance') THEN amount ELSE 0 END)
     AS cash_variance,
 sum(CASE WHEN kind IN ('cash_variance','opening_variance') THEN abs(amount) ELSE 0 END)
     AS variance_absolute,
 count(*) FILTER (WHERE kind='invoice') AS sale_count,
 count(*) FILTER (WHERE kind='credit') AS credit_count,
 count(*) FILTER (WHERE kind IN ('cash_variance','opening_variance')) AS variance_count
FROM activity GROUP BY day ORDER BY day
""")

SNAPSHOT_SQL = text("""
WITH stock AS (
 SELECT product_id, batch_id, sum(quantity) AS quantity, sum(quantity*unit_cost) AS value
 FROM stock_movement WHERE business_id=:business AND store_id=:store AND created_at<:end_at
 GROUP BY product_id, batch_id
), product_stock AS (
 SELECT product_id, sum(quantity) AS quantity FROM stock GROUP BY product_id
), payable AS (
 SELECT invoice_total AS amount FROM purchase
 WHERE business_id=:business AND store_id=:store AND created_at<:end_at
 UNION ALL SELECT -total FROM purchase_reversal
 WHERE business_id=:business AND store_id=:store AND created_at<:end_at
 UNION ALL SELECT -amount FROM supplier_payment
 WHERE business_id=:business AND store_id=:store AND created_at<:end_at
 UNION ALL SELECT total FROM supplier_payment_reversal
 WHERE business_id=:business AND store_id=:store AND created_at<:end_at
)
SELECT
 (SELECT coalesce(sum(amount),0) FROM financial_movement
  WHERE business_id=:business AND store_id=:store AND created_at<:end_at) AS recorded_balance,
 (SELECT coalesce(sum(amount),0) FROM payable) AS supplier_outstanding,
 (SELECT coalesce(sum(value),0) FROM stock) AS inventory_value,
 (SELECT count(*) FROM product_stock) AS stocked_product_count,
 (SELECT count(*) FROM product_stock s JOIN product p ON p.id=s.product_id
  WHERE p.business_id=:business AND p.active AND p.reorder_level>0
    AND s.quantity<=p.reorder_level) AS low_stock_count,
 (SELECT count(*) FROM stock s JOIN stock_batch b ON b.id=s.batch_id
  WHERE b.business_id=:business AND b.store_id=:store AND s.quantity>0
    AND b.expiry_date>=:end_day AND b.expiry_date<=:expiry_cutoff) AS expiring_batch_count,
 (SELECT count(*) FROM stock s JOIN stock_batch b ON b.id=s.batch_id
  WHERE b.business_id=:business AND b.store_id=:store AND s.quantity>0
    AND b.expiry_date<:end_day) AS expired_batch_count
""")


def insights_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}", tags=["owner intelligence"]
    )
    access = IdentityAccess(engine, settings)

    @router.get("/insights", response_model=InsightView)
    def insights(
        request: Request,
        business_id: UUID,
        store_id: UUID,
        start: date | None = None,
        end: date | None = None,
    ) -> InsightView:
        actor = access.actor_for(request)
        generated = datetime.now(UTC)
        last = end or generated.astimezone(TIMEZONE).date()
        first = start or last
        start_at, end_at = reporting_window(first, last, generated.astimezone(TIMEZONE).date())
        with (
            access.database()
            .connect()
            .execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            connection.execute(text("SET TRANSACTION READ ONLY"))
            scope = scope_for(connection, actor, str(business_id), "finance.read")
            if str(store_id) not in permitted_store_ids(connection, str(business_id), scope):
                raise HTTPException(404, "Store unavailable")
            params = {
                "business": str(business_id),
                "store": str(store_id),
                "start_at": start_at,
                "end_at": end_at,
                "end_day": last,
                "expiry_cutoff": last + timedelta(days=30),
            }
            rows = connection.execute(DAILY_SQL, params).mappings().all()
            snapshot = Snapshot.model_validate(
                dict(connection.execute(SNAPSHOT_SQL, params).mappings().one())
            )
        snapshot = snapshot.model_copy(
            update={
                name: rounded(getattr(snapshot, name))
                for name in ("recorded_balance", "supplier_outstanding", "inventory_value")
            }
        )
        by_day = {row["day"]: Activity.model_validate(dict(row)) for row in rows}
        metrics = summarize(list(by_day.values()))
        daily: list[DailyPoint] = []
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            activity = by_day.get(day, Activity())
            daily.append(
                DailyPoint(
                    date=day,
                    revenue=rounded(activity.revenue),
                    money_in=rounded(activity.money_in),
                    money_out=rounded(activity.money_out),
                    net_cash_flow=rounded(activity.money_in - activity.money_out),
                )
            )
        return InsightView(
            store_id=str(store_id),
            start=first,
            end=last,
            generated_at=generated,
            metrics=metrics,
            snapshot=snapshot,
            daily=daily,
            briefing=briefing(metrics, snapshot),
            limitations=[
                "Only posted records are included. Unsynchronized offline sales are absent.",
                "Estimated gross profit uses recorded stock costs, not verified final profit.",
                "After-expense estimates exclude accruals, depreciation, financing and stock "
                "count adjustments. Purchase taxes/landed-cost policy need accountant review.",
                "Bank, UPI and card entries are manually recorded, not bank-verified.",
                "Stock balances are as of the end date, using current product reorder settings.",
                "Briefings use transparent rules; no generative AI or predictions are enabled.",
            ],
        )

    return router
