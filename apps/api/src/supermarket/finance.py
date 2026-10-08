"Confirmed paid expenses and cashier reconciliation over an immutable ledger."

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, Engine, Table, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.catalog import Money
from supermarket.config import Settings
from supermarket.finance_models import accounts, closings, expenses, movements, reversals, sessions
from supermarket.identity import (
    Actor,
    IdentityAccess,
    InputModel,
    Scope,
    audit,
    permitted_store_ids,
    scope_for,
)
from supermarket.identity_models import terminals
from supermarket.offline_models import leases, seals


class Approved(InputModel):
    confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def approval(self) -> Approved:
        if not self.confirmed:
            raise ValueError("Explicit confirmation is required")
        return self


class AccountInput(Approved):
    name: str = Field(min_length=1, max_length=150)
    kind: Literal["cash", "bank", "upi", "card"]
    terminal_id: UUID | None = None
    opening_amount: Money
    reason: str = Field(min_length=3, max_length=500)


class ExpenseInput(Approved):
    account_id: UUID
    cash_session_id: UUID | None = None
    reference: str = Field(min_length=1, max_length=100, pattern=r"^[ -~]+$")
    expense_date: date
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=3, max_length=500)
    amount: Money

    @model_validator(mode="after")
    def positive(self) -> ExpenseInput:
        if self.amount <= 0:
            raise ValueError("Expense amount must be positive")
        return self


class ReversalInput(Approved):
    reason: str = Field(min_length=3, max_length=500)
    cash_session_id: UUID | None = None


class MoneyInput(ReversalInput):
    account_id: UUID
    kind: Literal["receipt", "withdrawal"]
    amount: Money

    @model_validator(mode="after")
    def positive(self) -> MoneyInput:
        if self.amount <= 0:
            raise ValueError("Movement amount must be positive")
        return self


class OpenInput(Approved):
    account_id: UUID
    opening_cash: Money
    reason: str = Field(min_length=3, max_length=500)


class CloseInput(Approved):
    expected_cash: Money
    actual_cash: Money
    reason: str = Field(min_length=3, max_length=500)


class AccountView(BaseModel):
    id: str
    store_id: str
    terminal_id: str | None
    name: str
    kind: str
    opening_amount: Decimal | None
    balance: Decimal | None


class ExpenseView(BaseModel):
    id: str
    store_id: str
    account_id: str
    movement_id: str
    reference: str
    expense_date: date
    category: str
    description: str
    amount: Decimal
    actor_user_id: str
    created_at: datetime
    reversed: bool


class MovementView(BaseModel):
    id: str
    account_id: str
    cash_session_id: str | None
    kind: str
    amount: Decimal
    resource_id: str
    reason: str
    actor_user_id: str
    source: str
    human_approved: bool
    created_at: datetime


class ReversalView(BaseModel):
    id: str
    expense_id: str
    account_id: str
    movement_id: str
    reason: str
    actor_user_id: str
    created_at: datetime


class SessionView(BaseModel):
    id: str
    account_id: str
    store_id: str
    actor_user_id: str
    opening_cash: Decimal
    expected_cash: Decimal
    actual_cash: Decimal | None
    variance: Decimal | None
    closed: bool
    created_at: datetime


class ClosingView(BaseModel):
    id: str
    cash_session_id: str
    expected_cash: Decimal
    actual_cash: Decimal
    variance: Decimal
    movement_id: str | None
    reason: str
    actor_user_id: str
    created_at: datetime


def row(connection: Connection, table: Table, resource: str) -> dict[str, object]:
    found = connection.execute(select(table).where(table.c.id == resource)).mappings().first()
    if found is None:
        raise HTTPException(404, "Record unavailable")
    return dict(found)


def balance(connection: Connection, account_id: str) -> Decimal:
    return Decimal(
        connection.execute(
            select(func.coalesce(func.sum(movements.c.amount), 0)).where(
                movements.c.account_id == account_id
            )
        ).scalar_one()
    )


def active_session(connection: Connection, session_id: str, account_id: str) -> dict[str, object]:
    found = row(connection, sessions, session_id)
    if (
        found["account_id"] != account_id
        or connection.execute(
            select(closings.c.id).where(closings.c.cash_session_id == session_id)
        ).first()
    ):
        raise HTTPException(409, "Cash session is closed or belongs to another account")
    return found


