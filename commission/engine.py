"""提成计算引擎核心：超额累进阶梯 + 折扣系数。

纯函数，不读写数据库，不做 I/O。金额一律以「分」(int) 表示。
全程使用 int 与 fractions.Fraction，不使用 float / Decimal 浮点路径，
只在返回值处按 ROUND_HALF_UP 取整到分一次。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "compute_gross",
    "CannotComputeCommission",
    "InvalidTierTable",
    "DISCOUNT_OUT_OF_RANGE_FLAG",
]

#: steps 中折扣超出规则表范围时使用的标记文本
DISCOUNT_OUT_OF_RANGE_FLAG = "折扣超出规则表范围"

BP_DENOMINATOR = 10_000


class CommissionError(Exception):
    """提成计算异常基类。"""


class CannotComputeCommission(CommissionError, ValueError):
    """输入不满足计算前提（如基数为负、上下文不完整）。"""


class InvalidTierTable(CommissionError, ValueError):
    """阶梯表本身不合规（空表、未按升序、首档不从 0 起、费率越界）。"""


# --------------------------------------------------------------------------
# 整数域取整：ROUND_HALF_UP，只此一处
# --------------------------------------------------------------------------
def round_half_up(value: Fraction) -> int:
    """把 Fraction 按 ROUND_HALF_UP 取整为 int（远离零方向四舍五入）。

    纯整数实现：|n|/d 商 q 余 r，2r >= d 即进位。不引入 float/Decimal。
    """
    if not isinstance(value, Fraction):
        value = Fraction(value)
    numerator, denominator = value.numerator, value.denominator
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def fraction_to_display(value: Fraction) -> str:
    """把 Fraction 精确格式化为两位小数（仅用于 steps 展示，不参与计算）。"""
    scaled = round_half_up(value * 100)  # 分 -> 两位小数
    sign = "-" if scaled < 0 else ""
    whole, cents = divmod(abs(scaled), 100)
    return f"{sign}{whole}.{cents:02d}"


# --------------------------------------------------------------------------
# 输入归一化与校验
# --------------------------------------------------------------------------
def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CannotComputeCommission(f"{field} 必须是整数（分），收到 {type(value).__name__}")
    return value


def _normalize_tiers(tiers: Iterable[Any]) -> list[tuple[int, int]]:
    """接受 [(from_cents, rate_bp), ...] 或 [{"from_cents":..,"rate_bp":..}, ...]。"""
    normalized: list[tuple[int, int]] = []
    for index, tier in enumerate(tiers):
        if isinstance(tier, Mapping):
            try:
                start = tier["from_cents"]
                rate = tier["rate_bp"]
            except KeyError as exc:  # pragma: no cover - 取决于调用方入参
                raise InvalidTierTable(f"第 {index} 档缺少字段 {exc}") from exc
        elif isinstance(tier, Sequence) and not isinstance(tier, (str, bytes)):
            if len(tier) != 2:
                raise InvalidTierTable(f"第 {index} 档应为 (from_cents, rate_bp) 二元组")
            start, rate = tier
        else:
            raise InvalidTierTable(f"第 {index} 档格式不支持：{tier!r}")
        normalized.append((_as_int(start, f"tiers[{index}].from_cents"),
                           _as_int(rate, f"tiers[{index}].rate_bp")))

    if not normalized:
        raise InvalidTierTable("阶梯表为空")
    if normalized[0][0] != 0:
        raise InvalidTierTable("首档必须从 0 分起")
    previous = -1
    for index, (start, rate) in enumerate(normalized):
        if start < 0:
            raise InvalidTierTable(f"第 {index} 档起征点为负：{start}")
        if start <= previous:
            raise InvalidTierTable("阶梯表必须按 from_cents 严格升序，且起征点不可重复")
        if rate < 0 or rate > BP_DENOMINATOR:
            raise InvalidTierTable(f"第 {index} 档费率须在 0..{BP_DENOMINATOR} 基点之间，收到 {rate}")
        previous = start
    return normalized


def _normalize_factors(discount_factors: Iterable[Any] | None) -> list[tuple[int, int]]:
    """接受 [(max_discount_bp, factor_bp), ...] 或 [{"max_discount_bp":..,"factor_bp":..}, ...]。"""
    if not discount_factors:
        return []
    normalized: list[tuple[int, int]] = []
    for index, item in enumerate(discount_factors):
        if isinstance(item, Mapping):
            try:
                ceiling = item["max_discount_bp"]
                factor = item["factor_bp"]
            except KeyError as exc:  # pragma: no cover
                raise CannotComputeCommission(f"折扣档第 {index} 项缺少字段 {exc}") from exc
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            if len(item) != 2:
                raise CannotComputeCommission(f"折扣档第 {index} 项应为 (max_discount_bp, factor_bp) 二元组")
            ceiling, factor = item
        else:  # pragma: no cover
            raise CannotComputeCommission(f"折扣档第 {index} 项格式不支持：{item!r}")
        ceiling = _as_int(ceiling, f"discount_factors[{index}].max_discount_bp")
        factor = _as_int(factor, f"discount_factors[{index}].factor_bp")
        if ceiling < 0:
            raise CannotComputeCommission(f"折扣档第 {index} 项 max_discount_bp 为负：{ceiling}")
        if factor < 0 or factor > BP_DENOMINATOR:
            raise CannotComputeCommission(
                f"折扣档第 {index} 项 factor_bp 须在 0..{BP_DENOMINATOR} 之间，收到 {factor}"
            )
        normalized.append((ceiling, factor))
    return normalized


def _resolve_discount_factor(
    discount_bp: int, factors: list[tuple[int, int]]
) -> tuple[int, bool]:
    """返回 (factor_bp, out_of_range)。无折扣档配置时按全额计提（10000）。"""
    if not factors:
        return BP_DENOMINATOR, False
    for ceiling, factor in factors:  # 已在调用方按 max_discount_bp 升序排列
        if ceiling >= discount_bp:
            return factor, False
    return 0, True


# --------------------------------------------------------------------------
# 主函数
# --------------------------------------------------------------------------
def compute_gross(
    base_cents: int,
    cumulative_before_cents: int,
    tiers: Iterable[Any],
    discount_bp: int = 0,
    discount_factors: Iterable[Any] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """计算本笔可计提回款的提成。

    参数
    ----
    base_cents:
        本笔实收中可计提的部分（押金、代收费用已在上游排除）。必须 >= 0。
    cumulative_before_cents:
        该业务员当月（按回款到账日）在本笔之前的累计可计提回款，作为超额累进的起点。
    tiers:
        阶梯表，形如 ``[(0, 500), (5_000_000, 600)]``，即「0 分起 500 基点、
        5,000,000 分起 600 基点」。必须首档从 0 起、按起征点严格升序。
    discount_bp:
        合同折扣率（基点）。0 表示无折扣。
    discount_factors:
        折扣系数表，形如 ``[(500, 10000), (1000, 9000)]``，取第一个
        ``max_discount_bp >= discount_bp`` 的档位，系数 = ``factor_bp / 10000``。

    返回
    ----
    (gross_cents, steps)
        ``gross_cents`` 为整数分，已在末尾按 ROUND_HALF_UP 取整一次；
        ``steps`` 逐段记录档位、段起止、段金额、费率与段提成。

    异常
    ----
    CannotComputeCommission
        base_cents 为负；参数类型/取值不满足前提。
    InvalidTierTable
        阶梯表不合规。
    """
    base = _as_int(base_cents, "base_cents")
    cumulative = _as_int(cumulative_before_cents, "cumulative_before_cents")
    discount = _as_int(discount_bp, "discount_bp")

    if base < 0:
        raise CannotComputeCommission(f"计提基数不能为负：{base}")
    if cumulative < 0:
        raise CannotComputeCommission(f"累计可计提回款不能为负：{cumulative}")
    if discount < 0 or discount > BP_DENOMINATOR:
        raise CannotComputeCommission(f"折扣率须在 0..{BP_DENOMINATOR} 基点之间，收到 {discount}")

    tier_table = _normalize_tiers(tiers)
    factor_table = _normalize_factors(discount_factors)
    # 折扣档按上限升序，保证「第一个满足」语义与配置顺序无关
    factor_table.sort(key=lambda pair: pair[0])
    factor_bp, out_of_range = _resolve_discount_factor(discount, factor_table)

    overall_factor = Fraction(factor_bp, BP_DENOMINATOR)
    start = cumulative
    end = cumulative + base

    steps: list[dict[str, Any]] = []
    total = Fraction(0, 1)

    for index, (tier_start, tier_rate) in enumerate(tier_table):
        next_start = tier_table[index + 1][0] if index + 1 < len(tier_table) else None
        segment_start = max(start, tier_start)
        segment_end = end if next_start is None else min(end, next_start)
        if segment_end <= segment_start:
            continue
        amount = segment_end - segment_start
        rate = Fraction(tier_rate, BP_DENOMINATOR)
        commission = amount * rate
        commission_after_factor = commission * overall_factor
        total += commission_after_factor
        steps.append(
            {
                "tier_index": index,
                "tier_from_cents": tier_start,
                "tier_to_cents": next_start,          # None 表示最高档（无上限）
                "segment_from_cents": segment_start,
                "segment_to_cents": segment_end,
                "segment_cents": amount,
                "rate_bp": tier_rate,
                "rate_fraction": f"{tier_rate}/{BP_DENOMINATOR}",
                "commission_fraction": f"{commission.numerator}/{commission.denominator}",
                "commission_after_factor_fraction": (
                    f"{commission_after_factor.numerator}/{commission_after_factor.denominator}"
                ),
                "commission_display": fraction_to_display(commission_after_factor),
                "discount_factor_bp": factor_bp,
            }
        )

    if out_of_range:
        # 折扣超出规则表范围：系数为 0，全额不计提，并在 steps 中留可识别标记
        for step in steps:
            step["discount_out_of_range"] = True
            step["flag"] = DISCOUNT_OUT_OF_RANGE_FLAG
        steps.append(
            {
                "type": "discount_out_of_range",
                "flag": DISCOUNT_OUT_OF_RANGE_FLAG,
                "discount_bp": discount,
                "max_known_discount_bp": max((c for c, _ in factor_table), default=None),
                "discount_factor_bp": 0,
                "gross_cents": 0,
            }
        )

    gross_cents = round_half_up(total)
    if out_of_range:
        gross_cents = 0

    return gross_cents, steps
