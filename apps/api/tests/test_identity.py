from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from supermarket.config import Settings
from supermarket.identity import COOKIE_NAME, set_context
from supermarket.identity_models import (
    audit_logs,
    memberships,
    security_events,
    sessions,
    stores,
    users,
)
from supermarket.main import create_app
from supermarket.security import token_digest

pytestmark = pytest.mark.database
ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "an-isolated-test-password"


def register(
    client: TestClient, email: str = "owner@example.com", name: str = "Owner"
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        headers=ORIGIN,
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    assert response.status_code == 201, response.text
    return {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def business(client: TestClient, headers: dict[str, str], name: str = "Test store") -> str:
    response = client.post(
        "/api/v1/businesses",
        headers=headers,
        json={"name": name, "store_name": "Main", "store_address": "Test fixture"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_account_sessions_validation_and_logout(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as client:
        assert client.get("/api/v1/auth/session").status_code == 401
        headers = register(client)
        token = client.cookies.get(COOKIE_NAME)
        assert token
        assert client.get("/api/v1/auth/session").json()["user"]["email"] == "owner@example.com"
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(sessions.c.token_digest)).scalar_one() == token_digest(
                token
            )
            assert connection.execute(select(users.c.password_hash)).scalar_one() != PASSWORD
        assert client.post("/api/v1/auth/logout", headers=ORIGIN).status_code == 403
        invalid_csrf = {**ORIGIN, "X-CSRF-Token": "invalid"}
        assert client.post("/api/v1/auth/logout", headers=invalid_csrf).status_code == 403
        assert (
            client.post(
                "/api/v1/auth/logout", headers={**headers, "Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/api/v1/auth/session").status_code == 401
        client.cookies.clear()
        login = client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "OWNER@example.com", "password": PASSWORD},
        )
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        assert (
            client.post(
                "/api/v1/auth/register",
                headers=ORIGIN,
                json={
                    "email": "owner@example.com",
                    "password": PASSWORD,
                    "display_name": "Duplicate",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "owner@example.com", "password": PASSWORD, "role": "OWNER"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "owner@example.com", "password": "short"},
            ).status_code
            == 422
        )


def test_session_expiry_disabled_user_and_oversize_cookie(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as client:
        register(client)
        token = client.cookies.get(COOKIE_NAME)
        with postgres_case.admin.begin() as connection:
            connection.execute(
                sessions.update().values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert client.get("/api/v1/auth/session").status_code == 401
        response = client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "owner@example.com", "password": PASSWORD},
        )
        assert response.status_code == 200
        with postgres_case.admin.begin() as connection:
            connection.execute(users.update().values(active=False))
        assert client.get("/api/v1/auth/session").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "owner@example.com", "password": PASSWORD},
            ).status_code
            == 401
        )
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, "x" * 200)
        assert client.get("/api/v1/auth/session").status_code == 401
        assert token


def test_failed_login_throttle_survives_rollback_and_resets(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as client:
        for _ in range(5):
            assert (
                client.post(
                    "/api/v1/auth/login",
                    headers=ORIGIN,
                    json={"email": "absent@example.com", "password": PASSWORD},
                ).status_code
                == 401
            )
        response = client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "absent@example.com", "password": PASSWORD},
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "900"
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text("UPDATE auth_rate_limit SET started_at = :old"),
                {"old": datetime.now(UTC) - timedelta(minutes=16)},
            )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "absent@example.com", "password": PASSWORD},
            ).status_code
            == 401
        )


