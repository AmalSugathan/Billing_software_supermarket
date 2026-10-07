"""Tenant-scoped catalog and immutable, approved opening-stock posting."""

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, Table, func, insert, or_, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog_models import (
    aliases,
    barcodes,
    batches,
    brands,
    categories,
    movements,
    products,
    suppliers,
)
from supermarket.config import Settings
from supermarket.identity import (
    IdentityAccess,
    InputModel,
    Scope,
    audit,
    permitted_store_ids,
    scope_for,
)


def decimal_text(value: object, places: int) -> Decimal:
    if (
        not isinstance(value, str)
        or re.fullmatch(rf"\d{{1,12}}(?:\.\d{{1,{places}}})?", value) is None
    ):
        raise ValueError(f"Use a non-negative decimal string with at most {places} decimal places")
    return Decimal(value)


Money = Annotated[
    Decimal,
    BeforeValidator(lambda value: decimal_text(value, 2)),
    Field(ge=0, le=Decimal("999999999999.99")),
]
Quantity = Annotated[
    Decimal,
    BeforeValidator(lambda value: decimal_text(value, 3)),
    Field(ge=0, le=Decimal("999999999999.999")),
]
Percent = Annotated[
    Decimal, BeforeValidator(lambda value: decimal_text(value, 2)), Field(ge=0, le=100)
]


def normalize(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold()).split())


class NamedInput(InputModel):
    name: str = Field(min_length=1, max_length=150)


class NamedView(BaseModel):
    id: str
    name: str


class SupplierInput(NamedInput):
    gstin: str | None = Field(
        default=None, min_length=15, max_length=15, pattern=r"^[0-9]{2}[A-Z0-9]{13}$"
    )
    phone: str = Field(default="", max_length=20, pattern=r"^[0-9+() \-]*$")
    address: str = Field(default="", max_length=500)
    payment_terms_days: int = Field(default=0, ge=0, le=365, strict=True)


class SupplierView(SupplierInput):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str
    active: bool


class ProductInput(InputModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._/\-]+$")
    name: str = Field(min_length=1, max_length=180)
    unit: Literal["pcs", "pack", "kg", "g", "l", "ml"] = "pcs"
    category_id: UUID | None = None
    brand_id: UUID | None = None
    supplier_id: UUID | None = None
    purchase_price: Money
    landed_cost: Money
    selling_price: Money
    mrp: Money
    minimum_selling_price: Money = Decimal("0")
    target_margin: Percent = Decimal("0")
    gst_rate: Percent = Decimal("0")
    hsn: str | None = Field(default=None, pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")
    reorder_level: Quantity = Decimal("0")
    reorder_quantity: Quantity = Decimal("0")
    batch_tracking: bool = Field(default=False, strict=True)
    expiry_tracking: bool = Field(default=False, strict=True)
    barcodes: list[str] = Field(default_factory=list[str], max_length=20)
    alternate_names: list[str] = Field(default_factory=list[str], max_length=20)
    confirm_distinct_product: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_product(self) -> ProductInput:
        if not normalize(self.name) or len(normalize(self.name)) > 180:
            raise ValueError("Product name must contain letters or digits")
        if self.selling_price > self.mrp or self.minimum_selling_price > self.selling_price:
            raise ValueError("Minimum price <= selling price <= MRP is required")
        if self.expiry_tracking and not self.batch_tracking:
            raise ValueError("Expiry tracking requires batch tracking")
        if len(set(self.barcodes)) != len(self.barcodes) or any(
            re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", value) is None for value in self.barcodes
        ):
            raise ValueError(
                "Barcodes must be distinct, 1–64 letters/digits/dots/dashes/underscores"
            )
        self.alternate_names = list(dict.fromkeys(name.strip() for name in self.alternate_names))
        if any(not normalize(name) or len(name) > 180 for name in self.alternate_names):
            raise ValueError("Alternate names must be nonempty and at most 180 characters")
        if self.unit in {"pcs", "pack"} and any(
            value != value.to_integral_value()
            for value in (self.reorder_level, self.reorder_quantity)
        ):
            raise ValueError("Piece/pack reorder quantities must be whole numbers")
        return self


class ProductView(BaseModel):
    id: str
    sku: str
    name: str
    unit: str
    category_id: str | None
    brand_id: str | None
    supplier_id: str | None
    purchase_price: Decimal | None
    landed_cost: Decimal | None
    selling_price: Decimal
    mrp: Decimal
    minimum_selling_price: Decimal
    target_margin: Decimal | None
    gst_rate: Decimal
    hsn: str | None
    reorder_level: Decimal
    reorder_quantity: Decimal
    batch_tracking: bool
    expiry_tracking: bool
    active: bool
    barcodes: list[str]
    alternate_names: list[str]


class Candidate(BaseModel):
    id: str
    sku: str
    name: str
    similarity: int


class BarcodeInput(InputModel):
    value: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._\-]+$")


