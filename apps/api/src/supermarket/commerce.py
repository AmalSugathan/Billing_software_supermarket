"""Atomic, permission-scoped online checkout and invoice-linked money out."""

import hashlib
import json
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, Engine, Table, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog import Money, Quantity
from supermarket.catalog_models import batches, products
from supermarket.catalog_models import movements as stock_moves
from supermarket.commerce_models import allocations, items, payments, sales, supplier_payments
from supermarket.config import Settings
from supermarket.finance import Approved, active_session, append_money, locked_account
from supermarket.identity import (
    Actor,
    IdentityAccess,
    InputModel,
    audit,
    permitted_store_ids,
    scope_for,
)
from supermarket.identity_models import terminals
from supermarket.purchase_models import purchases

PAISE = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")


def rounded(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


class SaleLine(InputModel):
    product_id: UUID
    quantity: Quantity
    expected_price: Money
    discount: Money = Decimal("0")

    @model_validator(mode="after")
    def positive(self) -> SaleLine:
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")
        return self


class Tender(InputModel):
    account_id: UUID
    cash_session_id: UUID | None = None
    method: Literal["cash", "upi", "card"]
    amount: Money

    @model_validator(mode="after")
    def positive(self) -> Tender:
        if self.amount <= 0:
            raise ValueError("Payment must be positive")
        return self


class SaleInput(InputModel):
    terminal_id: UUID
    lines: list[SaleLine] = Field(min_length=1, max_length=200)
    bill_discount: Money = Decimal("0")
    payments: list[Tender] = Field(default_factory=lambda: list[Tender](), max_length=10)
    confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def distinct(self) -> SaleInput:
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Combine quantities of the same product into one line")
        if len({payment.account_id for payment in self.payments}) != len(self.payments):
            raise ValueError("Use each payment account once")
        return self


class LineView(BaseModel):
    line_number: int
    product_id: str
    product_name: str
    sku: str
    unit: str
    hsn: str | None
    quantity: Decimal
    unit_price: Decimal
    gross: Decimal
    discount: Decimal
    gst_rate: Decimal
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    total: Decimal


class Quote(BaseModel):
    gross_total: Decimal
    discount: Decimal
    taxable_total: Decimal
    cgst: Decimal
    sgst: Decimal
    total: Decimal
    lines: list[LineView]


class PaymentView(BaseModel):
    id: str
    account_id: str
    method: str
    amount: Decimal
    movement_id: str


class Receipt(Quote):
    id: str
    store_id: str
    terminal_id: str
    invoice_number: str
    actor_user_id: str
    created_at: datetime
    payments: list[PaymentView]


class PayInput(Approved):
    purchase_id: UUID
    account_id: UUID
    cash_session_id: UUID | None = None
    amount: Money
    reference: str = Field(min_length=1, max_length=100, pattern=r"^[ -~]+$")
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def positive(self) -> PayInput:
        if self.amount <= 0:
            raise ValueError("Payment must be positive")
        return self


class SupplierPaymentView(BaseModel):
    id: str
    purchase_id: str
    supplier_id: str
    account_id: str
    movement_id: str
    amount: Decimal
    reference: str
    reason: str
    actor_user_id: str
    created_at: datetime


class Payable(BaseModel):
    purchase_id: str
    supplier_id: str
    supplier_name: str
    invoice_number: str
    total: Decimal
    paid: Decimal
    outstanding: Decimal


def calculate(payload: SaleInput, catalog: dict[str, dict[str, object]]) -> Quote:
    net: list[Decimal] = []
    gross: list[Decimal] = []
    for line in payload.lines:
        product = catalog.get(str(line.product_id))
        if product is None or not product["active"]:
            raise HTTPException(404, "Active product unavailable")
        if line.expected_price != product["selling_price"]:
            raise HTTPException(
                409, "Selling price changed; reload the product and review checkout"
            )
        if (
            product["unit"] in {"pcs", "pack"}
            and line.quantity != line.quantity.to_integral_value()
        ):
            raise HTTPException(422, "Pieces and packs require whole quantities")
        amount = rounded(line.quantity * line.expected_price)
        if amount > MAX_MONEY or line.discount > amount:
            raise HTTPException(422, "Line amount or discount is out of range")
        gross.append(amount)
        net.append(amount - line.discount)
    subtotal = sum(net, Decimal(0))
    if payload.bill_discount >= subtotal or subtotal > MAX_MONEY or sum(gross) > MAX_MONEY:
        raise HTTPException(422, "Bill total must be positive and within the monetary limit")
    shares = [payload.bill_discount * amount / subtotal for amount in net]
    apportioned = [share.quantize(PAISE, rounding=ROUND_FLOOR) for share in shares]
    remaining = int((payload.bill_discount - sum(apportioned)) / PAISE)
    order = sorted(range(len(net)), key=lambda i: (-(shares[i] - apportioned[i]), i))
    for i in order[:remaining]:
        apportioned[i] += PAISE
    lines: list[LineView] = []
    for i, line in enumerate(payload.lines):
        product = catalog[str(line.product_id)]
        total = net[i] - apportioned[i]
        if total < rounded(Decimal(str(product["minimum_selling_price"])) * line.quantity):
            raise HTTPException(422, "Discount would cross the product minimum selling price")
        gst = Decimal(str(product["gst_rate"]))
        taxable = rounded(total / (1 + gst / 100))
        tax = total - taxable
        cgst = rounded(tax / 2)
        lines.append(
            LineView(
                line_number=i + 1,
                product_id=str(line.product_id),
                product_name=str(product["name"]),
                sku=str(product["sku"]),
                unit=str(product["unit"]),
                hsn=str(product["hsn"]) if product["hsn"] else None,
                quantity=line.quantity,
                unit_price=line.expected_price,
                gross=gross[i],
                discount=gross[i] - total,
                gst_rate=gst,
                taxable=taxable,
                cgst=cgst,
                sgst=tax - cgst,
                total=total,
            )
        )
    return Quote(
        gross_total=sum(gross, Decimal(0)),
        discount=sum(gross, Decimal(0)) - sum(net) + payload.bill_discount,
        taxable_total=sum((line.taxable for line in lines), Decimal(0)),
        cgst=sum((line.cgst for line in lines), Decimal(0)),
        sgst=sum((line.sgst for line in lines), Decimal(0)),
        total=subtotal - payload.bill_discount,
        lines=lines,
    )


def lock(connection: Connection, key: str) -> None:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


def replay(connection: Connection, table: Table, key: UUID, request_hash: str) -> str | None:
    found = (
        connection.execute(select(table).where(table.c.idempotency_key == str(key)))
        .mappings()
        .first()
    )
    if found is not None:
        if found["request_hash"] != request_hash:
            raise HTTPException(409, "Idempotency key already used with different details")
        return str(found["id"])
    return None


def fingerprint(store: UUID, payload: BaseModel) -> str:
    return hashlib.sha256(
        json.dumps(
            {"store_id": str(store), **payload.model_dump(mode="json")}, sort_keys=True
        ).encode()
    ).hexdigest()


def command_values(
    actor: Actor, business: UUID, store: UUID, key: UUID, request_hash: str
) -> dict[str, object]:
    return {
        "business_id": str(business),
        "store_id": str(store),
        "actor_user_id": actor.user.id,
        "source": "human",
        "human_approved": True,
        "idempotency_key": str(key),
        "request_hash": request_hash,
    }


def receipt(connection: Connection, sale_id: str) -> Receipt:
    found = connection.execute(select(sales).where(sales.c.id == sale_id)).mappings().first()
    if found is None:
        raise HTTPException(404, "Sale unavailable")
    return Receipt(
        **dict(found),
        lines=[
            LineView(**dict(row))
            for row in connection.execute(
                select(items).where(items.c.sale_id == sale_id).order_by(items.c.line_number)
            ).mappings()
        ],
        payments=[
            PaymentView(**dict(row))
            for row in connection.execute(
                select(payments).where(payments.c.sale_id == sale_id).order_by(payments.c.id)
            ).mappings()
        ],
    )


def allocate_stock(
    connection: Connection,
    actor: Actor,
    business: UUID,
    store: UUID,
    item_id: str,
    line: LineView,
    request_hash: str,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    lots = (
        connection.execute(
            select(
                batches.c.id,
                batches.c.expiry_date,
                func.sum(stock_moves.c.quantity).label("quantity"),
                func.sum(stock_moves.c.quantity * stock_moves.c.unit_cost).label("value"),
            )
            .join(stock_moves, stock_moves.c.batch_id == batches.c.id)
            .where(batches.c.store_id == str(store), batches.c.product_id == line.product_id)
            .group_by(batches.c.id, batches.c.expiry_date, batches.c.created_at)
            .having(func.sum(stock_moves.c.quantity) > 0)
            .order_by(batches.c.expiry_date.asc().nulls_last(), batches.c.created_at, batches.c.id)
        )
        .mappings()
        .all()
    )
    remaining = line.quantity
    for lot in lots:
        if lot["expiry_date"] is not None and lot["expiry_date"] < today:
            continue
        take = min(remaining, Decimal(lot["quantity"]))
        if take <= 0:
            break
        cost = (Decimal(lot["value"]) / Decimal(lot["quantity"])).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        if cost < 0:
            raise HTTPException(409, "Inventory cost requires review")
        movement_id = str(uuid4())
        connection.execute(
            insert(stock_moves).values(
                id=movement_id,
                business_id=str(business),
                store_id=str(store),
                product_id=line.product_id,
                batch_id=lot["id"],
                kind="sale",
                quantity=-take,
                unit_cost=cost,
                reason="POS sale " + item_id,
                actor_user_id=actor.user.id,
                source="human",
                human_approved=True,
                idempotency_key=str(uuid5(UUID(item_id), str(lot["id"]))),
                request_hash=request_hash,
            )
        )
        connection.execute(
            insert(allocations).values(
                id=str(uuid4()),
                business_id=str(business),
                store_id=str(store),
                product_id=line.product_id,
                sale_item_id=item_id,
                batch_id=lot["id"],
                movement_id=movement_id,
                quantity=take,
                unit_cost=cost,
            )
        )
        remaining -= take
    if remaining > 0:
        raise HTTPException(409, "Insufficient sellable stock for " + line.product_name)


def commerce_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}",
        tags=["POS and supplier payments"],
    )
    access = IdentityAccess(engine, settings)

    def authorize(
        connection: Connection, actor: Actor, business: UUID, store: UUID, capability: str
    ) -> None:
        scope = scope_for(connection, actor, str(business), capability)
        if str(store) not in permitted_store_ids(connection, str(business), scope):
            raise HTTPException(403, "Store access required")

    def catalog_for(
        connection: Connection, payload: SaleInput, store: UUID, locked: bool = False
    ) -> dict[str, dict[str, object]]:
        if (
            connection.execute(
                select(terminals.c.id).where(
                    terminals.c.id == str(payload.terminal_id), terminals.c.store_id == str(store)
                )
            ).first()
            is None
        ):
            raise HTTPException(404, "Terminal unavailable")
        query = (
            select(products)
            .where(products.c.id.in_(sorted(str(line.product_id) for line in payload.lines)))
            .order_by(products.c.id)
        )
        if locked:
            query = query.with_for_update()
        return {str(row["id"]): dict(row) for row in connection.execute(query).mappings()}

    @router.post("/sales/preview", response_model=Quote)
    def preview(business_id: UUID, store_id: UUID, payload: SaleInput, request: Request) -> Quote:
        actor = access.actor_for(request, mutation=True)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "sales.create")
            return calculate(payload, catalog_for(connection, payload, store_id))

    @router.get("/sales", response_model=list[Receipt])
    def listing(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[Receipt]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "sales.create")
            ids = connection.execute(
                select(sales.c.id)
                .where(sales.c.store_id == str(store_id))
                .order_by(sales.c.created_at.desc(), sales.c.id)
                .limit(limit)
                .offset(offset)
            ).scalars()
            return [receipt(connection, sale_id) for sale_id in ids]

    @router.get("/sales/{sale_id}", response_model=Receipt)
    def detail(business_id: UUID, store_id: UUID, sale_id: UUID, request: Request) -> Receipt:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "sales.create")
            result = receipt(connection, str(sale_id))
            if result.store_id != str(store_id):
                raise HTTPException(404, "Sale unavailable")
            return result

    @router.post("/sales", response_model=Receipt, status_code=201)
    def post_sale(
        business_id: UUID,
        store_id: UUID,
        payload: SaleInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> Receipt:
        actor = access.actor_for(request, mutation=True)
        if not payload.confirmed:
            raise HTTPException(422, "Confirm received payments before checkout")
        request_hash = fingerprint(store_id, payload)
        try:
            with access.database().begin() as connection:
                authorize(connection, actor, business_id, store_id, "sales.create")
                lock(connection, "sale:" + str(business_id) + str(idempotency_key))
                existing = replay(connection, sales, idempotency_key, request_hash)
                if existing:
                    return receipt(connection, existing)
                quote = calculate(payload, catalog_for(connection, payload, store_id, locked=True))
                if sum((payment.amount for payment in payload.payments), Decimal(0)) != quote.total:
                    raise HTTPException(422, "Payments must equal the exact bill total")
                accounts = {
                    str(payment.account_id): locked_account(
                        connection, str(payment.account_id), str(store_id)
                    )
                    for payment in sorted(payload.payments, key=lambda p: str(p.account_id))
                }
                for payment in payload.payments:
                    account = accounts[str(payment.account_id)]
                    if account["kind"] != payment.method:
                        raise HTTPException(422, "Payment method does not match its account")
                    if payment.method == "cash":
                        if (
                            account["terminal_id"] != str(payload.terminal_id)
                            or payment.cash_session_id is None
                        ):
                            raise HTTPException(422, "Cash must use this terminal's open drawer")
                        session = active_session(
                            connection, str(payment.cash_session_id), str(payment.account_id)
                        )
                        if session["actor_user_id"] != actor.user.id:
                            raise HTTPException(403, "Use your own cashier session")
                sale_id = str(uuid4())
                connection.execute(
                    insert(sales).values(
                        id=sale_id,
                        terminal_id=str(payload.terminal_id),
                        invoice_number="POS-" + UUID(sale_id).hex.upper(),
                        **command_values(
                            actor, business_id, store_id, idempotency_key, request_hash
                        ),
                        **quote.model_dump(exclude={"lines"}),
                    )
                )
                for line in quote.lines:
                    item_id = str(uuid4())
                    connection.execute(
                        insert(items).values(
                            id=item_id,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            sale_id=sale_id,
                            **line.model_dump(),
                        )
                    )
                    allocate_stock(
                        connection, actor, business_id, store_id, item_id, line, request_hash
                    )
                for payment in payload.payments:
                    payment_id = str(uuid4())
                    movement_id = append_money(
                        connection,
                        actor,
                        str(business_id),
                        str(store_id),
                        payment_id,
                        accounts[str(payment.account_id)],
                        payment.amount,
                        "sale",
                        "POS receipt " + sale_id,
                        payment.cash_session_id,
                    )
                    connection.execute(
                        insert(payments).values(
                            id=payment_id,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            sale_id=sale_id,
                            account_id=str(payment.account_id),
                            method=payment.method,
                            amount=payment.amount,
                            movement_id=movement_id,
                        )
                    )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "sale.posted",
                    sale_id,
                    {**payload.model_dump(mode="json"), "quote": quote.model_dump(mode="json")},
                )
                return receipt(connection, sale_id)
        except IntegrityError as error:
            raise HTTPException(409, "Checkout conflict; retry the original request") from error

    @router.get("/supplier-payables", response_model=list[Payable])
    def payables(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[Payable]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "business.read")
            scope = scope_for(connection, actor, str(business_id), "business.read")
            if not scope.capabilities.intersection(
                {"purchases.manage", "finance.read", "actions.approve"}
            ):
                raise HTTPException(403, "Purchase or finance visibility required")
            paid = (
                select(
                    supplier_payments.c.purchase_id,
                    func.sum(supplier_payments.c.amount).label("paid"),
                )
                .group_by(supplier_payments.c.purchase_id)
                .subquery()
            )
            rows = connection.execute(
                select(purchases, func.coalesce(paid.c.paid, 0).label("paid"))
                .outerjoin(paid, paid.c.purchase_id == purchases.c.id)
                .where(purchases.c.store_id == str(store_id))
                .order_by(purchases.c.created_at.desc(), purchases.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
            return [
                Payable(
                    purchase_id=row["id"],
                    supplier_id=row["supplier_id"],
                    supplier_name=row["supplier_name"],
                    invoice_number=row["invoice_number"],
                    total=row["invoice_total"],
                    paid=row["paid"],
                    outstanding=row["invoice_total"] - row["paid"],
                )
                for row in rows
            ]

    @router.get("/supplier-payments", response_model=list[SupplierPaymentView])
    def payment_history(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[SupplierPaymentView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "finance.read")
            return [
                SupplierPaymentView(**dict(row))
                for row in connection.execute(
                    select(supplier_payments)
                    .where(supplier_payments.c.store_id == str(store_id))
                    .order_by(supplier_payments.c.created_at.desc(), supplier_payments.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
            ]

    @router.post("/supplier-payments", response_model=SupplierPaymentView, status_code=201)
    def pay_supplier(
        business_id: UUID,
        store_id: UUID,
        payload: PayInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> SupplierPaymentView:
        actor = access.actor_for(request, mutation=True)
        request_hash = fingerprint(store_id, payload)
        try:
            with access.database().begin() as connection:
                authorize(connection, actor, business_id, store_id, "actions.approve")
                lock(connection, "supplier-payment:" + str(business_id) + str(idempotency_key))
                existing = replay(connection, supplier_payments, idempotency_key, request_hash)
                if existing:
                    return SupplierPaymentView(
                        **dict(
                            connection.execute(
                                select(supplier_payments).where(supplier_payments.c.id == existing)
                            )
                            .mappings()
                            .one()
                        )
                    )
                lock(connection, "purchase-payment:" + str(business_id) + str(payload.purchase_id))
                purchase = (
                    connection.execute(
                        select(purchases).where(
                            purchases.c.id == str(payload.purchase_id),
                            purchases.c.store_id == str(store_id),
                        )
                    )
                    .mappings()
                    .first()
                )
                if purchase is None:
                    raise HTTPException(404, "Purchase unavailable")
                paid = Decimal(
                    connection.execute(
                        select(func.coalesce(func.sum(supplier_payments.c.amount), 0)).where(
                            supplier_payments.c.purchase_id == str(payload.purchase_id)
                        )
                    ).scalar_one()
                )
                if payload.amount > purchase["invoice_total"] - paid:
                    raise HTTPException(409, "Payment exceeds the invoice outstanding balance")
                account = locked_account(connection, str(payload.account_id), str(store_id))
                payment_id = str(uuid4())
                movement_id = append_money(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    payment_id,
                    account,
                    -payload.amount,
                    "supplier_payment",
                    payload.reason,
                    payload.cash_session_id,
                )
                values = {
                    "id": payment_id,
                    "purchase_id": str(payload.purchase_id),
                    "supplier_id": purchase["supplier_id"],
                    "account_id": str(payload.account_id),
                    "movement_id": movement_id,
                    "amount": payload.amount,
                    "reference": payload.reference,
                    "reference_identity": "".join(payload.reference.upper().split()),
                    "reason": payload.reason,
                    **command_values(actor, business_id, store_id, idempotency_key, request_hash),
                }
                result = (
                    connection.execute(
                        insert(supplier_payments).values(**values).returning(supplier_payments)
                    )
                    .mappings()
                    .one()
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "supplier.payment_recorded",
                    payment_id,
                    payload.model_dump(mode="json"),
                )
                return SupplierPaymentView(**dict(result))
        except IntegrityError as error:
            raise HTTPException(
                409, "Payment reference already recorded or posting conflict"
            ) from error

    return router
