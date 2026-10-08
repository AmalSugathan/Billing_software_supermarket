"""Real PostgreSQL finance tests with explicitly synthetic money evidence."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_identity import business, register

from supermarket.finance_models import accounts, expenses

pytestmark = pytest.mark.database


def post(client, path, headers, data, key=None):
    return client.post(path, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=data)


def setup(client):
    headers = register(client)
    base = "/api/v1/businesses/" + business(client, headers)
    store = client.get(base + "/stores").json()[0]["id"]
    path = base + f"/stores/{store}"
    terminal = client.post(
        path + "/terminals", headers=headers, json={"name": "DEMO drawer"}
    ).json()["id"]
    return headers, base, path, terminal


def account(client, path, headers, terminal=None, amount="1000.00", name="DEMO cash"):
    result = post(
        client,
        path + "/accounts",
        headers,
        {
            "name": name,
            "kind": "cash" if terminal else "bank",
            "terminal_id": terminal,
            "opening_amount": amount,
            "reason": "Synthetic opening funds",
            "confirmed": True,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def opening(account_id, **changes):
    return {
        "account_id": account_id,
        "opening_cash": "1000.00",
        "reason": "Synthetic counted opening cash",
        "confirmed": True,
        **changes,
    }


def expense(item_id, session_id=None, **changes):
    return {
        "account_id": item_id,
        "cash_session_id": session_id,
        "reference": "DEMO-EXPENSE-001",
        "expense_date": "2026-10-08",
        "category": "delivery",
        "description": "Synthetic delivery expense",
        "amount": "50.00",
        "confirmed": True,
        **changes,
    }


def test_accounts_expenses_reversals_and_cash_variance_are_distinct(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        cash = account(client, path, headers, terminal)
        bank = account(client, path, headers, amount="2000.00", name="DEMO bank")
        opened = post(client, path + "/cash-sessions", headers, opening(cash["id"]))
        assert opened.status_code == 201, opened.text
        session = opened.json()["id"]
        key = str(uuid4())
        data = expense(cash["id"], session)
        paid = post(client, path + "/expenses", headers, data, key)
        assert paid.status_code == 201, paid.text
        assert post(client, path + "/expenses", headers, data, key).json() == paid.json()
        assert (
            post(client, path + "/expenses", headers, {**data, "amount": "51"}, key).status_code
            == 409
        )
        assert post(client, path + "/expenses", headers, data).status_code == 409
        assert (
            post(
                client, path + "/expenses", headers, {**data, "reference": " demo-expense-001 "}
            ).status_code
            == 409
        )
        assert (
            post(
                client,
                path + "/expenses",
                headers,
                expense(bank["id"], reference="DEMO-BANK-001", amount="500.00"),
            ).status_code
            == 201
        )
        receipt = {
            "account_id": cash["id"],
            "cash_session_id": session,
            "kind": "receipt",
            "amount": "100.00",
            "reason": "Synthetic owner funds, not sales",
            "confirmed": True,
        }
        result = post(client, path + "/money-movements", headers, receipt)
        assert result.status_code == 201, result.text
        reverse = {
            "reason": "Synthetic delivery refund received",
            "cash_session_id": session,
            "confirmed": True,
        }
        reversed_result = post(
            client, path + f"/expenses/{paid.json()['id']}/reverse", headers, reverse
        )
        assert reversed_result.status_code == 201, reversed_result.text
        assert (
            post(
                client, path + f"/expenses/{paid.json()['id']}/reverse", headers, reverse
            ).status_code
            == 409
        )
        assert client.get(path + "/expenses").json()[1]["reversed"]
        close = {
            "expected_cash": "1100.00",
            "actual_cash": "1090.00",
            "reason": "Synthetic shortage requiring review",
            "confirmed": True,
        }
        close_key = str(uuid4())
        closed = post(client, path + f"/cash-sessions/{session}/close", headers, close, close_key)
        assert closed.status_code == 201, closed.text
        assert Decimal(closed.json()["variance"]) == -10
        assert (
            post(client, path + f"/cash-sessions/{session}/close", headers, close, close_key).json()
            == closed.json()
        )
        balances = {
            row["id"]: Decimal(row["balance"]) for row in client.get(path + "/accounts").json()
        }
        assert balances == {cash["id"]: Decimal("1090.00"), bank["id"]: Decimal("1500.00")}
        cash_view = client.get(path + "/cash-sessions").json()[0]
        assert cash_view["closed"] and Decimal(cash_view["expected_cash"]) == 1100
        assert Decimal(cash_view["actual_cash"]) == 1090
        kinds = {row["kind"] for row in client.get(path + "/money-movements").json()}
        assert kinds == {"opening", "expense", "expense_reversal", "receipt", "cash_variance"}
        assert (
            post(client, path + "/expenses", headers, {**data, "reference": "CLOSED"}).status_code
            == 409
        )
        assert (
            post(
                client,
                path + "/cash-sessions",
                headers,
                opening(cash["id"], opening_cash="1090.00"),
            ).status_code
            == 201
        )
        assert any(
            row["action"] == "cash.session_closed" for row in client.get(base + "/audit").json()
        )
        for table in (
            "financial_account",
            "cash_session",
            "financial_movement",
            "expense",
            "expense_reversal",
            "cash_closing",
        ):
            with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
        with postgres_case.runtime.begin() as connection:
            assert connection.execute(select(accounts)).all() == []


def test_account_validation_replay_confirmation_and_cash_session_rules(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        data = {
            "name": "DEMO drawer",
            "kind": "cash",
            "terminal_id": terminal,
            "opening_amount": "1000.00",
            "reason": "Synthetic funds",
            "confirmed": True,
        }
        key = str(uuid4())
        first = post(client, path + "/accounts", headers, data, key)
        assert first.status_code == 201
        assert post(client, path + "/accounts", headers, data, key).json() == first.json()
        assert (
            post(
                client, path + "/accounts", headers, {**data, "opening_amount": "999"}, key
            ).status_code
            == 409
        )
        assert post(client, path + "/accounts", headers, data).status_code == 409
        for change in (
            {"confirmed": False},
            {"opening_amount": 10.1},
            {"opening_amount": "NaN"},
            {"terminal_id": None},
            {"kind": "bank"},
        ):
            assert post(client, path + "/accounts", headers, {**data, **change}).status_code == 422
        assert (
            post(
                client, path + "/accounts", headers, {**data, "terminal_id": str(uuid4())}
            ).status_code
            == 404
        )
        cash_id = first.json()["id"]
        assert (
            post(
                client, path + "/cash-sessions", headers, opening(cash_id, opening_cash="999")
            ).status_code
            == 409
        )
        session_key = str(uuid4())
        opened = post(client, path + "/cash-sessions", headers, opening(cash_id), session_key)
        assert opened.status_code == 201
        assert (
            post(client, path + "/cash-sessions", headers, opening(cash_id), session_key).json()[
                "id"
            ]
            == opened.json()["id"]
        )
        assert post(client, path + "/cash-sessions", headers, opening(cash_id)).status_code == 409
        bank = account(client, path, headers, name="DEMO bank")
        assert (
            post(client, path + "/cash-sessions", headers, opening(bank["id"])).status_code == 422
        )
        session = opened.json()["id"]
        close = {
            "expected_cash": "999",
            "actual_cash": "1000",
            "reason": "Synthetic counted close",
            "confirmed": True,
        }
        assert (
            post(client, path + f"/cash-sessions/{session}/close", headers, close).status_code
            == 409
        )
        correct = post(
            client,
            path + f"/cash-sessions/{session}/close",
            headers,
            {**close, "expected_cash": "1000"},
        )
        assert correct.status_code == 201, correct.text
        assert correct.json()["movement_id"] is None
        assert (
            post(
                client,
                path + f"/cash-sessions/{session}/close",
                headers,
                {**close, "expected_cash": "1000"},
            ).status_code
            == 409
        )
        assert client.get(path + "/expenses", params={"limit": 201}).status_code == 422
        assert client.get(path + "/money-movements", params={"offset": 100001}).status_code == 422
        assert client.post(path + "/accounts", headers=headers, json=data).status_code == 422
        assert (
            post(
                client, path + "/expenses", {"Origin": "http://testserver"}, expense(bank["id"])
            ).status_code
            == 403
        )


def test_expense_money_validation_and_non_cash_sessions(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        cash = account(client, path, headers, terminal)
        bank = account(client, path, headers, name="DEMO bank")
        assert post(client, path + "/expenses", headers, expense(cash["id"])).status_code == 422
        for change in (
            {"amount": "0"},
            {"amount": "1001"},
            {"amount": 50.0},
            {"account_id": str(uuid4())},
            {"cash_session_id": str(uuid4())},
        ):
            assert post(
                client, path + "/expenses", headers, expense(bank["id"], **change)
            ).status_code in {404, 422}
        session = post(client, path + "/cash-sessions", headers, opening(cash["id"])).json()["id"]
        other_cash = account(
            client,
            path,
            headers,
            client.post(path + "/terminals", headers=headers, json={"name": "Other"}).json()["id"],
            name="Other drawer",
        )
        assert (
            post(
                client, path + "/expenses", headers, expense(other_cash["id"], session)
            ).status_code
            == 409
        )
        for kind in ("receipt", "withdrawal"):
            assert (
                post(
                    client,
                    path + "/money-movements",
                    headers,
                    {
                        "account_id": bank["id"],
                        "kind": kind,
                        "amount": "0",
                        "reason": "Synthetic",
                        "confirmed": True,
                    },
                ).status_code
                == 422
            )
        for changes in (
            {"account_id": cash["id"], "cash_session_id": None},
            {"account_id": bank["id"], "cash_session_id": session},
            {"amount": "1001", "kind": "withdrawal"},
        ):
            result = post(
                client,
                path + "/money-movements",
                headers,
                {
                    "account_id": bank["id"],
                    "kind": "receipt",
                    "amount": "10",
                    "reason": "Synthetic funds",
                    "confirmed": True,
                    **changes,
                },
            )
            assert result.status_code == 422
        assert (
            post(
                client,
                path + "/money-movements",
                headers,
                {
                    "account_id": bank["id"],
                    "kind": "withdrawal",
                    "amount": "10",
                    "reason": "Synthetic owner withdrawal",
                    "confirmed": True,
                },
            ).status_code
            == 201
        )
        assert (
            post(
                client,
                path + "/expenses/" + str(uuid4()) + "/reverse",
                headers,
                {"reason": "Synthetic", "confirmed": True},
            ).status_code
            == 404
        )
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(func.count()).select_from(expenses)).scalar() == 0


def test_concurrent_expense_retries_and_overspending_are_safe(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        bank = account(client, path, headers, name="DEMO bank")
        data = expense(bank["id"], amount="800")
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda _: post(client, path + "/expenses", headers, data, key), range(2))
            )
        assert all(response.status_code == 201 for response in responses), [
            r.text for r in responses
        ]
        assert responses[0].json()["id"] == responses[1].json()["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda ref: post(
                        client,
                        path + "/expenses",
                        headers,
                        expense(bank["id"], amount="150", reference=ref),
                    ),
                    ["DEMO-A", "DEMO-B"],
                )
            )
        assert sorted(result.status_code for result in results) == [201, 422]
        assert Decimal(client.get(path + "/accounts").json()[0]["balance"]) == 50


def test_finance_permissions_stores_and_cashier_privacy(postgres_case: PostgreSQLCase):
    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as second,
    ):
        headers, base, path, terminal = setup(owner)
        cash = account(owner, path, headers, terminal)
        bank = account(owner, path, headers, name="DEMO bank")
        cash_headers = register(cashier, "cash@example.com")
        second_headers = register(second, "second@example.com")
        store = path.split("/stores/")[1]
        for email in ("cash@example.com", "second@example.com"):
            assert (
                owner.post(
                    base + "/members",
                    headers=headers,
                    json={"email": email, "role": "CASHIER", "store_ids": [store]},
                ).status_code
                == 201
            )
        masked = {row["kind"]: row for row in cashier.get(path + "/accounts").json()}
        assert masked["bank"]["balance"] is None and masked["bank"]["opening_amount"] is None
        assert masked["cash"]["balance"] == "1000.00"
        for endpoint in ("expenses", "money-movements"):
            assert cashier.get(path + "/" + endpoint).status_code == 403
        assert (
            post(cashier, path + "/expenses", cash_headers, expense(bank["id"])).status_code == 403
        )
        session_key = str(uuid4())
        opened = post(
            cashier, path + "/cash-sessions", cash_headers, opening(cash["id"]), session_key
        )
        assert opened.status_code == 201, opened.text
        assert second.get(path + "/cash-sessions").json() == []
        assert (
            post(
                second, path + "/cash-sessions", second_headers, opening(cash["id"]), session_key
            ).status_code
            == 403
        )
        close = {
            "expected_cash": "1000",
            "actual_cash": "1000",
            "reason": "Synthetic count",
            "confirmed": True,
        }
        assert (
            post(
                second,
                path + "/cash-sessions/" + opened.json()["id"] + "/close",
                second_headers,
                close,
            ).status_code
            == 403
        )
        other = owner.post(base + "/stores", headers=headers, json={"name": "Other store"}).json()[
            "id"
        ]
        other_path = base + "/stores/" + other
        assert cashier.get(other_path + "/accounts").status_code == 404
        assert (
            post(owner, other_path + "/expenses", headers, expense(bank["id"])).status_code == 404
        )
        assert (
            post(
                owner,
                other_path + "/cash-sessions/" + opened.json()["id"] + "/close",
                headers,
                close,
            ).status_code
            == 404
        )
        other_business = "/api/v1/businesses/" + business(owner, headers, "Other tenant")
        assert cashier.get(other_business + "/stores/" + store + "/accounts").status_code == 404
        assert (
            post(
                owner, path + "/cash-sessions/" + opened.json()["id"] + "/close", headers, close
            ).status_code
            == 201
        )


def test_expense_audit_failure_rolls_back_payment_and_allows_retry(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        bank = account(client, path, headers, name="DEMO bank")
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text("""CREATE FUNCTION fail_expense_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.action = 'expense.posted' THEN
                RAISE EXCEPTION 'Synthetic audit failure';
              END IF; RETURN NEW; END; $$""")
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_failure BEFORE INSERT ON audit_log FOR EACH "
                    "ROW EXECUTE FUNCTION fail_expense_audit()"
                )
            )
        key = str(uuid4())
        assert (
            post(client, path + "/expenses", headers, expense(bank["id"]), key).status_code == 503
        )
        assert client.get(path + "/expenses").json() == []
        assert Decimal(client.get(path + "/accounts").json()[0]["balance"]) == 1000
        with postgres_case.admin.begin() as connection:
            connection.execute(text("DROP TRIGGER test_failure ON audit_log"))
            connection.execute(text("DROP FUNCTION fail_expense_audit()"))
        assert (
            post(client, path + "/expenses", headers, expense(bank["id"]), key).status_code == 201
        )


def test_close_and_expense_race_never_changes_closed_cash(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        cash = account(client, path, headers, terminal)
        session = post(client, path + "/cash-sessions", headers, opening(cash["id"])).json()["id"]
        commands = [
            (path + "/expenses", expense(cash["id"], session)),
            (
                path + "/cash-sessions/" + session + "/close",
                {
                    "expected_cash": "1000",
                    "actual_cash": "1000",
                    "reason": "Synthetic close",
                    "confirmed": True,
                },
            ),
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda command: post(client, command[0], headers, command[1]), commands)
            )
        assert sorted(result.status_code for result in results) == [201, 409]
        current = client.get(path + "/cash-sessions").json()[0]
        if current["closed"]:
            assert Decimal(current["actual_cash"]) == 1000
            assert client.get(path + "/expenses").json() == []
        else:
            assert Decimal(current["expected_cash"]) == 950


def test_account_opening_failure_is_atomic_and_funds_cannot_overflow(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        bank = account(client, path, headers, name="DEMO max", amount="999999999999.99")
        assert (
            post(
                client,
                path + "/money-movements",
                headers,
                {
                    "account_id": bank["id"],
                    "kind": "receipt",
                    "amount": "0.01",
                    "reason": "Synthetic overflow",
                    "confirmed": True,
                },
            ).status_code
            == 422
        )
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text("""CREATE FUNCTION reject_test_opening() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.kind = 'opening' THEN
                RAISE EXCEPTION 'Synthetic opening failure';
              END IF; RETURN NEW; END; $$""")
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_opening BEFORE INSERT ON financial_movement "
                    "FOR EACH ROW EXECUTE FUNCTION reject_test_opening()"
                )
            )
        data = {
            "name": "DEMO failing",
            "kind": "bank",
            "opening_amount": "10",
            "reason": "Synthetic funds",
            "confirmed": True,
        }
        assert post(client, path + "/accounts", headers, data).status_code == 503
        assert len(client.get(path + "/accounts").json()) == 1
        with postgres_case.admin.begin() as connection:
            assert connection.execute(select(func.count()).select_from(accounts)).scalar() == 1
            connection.execute(text("DROP TRIGGER test_opening ON financial_movement"))
            connection.execute(text("DROP FUNCTION reject_test_opening()"))
        assert post(client, path + "/accounts", headers, data).status_code == 201


def test_existing_cashier_roles_receive_new_permission_on_upgrade(postgres_case: PostgreSQLCase):
    from alembic import command
    from alembic.config import Config

    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = postgres_case.admin_url
        command.downgrade(configuration, "0004_purchases")
        assert "cash.sessions" not in client.get("/api/v1/businesses").json()[0]["capabilities"]
        command.upgrade(configuration, "head")
        assert "cash.sessions" in client.get("/api/v1/businesses").json()[0]["capabilities"]


def test_opening_cash_difference_needs_owner_review_and_closed_drawer(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        cash = account(client, path, headers, terminal)
        endpoint = path + "/accounts/" + cash["id"] + "/opening-variance"
        data = {
            "expected_cash": "1000",
            "actual_cash": "990",
            "reason": "Synthetic opening shortage reviewed",
            "confirmed": True,
        }
        key = str(uuid4())
        adjusted = post(client, endpoint, headers, data, key)
        assert adjusted.status_code == 201, adjusted.text
        assert Decimal(adjusted.json()["amount"]) == -10
        assert adjusted.json()["kind"] == "opening_variance"
        assert post(client, endpoint, headers, data, key).json() == adjusted.json()
        assert post(client, endpoint, headers, data).status_code == 409
        assert (
            post(
                client, endpoint, headers, {**data, "expected_cash": "990", "actual_cash": "990"}
            ).status_code
            == 422
        )
        opened = post(
            client, path + "/cash-sessions", headers, opening(cash["id"], opening_cash="990")
        )
        assert opened.status_code == 201, opened.text
        assert (
            post(
                client, endpoint, headers, {**data, "expected_cash": "990", "actual_cash": "980"}
            ).status_code
            == 409
        )
        bank = account(client, path, headers, name="DEMO bank")
        assert (
            post(
                client, path + "/accounts/" + bank["id"] + "/opening-variance", headers, data
            ).status_code
            == 422
        )
