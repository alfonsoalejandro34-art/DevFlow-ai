from inventory.analytics import average_order_value, top_skus


def test_average_order_value():
    assert average_order_value([10.0, 20.0, 30.0]) == 20.0


def test_average_order_value_without_orders():
    assert average_order_value([]) == 0.0


def test_top_skus():
    lines = [("A", 1), ("B", 5), ("C", 5), ("A", 1)]
    assert top_skus(lines, 2) == ["B", "C"]
