from decimal import Decimal

import pytest

from supermarket.catalog import decimal_text, normalize


def test_decimal_strings_are_exact_and_floats_are_rejected():
    assert decimal_text("18.50", 2) * decimal_text("1.125", 3) == Decimal("20.81250")
    for value in (18.50, "1e3", "NaN", "1.005", "-1", "1000000000000", None):
        with pytest.raises(ValueError):
            decimal_text(value, 2)
    assert normalize("  GOOD DAY / Cashew 100Ｇ ") == "good day cashew 100g"
