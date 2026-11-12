import pytest

from inventory.stock import InsufficientStock, Inventory


def test_add_and_level():
    inv = Inventory()
    inv.add("WIDGET", 5)
    assert inv.level("WIDGET") == 5


def test_remove_reduces_level():
    inv = Inventory()
    inv.add("WIDGET", 5)
    assert inv.remove("WIDGET", 2) == 3


def test_cannot_remove_more_than_available():
    inv = Inventory()
    inv.add("WIDGET", 2)
    with pytest.raises(InsufficientStock):
        inv.remove("WIDGET", 3)
    assert inv.level("WIDGET") == 2


def test_low_stock_report():
    inv = Inventory()
    inv.add("A", 1)
    inv.add("B", 10)
    assert inv.low_stock(5) == ["A"]
