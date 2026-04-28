"""Pure utility — compute Order total from its lines.

Implements invariant 1 of shared ADR-0059 :
    total_amount == sum(line.quantity * line.unit_price_at_order)

Side-effect-free, no DB, no session. Used by future router code that
needs to recompute totals when lines are added/removed/cancelled.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from iris_service.order.order_line_models import OrderLine


def compute_total(lines: Iterable[OrderLine] | None) -> Decimal:
    """Compute the order total as sum(qty * unit_price_at_order).

    Args:
        lines: iterable of OrderLine instances. May be None or empty.

    Returns:
        Decimal total. Decimal("0") for empty/None input. Never None.
    """
    if not lines:
        return Decimal("0")
    return sum(
        (line.unit_price_at_order * Decimal(line.quantity) for line in lines),
        start=Decimal("0"),
    )
