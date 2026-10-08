"""Prepared offline cash checkout; reservations protect other tills and sync preserves IDs."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Annotated, cast
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog import Money, Quantity
from supermarket.catalog_models import barcodes, batches, products
from supermarket.catalog_models import movements as stock_moves
from supermarket.commerce import (
    Receipt,
    SaleInput,
    SaleLine,
    calculate,
    command_values,
    fingerprint,
    lock,
    receipt,
    replay,
)
from supermarket.commerce_models import allocations, items, payments, sales
from supermarket.config import Settings
from supermarket.corrections import Reason, batch_balance
from supermarket.finance import active_session, append_money, locked_account, row
from supermarket.finance_models import sessions
from supermarket.identity import Actor, IdentityAccess, audit, permitted_store_ids, scope_for
from supermarket.offline_models import consumptions, journal, leases, reservations, seals


class Quota(BaseModel):
    model_config = {"extra": "forbid"}
    product_id: UUID
    quantity: Quantity

    @model_validator(mode="after")
    def bounded(self) -> Quota:
        if self.quantity <= 0 or self.quantity > 10000:
            raise ValueError("Offline quantity must be between zero and 10,000 units")
        return self


class PrepareInput(Reason):
    terminal_id: UUID
    cash_session_id: UUID
    products: list[Quota] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct(self) -> PrepareInput:
        if len({item.product_id for item in self.products}) != len(self.products):
            raise ValueError("Reserve each product once")
        return self


class ProductSnapshot(BaseModel):
    id: str
    name: str
    sku: str
    unit: str
    selling_price: Decimal
    minimum_selling_price: Decimal
    gst_rate: Decimal
    hsn: str | None
    quantity: Decimal
    barcodes: list[str]


class LeaseView(BaseModel):
    id: str
    store_id: str
    terminal_id: str
    account_id: str
    cash_session_id: str
    actor_user_id: str
    created_at: datetime
    expires_at: datetime
    sealed: bool
    synced_sequence: int
    products: list[ProductSnapshot]


class SyncInput(Reason):
    client_sale_id: UUID
    sequence: int = Field(strict=True, ge=1, le=100000)
    completed_at: datetime
    lines: list[SaleLine] = Field(min_length=1, max_length=200)
    expected_total: Money
    received_cash: Money
    recovery_reason: str | None = Field(default=None, min_length=5, max_length=500)

    @model_validator(mode="after")
    def valid_sale(self) -> SyncInput:
        if self.completed_at.tzinfo is None:
            raise ValueError("Completion time must contain a timezone")
        if self.received_cash < self.expected_total:
            raise ValueError("Cash received cannot be below the receipt amount")
        if any(line.discount != 0 for line in self.lines):
            raise ValueError("This offline mode uses fixed prepared prices without discounts")
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Combine product quantities")
        return self


class SealInput(Reason):
    last_sequence: int = Field(strict=True, ge=0, le=100000)


def lease_view(connection: Connection, resource: str) -> LeaseView:
    header = row(connection, leases, resource)
    snapshots: dict[str, dict[str, object]] = {}
    for reservation in connection.execute(
        select(reservations)
        .where(reservations.c.lease_id == resource)
        .order_by(reservations.c.product_id)
    ).mappings():
        product_id = str(reservation["product_id"])
        if product_id not in snapshots:
            snapshots[product_id] = {**reservation["product_snapshot"], "quantity": Decimal(0)}
        used = Decimal(
            connection.execute(
                select(func.coalesce(func.sum(consumptions.c.quantity), 0)).where(
                    consumptions.c.reservation_id == reservation["id"]
                )
            ).scalar_one()
        )
        snapshots[product_id]["quantity"] = (
            Decimal(str(snapshots[product_id]["quantity"]))
            + Decimal(reservation["quantity"])
            - used
        )
    return LeaseView.model_validate(
        {
            **header,
            "sealed": connection.execute(
                select(seals.c.id).where(seals.c.target_id == resource)
            ).first()
            is not None,
            "products": list(snapshots.values()),
            "synced_sequence": int(
                connection.execute(
                    select(func.coalesce(func.max(journal.c.sequence), 0)).where(
                        journal.c.lease_id == resource
                    )
                ).scalar_one()
            ),
        }
    )


def available(connection: Connection, batch_id: str) -> tuple[Decimal, Decimal]:
    quantity, value = batch_balance(connection, batch_id)
    held = connection.execute(
        text("SELECT quantity,value FROM offline_held(:batch)"), {"batch": batch_id}
    ).one()
    return quantity - Decimal(held[0]), value - Decimal(held[1])


def offline_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}/offline-leases",
        tags=["Offline cash POS"],
    )
    access = IdentityAccess(engine, settings)

    def authorize(
        connection: Connection,
        actor: Actor,
        business: UUID,
        store: UUID,
        lease_id: str | None = None,
    ) -> dict[str, object] | None:
        scope = scope_for(connection, actor, str(business), "sales.create")
        if str(store) not in permitted_store_ids(connection, str(business), scope):
            raise HTTPException(403, "Store access required")
        if lease_id is None:
            return None
        lease = row(connection, leases, lease_id)
        if lease["store_id"] != str(store):
            raise HTTPException(404, "Offline terminal unavailable")
        if lease["actor_user_id"] != actor.user.id and "actions.approve" not in scope.capabilities:
            raise HTTPException(403, "Offline terminal belongs to another operator")
        return lease

    @router.get("", response_model=list[LeaseView])
    def listing(business_id: UUID, store_id: UUID, request: Request) -> list[LeaseView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            scope = scope_for(connection, actor, str(business_id), "sales.create")
            query = (
                select(leases.c.id)
                .where(leases.c.store_id == str(store_id))
                .order_by(leases.c.created_at.desc())
                .limit(200)
            )
            if "actions.approve" not in scope.capabilities:
                query = query.where(leases.c.actor_user_id == actor.user.id)
            return [
                lease_view(connection, resource) for resource in connection.execute(query).scalars()
            ]

    @router.post("", response_model=LeaseView, status_code=201)
    def prepare(
        business_id: UUID,
        store_id: UUID,
        payload: PrepareInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> LeaseView:
        actor = access.actor_for(request, mutation=True)
        request_hash = fingerprint(store_id, payload)
        try:
            with access.database().begin() as connection:
                authorize(connection, actor, business_id, store_id)
                lock(connection, "offline-prepare:" + str(business_id) + str(idempotency_key))
                existing = replay(connection, leases, idempotency_key, request_hash)
                if existing:
                    return lease_view(connection, existing)
                ids = sorted(str(item.product_id) for item in payload.products)
                catalog = {
                    str(product["id"]): dict(product)
                    for product in connection.execute(
                        select(products)
                        .where(products.c.id.in_(ids))
                        .order_by(products.c.id)
                        .with_for_update()
                    ).mappings()
                }
                session = row(connection, sessions, str(payload.cash_session_id))
                account = locked_account(connection, str(session["account_id"]), str(store_id))
                active_session(connection, str(payload.cash_session_id), str(session["account_id"]))
                if session["actor_user_id"] != actor.user.id or account["terminal_id"] != str(
                    payload.terminal_id
                ):
                    raise HTTPException(403, "Prepare your own terminal's open drawer")
                if connection.execute(
                    select(leases.c.id).where(
                        leases.c.cash_session_id == str(payload.cash_session_id),
                        ~select(seals.c.id).where(seals.c.target_id == leases.c.id).exists(),
                    )
                ).first():
                    raise HTTPException(
                        409, "This drawer already has an offline lease; recover/finalize it first"
                    )
                now = datetime.now(UTC)
                local = now.astimezone(ZoneInfo("Asia/Kolkata"))
                midnight = (local + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                expires = min(now + timedelta(hours=8), midnight.astimezone(UTC))
                resource = str(uuid4())
                connection.execute(
                    insert(leases).values(
                        id=resource,
                        terminal_id=str(payload.terminal_id),
                        cash_session_id=str(payload.cash_session_id),
                        account_id=session["account_id"],
                        expires_at=expires,
                        reason=payload.reason,
                        **command_values(
                            actor, business_id, store_id, idempotency_key, request_hash
                        ),
                    )
                )
                for quota in payload.products:
                    product = catalog.get(str(quota.product_id))
                    if product is None or not product["active"]:
                        raise HTTPException(404, "Active product unavailable")
                    if (
                        product["unit"] in {"pcs", "pack"}
                        and quota.quantity != quota.quantity.to_integral_value()
                    ):
                        raise HTTPException(422, "Pieces and packs require whole offline quotas")
                    snapshot = {
                        field: product[field] for field in ["id", "name", "sku", "unit", "hsn"]
                    }
                    snapshot.update(
                        {
                            field: str(product[field])
                            for field in ["selling_price", "minimum_selling_price", "gst_rate"]
                        }
                    )
                    snapshot["barcodes"] = list(
                        connection.execute(
                            select(barcodes.c.value).where(
                                barcodes.c.product_id == str(quota.product_id)
                            )
                        ).scalars()
                    )
                    remaining = quota.quantity
                    lots = connection.execute(
                        select(batches)
                        .where(
                            batches.c.store_id == str(store_id),
                            batches.c.product_id == str(quota.product_id),
                        )
                        .order_by(
                            batches.c.expiry_date.asc().nulls_last(),
                            batches.c.created_at,
                            batches.c.id,
                        )
                    ).mappings()
                    for lot in lots:
                        if lot["expiry_date"] is not None and lot["expiry_date"] < local.date():
                            continue
                        count, value = available(connection, str(lot["id"]))
                        take = min(remaining, count)
                        if take <= 0:
                            continue
                        cost = (value / count).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)
                        connection.execute(
                            insert(reservations).values(
                                id=str(uuid4()),
                                business_id=str(business_id),
                                store_id=str(store_id),
                                lease_id=resource,
                                product_id=str(quota.product_id),
                                batch_id=lot["id"],
                                quantity=take,
                                unit_cost=cost,
                                product_snapshot=snapshot,
                            )
                        )
                        remaining -= take
                        if remaining == 0:
                            break
                    if remaining != 0:
                        raise HTTPException(
                            409, "Insufficient unreserved stock for " + str(product["name"])
                        )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "offline.prepared",
                    resource,
                    payload.model_dump(mode="json"),
                )
                return lease_view(connection, resource)
        except IntegrityError as error:
            raise HTTPException(409, "Offline preparation conflict; review the drawer") from error

    @router.post("/{lease_id}/sales", response_model=Receipt, status_code=201)
    def synchronize(
        business_id: UUID,
        store_id: UUID,
        lease_id: UUID,
        payload: SyncInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> Receipt:
        actor = access.actor_for(request, mutation=True)
        if idempotency_key != payload.client_sale_id:
            raise HTTPException(422, "Offline request key must be the saved receipt ID")
        request_hash = hashlib.sha256(
            (
                str(lease_id)
                + str(store_id)
                + json.dumps(
                    payload.model_dump(mode="json", exclude={"recovery_reason"}), sort_keys=True
                )
            ).encode()
        ).hexdigest()
        try:
            with access.database().begin() as connection:
                lease = authorize(connection, actor, business_id, store_id, str(lease_id))
                if lease is None:
                    raise HTTPException(404, "Offline terminal unavailable")
                lock(connection, "offline-sync:" + str(business_id) + str(lease_id))
                existing = (
                    connection.execute(
                        select(journal).where(journal.c.sale_id == str(payload.client_sale_id))
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise HTTPException(409, "Saved offline receipt was changed")
                    return receipt(connection, str(payload.client_sale_id))
                if connection.execute(
                    select(seals.c.id).where(seals.c.target_id == str(lease_id))
                ).first():
                    raise HTTPException(
                        409, "Offline terminal was finalized; keep this receipt for owner review"
                    )
                expected_sequence = (
                    int(
                        connection.execute(
                            select(func.coalesce(func.max(journal.c.sequence), 0)).where(
                                journal.c.lease_id == str(lease_id)
                            )
                        ).scalar_one()
                    )
                    + 1
                )
                if payload.sequence != expected_sequence:
                    raise HTTPException(409, "Synchronize offline receipts in their original order")
                scope = scope_for(connection, actor, str(business_id), "sales.create")
                unusual_time = (
                    payload.completed_at
                    < cast(datetime, lease["created_at"]) - timedelta(minutes=5)
                    or payload.completed_at > cast(datetime, lease["expires_at"])
                    or payload.completed_at > datetime.now(UTC) + timedelta(minutes=5)
                )
                if unusual_time or lease["actor_user_id"] != actor.user.id:
                    if (
                        "actions.approve" not in scope.capabilities
                        or payload.recovery_reason is None
                    ):
                        raise HTTPException(
                            409,
                            "Owner approval and recovery reason required; keep the saved receipt",
                        )
                quota_rows = (
                    connection.execute(
                        select(reservations)
                        .where(reservations.c.lease_id == str(lease_id))
                        .order_by(reservations.c.id)
                    )
                    .mappings()
                    .all()
                )
                catalog: dict[str, dict[str, object]] = {}
                for quota in quota_rows:
                    snapshot = dict(quota["product_snapshot"])
                    for field in ["selling_price", "minimum_selling_price", "gst_rate"]:
                        snapshot[field] = Decimal(snapshot[field])
                    snapshot["active"] = True
                    catalog[str(quota["product_id"])] = snapshot
                selected = sorted(str(line.product_id) for line in payload.lines)
                connection.execute(
                    select(products.c.id)
                    .where(products.c.id.in_(selected))
                    .order_by(products.c.id)
                    .with_for_update()
                ).all()
                sale_input = SaleInput(
                    terminal_id=UUID(str(lease["terminal_id"])), lines=payload.lines, confirmed=True
                )
                quote = calculate(sale_input, catalog)
                if quote.total != payload.expected_total:
                    raise HTTPException(
                        409, "Offline receipt total differs; retain it for owner review"
                    )
                account = locked_account(connection, str(lease["account_id"]), str(store_id))
                active_session(connection, str(lease["cash_session_id"]), str(lease["account_id"]))
                resource = str(payload.client_sale_id)
                connection.execute(
                    insert(sales).values(
                        id=resource,
                        terminal_id=lease["terminal_id"],
                        invoice_number="OFF-" + payload.client_sale_id.hex.upper(),
                        **quote.model_dump(exclude={"lines"}),
                        **command_values(
                            actor, business_id, store_id, idempotency_key, request_hash
                        ),
                    )
                )
                for line in quote.lines:
                    item_id = str(uuid4())
                    connection.execute(
                        insert(items).values(
                            id=item_id,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            sale_id=resource,
                            **line.model_dump(),
                        )
                    )
                    remaining = line.quantity
                    for quota in quota_rows:
                        if quota["product_id"] != line.product_id:
                            continue
                        used = Decimal(
                            connection.execute(
                                select(func.coalesce(func.sum(consumptions.c.quantity), 0)).where(
                                    consumptions.c.reservation_id == quota["id"]
                                )
                            ).scalar_one()
                        )
                        take = min(remaining, Decimal(quota["quantity"]) - used)
                        if take <= 0:
                            continue
                        movement_id = str(uuid4())
                        connection.execute(
                            insert(consumptions).values(
                                id=str(uuid4()),
                                business_id=str(business_id),
                                store_id=str(store_id),
                                product_id=line.product_id,
                                reservation_id=quota["id"],
                                movement_id=movement_id,
                                quantity=take,
                            )
                        )
                        connection.execute(
                            insert(stock_moves).values(
                                id=movement_id,
                                business_id=str(business_id),
                                store_id=str(store_id),
                                product_id=line.product_id,
                                batch_id=quota["batch_id"],
                                kind="sale",
                                quantity=-take,
                                unit_cost=quota["unit_cost"],
                                reason="Offline receipt " + resource,
                                actor_user_id=actor.user.id,
                                source="human",
                                human_approved=True,
                                idempotency_key=str(uuid5(UUID(item_id), str(quota["id"]))),
                                request_hash=request_hash,
                            )
                        )
                        connection.execute(
                            insert(allocations).values(
                                id=str(uuid4()),
                                business_id=str(business_id),
                                store_id=str(store_id),
                                product_id=line.product_id,
                                sale_item_id=item_id,
                                batch_id=quota["batch_id"],
                                movement_id=movement_id,
                                quantity=take,
                                unit_cost=quota["unit_cost"],
                            )
                        )
                        remaining -= take
                        if remaining == 0:
                            break
                    if remaining != 0:
                        raise HTTPException(
                            409,
                            (
                                "Offline quantity exceeds its reservation; keep receipt for owner "
                                "review"
                            ),
                        )
                payment_id = str(uuid4())
                money = append_money(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    payment_id,
                    account,
                    quote.total,
                    "sale",
                    "Offline receipt " + resource,
                    UUID(str(lease["cash_session_id"])),
                )
                connection.execute(
                    insert(payments).values(
                        id=payment_id,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        sale_id=resource,
                        account_id=lease["account_id"],
                        movement_id=money,
                        method="cash",
                        amount=quote.total,
                    )
                )
                connection.execute(
                    insert(journal).values(
                        id=str(uuid4()),
                        business_id=str(business_id),
                        store_id=str(store_id),
                        lease_id=str(lease_id),
                        sale_id=resource,
                        sequence=payload.sequence,
                        completed_at=payload.completed_at,
                        request_hash=request_hash,
                    )
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "offline.sale_synchronized",
                    resource,
                    {
                        **payload.model_dump(mode="json"),
                        "lease_id": str(lease_id),
                        "original_operator_id": lease["actor_user_id"],
                    },
                )
                return receipt(connection, resource)
        except IntegrityError as error:
            raise HTTPException(
                409, "Offline sync conflict; preserve the original receipt and retry"
            ) from error

    @router.post("/{lease_id}/finalize", response_model=LeaseView)
    def finalize(
        business_id: UUID,
        store_id: UUID,
        lease_id: UUID,
        payload: SealInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> LeaseView:
        actor = access.actor_for(request, mutation=True)
        request_hash = hashlib.sha256(
            (
                str(lease_id)
                + str(store_id)
                + json.dumps(
                    payload.model_dump(mode="json", exclude={"recovery_reason"}), sort_keys=True
                )
            ).encode()
        ).hexdigest()
        try:
            with access.database().begin() as connection:
                lease = authorize(connection, actor, business_id, store_id, str(lease_id))
                if lease is None:
                    raise HTTPException(404, "Offline terminal unavailable")
                lock(connection, "offline-sync:" + str(business_id) + str(lease_id))
                if replay(connection, seals, idempotency_key, request_hash):
                    return lease_view(connection, str(lease_id))
                # Same order as preparation/sync: product locks before the drawer lock.
                ids = list(
                    connection.execute(
                        select(reservations.c.product_id).where(
                            reservations.c.lease_id == str(lease_id)
                        )
                    ).scalars()
                )
                connection.execute(
                    select(products.c.id)
                    .where(products.c.id.in_(ids))
                    .order_by(products.c.id)
                    .with_for_update()
                ).all()
                locked_account(connection, str(lease["account_id"]), str(store_id))
                count = int(
                    connection.execute(
                        select(func.count(journal.c.id)).where(journal.c.lease_id == str(lease_id))
                    ).scalar_one()
                )
                if count != payload.last_sequence:
                    raise HTTPException(
                        409, "Upload all locally completed receipts before finalizing"
                    )
                connection.execute(
                    insert(seals).values(
                        id=str(uuid4()),
                        target_id=str(lease_id),
                        last_sequence=count,
                        reason=payload.reason,
                        **command_values(
                            actor, business_id, store_id, idempotency_key, request_hash
                        ),
                    )
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "offline.finalized",
                    str(lease_id),
                    payload.model_dump(mode="json"),
                )
                return lease_view(connection, str(lease_id))
        except IntegrityError as error:
            raise HTTPException(409, "Offline terminal already finalized or changed") from error

    return router