class OpeningInput(InputModel):
    product_id: UUID
    quantity: Quantity
    unit_cost: Money
    reason: str = Field(min_length=3, max_length=500)
    batch_number: str | None = Field(default=None, min_length=1, max_length=100)
    expiry_date: date | None = None
    confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def approved(self) -> OpeningInput:
        if not self.confirmed or self.quantity <= 0:
            raise ValueError("Explicit confirmation and a positive opening quantity are required")
        return self


class MovementView(BaseModel):
    id: str
    store_id: str
    product_id: str
    batch_id: str
    kind: str
    quantity: Decimal
    unit_cost: Decimal
    reason: str
    actor_user_id: str
    source: str
    human_approved: bool
    created_at: datetime


class StockView(BaseModel):
    product_id: str
    sku: str
    name: str
    unit: str
    quantity: Decimal
    opening_value: Decimal


def product_view(connection: Connection, row: dict[str, object], scope: Scope) -> ProductView:
    return product_views(connection, [row], scope)[0]


def product_views(
    connection: Connection, rows: list[dict[str, object]], scope: Scope
) -> list[ProductView]:
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    codes: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    # Three catalog queries per page, rather than two extra queries for every product.
    for product_id, code in connection.execute(
        select(barcodes.c.product_id, barcodes.c.value)
        .where(barcodes.c.product_id.in_(ids))
        .order_by(barcodes.c.value)
    ):
        codes.setdefault(str(product_id), []).append(str(code))
    for product_id, name in connection.execute(
        select(aliases.c.product_id, aliases.c.name)
        .where(aliases.c.product_id.in_(ids))
        .order_by(aliases.c.name)
    ):
        names.setdefault(str(product_id), []).append(str(name))
    result: list[ProductView] = []
    show_cost = bool(
        scope.capabilities.intersection({"catalog.manage", "purchases.manage", "finance.read"})
    )
    for row in rows:
        product_id = str(row["id"])
        if not show_cost:
            row = {**row, "purchase_price": None, "landed_cost": None, "target_margin": None}
        result.append(
            ProductView.model_validate(
                {
                    **row,
                    "barcodes": codes.get(product_id, []),
                    "alternate_names": names.get(product_id, []),
                }
            )
        )
    return result


def candidates(connection: Connection, name: str) -> list[Candidate]:
    normalized = normalize(name)
    # Bounded local fuzzy review, not an AI confidence claim. No auto-creation or auto-match.
    rows = connection.execute(
        select(products.c.id, products.c.name, products.c.sku, products.c.normalized_name)
        .order_by(products.c.created_at.desc())
        .limit(2000)
    ).mappings()
    result: list[Candidate] = []
    for row in rows:
        similarity = round(100 * SequenceMatcher(None, normalized, row["normalized_name"]).ratio())
        if similarity >= 75:
            result.append(
                Candidate(id=row["id"], name=row["name"], sku=row["sku"], similarity=similarity)
            )
    return sorted(result, key=lambda item: item.similarity, reverse=True)[:10]


