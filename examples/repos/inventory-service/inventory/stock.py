"""In-memory stock ledger."""

from __future__ import annotations


class InsufficientStock(Exception):
    """Raised when removing more units than are available."""


class Inventory:
    def __init__(self) -> None:
        self._levels: dict[str, int] = {}

    def add(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._levels[sku] = self._levels.get(sku, 0) + quantity
        return self._levels[sku]

    def remove(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._levels[sku] = self._levels.get(sku, 0) - quantity
        return self._levels[sku]

    def level(self, sku: str) -> int:
        return self._levels.get(sku, 0)

    def low_stock(self, threshold: int) -> list[str]:
        return sorted(sku for sku, qty in self._levels.items() if qty < threshold)
