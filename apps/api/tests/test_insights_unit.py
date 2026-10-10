from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from supermarket.insights import Activity, Snapshot, briefing, reporting_window, summarize


def test_indian_calendar_boundaries_and_invalid_periods():
    start, end = reporting_window(date(2026, 10, 10), date(2026, 10, 10), date(2026, 10, 10))
    assert start == datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 10, 10, 18, 30, tzinfo=UTC)
    for first, last in [
        (date(2026, 10, 11), date(2026, 10, 10)),
        (date(2026, 10, 10), date(2026, 10, 11)),
        (date(2024, 1, 1), date(2026, 10, 10)),
    ]:
        with pytest.raises(HTTPException) as error:
            reporting_window(first, last, date(2026, 10, 10))
        assert error.value.status_code == 422


def test_exact_costs_round_after_aggregation_and_do_not_count_opening_as_profit():
    rows = [
        Activity(
            revenue=Decimal("0.03"),
            cogs=Decimal("0.005"),
            opening_funds=Decimal("1000"),
            money_in=Decimal("0.03"),
        ),
        Activity(
            revenue=Decimal("0.03"),
            cogs=Decimal("0.005"),
            expenses=Decimal("0.02"),
            money_out=Decimal("0.02"),
        ),
    ]
    metrics = summarize(rows)
    assert metrics.estimated_gross_profit == Decimal("0.05")
    assert metrics.after_recorded_expenses == Decimal("0.03")
    assert metrics.net_cash_flow == Decimal("0.01")
    assert metrics.opening_funds == Decimal("1000.00")
    assert metrics.model_dump(mode="json")["revenue"] == "0.06"


def test_briefing_preserves_opposing_variances_and_never_invents_predictions():
    metrics = summarize(
        [
            Activity(
                cash_variance=Decimal("0"),
                variance_absolute=Decimal("20"),
                variance_count=2,
                revenue=Decimal("-10"),
                sale_count=1,
            )
        ]
    )
    snapshot = Snapshot(
        recorded_balance=0,
        supplier_outstanding=20,
        inventory_value=10,
        low_stock_count=1,
        expiring_batch_count=2,
        expired_batch_count=1,
        stocked_product_count=1,
    )
    items = briefing(metrics, snapshot)
    assert items[0].title == "Review expired stock"
    assert any("2 recorded differences" in item.detail for item in items)
    assert any("negative estimated gross profit" in item.title for item in items)
    empty = briefing(summarize([]), snapshot.model_copy(update={"expired_batch_count": 0}))
    assert any("No posted sales" in item.title for item in empty)
