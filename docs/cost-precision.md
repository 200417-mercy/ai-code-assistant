# Cost precision and rounding

All USD cost values in the application use **one deterministic rounding rule**
(#444):

> Round to **6 decimal places** (0.000001 USD) using **round-half-up** on the
> decimal representation of the value.

This applies to:

- `pricing.estimate_cost_usd()` — every estimated cost is rounded before it is
  returned;
- `usage_ledger.record_usage()` — computed and caller-supplied costs are
  rounded before the ledger row is written;
- `usage_ledger.summarize_records()` — aggregate totals are rounded once, after
  summing the already-rounded rows.

## Why

- **Determinism.** `Decimal(str(value)).quantize(0.000001, ROUND_HALF_UP)`
  always produces the same result for the same input, independent of platform.
- **No bank­er's-rounding surprises.** Python's built-in `round()` uses
  round-half-to-even, so `round(0.0000005, 6)` can differ from the intuitive
  half-up result. `pricing.round_cost()` avoids that.
- **Stable aggregates.** Because each stored row is already at the canonical
  precision, summing rows and rounding the total cannot accumulate binary-float
  noise.

## Boundary examples

| Input        | Result     | Note                          |
| ------------ | ---------- | ----------------------------- |
| `0.0000005`  | `0.000001` | exact half rounds up          |
| `0.0000004`  | `0.0`      | below half                    |
| `0.0000015`  | `0.000002` | exact half rounds up          |
| `1.2345675`  | `1.234568` | half-up at the 6th decimal    |
| `None`       | `None`     | unknown prices stay unknown   |

`None` is preserved so "price unknown" never becomes a free operation.

## Code

```python
from app.services.pricing import COST_PRECISION, round_cost

round_cost(0.0000005)  # 1e-06
round_cost(None)       # None
```