def test_business_store_terminal_and_atomic_audit(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as client:
        headers = register(client)
        tenant = business(client, headers)
        base = f"/api/v1/businesses/{tenant}"
        row = client.get("/api/v1/businesses").json()[0]
        assert row["role"] == "OWNER" and row["currency"] == "INR" and row["all_stores"]
        assert (
            client.post(
                base + "/stores", headers=headers, json={"name": "Main", "address": ""}
            ).status_code
            == 409
        )
        second = client.post(
            base + "/stores", headers=headers, json={"name": "Second", "address": ""}
        )
        assert second.status_code == 201
        assert len(client.get(base + "/stores").json()) == 2
        store_id = second.json()["id"]
        path = base + f"/stores/{store_id}/terminals"
        assert client.post(path, headers=headers, json={"name": "Counter 1"}).status_code == 201
        assert client.post(path, headers=headers, json={"name": "Counter 1"}).status_code == 409
        assert len(client.get(base + "/terminals").json()) == 1
        assert (
            client.post(
                base + f"/stores/{uuid4()}/terminals", headers=headers, json={"name": "Invalid"}
            ).status_code
            == 404
        )
        events = client.get(base + "/audit").json()
        assert len(events) == 4  # Duplicate/conflicting requests must not append events.
        assert all(event["source"] == "human" for event in events)
        assert client.get(base + "/audit?limit=0").status_code == 422
        assert len(client.get(base + "/audit?limit=1").json()) == 1


def test_cross_business_api_and_database_isolation(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as first, postgres_case.client() as second:
        first_headers, second_headers = register(first), register(second, "other@example.com")
        first_id, second_id = (
            business(first, first_headers),
            business(second, second_headers, "Other"),
        )
        first_user = first.get("/api/v1/auth/session").json()["user"]["id"]
        second_user = second.get("/api/v1/auth/session").json()["user"]["id"]
        assert len(first.get("/api/v1/businesses").json()) == 1
        for suffix in ("stores", "terminals", "members", "audit"):
            assert first.get(f"/api/v1/businesses/{second_id}/{suffix}").status_code == 404
        assert (
            first.post(
                f"/api/v1/businesses/{second_id}/stores",
                headers=first_headers,
                json={"name": "Wrong tenant"},
            ).status_code
            == 404
        )
        with postgres_case.runtime.begin() as connection:
            assert not connection.execute(select(stores)).all()  # Default deny.
            set_context(connection, first_user, first_id)
            assert {
                str(value) for value in connection.execute(select(stores.c.business_id)).scalars()
            } == {first_id}
        with postgres_case.runtime.begin() as connection:
            assert not connection.execute(select(stores)).all()  # No pooled context leak.
        barrier = Barrier(2)

        def concurrent_scope(user_id: str, tenant_id: str) -> set[str]:
            with postgres_case.runtime.begin() as connection:
                set_context(connection, user_id, tenant_id)
                barrier.wait(timeout=5)
                return {
                    str(item) for item in connection.execute(select(stores.c.business_id)).scalars()
                }

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_result = executor.submit(concurrent_scope, first_user, first_id)
            second_result = executor.submit(concurrent_scope, second_user, second_id)
            assert first_result.result(timeout=10) == {first_id}
            assert second_result.result(timeout=10) == {second_id}
        with pytest.raises(DBAPIError), postgres_case.runtime.begin() as connection:
            set_context(connection, first_user, first_id)
            connection.execute(
                insert(stores).values(
                    id=str(uuid4()), business_id=second_id, name="Foreign", address=""
                )
            )
        with postgres_case.admin.connect() as connection:
            role_id = connection.execute(
                select(memberships.c.role_id).where(memberships.c.business_id == second_id)
            ).scalar_one()
        with pytest.raises(IntegrityError), postgres_case.runtime.begin() as connection:
            set_context(connection, first_user, first_id)
            connection.execute(
                insert(memberships).values(
                    id=str(uuid4()),
                    business_id=first_id,
                    user_id=second_user,
                    role_id=role_id,
                    all_stores=True,
                )
            )


def test_staff_store_scopes_permissions_and_duplicate_membership(
    postgres_case: PostgreSQLCase,
) -> None:
    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as manager,
    ):
        headers = register(owner)
        tenant = business(owner, headers)
        base = f"/api/v1/businesses/{tenant}"
        store_id = owner.get(base + "/stores").json()[0]["id"]
        owner.post(base + "/stores", headers=headers, json={"name": "Private store"})
        cashier_headers = register(cashier, "cashier@example.com", "Cashier")
        manager_headers = register(manager, "manager@example.com", "Manager")
        payload = {"email": "cashier@example.com", "role": "CASHIER", "store_ids": [store_id]}
        assert owner.post(base + "/members", headers=headers, json=payload).status_code == 201
        assert owner.post(base + "/members", headers=headers, json=payload).status_code == 409
        assert (
            owner.post(
                base + "/members",
                headers=headers,
                json={
                    "email": "manager@example.com",
                    "role": "STORE_MANAGER",
                    "store_ids": [store_id],
                },
            ).status_code
            == 201
        )
        assert len(cashier.get(base + "/stores").json()) == 1
        assert cashier.get(base + "/members").status_code == 403
        assert cashier.get(base + "/audit").status_code == 403
        assert (
            cashier.post(
                base + "/stores", headers=cashier_headers, json={"name": "Unauthorized"}
            ).status_code
            == 403
        )
        assert manager.get(base + "/audit").status_code == 403
        assert (
            manager.post(
                base + "/stores", headers=manager_headers, json={"name": "Unauthorized"}
            ).status_code
            == 403
        )
        assert (
            manager.post(
                base + f"/stores/{store_id}/terminals",
                headers=manager_headers,
                json={"name": "Allowed counter"},
            ).status_code
            == 201
        )
        assert len(owner.get(base + "/members").json()) == 3
        assert (
            owner.post(
                base + "/members",
                headers=headers,
                json={"email": "absent@example.com", "role": "CASHIER", "store_ids": [store_id]},
            ).status_code
            == 422
        )


def test_member_scope_validation_and_no_admin_privilege_escalation(
    postgres_case: PostgreSQLCase,
) -> None:
    with (
        postgres_case.client() as owner,
        postgres_case.client() as admin,
        postgres_case.client() as candidate,
    ):
        owner_headers = register(owner)
        tenant = business(owner, owner_headers)
        base = f"/api/v1/businesses/{tenant}"
        store_id = owner.get(base + "/stores").json()[0]["id"]
        admin_headers = register(admin, "admin@example.com")
        register(candidate, "candidate@example.com")
        assert (
            owner.post(
                base + "/members",
                headers=owner_headers,
                json={"email": "admin@example.com", "role": "ADMIN"},
            ).status_code
            == 201
        )
        for role in ("OWNER", "ACCOUNTANT", "CASHIER"):
            assert (
                admin.post(
                    base + "/members",
                    headers=admin_headers,
                    json={
                        "email": "candidate@example.com",
                        "role": role,
                        "store_ids": [store_id] if role == "CASHIER" else [],
                    },
                ).status_code
                == 403
            )
        assert (
            owner.post(
                base + "/members",
                headers=owner_headers,
                json={"email": "candidate@example.com", "role": "CASHIER"},
            ).status_code
            == 422
        )
        assert (
            owner.post(
                base + "/members",
                headers=owner_headers,
                json={
                    "email": "candidate@example.com",
                    "role": "CASHIER",
                    "store_ids": [str(uuid4())],
                },
            ).status_code
            == 422
        )
        assert (
            owner.post(
                base + "/members",
                headers=owner_headers,
                json={
                    "email": "candidate@example.com",
                    "role": "ACCOUNTANT",
                    "store_ids": [store_id],
                },
            ).status_code
            == 422
        )
        assert (
            owner.post(
                base + "/members",
                headers=owner_headers,
                json={"email": "candidate@example.com", "role": "ACCOUNTANT"},
            ).status_code
            == 201
        )


def test_audit_immutability_and_unsafe_database_role(postgres_case: PostgreSQLCase) -> None:
    with postgres_case.client() as client:
        headers = register(client)
        business(client, headers)
        for table in (audit_logs, security_events):
            with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
                connection.execute(table.delete())
    with TestClient(
        create_app(Settings(allowed_origins=("http://testserver",)), postgres_case.admin)
    ) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"email": "owner@example.com", "password": PASSWORD},
        )
        assert response.status_code == 503
        assert "tenant isolation" in response.json()["detail"]
