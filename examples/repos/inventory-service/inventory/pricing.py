"""Pricing rules."""

from __future__ import annotations

BULK_TIERS = ((100, 0.15), (10, 0.05))


def apply_discount(price: float, percent: float) -> float:
    """Return ``price`` reduced by ``percent`` percent, rounded to cents."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * percent / 100, 2)


def bulk_unit_price(unit_price: float, quantity: int) -> float:
    """Unit price after volume discounts: 5% from 10 units, 15% from 100 units."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    for threshold, discount in BULK_TIERS:
        if quantity > threshold:
            return round(unit_price * (1 - discount), 2)
    return unit_price


def order_total(lines: list[tuple[float, int]], tax_rate: float = 0.0) -> float:
    """Total for (unit_price, quantity) lines including bulk discounts and tax."""
    subtotal = sum(bulk_unit_price(price, qty) * qty for price, qty in lines)
    return round(subtotal * (1 + tax_rate), 2)
