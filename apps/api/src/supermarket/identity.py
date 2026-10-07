"""Identity and business onboarding with scoped authorization and atomic audit events."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Connection, Engine, RowMapping, insert, select, text
from sqlalchemy.exc import IntegrityError

from supermarket.config import Settings
from supermarket.identity_models import (
    ROLE_CAPABILITIES,
    audit_logs,
    businesses,
    membership_stores,
    memberships,
    role_permissions,
    roles,
    security_events,
    sessions,
    stores,
    terminals,
    users,
)
from supermarket.security import (
    csrf_token,
    hash_password,
    new_session_token,
    token_digest,
    verify_password,
)

COOKIE_NAME = "supermarket_session"
RoleName = Literal[
    "OWNER",
    "STORE_MANAGER",
    "CASHIER",
    "INVENTORY_MANAGER",
    "PURCHASE_MANAGER",
    "ACCOUNTANT",
    "ADMIN",
]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


class UserView(BaseModel):
    id: str
    email: str
    display_name: str


class SessionView(BaseModel):
    user: UserView
    csrf_token: str


class BusinessInput(InputModel):
    name: str = Field(min_length=1, max_length=150)
    store_name: str = Field(min_length=1, max_length=150)
    store_address: str = Field(max_length=500, default="")


class StoreInput(InputModel):
    name: str = Field(min_length=1, max_length=150)
    address: str = Field(max_length=500, default="")


class TerminalInput(InputModel):
    name: str = Field(min_length=1, max_length=100)


class MemberInput(InputModel):
    email: EmailStr
    role: RoleName
    store_ids: list[UUID] = Field(default_factory=list[UUID], max_length=100)


class BusinessView(BaseModel):
    id: str
    name: str
    currency: str
    timezone: str
    role: RoleName
    capabilities: list[str]
    all_stores: bool


class StoreView(BaseModel):
    id: str
    name: str
    address: str


class TerminalView(BaseModel):
    id: str
    store_id: str
    name: str


class MemberView(BaseModel):
    id: str
    email: str
    display_name: str
    role: RoleName
    all_stores: bool
    store_ids: list[str]


class AuditView(BaseModel):
    id: str
    actor_user_id: str
    action: str
    resource_id: str
    source: str
    before_value: dict[str, object] | None
    after_value: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class Actor:
    user: UserView
    token: str


@dataclass(frozen=True)
class Scope:
    membership_id: str
    role: str
    capabilities: frozenset[str]
    all_stores: bool


def set_context(connection: Connection, user_id: str, business_id: str | None = None) -> None:
    # Transaction-local settings reset on commit/rollback; pooled connections cannot retain scope.
    connection.execute(
        text(
            "SELECT set_config('app.user_id', :actor, true), "
            "set_config('app.business_id', :tenant, true)"
        ),
        {"actor": user_id, "tenant": business_id or ""},
    )


def audit(
    connection: Connection,
    business_id: str,
    actor_id: str,
    action: str,
    resource_id: str,
    after: dict[str, object],
) -> None:
    connection.execute(
        insert(audit_logs).values(
            id=str(uuid4()),
            business_id=business_id,
            actor_user_id=actor_id,
            action=action,
            resource_id=resource_id,
            source="human",
            before_value=None,
            after_value=after,
        )
    )


def scope_for(connection: Connection, actor: Actor, business_id: str, capability: str) -> Scope:
    # First query with actor-only scope, before enabling any selected business context.
    set_context(connection, actor.user.id)
    membership = (
        connection.execute(
            select(memberships).where(
                memberships.c.business_id == business_id,
                memberships.c.user_id == actor.user.id,
            )
        )
        .mappings()
        .first()
    )
    if membership is None:
        raise HTTPException(404, "Business unavailable")
    set_context(connection, actor.user.id, business_id)
    role = connection.execute(
        select(roles.c.name).where(
            roles.c.business_id == business_id,
            roles.c.id == membership["role_id"],
        )
    ).scalar_one()
    capabilities = frozenset(
        connection.execute(
            select(role_permissions.c.permission_code).where(
                role_permissions.c.business_id == business_id,
                role_permissions.c.role_id == membership["role_id"],
            )
        ).scalars()
    )
    if capability not in capabilities:
        raise HTTPException(403, "Permission denied")
    return Scope(str(membership["id"]), str(role), capabilities, bool(membership["all_stores"]))


def permitted_store_ids(connection: Connection, business_id: str, scope: Scope) -> list[str]:
    query = select(stores.c.id).where(stores.c.business_id == business_id)
    if not scope.all_stores:
        query = query.where(
            stores.c.id.in_(
                select(membership_stores.c.store_id).where(
                    membership_stores.c.business_id == business_id,
                    membership_stores.c.membership_id == scope.membership_id,
                )
            )
        )
    return [str(value) for value in connection.execute(query).scalars()]


class IdentityAccess:
    """Shared authentication boundary for all business modules."""

    def __init__(self, engine: Engine | None, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def database(self) -> Engine:
        if self.engine is None:
            raise HTTPException(503, "Database unavailable")
        with self.engine.connect() as connection:
            safe = connection.execute(
                text("""
                SELECT NOT (r.rolsuper OR r.rolbypassrls OR EXISTS (
                  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE c.relname = 'business' AND n.nspname = current_schema()
                    AND c.relowner = r.oid
                )) FROM pg_roles r WHERE r.rolname = current_user
            """)
            ).scalar()
        if safe is not True:
            raise HTTPException(503, "Configure a non-owner database role with tenant isolation")
        return self.engine

    def origin(self, request: Request) -> None:
        if request.headers.get("origin") not in self.settings.allowed_origins:
            raise HTTPException(403, "Request origin is not allowed")

    def actor_for(self, request: Request, *, mutation: bool = False) -> Actor:
        token = request.cookies.get(COOKIE_NAME, "")
        if not token or len(token) > 128:
            raise HTTPException(401, "Sign in required")
        with self.database().begin() as connection:
            row = (
                connection.execute(
                    select(users.c.id, users.c.email, users.c.display_name)
                    .join(
                        sessions,
                        users.c.id == sessions.c.user_id,
                    )
                    .where(
                        sessions.c.token_digest == token_digest(token),
                        sessions.c.expires_at > datetime.now(UTC),
                        users.c.active.is_(True),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(401, "Session expired or unavailable")
        if mutation:
            self.origin(request)
            if not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode("utf-8"),
                csrf_token(token).encode("ascii"),
            ):
                raise HTTPException(403, "Request verification failed")
        return Actor(UserView.model_validate(dict(row)), token)


def identity_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["identity"])
    access = IdentityAccess(engine, settings)
    database, origin, actor_for = access.database, access.origin, access.actor_for

    def rate_limit(request: Request, email: str, action: str) -> None:
        address = request.client.host if request.client is not None else "unknown"
        now = datetime.now(UTC)
        counts: list[tuple[int, int]] = []
        # Separate committed transaction: failed logins cannot roll back the attempt counters.
        with database().begin() as connection:
            for value, limit in ((f"{action}:ip:{address}", 50), (f"{action}:email:{email}", 5)):
                count = connection.execute(
                    text("""
                    INSERT INTO auth_rate_limit (key, started_at, attempts) VALUES (:key, :now, 1)
                    ON CONFLICT (key) DO UPDATE SET
                      attempts = CASE WHEN auth_rate_limit.started_at < :cutoff
                        THEN 1 ELSE auth_rate_limit.attempts + 1 END,
                      started_at = CASE WHEN auth_rate_limit.started_at < :cutoff
                        THEN :now ELSE auth_rate_limit.started_at END
                    RETURNING attempts
                """),
                    {"key": token_digest(value), "now": now, "cutoff": now - timedelta(minutes=15)},
                ).scalar_one()
                counts.append((int(count), limit))
        if any(count > limit for count, limit in counts):
            raise HTTPException(
                429, "Too many attempts. Try again later.", headers={"Retry-After": "900"}
            )

    def start_session(
        connection: Connection, user: UserView, response: Response, action: str
    ) -> SessionView:
        token = new_session_token()
        connection.execute(
            insert(sessions).values(
                token_digest=token_digest(token),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=settings.session_hours),
            )
        )
        connection.execute(
            insert(security_events).values(id=str(uuid4()), user_id=user.id, action=action)
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return SessionView(user=user, csrf_token=csrf_token(token))

    @router.post("/auth/register", response_model=SessionView, status_code=201)
    def register(payload: Registration, request: Request, response: Response) -> SessionView:
        origin(request)
        email = str(payload.email).lower()
        rate_limit(request, email, "register")
        user = UserView(id=str(uuid4()), email=email, display_name=payload.display_name.strip())
        encoded = hash_password(payload.password)
        try:
            with database().begin() as connection:
                connection.execute(
                    insert(users).values(**user.model_dump(), password_hash=encoded, active=True)
                )
                return start_session(connection, user, response, "auth.register")
        except IntegrityError as error:
            raise HTTPException(409, "Unable to register with those details") from error

    @router.post("/auth/login", response_model=SessionView)
    def login(payload: Credentials, request: Request, response: Response) -> SessionView:
        origin(request)
        email = str(payload.email).lower()
        rate_limit(request, email, "login")
        with database().begin() as connection:
            row = connection.execute(select(users).where(users.c.email == email)).mappings().first()
            encoded = str(row["password_hash"]) if row is not None else None
            verified = verify_password(payload.password, encoded)
            if row is None or not verified or not row["active"]:
                raise HTTPException(401, "Email or password is incorrect")
            return start_session(
                connection, UserView.model_validate(dict(row)), response, "auth.login"
            )

    @router.get("/auth/session", response_model=SessionView)
    def session(request: Request, response: Response) -> SessionView:
        actor = actor_for(request)
        response.headers["Cache-Control"] = "no-store"
        return SessionView(user=actor.user, csrf_token=csrf_token(actor.token))

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> None:
        actor = actor_for(request, mutation=True)
        with database().begin() as connection:
            connection.execute(
                sessions.delete().where(sessions.c.token_digest == token_digest(actor.token))
            )
            connection.execute(
                insert(security_events).values(
                    id=str(uuid4()), user_id=actor.user.id, action="auth.logout"
                )
            )
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
        )

    def business_view(connection: Connection, row: RowMapping, actor: Actor) -> BusinessView:
        scope = scope_for(connection, actor, str(row["id"]), "business.read")
        return BusinessView.model_validate(
            {
                **dict(row),
                "role": scope.role,
                "capabilities": sorted(scope.capabilities),
                "all_stores": scope.all_stores,
            }
        )

    @router.get("/businesses", response_model=list[BusinessView])
    def list_businesses(request: Request) -> list[BusinessView]:
        actor = actor_for(request)
        with database().begin() as connection:
            set_context(connection, actor.user.id)
            rows = (
                connection.execute(
                    select(businesses)
                    .join(memberships, businesses.c.id == memberships.c.business_id)
                    .where(memberships.c.user_id == actor.user.id)
                    .order_by(businesses.c.created_at)
                )
                .mappings()
                .all()
            )
            return [business_view(connection, row, actor) for row in rows]

    @router.post("/businesses", response_model=BusinessView, status_code=201)
    def create_business(payload: BusinessInput, request: Request) -> BusinessView:
        actor = actor_for(request, mutation=True)
        business_id, store_id = str(uuid4()), str(uuid4())
        with database().begin() as connection:
            set_context(connection, actor.user.id, business_id)
            connection.execute(
                insert(businesses).values(
                    id=business_id,
                    name=payload.name,
                    currency="INR",
                    timezone="Asia/Kolkata",
                )
            )
            role_ids: dict[str, str] = {}
            for name, capabilities in ROLE_CAPABILITIES.items():
                role_ids[name] = str(uuid4())
                connection.execute(
                    insert(roles).values(id=role_ids[name], business_id=business_id, name=name)
                )
                connection.execute(
                    insert(role_permissions),
                    [
                        {
                            "business_id": business_id,
                            "role_id": role_ids[name],
                            "permission_code": code,
                        }
                        for code in sorted(capabilities)
                    ],
                )
            connection.execute(
                insert(memberships).values(
                    id=str(uuid4()),
                    business_id=business_id,
                    user_id=actor.user.id,
                    role_id=role_ids["OWNER"],
                    all_stores=True,
                )
            )
            connection.execute(
                insert(stores).values(
                    id=store_id,
                    business_id=business_id,
                    name=payload.store_name,
                    address=payload.store_address,
                )
            )
            audit(
                connection,
                business_id,
                actor.user.id,
                "business.created",
                business_id,
                {"name": payload.name, "currency": "INR", "timezone": "Asia/Kolkata"},
            )
            audit(
                connection,
                business_id,
                actor.user.id,
                "store.created",
                store_id,
                {"name": payload.store_name, "address": payload.store_address},
            )
            row = (
                connection.execute(select(businesses).where(businesses.c.id == business_id))
                .mappings()
                .one()
            )
            return business_view(connection, row, actor)

    @router.get("/businesses/{business_id}/stores", response_model=list[StoreView])
    def list_stores(business_id: UUID, request: Request) -> list[StoreView]:
        actor = actor_for(request)
        with database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "business.read")
            allowed = permitted_store_ids(connection, str(business_id), scope)
            rows = connection.execute(
                select(stores)
                .where(stores.c.business_id == str(business_id), stores.c.id.in_(allowed))
                .order_by(stores.c.name)
            ).mappings()
            return [StoreView.model_validate(dict(row)) for row in rows]

    @router.post("/businesses/{business_id}/stores", response_model=StoreView, status_code=201)
    def create_store(business_id: UUID, payload: StoreInput, request: Request) -> StoreView:
        actor = actor_for(request, mutation=True)
        store = StoreView(id=str(uuid4()), **payload.model_dump())
        try:
            with database().begin() as connection:
                scope = scope_for(connection, actor, str(business_id), "stores.manage")
                if not scope.all_stores:
                    raise HTTPException(403, "Business-wide store authority required")
                connection.execute(
                    insert(stores).values(**store.model_dump(), business_id=str(business_id))
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "store.created",
                    store.id,
                    payload.model_dump(),
                )
                return store
        except IntegrityError as error:
            raise HTTPException(409, "Store name already exists") from error

    @router.get("/businesses/{business_id}/terminals", response_model=list[TerminalView])
    def list_terminals(business_id: UUID, request: Request) -> list[TerminalView]:
        actor = actor_for(request)
        with database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "business.read")
            allowed = permitted_store_ids(connection, str(business_id), scope)
            rows = connection.execute(
                select(terminals)
                .where(
                    terminals.c.business_id == str(business_id), terminals.c.store_id.in_(allowed)
                )
                .order_by(terminals.c.name)
            ).mappings()
            return [TerminalView.model_validate(dict(row)) for row in rows]

    @router.post(
        "/businesses/{business_id}/stores/{store_id}/terminals",
        response_model=TerminalView,
        status_code=201,
    )
    def create_terminal(
        business_id: UUID, store_id: UUID, payload: TerminalInput, request: Request
    ) -> TerminalView:
        actor = actor_for(request, mutation=True)
        terminal = TerminalView(id=str(uuid4()), store_id=str(store_id), name=payload.name)
        try:
            with database().begin() as connection:
                scope = scope_for(connection, actor, str(business_id), "stores.manage")
                if str(store_id) not in permitted_store_ids(connection, str(business_id), scope):
                    raise HTTPException(404, "Store unavailable")
                connection.execute(
                    insert(terminals).values(**terminal.model_dump(), business_id=str(business_id))
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "terminal.created",
                    terminal.id,
                    terminal.model_dump(),
                )
                return terminal
        except IntegrityError as error:
            raise HTTPException(409, "Terminal name already exists in this store") from error

    @router.get("/businesses/{business_id}/members", response_model=list[MemberView])
    def list_members(business_id: UUID, request: Request) -> list[MemberView]:
        actor = actor_for(request)
        with database().begin() as connection:
            scope_for(connection, actor, str(business_id), "staff.manage")
            rows = connection.execute(
                select(
                    memberships.c.id,
                    users.c.email,
                    users.c.display_name,
                    roles.c.name.label("role"),
                    memberships.c.all_stores,
                )
                .join(users, users.c.id == memberships.c.user_id)
                .join(roles, roles.c.id == memberships.c.role_id)
                .where(memberships.c.business_id == str(business_id))
            )
            result: list[MemberView] = []
            for row in rows.mappings():
                store_ids = connection.execute(
                    select(membership_stores.c.store_id).where(
                        membership_stores.c.business_id == str(business_id),
                        membership_stores.c.membership_id == row["id"],
                    )
                ).scalars()
                result.append(
                    MemberView.model_validate(
                        {**dict(row), "store_ids": [str(item) for item in store_ids]}
                    )
                )
            return result

    @router.post("/businesses/{business_id}/members", response_model=MemberView, status_code=201)
    def add_member(business_id: UUID, payload: MemberInput, request: Request) -> MemberView:
        actor = actor_for(request, mutation=True)
        try:
            with database().begin() as connection:
                scope = scope_for(connection, actor, str(business_id), "staff.manage")
                if not ROLE_CAPABILITIES[payload.role].issubset(scope.capabilities):
                    raise HTTPException(403, "Cannot grant permissions you do not hold")
                row = (
                    connection.execute(
                        select(users.c.id, users.c.email, users.c.display_name).where(
                            users.c.email == str(payload.email).lower(), users.c.active.is_(True)
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise HTTPException(422, "Staff member must register an account first")
                all_stores = payload.role in {"OWNER", "ADMIN", "ACCOUNTANT"}
                requested = {str(item) for item in payload.store_ids}
                allowed = set(permitted_store_ids(connection, str(business_id), scope))
                if not requested.issubset(allowed) or (not all_stores and not requested):
                    raise HTTPException(
                        422, "Choose at least one permitted store for a store-scoped role"
                    )
                if all_stores and requested:
                    raise HTTPException(
                        422, "Business-wide roles do not take individual store scopes"
                    )
                role_id = connection.execute(
                    select(roles.c.id).where(
                        roles.c.business_id == str(business_id), roles.c.name == payload.role
                    )
                ).scalar_one()
                member_id = str(uuid4())
                connection.execute(
                    insert(memberships).values(
                        id=member_id,
                        business_id=str(business_id),
                        user_id=row["id"],
                        role_id=role_id,
                        all_stores=all_stores,
                    )
                )
                if requested:
                    connection.execute(
                        insert(membership_stores),
                        [
                            {
                                "business_id": str(business_id),
                                "membership_id": member_id,
                                "store_id": item,
                            }
                            for item in sorted(requested)
                        ],
                    )
                after: dict[str, object] = {
                    "user_id": str(row["id"]),
                    "role": payload.role,
                    "store_ids": sorted(requested),
                    "all_stores": all_stores,
                }
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "membership.created",
                    member_id,
                    after,
                )
                return MemberView.model_validate(
                    {
                        **dict(row),
                        "id": member_id,
                        "role": payload.role,
                        "all_stores": all_stores,
                        "store_ids": sorted(requested),
                    }
                )
        except IntegrityError as error:
            raise HTTPException(409, "Staff member already belongs to this business") from error

    @router.get("/businesses/{business_id}/audit", response_model=list[AuditView])
    def list_audit(business_id: UUID, request: Request, limit: int = 50) -> list[AuditView]:
        if not 1 <= limit <= 100:
            raise HTTPException(422, "Limit must be between 1 and 100")
        actor = actor_for(request)
        with database().begin() as connection:
            scope = scope_for(connection, actor, str(business_id), "audit.read")
            # Business events may include other stores; store-only roles cannot read them.
            if not scope.all_stores:
                raise HTTPException(403, "Business-wide audit authority required")
            rows = connection.execute(
                select(audit_logs)
                .where(audit_logs.c.business_id == str(business_id))
                .order_by(audit_logs.c.created_at.desc(), audit_logs.c.id)
                .limit(limit)
            ).mappings()
            return [AuditView.model_validate(dict(row)) for row in rows]

    return router