def locked_account(connection: Connection, account_id: str, store_id: str) -> dict[str, object]:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {
            "key": "money-account:"
            + str(
                connection.execute(text("SELECT current_setting('app.business_id')")).scalar_one()
            )
            + account_id
        },
    )
    found = (
        connection.execute(
            select(accounts).where(accounts.c.id == account_id, accounts.c.store_id == store_id)
        )
        .mappings()
        .first()
    )
    if found is None:
        raise HTTPException(404, "Account unavailable")
    return dict(found)


def append_money(
    connection: Connection,
    actor: Actor,
    business_id: str,
    store_id: str,
    resource: str,
    account: dict[str, object],
    amount: Decimal,
    kind: str,
    reason: str,
    session_id: UUID | None,
) -> str:
    account_id = str(account["id"])
    if account["kind"] == "cash" and kind != "opening":
        if session_id is None:
            raise HTTPException(422, "An open cash session is required for drawer movements")
        active_session(connection, str(session_id), account_id)
    elif session_id is not None:
        raise HTTPException(422, "Cash session cannot be attached to a noncash account")
    new_balance = balance(connection, account_id) + amount
    if new_balance < 0 or new_balance > Decimal("999999999999.99"):
        raise HTTPException(422, "Insufficient recorded funds or account balance overflow")
    movement_id = str(uuid4())
    connection.execute(
        insert(movements).values(
            id=movement_id,
            business_id=business_id,
            store_id=store_id,
            account_id=account_id,
            cash_session_id=str(session_id) if session_id else None,
            kind=kind,
            amount=amount,
            resource_id=resource,
            reason=reason,
            actor_user_id=actor.user.id,
            source="human",
            human_approved=True,
            idempotency_key=str(uuid5(UUID(resource), kind)),
            request_hash=hashlib.sha256((resource + kind).encode()).hexdigest(),
        )
    )
    return movement_id


def session_view(connection: Connection, record: dict[str, object]) -> dict[str, object]:
    closing = (
        connection.execute(select(closings).where(closings.c.cash_session_id == record["id"]))
        .mappings()
        .first()
    )
    delta = connection.execute(
        select(func.coalesce(func.sum(movements.c.amount), 0)).where(
            movements.c.cash_session_id == record["id"], movements.c.kind != "cash_variance"
        )
    ).scalar_one()
    return {
        **record,
        "expected_cash": closing["expected_cash"]
        if closing
        else Decimal(str(record["opening_cash"])) + Decimal(delta),
        "actual_cash": closing["actual_cash"] if closing else None,
        "variance": closing["variance"] if closing else None,
        "closed": closing is not None,
    }


