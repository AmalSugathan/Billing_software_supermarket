"""Owner-approved compensating documents with exact source quantities and money."""

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, Engine, Table, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog import Quantity
from supermarket.catalog_models import batches, products
from supermarket.catalog_models import movements as stock_moves
from supermarket.commerce import PAISE, Tender, command_values, lock, replay, rounded
from supermarket.commerce_models import allocations, items, sales, supplier_payments
from supermarket.config import Settings
from supermarket.correction_models import (
    adjustments,
    credit_items,
    credit_stock,
    credits,
    payment_reversals,
    purchase_reversal_items,
    purchase_reversals,
    refunds,
)
from supermarket.finance import Approved, append_money, locked_account, row
from supermarket.identity import Actor, IdentityAccess, audit, permitted_store_ids, scope_for
from supermarket.purchase_models import items as purchase_items
from supermarket.purchase_models import purchases
from supermarket.purchases import Rate


class Reason(Approved):
    reason: str = Field(min_length=3, max_length=500)


class ReturnLine(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: UUID
    quantity: Quantity
    disposition: Literal["restock", "discard"]

    @model_validator(mode="after")
    def positive(self) -> ReturnLine:
        if self.quantity <= 0:
            raise ValueError("Returned quantity must be positive")
        return self


class CreditInput(Reason):
    kind: Literal["return", "cancel"]
    lines: list[ReturnLine] = Field(min_length=1, max_length=200)
    payments: list[Tender] = Field(default_factory=lambda: list[Tender](), max_length=10)

    @model_validator(mode="after")
    def distinct(self) -> CreditInput:
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Return each product once per credit note")
        if len({payment.account_id for payment in self.payments}) != len(self.payments):
            raise ValueError("Use each refund account once")
        if self.kind == "cancel" and any(line.disposition != "restock" for line in self.lines):
            raise ValueError("Cancellation requires all goods back in their original stock")
        return self


class ReversePayment(Reason):
    cash_session_id: UUID | None = None


class CountInput(Reason):
    expected_count: Quantity
    actual_count: Quantity
    added_unit_cost: Rate | None = None


class BatchView(BaseModel):
    id: str
    product_id: str
    product_name: str
    sku: str
    quantity: Decimal
    batch_number: str | None
    expiry_date: date | None


class CreditLineView(BaseModel):
    product_id: str
    quantity: Decimal
    disposition: str
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    total: Decimal


class CorrectionView(BaseModel):
    id: str
    target_id: str
    kind: str
    reason: str
    total: Decimal
    actor_user_id: str
    created_at: datetime
    expected_count: Decimal | None = None
    actual_count: Decimal | None = None
    lines: list[CreditLineView] = Field(default_factory=lambda: list[CreditLineView]())


def presentation(connection: Connection, table: Table, resource: str) -> CorrectionView:
    record = row(connection, table, resource)
    return CorrectionView.model_validate(
        {
            **record,
            "kind": record.get("kind", table.name),
            "lines": [
                dict(line)
                for line in connection.execute(
                    select(credit_items).where(credit_items.c.credit_id == resource)
                ).mappings()
            ]
            if table is credits
            else [],
        }
    )


def paid_amount(connection: Connection, purchase_id: str) -> Decimal:
    paid = connection.execute(
        select(func.coalesce(func.sum(supplier_payments.c.amount), 0)).where(
            supplier_payments.c.purchase_id == purchase_id
        )
    ).scalar_one()
    reversed_amount = connection.execute(
        select(func.coalesce(func.sum(payment_reversals.c.total), 0))
        .join(supplier_payments, supplier_payments.c.id == payment_reversals.c.target_id)
        .where(supplier_payments.c.purchase_id == purchase_id)
    ).scalar_one()
    return Decimal(paid) - Decimal(reversed_amount)


def stock_movement(
    connection: Connection,
    actor: Actor,
    business: str,
    store: str,
    resource: str,
    product: str,
    batch: str,
    quantity: Decimal,
    cost: Decimal,
    kind: str,
    reason: str,
    request_hash: str,
    label: str,
) -> str:
    movement = str(uuid4())
    connection.execute(
        insert(stock_moves).values(
            id=movement,
            business_id=business,
            store_id=store,
            product_id=product,
            batch_id=batch,
            quantity=quantity,
            unit_cost=cost,
            kind=kind,
            reason=reason,
            actor_user_id=actor.user.id,
            source="human",
            human_approved=True,
            idempotency_key=str(uuid5(UUID(resource), label)),
            request_hash=request_hash,
        )
    )
    return movement


def batch_balance(connection: Connection, batch: str) -> tuple[Decimal, Decimal]:
    result = connection.execute(
        select(
            func.coalesce(func.sum(stock_moves.c.quantity), 0),
            func.coalesce(func.sum(stock_moves.c.quantity * stock_moves.c.unit_cost), 0),
        ).where(stock_moves.c.batch_id == batch)
    ).one()
    return Decimal(result[0]), Decimal(result[1])


def split_remaining(amount: Decimal, components: list[Decimal]) -> list[Decimal]:
    """Allocate this credit against remaining source components, never a negative tax refund."""
    remaining = sum(components, Decimal(0))
    if amount == 0:
        return [Decimal(0)] * len(components)
    if remaining < amount:
        raise HTTPException(409, "Credit exceeds remaining original amount")
    exact = [amount * component / remaining for component in components]
    result = [value.quantize(PAISE, rounding=ROUND_FLOOR) for value in exact]
    pennies = int((amount - sum(result)) / PAISE)
    order = sorted(range(len(result)), key=lambda i: (-(exact[i] - result[i]), i))
    for i in order[:pennies]:
        result[i] += PAISE
    return result


def credit_lines(
    connection: Connection, sale_id: UUID, payload: CreditInput
) -> list[dict[str, object]]:
    original_lines = {
        str(line["product_id"]): dict(line)
        for line in connection.execute(
            select(items).where(items.c.sale_id == str(sale_id))
        ).mappings()
    }
    selected = {str(line.product_id) for line in payload.lines}
    if not selected.issubset(original_lines):
        raise HTTPException(422, "Product was not sold on this invoice")
    computed: list[dict[str, object]] = []
    for line in payload.lines:
        original = original_lines[str(line.product_id)]
        previous = (
            connection.execute(
                select(
                    *(
                        func.coalesce(func.sum(credit_items.c[c]), 0).label(c)
                        for c in ["quantity", "total", "taxable", "cgst", "sgst"]
                    )
                ).where(credit_items.c.sale_item_id == original["id"])
            )
            .mappings()
            .one()
        )
        returned = Decimal(previous["quantity"])
        if line.quantity + returned > original["quantity"]:
            raise HTTPException(409, "Return exceeds remaining sold quantity")
        if (
            original["unit"] in {"pcs", "pack"}
            and line.quantity != line.quantity.to_integral_value()
        ):
            raise HTTPException(422, "Pieces and packs require whole return quantities")
        if payload.kind == "cancel" and (
            selected != set(original_lines)
            or returned != 0
            or line.quantity != original["quantity"]
        ):
            raise HTTPException(
                409, "Cancellation requires the whole invoice with no earlier returns"
            )
        amount = rounded(
            Decimal(original["total"]) * (returned + line.quantity) / Decimal(original["quantity"])
        ) - Decimal(previous["total"])
        tax = split_remaining(
            amount,
            [Decimal(original[c]) - Decimal(previous[c]) for c in ["taxable", "cgst", "sgst"]],
        )
        computed.append(
            {
                "id": str(uuid4()),
                "sale_item_id": original["id"],
                "product_id": str(line.product_id),
                "quantity": line.quantity,
                "disposition": line.disposition,
                "total": amount,
                "taxable": tax[0],
                "cgst": tax[1],
                "sgst": tax[2],
            }
        )
    return computed


def corrections_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}", tags=["Approved corrections"]
    )
    access = IdentityAccess(engine, settings)

    def authorize(
        connection: Connection,
        actor: Actor,
        business: UUID,
        store: UUID,
        capability: str = "actions.approve",
    ) -> None:
        scope = scope_for(connection, actor, str(business), capability)
        if str(store) not in permitted_store_ids(connection, str(business), scope):
            raise HTTPException(403, "Store access required")

    def execute(
        table: Table,
        target: UUID,
        business: UUID,
        store: UUID,
        payload: Reason,
        request: Request,
        key: UUID,
        work: Callable[[Connection, Actor, str, str], None],
    ) -> CorrectionView:
        actor = access.actor_for(request, mutation=True)
        request_hash = hashlib.sha256(
            json.dumps(
                {"target": str(target), "store": str(store), **payload.model_dump(mode="json")},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        try:
            with access.database().begin() as connection:
                authorize(connection, actor, business, store)
                lock(connection, "correction:" + table.name + str(business) + str(key))
                original = replay(connection, table, key, request_hash)
                if original:
                    return presentation(connection, table, original)
                resource = str(uuid4())
                work(connection, actor, resource, request_hash)
                audit(
                    connection,
                    str(business),
                    actor.user.id,
                    table.name + ".posted",
                    resource,
                    {"target_id": str(target), **payload.model_dump(mode="json")},
                )
                return presentation(connection, table, resource)
        except IntegrityError as error:
            raise HTTPException(
                409, "Correction conflicts with an existing record; review before retrying"
            ) from error

    @router.get("/corrections", response_model=list[CorrectionView])
    def listing(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[CorrectionView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            results: list[CorrectionView] = []
            for table in (credits, payment_reversals, purchase_reversals, adjustments):
                ids = connection.execute(
                    select(table.c.id)
                    .where(table.c.store_id == str(store_id))
                    .order_by(table.c.created_at.desc(), table.c.id)
                    .limit(limit + offset)
                ).scalars()
                results.extend(presentation(connection, table, resource) for resource in ids)
            return sorted(results, key=lambda result: (result.created_at, result.id), reverse=True)[
                offset : offset + limit
            ]

    @router.get("/sales/{sale_id}/credits", response_model=list[CorrectionView])
    def sale_credits(
        business_id: UUID, store_id: UUID, sale_id: UUID, request: Request
    ) -> list[CorrectionView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "sales.create")
            sale = row(connection, sales, str(sale_id))
            if sale["store_id"] != str(store_id):
                raise HTTPException(404, "Sale unavailable")
            ids = connection.execute(
                select(credits.c.id)
                .where(credits.c.target_id == str(sale_id))
                .order_by(credits.c.created_at)
            ).scalars()
            return [presentation(connection, credits, resource) for resource in ids]

    @router.post("/sales/{sale_id}/credits/preview", response_model=list[CreditLineView])
    def preview_credit(
        business_id: UUID, store_id: UUID, sale_id: UUID, payload: CreditInput, request: Request
    ) -> list[CreditLineView]:
        actor = access.actor_for(request, mutation=True)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            original = row(connection, sales, str(sale_id))
            if original["store_id"] != str(store_id):
                raise HTTPException(404, "Sale unavailable")
            return [
                CreditLineView.model_validate(line)
                for line in credit_lines(connection, sale_id, payload)
            ]

    @router.post("/sales/{sale_id}/credits", response_model=CorrectionView, status_code=201)
    def credit(
        business_id: UUID,
        store_id: UUID,
        sale_id: UUID,
        payload: CreditInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> CorrectionView:
        def work(connection: Connection, actor: Actor, resource: str, request_hash: str) -> None:
            lock(connection, "sale-return:" + str(business_id) + str(sale_id))
            sale = row(connection, sales, str(sale_id))
            if sale["store_id"] != str(store_id):
                raise HTTPException(404, "Sale unavailable")
            connection.execute(
                select(products.c.id)
                .where(products.c.id.in_(sorted(str(line.product_id) for line in payload.lines)))
                .order_by(products.c.id)
                .with_for_update()
            ).all()
            computed = credit_lines(connection, sale_id, payload)
            total = sum((Decimal(str(line["total"])) for line in computed), Decimal(0))
            if sum((payment.amount for payment in payload.payments), Decimal(0)) != total:
                raise HTTPException(422, "Refund payments must equal the exact credit amount")
            accounts = {
                str(payment.account_id): locked_account(
                    connection, str(payment.account_id), str(store_id)
                )
                for payment in sorted(payload.payments, key=lambda item: str(item.account_id))
            }
            connection.execute(
                insert(credits).values(
                    id=resource,
                    target_id=str(sale_id),
                    kind=payload.kind,
                    reason=payload.reason,
                    total=total,
                    taxable_total=sum(
                        (Decimal(str(line["taxable"])) for line in computed), Decimal(0)
                    ),
                    cgst=sum((Decimal(str(line["cgst"])) for line in computed), Decimal(0)),
                    sgst=sum((Decimal(str(line["sgst"])) for line in computed), Decimal(0)),
                    **command_values(actor, business_id, store_id, idempotency_key, request_hash),
                )
            )
            for line in computed:
                connection.execute(
                    insert(credit_items).values(
                        **line,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        credit_id=resource,
                    )
                )
                original_allocations = (
                    connection.execute(
                        select(allocations)
                        .where(allocations.c.sale_item_id == line["sale_item_id"])
                        .order_by(allocations.c.id)
                    )
                    .mappings()
                    .all()
                )
                remaining = Decimal(str(line["quantity"]))
                for allocation in original_allocations:
                    returned_qty = Decimal(
                        connection.execute(
                            select(func.coalesce(func.sum(credit_stock.c.quantity), 0)).where(
                                credit_stock.c.original_allocation_id == allocation["id"]
                            )
                        ).scalar_one()
                    )
                    quantity = min(remaining, Decimal(allocation["quantity"]) - returned_qty)
                    if quantity <= 0:
                        continue
                    args = (
                        connection,
                        actor,
                        str(business_id),
                        str(store_id),
                        str(line["id"]),
                        str(line["product_id"]),
                        str(allocation["batch_id"]),
                    )
                    movement = stock_movement(
                        *args,
                        quantity,
                        allocation["unit_cost"],
                        "sales_return",
                        payload.reason,
                        request_hash,
                        str(allocation["id"]) + ":return",
                    )
                    damage = (
                        stock_movement(
                            *args,
                            -quantity,
                            allocation["unit_cost"],
                            "damage",
                            payload.reason,
                            request_hash,
                            str(allocation["id"]) + ":discard",
                        )
                        if line["disposition"] == "discard"
                        else None
                    )
                    connection.execute(
                        insert(credit_stock).values(
                            id=str(uuid4()),
                            business_id=str(business_id),
                            store_id=str(store_id),
                            product_id=line["product_id"],
                            credit_item_id=line["id"],
                            original_allocation_id=allocation["id"],
                            movement_id=movement,
                            damage_movement_id=damage,
                            quantity=quantity,
                        )
                    )
                    remaining -= quantity
                if remaining != 0:
                    raise HTTPException(409, "Original stock allocation requires review")
            for payment in payload.payments:
                account = accounts[str(payment.account_id)]
                if account["kind"] != payment.method:
                    raise HTTPException(422, "Refund account kind does not match its method")
                refund_id = str(uuid4())
                movement = append_money(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    refund_id,
                    account,
                    -payment.amount,
                    "sales_refund",
                    payload.reason,
                    payment.cash_session_id,
                )
                connection.execute(
                    insert(refunds).values(
                        id=refund_id,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        credit_id=resource,
                        account_id=str(payment.account_id),
                        movement_id=movement,
                        amount=payment.amount,
                    )
                )

        return execute(
            credits, sale_id, business_id, store_id, payload, request, idempotency_key, work
        )

    @router.post(
        "/supplier-payments/{payment_id}/reverse", response_model=CorrectionView, status_code=201
    )
    def reverse_payment(
        business_id: UUID,
        store_id: UUID,
        payment_id: UUID,
        payload: ReversePayment,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> CorrectionView:
        def work(connection: Connection, actor: Actor, resource: str, request_hash: str) -> None:
            original = row(connection, supplier_payments, str(payment_id))
            if original["store_id"] != str(store_id):
                raise HTTPException(404, "Payment unavailable")
            lock(connection, "purchase-payment:" + str(business_id) + str(original["purchase_id"]))
            if connection.execute(
                select(payment_reversals.c.id).where(
                    payment_reversals.c.target_id == str(payment_id)
                )
            ).first():
                raise HTTPException(409, "Payment already reversed")
            account = locked_account(connection, str(original["account_id"]), str(store_id))
            movement = append_money(
                connection,
                actor,
                str(business_id),
                str(store_id),
                resource,
                account,
                Decimal(str(original["amount"])),
                "supplier_payment_reversal",
                payload.reason,
                payload.cash_session_id,
            )
            connection.execute(
                insert(payment_reversals).values(
                    id=resource,
                    target_id=str(payment_id),
                    account_id=original["account_id"],
                    movement_id=movement,
                    reason=payload.reason,
                    total=original["amount"],
                    **command_values(actor, business_id, store_id, idempotency_key, request_hash),
                )
            )

        return execute(
            payment_reversals,
            payment_id,
            business_id,
            store_id,
            payload,
            request,
            idempotency_key,
            work,
        )

    @router.post("/purchases/{purchase_id}/reverse", response_model=CorrectionView, status_code=201)
    def reverse_purchase(
        business_id: UUID,
        store_id: UUID,
        purchase_id: UUID,
        payload: Reason,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> CorrectionView:
        def work(connection: Connection, actor: Actor, resource: str, request_hash: str) -> None:
            lock(connection, "purchase-payment:" + str(business_id) + str(purchase_id))
            original = row(connection, purchases, str(purchase_id))
            if original["store_id"] != str(store_id):
                raise HTTPException(404, "Purchase unavailable")
            if connection.execute(
                select(purchase_reversals.c.id).where(
                    purchase_reversals.c.target_id == str(purchase_id)
                )
            ).first():
                raise HTTPException(409, "Purchase already reversed")
            if paid_amount(connection, str(purchase_id)) != 0:
                raise HTTPException(
                    409, "Reverse recorded supplier payments before reversing the purchase"
                )
            lines = (
                connection.execute(
                    select(purchase_items)
                    .where(purchase_items.c.purchase_id == str(purchase_id))
                    .order_by(purchase_items.c.product_id)
                )
                .mappings()
                .all()
            )
            connection.execute(
                select(products.c.id)
                .where(products.c.id.in_(sorted({line["product_id"] for line in lines})))
                .order_by(products.c.id)
                .with_for_update()
            ).all()
            connection.execute(
                insert(purchase_reversals).values(
                    id=resource,
                    target_id=str(purchase_id),
                    reason=payload.reason,
                    total=original["invoice_total"],
                    **{
                        c: original[c]
                        for c in ["taxable_total", "cgst", "sgst", "igst", "round_off"]
                    },
                    **command_values(actor, business_id, store_id, idempotency_key, request_hash),
                )
            )
            for line in lines:
                incoming = row(connection, stock_moves, str(line["movement_id"]))
                if connection.execute(
                    select(stock_moves.c.id).where(
                        stock_moves.c.batch_id == line["batch_id"],
                        stock_moves.c.quantity < 0,
                        stock_moves.c.created_at >= incoming["created_at"],
                    )
                ).first():
                    raise HTTPException(
                        409,
                        "Purchase stock was consumed or adjusted; use a reviewed purchase return",
                    )
                quantity, value = batch_balance(connection, str(line["batch_id"]))
                if quantity < line["stock_quantity"] or value - Decimal(
                    line["stock_quantity"]
                ) * Decimal(line["unit_cost"]) < Decimal("-0.000001"):
                    raise HTTPException(409, "Purchase stock/cost cannot be safely reversed")
                movement = stock_movement(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    resource,
                    str(line["product_id"]),
                    str(line["batch_id"]),
                    -Decimal(line["stock_quantity"]),
                    Decimal(line["unit_cost"]),
                    "purchase_return",
                    payload.reason,
                    request_hash,
                    str(line["id"]),
                )
                connection.execute(
                    insert(purchase_reversal_items).values(
                        id=str(uuid4()),
                        business_id=str(business_id),
                        store_id=str(store_id),
                        product_id=line["product_id"],
                        reversal_id=resource,
                        purchase_item_id=line["id"],
                        movement_id=movement,
                    )
                )

        return execute(
            purchase_reversals,
            purchase_id,
            business_id,
            store_id,
            payload,
            request,
            idempotency_key,
            work,
        )

    @router.get("/stock-batches", response_model=list[BatchView])
    def batch_listing(business_id: UUID, store_id: UUID, request: Request) -> list[BatchView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id, "inventory.read")
            rows = connection.execute(
                select(
                    batches.c.id,
                    batches.c.product_id,
                    batches.c.batch_number,
                    batches.c.expiry_date,
                    products.c.name.label("product_name"),
                    products.c.sku,
                    func.sum(stock_moves.c.quantity).label("quantity"),
                )
                .join(products, products.c.id == batches.c.product_id)
                .join(stock_moves, stock_moves.c.batch_id == batches.c.id)
                .where(batches.c.store_id == str(store_id))
                .group_by(batches.c.id, products.c.id)
                .order_by(products.c.name, batches.c.id)
                .limit(200)
            ).mappings()
            return [BatchView.model_validate(record) for record in rows]

    @router.post("/stock-batches/{batch_id}/adjust", response_model=CorrectionView, status_code=201)
    def adjust(
        business_id: UUID,
        store_id: UUID,
        batch_id: UUID,
        payload: CountInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> CorrectionView:
        def work(connection: Connection, actor: Actor, resource: str, request_hash: str) -> None:
            batch = row(connection, batches, str(batch_id))
            if batch["store_id"] != str(store_id):
                raise HTTPException(404, "Batch unavailable")
            product = (
                connection.execute(
                    select(products).where(products.c.id == batch["product_id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            expected, value = batch_balance(connection, str(batch_id))
            if expected != payload.expected_count:
                raise HTTPException(409, "Stock changed after counting; review the current ledger")
            if (
                product["unit"] in {"pcs", "pack"}
                and payload.actual_count != payload.actual_count.to_integral_value()
            ):
                raise HTTPException(422, "Pieces and packs require whole counts")
            delta = payload.actual_count - expected
            if delta == 0:
                raise HTTPException(422, "Count matches the ledger; no adjustment required")
            if delta > 0 and payload.added_unit_cost is None:
                raise HTTPException(422, "Confirm the cost of additional stock")
            held = connection.execute(
                text("SELECT quantity,value FROM offline_held(:batch)"), {"batch": str(batch_id)}
            ).one()
            free_count, free_value = expected - Decimal(held[0]), value - Decimal(held[1])
            if delta < 0 and free_count + delta < 0:
                raise HTTPException(
                    409, "Count conflicts with an offline till; synchronize it first"
                )
            cost = (
                payload.added_unit_cost
                if delta > 0
                else (free_value / free_count).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)
            )
            movement = stock_movement(
                connection,
                actor,
                str(business_id),
                str(store_id),
                resource,
                str(batch["product_id"]),
                str(batch_id),
                delta,
                Decimal(str(cost)),
                "adjustment",
                payload.reason,
                request_hash,
                "count",
            )
            connection.execute(
                insert(adjustments).values(
                    id=resource,
                    target_id=str(batch_id),
                    product_id=batch["product_id"],
                    movement_id=movement,
                    reason=payload.reason,
                    total=0,
                    expected_count=expected,
                    actual_count=payload.actual_count,
                    **command_values(actor, business_id, store_id, idempotency_key, request_hash),
                )
            )

        return execute(
            adjustments, batch_id, business_id, store_id, payload, request, idempotency_key, work
        )

    return router
