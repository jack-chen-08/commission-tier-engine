"""提成计算引擎（纯函数，无 I/O）。"""

from .engine import (
    DISCOUNT_OUT_OF_RANGE_FLAG,
    CannotComputeCommission,
    CommissionError,
    InvalidTierTable,
    compute_gross,
    fraction_to_display,
    round_half_up,
)

__all__ = [
    "compute_gross",
    "round_half_up",
    "fraction_to_display",
    "CommissionError",
    "CannotComputeCommission",
    "InvalidTierTable",
    "DISCOUNT_OUT_OF_RANGE_FLAG",
]
