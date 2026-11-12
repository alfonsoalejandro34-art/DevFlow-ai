import pytest

from inventory.pricing import apply_discount, bulk_unit_price, order_total


def test_discount_reduces_price():
    assert apply_discount(200.0, 25) == 150.0


def test_zero_discount_keeps_price():
    assert apply_discount(19.99, 0) == 19.99


def test_discount_rejects_invalid_percent():
    with pytest.raises(ValueError):
        apply_discount(10.0, 120)


def test_no_bulk_discount_below_threshold():
    assert bulk_unit_price(10.0, 9) == 10.0


def test_bulk_discount_starts_at_ten_units():
    assert bulk_unit_price(10.0, 10) == 9.5


def test_large_bulk_discount():
    assert bulk_unit_price(10.0, 150) == 8.5


def test_order_total_with_tax():
    assert order_total([(10.0, 2), (5.0, 1)], tax_rate=0.1) == 27.5
