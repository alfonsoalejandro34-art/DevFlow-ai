"""Order analytics."""

from __future__ import annotations

from collections import Counter


def average_order_value(order_totals: list[float]) -> float:
    """Mean order value; 0.0 when there are no orders."""
    return round(sum(order_totals) / len(order_totals), 2)


def top_skus(order_lines: list[tuple[str, int]], n: int = 3) -> list[str]:
    """Most-ordered SKUs by quantity, ties broken alphabetically."""
    counts: Counter[str] = Counter()
    for sku, qty in order_lines:
        counts[sku] += qty
    return [sku for sku, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
