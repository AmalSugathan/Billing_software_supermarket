"""Reviewed, decimal-safe purchases; no OCR, payments or silent catalog edits."""

import hashlib
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, BeforeValidator, Field, model_validator
from sqlalchemy import Connection, Engine, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog import Money, Percent, Quantity, decimal_text
from supermarket.catalog_models import batches, movements, products, suppliers
from supermarket.config import Settings
from supermarket.identity import (
    Actor,
    IdentityAccess,
    InputModel,
    audit,
    permitted_store_ids,
    scope_for,
)
from supermarket.purchase_models import items, purchases

CENT = Decimal("0.01")
MAX_MONEY = Decimal("999999999999.99")
Rate = Annotated[
    Decimal,
    BeforeValidator(lambda value: decimal_text(value, 6)),
    Field(ge=0, le=Decimal("999999999999.999999")),
]


def signed_round_off(value: object) -> Decimal:
    if not isinstance(value, str) or re.fullmatch(r"-?\d(?:\.\d{1,2})?", value) is None:
        raise ValueError("Round-off must be a decimal string")
    result = Decimal(value)
    if abs(result) > 1:
        raise ValueError("Round-off must be between -1.00 and 1.00")
    return result


class PurchaseLine(InputModel):
    product_id: UUID
    supplier_description: str = Field(min_length=1, max_length=250)
    purchase_unit: Literal["pcs", "pack", "carton", "bag", "kg", "g", "l", "ml"]
    purchase_quantity: Quantity
    units_per_purchase: Quantity
    free_stock_quantity: Quantity = Decimal("0")
    conversion_evidence: str = Field(min_length=3, max_length=500)
    unit_rate: Rate
    discount: Money = Decimal("0")
    gst_rate: Percent
    hsn: str | None = Field(default=None, pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")
    batch_number: str | None = Field(default=None, min_length=1, max_length=100)
    expiry_date: date | None = None

    @model_validator(mode="after")
    def valid_quantity(self) -> PurchaseLine:
        if self.purchase_quantity <= 0 or self.units_per_purchase <= 0:
            raise ValueError("Purchase quantity and conversion factor must be positive")
        if (
            self.purchase_unit in {"pcs", "pack", "carton", "bag"}
            and self.purchase_quantity != self.purchase_quantity.to_integral_value()
        ):
            raise ValueError("Piece, pack, carton and bag quantities must be whole numbers")
        return self


class PurchaseInput(InputModel):
    supplier_id: UUID
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    tax_mode: Literal["exclusive", "inclusive"]
    tax_kind: Literal["intra", "inter"]
    round_off: Annotated[Decimal, BeforeValidator(signed_round_off)] = Decimal("0")
    invoice_total: Money
    review_reason: str = Field(min_length=3, max_length=500)
    lines: list[PurchaseLine] = Field(min_length=1, max_length=200)
    confirmed: bool = Field(default=False, strict=True)


class CalculatedLine(BaseModel):
    line_number: int
    product_id: str
    product_name: str
    sku: str
    stock_unit: str
    stock_quantity: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    line_total: Decimal
    unit_cost: Decimal


class PurchaseTotals(BaseModel):
    taxable_total: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    round_off: Decimal
    invoice_total: Decimal


class PurchasePreview(PurchaseTotals):
    lines: list[CalculatedLine]


class PostedLine(CalculatedLine):
    id: str
    movement_id: str
    batch_id: str
    supplier_description: str
    purchase_unit: str
    purchase_quantity: Decimal
    units_per_purchase: Decimal
    free_stock_quantity: Decimal
    conversion_evidence: str
    unit_rate: Decimal
    discount: Decimal
    gst_rate: Decimal
    hsn: str | None
    batch_number: str | None
    expiry_date: date | None


class PurchaseView(PurchaseTotals):
    lines: list[PostedLine]
    id: str
    store_id: str
    supplier_id: str
    supplier_name: str
    supplier_gstin: str | None
    invoice_number: str
    invoice_date: date
    tax_mode: str
    tax_kind: str
    review_reason: str
    actor_user_id: str
    human_approved: bool
    source: str
    created_at: datetime


def calculate(payload: PurchaseInput, catalog: dict[str, dict[str, object]]) -> PurchasePreview:
    result: list[CalculatedLine] = []
    with localcontext() as context:
        context.prec = 50
        for index, line in enumerate(payload.lines, 1):
            product = catalog.get(str(line.product_id))
            if product is None or not product["active"]:
                raise HTTPException(404, "Product unavailable")
            stock = line.purchase_quantity * line.units_per_purchase + line.free_stock_quantity
            if stock > Decimal("999999999999.999") or stock != stock.quantize(Decimal("0.001")):
                raise HTTPException(422, "Converted stock exceeds supported quantity precision")
            if product["unit"] in {"pcs", "pack"} and stock != stock.to_integral_value():
                raise HTTPException(422, "Converted piece/pack stock must be whole quantities")
            if line.purchase_unit == product["unit"] and line.units_per_purchase != 1:
                raise HTTPException(
                    422, "Same purchase and stock unit requires a conversion factor of one"
                )
            if bool(product["batch_tracking"]) != bool(line.batch_number) or bool(
                product["expiry_tracking"]
            ) != bool(line.expiry_date):
                raise HTTPException(422, "Batch and expiry fields must match product tracking")
            gross = (line.purchase_quantity * line.unit_rate).quantize(CENT, rounding=ROUND_HALF_UP)
            if gross > MAX_MONEY or line.discount > gross:
                raise HTTPException(422, "Line amount overflow or discount exceeds gross amount")
            net = gross - line.discount
            if payload.tax_mode == "inclusive":
                taxable = (net / (1 + line.gst_rate / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
                tax = net - taxable
            else:
                taxable = net
                tax = (taxable * line.gst_rate / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            cgst = (
                (tax / 2).quantize(CENT, rounding=ROUND_HALF_UP)
                if payload.tax_kind == "intra"
                else Decimal("0.00")
            )
            sgst = tax - cgst if payload.tax_kind == "intra" else Decimal("0.00")
            igst = tax if payload.tax_kind == "inter" else Decimal("0.00")
            cost = (taxable / stock).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if cost > Decimal("999999999999.999999"):
                raise HTTPException(422, "Converted unit cost exceeds supported precision")
            result.append(
                CalculatedLine(
                    line_number=index,
                    product_id=str(line.product_id),
                    product_name=str(product["name"]),
                    sku=str(product["sku"]),
                    stock_unit=str(product["unit"]),
                    stock_quantity=stock,
                    taxable_value=taxable,
                    cgst=cgst,
                    sgst=sgst,
                    igst=igst,
                    line_total=taxable + tax,
                    unit_cost=cost,
                )
            )
        totals = {
            name: sum((getattr(line, name) for line in result), Decimal("0.00"))
            for name in ("taxable_value", "cgst", "sgst", "igst", "line_total")
        }
        total = totals["line_total"] + payload.round_off
        if total <= 0 or any(value > MAX_MONEY for value in totals.values()) or total > MAX_MONEY:
            raise HTTPException(
                422, "Invoice amount must be positive and within supported precision"
            )
        if total != payload.invoice_total:
            raise HTTPException(
                422,
                f"Invoice total mismatch: calculated {total:.2f}; review rates, "
                f"discounts, tax mode and round-off",
            )
        return PurchasePreview(
            taxable_total=totals["taxable_value"],
            cgst=totals["cgst"],
            sgst=totals["sgst"],
            igst=totals["igst"],
            round_off=payload.round_off,
            invoice_total=total,
            lines=result,
        )


def purchase_views(connection: Connection, headers: list[dict[str, object]]) -> list[PurchaseView]:
    if not headers:
        return []
    grouped: dict[str, list[PostedLine]] = {}
    query = (
        select(items, batches.c.batch_number, batches.c.expiry_date)
        .join(batches, items.c.batch_id == batches.c.id)
        .where(items.c.purchase_id.in_([row["id"] for row in headers]))
        .order_by(items.c.purchase_id, items.c.line_number)
    )
    for row in connection.execute(query).mappings():
        grouped.setdefault(str(row["purchase_id"]), []).append(PostedLine.model_validate(dict(row)))
    return [
        PurchaseView.model_validate({**row, "lines": grouped.get(str(row["id"]), [])})
        for row in headers
    ]


def view(connection: Connection, purchase_id: str) -> PurchaseView:
    header = dict(
        connection.execute(select(purchases).where(purchases.c.id == purchase_id)).mappings().one()
    )
    return purchase_views(connection, [header])[0]


def purchases_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}/purchases", tags=["purchases"]
    )
    access = IdentityAccess(engine, settings)

    def authorized(connection: Connection, actor: Actor, business_id: UUID, store_id: UUID) -> str:
        scope = scope_for(connection, actor, str(business_id), "purchases.manage")
        if str(store_id) not in permitted_store_ids(connection, str(business_id), scope):
            raise HTTPException(404, "Store unavailable")
        return actor.user.id

    def reviewed_data(
        connection: Connection, payload: PurchaseInput, *, locked: bool = False
    ) -> tuple[dict[str, object], PurchasePreview]:
        supplier = (
            connection.execute(
                select(suppliers).where(
                    suppliers.c.id == str(payload.supplier_id), suppliers.c.active.is_(True)
                )
            )
            .mappings()
            .first()
        )
        if supplier is None:
            raise HTTPException(404, "Supplier unavailable")
        query = (
            select(products)
            .where(products.c.id.in_(sorted({str(line.product_id) for line in payload.lines})))
            .order_by(products.c.id)
        )
        if locked:
            query = query.with_for_update()
        catalog = {str(row["id"]): dict(row) for row in connection.execute(query).mappings()}
        return dict(supplier), calculate(payload, catalog)

    @router.post("/preview", response_model=PurchasePreview)
    def preview(
        business_id: UUID, store_id: UUID, payload: PurchaseInput, request: Request
    ) -> PurchasePreview:
        actor = access.actor_for(request, mutation=True)
        with access.database().begin() as connection:
            authorized(connection, actor, business_id, store_id)
            return reviewed_data(connection, payload)[1]

    @router.get("", response_model=list[PurchaseView])
    def listing(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[PurchaseView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorized(connection, actor, business_id, store_id)
            headers = connection.execute(
                select(purchases)
                .where(purchases.c.store_id == str(store_id))
                .order_by(purchases.c.created_at.desc(), purchases.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
            return purchase_views(connection, [dict(row) for row in headers])

    @router.get("/{purchase_id}", response_model=PurchaseView)
    def detail(
        business_id: UUID, store_id: UUID, purchase_id: UUID, request: Request
    ) -> PurchaseView:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorized(connection, actor, business_id, store_id)
            if (
                connection.execute(
                    select(purchases.c.id).where(
                        purchases.c.id == str(purchase_id), purchases.c.store_id == str(store_id)
                    )
                ).scalar_one_or_none()
                is None
            ):
                raise HTTPException(404, "Purchase unavailable")
            return view(connection, str(purchase_id))

    @router.post("", response_model=PurchaseView, status_code=201)
    def post(
        business_id: UUID,
        store_id: UUID,
        payload: PurchaseInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> PurchaseView:
        if not payload.confirmed:
            raise HTTPException(422, "Review and explicit confirmation are required before posting")
        request_hash = hashlib.sha256(
            json.dumps(
                {"store_id": str(store_id), **payload.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        actor = access.actor_for(request, mutation=True)
        try:
            with access.database().begin() as connection:
                actor_id = authorized(connection, actor, business_id, store_id)
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "purchase:" + str(business_id) + str(idempotency_key)},
                )
                existing = (
                    connection.execute(
                        select(purchases).where(purchases.c.idempotency_key == str(idempotency_key))
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["request_hash"] != request_hash or existing["store_id"] != str(
                        store_id
                    ):
                        raise HTTPException(
                            409, "Idempotency key already used for different purchase details"
                        )
                    return view(connection, existing["id"])
                supplier, calculated = reviewed_data(connection, payload, locked=True)
                purchase_id = str(uuid4())
                identity = "".join(payload.invoice_number.upper().split())
                year = payload.invoice_date.year - (payload.invoice_date.month < 4)
                connection.execute(
                    insert(purchases).values(
                        id=purchase_id,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        supplier_id=str(payload.supplier_id),
                        supplier_name=supplier["name"],
                        supplier_gstin=supplier["gstin"],
                        invoice_number=payload.invoice_number,
                        invoice_identity=identity,
                        invoice_date=payload.invoice_date,
                        financial_year=year,
                        tax_mode=payload.tax_mode,
                        tax_kind=payload.tax_kind,
                        review_reason=payload.review_reason,
                        actor_user_id=actor_id,
                        human_approved=True,
                        source="human",
                        idempotency_key=str(idempotency_key),
                        request_hash=request_hash,
                        **calculated.model_dump(exclude={"lines"}),
                    )
                )
                for line, computed in zip(payload.lines, calculated.lines, strict=True):
                    batch_id = connection.execute(
                        select(batches.c.id).where(
                            batches.c.store_id == str(store_id),
                            batches.c.product_id == str(line.product_id),
                            batches.c.batch_number == line.batch_number,
                            batches.c.expiry_date == line.expiry_date,
                        )
                    ).scalar_one_or_none()
                    if batch_id is None:
                        batch_id = str(uuid4())
                        connection.execute(
                            insert(batches).values(
                                id=batch_id,
                                business_id=str(business_id),
                                store_id=str(store_id),
                                product_id=str(line.product_id),
                                batch_number=line.batch_number,
                                expiry_date=line.expiry_date,
                            )
                        )
                    movement_id = str(uuid4())
                    connection.execute(
                        insert(movements).values(
                            id=movement_id,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            product_id=str(line.product_id),
                            batch_id=batch_id,
                            kind="purchase",
                            quantity=computed.stock_quantity,
                            unit_cost=computed.unit_cost,
                            reason=payload.review_reason,
                            actor_user_id=actor_id,
                            source="human",
                            human_approved=True,
                            idempotency_key=str(
                                uuid5(UUID(purchase_id), str(computed.line_number))
                            ),
                            request_hash=request_hash,
                        )
                    )
                    source = line.model_dump(exclude={"product_id", "batch_number", "expiry_date"})
                    connection.execute(
                        insert(items).values(
                            id=str(uuid4()),
                            business_id=str(business_id),
                            purchase_id=purchase_id,
                            store_id=str(store_id),
                            batch_id=batch_id,
                            movement_id=movement_id,
                            **source,
                            **computed.model_dump(),
                        )
                    )
                audit(
                    connection,
                    str(business_id),
                    actor_id,
                    "purchase.posted",
                    purchase_id,
                    {
                        **payload.model_dump(mode="json"),
                        "idempotency_key": str(idempotency_key),
                        "calculated": calculated.model_dump(mode="json"),
                        "payment_status": "not_recorded",
                    },
                )
                return view(connection, purchase_id)
        except IntegrityError as error:
            raise HTTPException(
                409,
                "Supplier invoice already posted or posting conflict; review the existing purchase",
            ) from error

    return router
