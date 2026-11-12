# inventory-service (DevFlow example)

A small order/inventory library used to demonstrate DevFlow AI. It contains four
**intentional bugs** so the failure-analysis and review workflow has something real
to work on:

| Bug | Location | Symptom |
| --- | --- | --- |
| Discount returns the discount amount instead of the discounted price | `inventory/pricing.py` | assertion failure |
| Bulk tier starts at 11 units instead of 10 (off-by-one) | `inventory/pricing.py` | assertion failure |
| Removing more stock than available drives stock negative | `inventory/stock.py` | expected exception not raised |
| Average order value divides by zero for no orders | `inventory/analytics.py` | `ZeroDivisionError` |