def catalog_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/businesses/{business_id}", tags=["catalog and inventory"])
    access = IdentityAccess(engine, settings)

    def allowed_store(
        connection: Connection, business_id: str, scope: Scope, store_id: str
    ) -> None:
        # Store scope is checked in every inventory endpoint, independently of UI visibility.
        if store_id not in permitted_store_ids(connection, business_id, scope):
            raise HTTPException(404, "Store unavailable")

    def named_routes(table: Table, path: str) -> None:
        @router.get(path, response_model=list[NamedView])
        def list_named(business_id: UUID, request: Request) -> list[NamedView]:
            actor = access.actor_for(request)
            with access.database().begin() as connection:
                scope_for(connection, actor, str(business_id), "business.read")
                return [
                    NamedView.model_validate(dict(row))
                    for row in connection.execute(
                        select(table).order_by(table.c.name).limit(200)
                    ).mappings()
                ]

        @router.post(path, response_model=NamedView, status_code=201)
        def create_named(business_id: UUID, payload: NamedInput, request: Request) -> NamedView:
            actor = access.actor_for(request, mutation=True)
            value = NamedView(id=str(uuid4()), name=payload.name)
            try:
                with access.database().begin() as connection:
                    scope_for(connection, actor, str(business_id), "catalog.manage")
                    connection.execute(
                        insert(table).values(**value.model_dump(), business_id=str(business_id))
                    )
                    audit(
                        connection,
                        str(business_id),
                        actor.user.id,
                        table.name + ".created",
                        value.id,
                        payload.model_dump(),
                    )
                    return value
            except IntegrityError as error:
                raise HTTPException(409, "Name already exists") from error

    named_routes(categories, "/categories")
    named_routes(brands, "/brands")

    @router.get("/suppliers", response_model=list[SupplierView])
    def list_suppliers(
        business_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[SupplierView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope_for(connection, actor, str(business_id), "purchases.manage")
            return [
                SupplierView.model_validate(dict(row))
                for row in connection.execute(
                    select(suppliers)
                    .order_by(suppliers.c.name, suppliers.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
            ]

    @router.post("/suppliers", response_model=SupplierView, status_code=201)
    def create_supplier(
        business_id: UUID, payload: SupplierInput, request: Request
    ) -> SupplierView:
        actor = access.actor_for(request, mutation=True)
        value = SupplierView(id=str(uuid4()), active=True, **payload.model_dump())
        try:
            with access.database().begin() as connection:
                scope_for(connection, actor, str(business_id), "purchases.manage")
                connection.execute(
                    insert(suppliers).values(**value.model_dump(), business_id=str(business_id))
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "supplier.created",
                    value.id,
                    payload.model_dump(),
                )
                return value
        except IntegrityError as error:
            raise HTTPException(409, "Supplier name or GSTIN already exists") from error

    @router.get("/products/matches", response_model=list[Candidate])
    def matching_products(
        business_id: UUID, request: Request, name: str = Query(min_length=1, max_length=180)
    ) -> list[Candidate]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope_for(connection, actor, str(business_id), "catalog.manage")
            return candidates(connection, name)

    @router.get("/products/resolve-barcode", response_model=ProductView)
    def resolve_barcode(
        business_id: UUID, request: Request, value: str = Query(min_length=1, max_length=64)
    ) -> ProductView:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "business.read")
            row = (
                connection.execute(
                    select(products)
                    .join(barcodes, products.c.id == barcodes.c.product_id)
                    .where(barcodes.c.value == value, products.c.active.is_(True))
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(404, "Barcode not found")
            return product_view(connection, dict(row), scope)

    @router.get("/products", response_model=list[ProductView])
    def list_products(
        business_id: UUID,
        request: Request,
        q: str = Query("", max_length=180),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[ProductView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "business.read")
            query = (
                select(products)
                .order_by(products.c.name, products.c.id)
                .limit(limit)
                .offset(offset)
            )
            if q:
                query = query.where(
                    or_(
                        products.c.name.icontains(q, autoescape=True),
                        products.c.sku.icontains(q, autoescape=True),
                        products.c.id.in_(
                            select(aliases.c.product_id).where(
                                aliases.c.name.icontains(q, autoescape=True)
                            )
                        ),
                    )
                )
            return product_views(
                connection, [dict(row) for row in connection.execute(query).mappings()], scope
            )

    @router.post("/products", response_model=ProductView, status_code=201)
    def create_product(business_id: UUID, payload: ProductInput, request: Request) -> ProductView:
        actor = access.actor_for(request, mutation=True)
        product_id = str(uuid4())
        try:
            with access.database().begin() as connection:
                scope = scope_for(connection, actor, str(business_id), "catalog.manage")
                matches = candidates(connection, payload.name)
                if matches and not payload.confirm_distinct_product:
                    raise HTTPException(
                        409,
                        "Similar product exists. Review matches and explicitly confirm "
                        "this is a distinct product.",
                    )
                for table, reference in (
                    (categories, payload.category_id),
                    (brands, payload.brand_id),
                    (suppliers, payload.supplier_id),
                ):
                    if (
                        reference is not None
                        and connection.execute(
                            select(table.c.id).where(table.c.id == str(reference))
                        ).scalar_one_or_none()
                        is None
                    ):
                        raise HTTPException(
                            422, "Category, brand or supplier is unavailable in this business"
                        )
                values = payload.model_dump(
                    exclude={"barcodes", "alternate_names", "confirm_distinct_product"}
                )
                for key in ("category_id", "brand_id", "supplier_id"):
                    values[key] = str(values[key]) if values[key] else None
                values["sku"] = payload.sku.upper()
                connection.execute(
                    insert(products).values(
                        **values,
                        id=product_id,
                        business_id=str(business_id),
                        normalized_name=normalize(payload.name),
                        active=True,
                    )
                )
                for code in payload.barcodes:
                    connection.execute(
                        insert(barcodes).values(
                            business_id=str(business_id), product_id=product_id, value=code
                        )
                    )
                for name in payload.alternate_names:
                    connection.execute(
                        insert(aliases).values(
                            business_id=str(business_id), product_id=product_id, name=name
                        )
                    )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "product.created",
                    product_id,
                    payload.model_dump(mode="json"),
                )
                row = (
                    connection.execute(select(products).where(products.c.id == product_id))
                    .mappings()
                    .one()
                )
                return product_view(connection, dict(row), scope)
        except IntegrityError as error:
            raise HTTPException(
                409, "SKU, barcode or normalized product name already exists"
            ) from error

    @router.post("/products/{product_id}/barcodes", response_model=ProductView, status_code=201)
    def add_barcode(
        business_id: UUID, product_id: UUID, payload: BarcodeInput, request: Request
    ) -> ProductView:
        actor = access.actor_for(request, mutation=True)
        try:
            with access.database().begin() as connection:
                scope = scope_for(connection, actor, str(business_id), "catalog.manage")
                row = (
                    connection.execute(select(products).where(products.c.id == str(product_id)))
                    .mappings()
                    .first()
                )
                if row is None:
                    raise HTTPException(404, "Product unavailable")
                connection.execute(
                    insert(barcodes).values(
                        business_id=str(business_id),
                        product_id=str(product_id),
                        value=payload.value,
                    )
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "barcode.created",
                    str(product_id),
                    payload.model_dump(),
                )
                return product_view(connection, dict(row), scope)
        except IntegrityError as error:
            raise HTTPException(409, "Barcode already exists") from error

    @router.post("/stores/{store_id}/opening-stock", response_model=MovementView, status_code=201)
    def opening_stock(
        business_id: UUID,
        store_id: UUID,
        payload: OpeningInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> MovementView:
        actor = access.actor_for(request, mutation=True)
        request_hash = hashlib.sha256(
            json.dumps(
                {"store_id": str(store_id), **payload.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        with access.database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "actions.approve")
            allowed_store(connection, str(business_id), scope, str(store_id))
            # A tenant/request lock serializes retries across stores and products.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": str(business_id) + str(idempotency_key)},
            )
            existing = (
                connection.execute(
                    select(movements).where(movements.c.idempotency_key == str(idempotency_key))
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(
                        409, "Idempotency key was already used for different stock details"
                    )
                return MovementView.model_validate(dict(existing))
            product = (
                connection.execute(
                    select(products)
                    .where(products.c.id == str(payload.product_id), products.c.active.is_(True))
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if product is None:
                raise HTTPException(404, "Product unavailable")
            if (
                product["unit"] in {"pcs", "pack"}
                and payload.quantity != payload.quantity.to_integral_value()
            ):
                raise HTTPException(422, "Piece/pack stock must use whole quantities")
            if bool(product["batch_tracking"]) != bool(payload.batch_number) or bool(
                product["expiry_tracking"]
            ) != bool(payload.expiry_date):
                raise HTTPException(
                    422, "Batch and expiry fields must match this product's tracking configuration"
                )
            lot_query = select(batches.c.id).where(
                batches.c.store_id == str(store_id),
                batches.c.product_id == str(payload.product_id),
                batches.c.batch_number == payload.batch_number,
                batches.c.expiry_date == payload.expiry_date,
            )
            if connection.execute(lot_query).scalar_one_or_none() is not None:
                raise HTTPException(
                    409,
                    "Opening stock for this lot is already recorded; "
                    "use a reviewed correction workflow",
                )
            batch_id, movement_id = str(uuid4()), str(uuid4())
            connection.execute(
                insert(batches).values(
                    id=batch_id,
                    business_id=str(business_id),
                    store_id=str(store_id),
                    product_id=str(payload.product_id),
                    batch_number=payload.batch_number,
                    expiry_date=payload.expiry_date,
                )
            )
            connection.execute(
                insert(movements).values(
                    id=movement_id,
                    business_id=str(business_id),
                    store_id=str(store_id),
                    product_id=str(payload.product_id),
                    batch_id=batch_id,
                    kind="opening",
                    quantity=payload.quantity,
                    unit_cost=payload.unit_cost,
                    reason=payload.reason,
                    actor_user_id=actor.user.id,
                    source="human",
                    human_approved=True,
                    idempotency_key=str(idempotency_key),
                    request_hash=request_hash,
                )
            )
            audit(
                connection,
                str(business_id),
                actor.user.id,
                "stock.opening_recorded",
                movement_id,
                {**payload.model_dump(mode="json"), "idempotency_key": str(idempotency_key)},
            )
            return MovementView.model_validate(
                dict(
                    connection.execute(select(movements).where(movements.c.id == movement_id))
                    .mappings()
                    .one()
                )
            )

    @router.get("/stores/{store_id}/stock", response_model=list[StockView])
    def stock(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[StockView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "inventory.read")
            allowed_store(connection, str(business_id), scope, str(store_id))
            totals = (
                select(
                    movements.c.product_id,
                    func.sum(movements.c.quantity).label("quantity"),
                    func.sum(movements.c.quantity * movements.c.unit_cost)
                    .filter(movements.c.kind == "opening")
                    .label("opening_value"),
                )
                .where(movements.c.store_id == str(store_id))
                .group_by(movements.c.product_id)
                .subquery()
            )
            query = (
                select(
                    products.c.id.label("product_id"),
                    products.c.name,
                    products.c.sku,
                    products.c.unit,
                    func.coalesce(totals.c.quantity, 0).label("quantity"),
                    func.coalesce(totals.c.opening_value, 0).label("opening_value"),
                )
                .outerjoin(totals, products.c.id == totals.c.product_id)
                .order_by(products.c.name, products.c.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                StockView.model_validate(dict(row)) for row in connection.execute(query).mappings()
            ]

    @router.get("/stores/{store_id}/stock-movements", response_model=list[MovementView])
    def stock_movements(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[MovementView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "inventory.read")
            allowed_store(connection, str(business_id), scope, str(store_id))
            query = (
                select(movements)
                .where(movements.c.store_id == str(store_id))
                .order_by(movements.c.created_at.desc(), movements.c.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                MovementView.model_validate(dict(row))
                for row in connection.execute(query).mappings()
            ]

    return router