def finance_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}", tags=["expenses and cash"]
    )
    access = IdentityAccess(engine, settings)

    def authorized(
        connection: Connection, actor: Actor, business_id: UUID, store_id: UUID, capability: str
    ) -> Scope:
        scope = scope_for(connection, actor, str(business_id), capability)
        if str(store_id) not in permitted_store_ids(connection, str(business_id), scope):
            raise HTTPException(404, "Store unavailable")
        return scope

    def account_view(
        connection: Connection, record: dict[str, object], scope: Scope
    ) -> dict[str, object]:
        visible = bool(scope.capabilities.intersection({"finance.read", "expenses.manage"})) or (
            record["kind"] == "cash" and "cash.sessions" in scope.capabilities
        )
        return {
            **record,
            "opening_amount": record["opening_amount"] if visible else None,
            "balance": balance(connection, str(record["id"])) if visible else None,
        }

    def expense_view(connection: Connection, record: dict[str, object]) -> dict[str, object]:
        return {
            **record,
            "reversed": connection.execute(
                select(reversals.c.id).where(reversals.c.expense_id == record["id"])
            ).first()
            is not None,
        }

    def execute[T: BaseModel](
        table: Table,
        response: type[T],
        request: Request,
        business_id: UUID,
        store_id: UUID,
        payload: Approved,
        key: UUID,
        capability: str,
        action: str,
        work: Callable[[Connection, Actor, Scope, str], dict[str, object]],
        present: Callable[[Connection, dict[str, object], Scope, Actor], dict[str, object]]
        | None = None,
        target: str = "",
    ) -> T:
        actor = access.actor_for(request, mutation=True)
        digest = hashlib.sha256(
            json.dumps(
                {"store_id": str(store_id), "target": target, **payload.model_dump(mode="json")},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        try:
            with access.database().begin() as connection:
                scope = authorized(connection, actor, business_id, store_id, capability)
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "finance:" + table.name + str(business_id) + str(key)},
                )
                existing = (
                    connection.execute(select(table).where(table.c.idempotency_key == str(key)))
                    .mappings()
                    .first()
                )
                if existing:
                    if (
                        table in (sessions, closings)
                        and existing["actor_user_id"] != actor.user.id
                        and "actions.approve" not in scope.capabilities
                    ):
                        raise HTTPException(403, "Cashier command belongs to another user")
                    if existing["request_hash"] != digest:
                        raise HTTPException(
                            409, "Idempotency key already used for different details"
                        )
                    record = dict(existing)
                else:
                    resource = str(uuid4())
                    record = work(connection, actor, scope, resource)
                    connection.execute(
                        insert(table).values(
                            **record,
                            id=resource,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            actor_user_id=actor.user.id,
                            source="human",
                            human_approved=True,
                            idempotency_key=str(key),
                            request_hash=digest,
                        )
                    )
                    audit(
                        connection,
                        str(business_id),
                        actor.user.id,
                        action,
                        resource,
                        {
                            **payload.model_dump(mode="json"),
                            "target": target,
                            "idempotency_key": str(key),
                        },
                    )
                    record = row(connection, table, resource)
                return response.model_validate(
                    present(connection, record, scope, actor) if present else record
                )
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) != "23505":
                raise
            raise HTTPException(
                409, "Duplicate reference or conflicting financial command"
            ) from error

    @router.get("/accounts", response_model=list[AccountView])
    def list_accounts(business_id: UUID, store_id: UUID, request: Request) -> list[AccountView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = authorized(connection, actor, business_id, store_id, "business.read")
            return [
                AccountView.model_validate(account_view(connection, dict(record), scope))
                for record in connection.execute(
                    select(accounts)
                    .where(accounts.c.store_id == str(store_id))
                    .order_by(accounts.c.name)
                    .limit(200)
                ).mappings()
            ]

    @router.post("/accounts", response_model=AccountView, status_code=201)
    def create_account(
        business_id: UUID,
        store_id: UUID,
        payload: AccountInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> AccountView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            if (payload.kind == "cash") != bool(payload.terminal_id):
                raise HTTPException(
                    422, "Cash account requires a terminal; noncash accounts must omit it"
                )
            if (
                payload.terminal_id
                and connection.execute(
                    select(terminals.c.id).where(
                        terminals.c.id == str(payload.terminal_id),
                        terminals.c.store_id == str(store_id),
                    )
                ).first()
                is None
            ):
                raise HTTPException(404, "Terminal unavailable")
            return {
                **payload.model_dump(exclude={"confirmed"}),
                "terminal_id": str(payload.terminal_id) if payload.terminal_id else None,
            }

        # Account opening and its movement commit in the same transaction.
        def opened(
            connection: Connection, record: dict[str, object], scope: Scope, actor: Actor
        ) -> dict[str, object]:
            if (
                connection.execute(
                    select(movements.c.id).where(
                        movements.c.account_id == record["id"], movements.c.kind == "opening"
                    )
                ).first()
                is None
            ):
                append_money(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    str(record["id"]),
                    record,
                    Decimal(str(record["opening_amount"])),
                    "opening",
                    str(record["reason"]),
                    None,
                )
            return account_view(connection, record, scope)

        return execute(
            accounts,
            AccountView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "actions.approve",
            "finance.account_created",
            work,
            opened,
        )

    @router.get("/expenses", response_model=list[ExpenseView])
    def list_expenses(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[ExpenseView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorized(connection, actor, business_id, store_id, "expenses.manage")
            query = (
                select(expenses, reversals.c.id.label("reversal_id"))
                .outerjoin(reversals, expenses.c.id == reversals.c.expense_id)
                .where(expenses.c.store_id == str(store_id))
                .order_by(expenses.c.created_at.desc(), expenses.c.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                ExpenseView.model_validate(
                    {**dict(record), "reversed": record["reversal_id"] is not None}
                )
                for record in connection.execute(query).mappings()
            ]

    @router.post("/expenses", response_model=ExpenseView, status_code=201)
    def create_expense(
        business_id: UUID,
        store_id: UUID,
        payload: ExpenseInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> ExpenseView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            account = locked_account(connection, str(payload.account_id), str(store_id))
            movement = append_money(
                connection,
                actor,
                str(business_id),
                str(store_id),
                resource,
                account,
                -payload.amount,
                "expense",
                payload.description,
                payload.cash_session_id,
            )
            return {
                **payload.model_dump(exclude={"confirmed", "cash_session_id"}),
                "account_id": str(payload.account_id),
                "reference_identity": payload.reference.upper().strip(),
                "movement_id": movement,
            }

        return execute(
            expenses,
            ExpenseView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "expenses.manage",
            "expense.posted",
            work,
            lambda connection, record, scope, actor: expense_view(connection, record),
        )

    @router.post("/expenses/{expense_id}/reverse", response_model=ReversalView, status_code=201)
    def reverse_expense(
        business_id: UUID,
        store_id: UUID,
        expense_id: UUID,
        payload: ReversalInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> ReversalView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            expense = row(connection, expenses, str(expense_id))
            if expense["store_id"] != str(store_id):
                raise HTTPException(404, "Expense unavailable")
            account = locked_account(connection, str(expense["account_id"]), str(store_id))
            if connection.execute(
                select(reversals.c.id).where(reversals.c.expense_id == str(expense_id))
            ).first():
                raise HTTPException(409, "Expense already reversed")
            movement = append_money(
                connection,
                actor,
                str(business_id),
                str(store_id),
                resource,
                account,
                Decimal(str(expense["amount"])),
                "expense_reversal",
                payload.reason,
                payload.cash_session_id,
            )
            return {
                "account_id": expense["account_id"],
                "expense_id": str(expense_id),
                "movement_id": movement,
                "reason": payload.reason,
            }

        return execute(
            reversals,
            ReversalView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "actions.approve",
            "expense.reversed",
            work,
            target=str(expense_id),
        )

    @router.get("/money-movements", response_model=list[MovementView])
    def list_movements(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[MovementView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorized(connection, actor, business_id, store_id, "finance.read")
            return [
                MovementView.model_validate(dict(record))
                for record in connection.execute(
                    select(movements)
                    .where(movements.c.store_id == str(store_id))
                    .order_by(movements.c.created_at.desc(), movements.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
            ]

    @router.post("/money-movements", response_model=MovementView, status_code=201)
    def manual_money(
        business_id: UUID,
        store_id: UUID,
        payload: MoneyInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> MovementView:
        # Validate the balance here and again at the database boundary.
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            account = locked_account(connection, str(payload.account_id), str(store_id))
            if account["kind"] == "cash":
                if payload.cash_session_id is None:
                    raise HTTPException(422, "Open drawer session required")
                active_session(connection, str(payload.cash_session_id), str(payload.account_id))
            elif payload.cash_session_id:
                raise HTTPException(422, "Noncash account cannot use a cash session")
            amount = payload.amount if payload.kind == "receipt" else -payload.amount
            if (
                not Decimal("0")
                <= balance(connection, str(payload.account_id)) + amount
                <= Decimal("999999999999.99")
            ):
                raise HTTPException(422, "Insufficient recorded funds or account balance overflow")
            return {
                "account_id": str(payload.account_id),
                "cash_session_id": str(payload.cash_session_id)
                if payload.cash_session_id
                else None,
                "kind": payload.kind,
                "amount": amount,
                "resource_id": resource,
                "reason": payload.reason,
            }

        return execute(
            movements,
            MovementView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "actions.approve",
            "finance.manual_movement",
            work,
        )

    @router.post(
        "/accounts/{account_id}/opening-variance", response_model=MovementView, status_code=201
    )
    def opening_variance(
        business_id: UUID,
        store_id: UUID,
        account_id: UUID,
        payload: CloseInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> MovementView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            account = locked_account(connection, str(account_id), str(store_id))
            if account["kind"] != "cash":
                raise HTTPException(422, "Opening-count adjustment requires a cash drawer")
            if connection.execute(
                select(sessions.c.id).where(
                    sessions.c.account_id == str(account_id),
                    ~select(closings.c.id)
                    .where(closings.c.cash_session_id == sessions.c.id)
                    .exists(),
                )
            ).first():
                raise HTTPException(409, "An open drawer must be reconciled through closing")
            if balance(connection, str(account_id)) != payload.expected_cash:
                raise HTTPException(409, "Recorded cash changed; refresh and review again")
            delta = payload.actual_cash - payload.expected_cash
            if not delta:
                raise HTTPException(422, "No opening-count difference to record")
            return {
                "account_id": str(account_id),
                "cash_session_id": None,
                "kind": "opening_variance",
                "amount": delta,
                "resource_id": resource,
                "reason": payload.reason,
            }

        return execute(
            movements,
            MovementView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "actions.approve",
            "cash.opening_reconciled",
            work,
            target=str(account_id),
        )

    @router.get("/cash-sessions", response_model=list[SessionView])
    def list_sessions(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[SessionView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            scope = authorized(connection, actor, business_id, store_id, "business.read")
            if not scope.capabilities.intersection(
                {"cash.sessions", "finance.read", "expenses.manage"}
            ):
                raise HTTPException(403, "Permission denied")
            query = (
                select(sessions)
                .where(sessions.c.store_id == str(store_id))
                .order_by(sessions.c.created_at.desc(), sessions.c.id)
                .limit(limit)
                .offset(offset)
            )
            if not scope.capabilities.intersection({"expenses.manage", "finance.read"}):
                query = query.where(sessions.c.actor_user_id == actor.user.id)
            return [
                SessionView.model_validate(session_view(connection, dict(record)))
                for record in connection.execute(query).mappings()
            ]

    @router.post("/cash-sessions", response_model=SessionView, status_code=201)
    def open_session(
        business_id: UUID,
        store_id: UUID,
        payload: OpenInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> SessionView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "cashier:" + str(business_id) + actor.user.id},
            )
            account = locked_account(connection, str(payload.account_id), str(store_id))
            if account["kind"] != "cash":
                raise HTTPException(422, "Cash drawer account required")
            if payload.opening_cash != balance(connection, str(payload.account_id)):
                raise HTTPException(
                    409, "Opening count does not match recorded cash; reconcile before opening"
                )
            return {
                "account_id": str(payload.account_id),
                "opening_cash": payload.opening_cash,
                "reason": payload.reason,
            }

        return execute(
            sessions,
            SessionView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "cash.sessions",
            "cash.session_opened",
            work,
            lambda connection, record, scope, actor: session_view(connection, record),
        )

    @router.post("/cash-sessions/{session_id}/close", response_model=ClosingView, status_code=201)
    def close_session(
        business_id: UUID,
        store_id: UUID,
        session_id: UUID,
        payload: CloseInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> ClosingView:
        def work(
            connection: Connection, actor: Actor, scope: Scope, resource: str
        ) -> dict[str, object]:
            session = row(connection, sessions, str(session_id))
            if session["store_id"] != str(store_id):
                raise HTTPException(404, "Cash session unavailable")
            if (
                session["actor_user_id"] != actor.user.id
                and "actions.approve" not in scope.capabilities
            ):
                raise HTTPException(403, "Only the cashier or owner can close this session")
            account = locked_account(connection, str(session["account_id"]), str(store_id))
            active_session(connection, str(session_id), str(session["account_id"]))
            if connection.execute(
                select(leases.c.id).where(
                    leases.c.cash_session_id == str(session_id),
                    ~select(seals.c.id).where(seals.c.target_id == leases.c.id).exists(),
                )
            ).first():
                raise HTTPException(409, "Synchronize and finalize the offline till before closing")
            expected = balance(connection, str(session["account_id"]))
            if payload.expected_cash != expected:
                raise HTTPException(
                    409, "Cash changed after review; refresh expected cash and confirm again"
                )
            variance = payload.actual_cash - expected
            movement = (
                append_money(
                    connection,
                    actor,
                    str(business_id),
                    str(store_id),
                    resource,
                    account,
                    variance,
                    "cash_variance",
                    payload.reason,
                    session_id,
                )
                if variance
                else None
            )
            return {
                "account_id": session["account_id"],
                "cash_session_id": str(session_id),
                "expected_cash": expected,
                "actual_cash": payload.actual_cash,
                "variance": variance,
                "reason": payload.reason,
                "movement_id": movement,
            }

        return execute(
            closings,
            ClosingView,
            request,
            business_id,
            store_id,
            payload,
            idempotency_key,
            "cash.sessions",
            "cash.session_closed",
            work,
            target=str(session_id),
        )

    return router
