"""compute_gross 单元测试。

覆盖验收标准 1-4 与关键边界：
  1. README 跨档样例 = 22000 分
  2. 引擎代码无 float（由 scripts/check_no_float.py 负责，见 test_no_float_by_source_scan）
  3. 基数 0 -> 0；基数为负 -> 抛异常
  4. 折扣超出全部档位 -> 0 且 steps 含超范围标记

运行：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commission import (  # noqa: E402
    DISCOUNT_OUT_OF_RANGE_FLAG,
    CannotComputeCommission,
    InvalidTierTable,
    compute_gross,
    round_half_up,
)
from fractions import Fraction  # noqa: E402

# README 样例的原样入参
README_TIERS = [(0, 500), (5_000_000, 600)]
README_FACTORS = [(500, 10_000)]


class TestReadmeSample(unittest.TestCase):
    """验收 1：README 跨档样例。"""

    def test_readme_cross_tier_sample(self):
        gross, steps = compute_gross(
            base_cents=400_000,
            cumulative_before_cents=4_800_000,
            tiers=README_TIERS,
            discount_bp=0,
            discount_factors=README_FACTORS,
        )
        self.assertEqual(gross, 22_000)
        # 200000 分按 500bp、200000 分按 600bp
        self.assertEqual(len([s for s in steps if "segment_cents" in s]), 2)
        first, second = steps[0], steps[1]
        self.assertEqual((first["segment_from_cents"], first["segment_to_cents"]), (4_800_000, 5_000_000))
        self.assertEqual(first["segment_cents"], 200_000)
        self.assertEqual(first["rate_bp"], 500)
        self.assertEqual(first["commission_display"], "10000.00")
        self.assertEqual((second["segment_from_cents"], second["segment_to_cents"]), (5_000_000, 5_200_000))
        self.assertEqual(second["segment_cents"], 200_000)
        self.assertEqual(second["rate_bp"], 600)
        self.assertEqual(second["commission_display"], "12000.00")

    def test_steps_carry_fraction_form_and_two_decimals(self):
        gross, steps = compute_gross(400_000, 4_800_000, README_TIERS, 0, README_FACTORS)
        for step in steps:
            self.assertIn("commission_fraction", step)
            numerator, denominator = step["commission_fraction"].split("/")
            expected = Fraction(step["segment_cents"] * step["rate_bp"], 10_000)
            self.assertEqual(Fraction(int(numerator), int(denominator)), expected)
            self.assertRegex(step["commission_display"], r"^-?\d+\.\d{2}$")
            self.assertEqual(step["rate_fraction"], f"{step['rate_bp']}/10000")
            self.assertIn("tier_index", step)
            self.assertIn("segment_cents", step)


class TestZeroAndNegativeBase(unittest.TestCase):
    """验收 3：基数为 0 与负数。"""

    def test_zero_base_returns_zero(self):
        gross, steps = compute_gross(0, 4_800_000, README_TIERS, 0, README_FACTORS)
        self.assertEqual(gross, 0)
        self.assertEqual([s for s in steps if "segment_cents" in s], [])

    def test_zero_base_with_zero_cumulative(self):
        gross, steps = compute_gross(0, 0, README_TIERS, 0, README_FACTORS)
        self.assertEqual(gross, 0)
        self.assertEqual(steps, [])

    def test_negative_base_raises(self):
        with self.assertRaises(CannotComputeCommission):
            compute_gross(-1, 0, README_TIERS, 0, README_FACTORS)

    def test_negative_base_raises_even_if_others_valid(self):
        with self.assertRaises(ValueError):
            compute_gross(-100_000, 0, README_TIERS, 0, README_FACTORS)


class TestDiscountOutOfRange(unittest.TestCase):
    """验收 4：折扣超出全部档位。"""

    def test_out_of_range_gives_zero_and_flag(self):
        gross, steps = compute_gross(
            base_cents=400_000,
            cumulative_before_cents=4_800_000,
            tiers=README_TIERS,
            discount_bp=800,             # 高于唯一档位上限 500
            discount_factors=[(500, 10_000)],
        )
        self.assertEqual(gross, 0)
        flags = [s for s in steps if s.get("flag") == DISCOUNT_OUT_OF_RANGE_FLAG]
        self.assertTrue(flags, "steps 必须含「折扣超出规则表范围」标记")
        self.assertEqual(flags[-1]["type"], "discount_out_of_range")
        self.assertEqual(flags[-1]["discount_factor_bp"], 0)
        self.assertEqual(flags[-1]["max_known_discount_bp"], 500)

    def test_out_of_range_marks_every_segment(self):
        gross, steps = compute_gross(400_000, 4_800_000, README_TIERS, 800, [(500, 10_000)])
        segments = [s for s in steps if "segment_cents" in s]
        self.assertTrue(segments)
        for step in segments:
            self.assertTrue(step["discount_out_of_range"])
            self.assertEqual(step["flag"], DISCOUNT_OUT_OF_RANGE_FLAG)
            self.assertEqual(step["commission_display"], "0.00")

    def test_empty_factor_table_means_full_commission(self):
        gross, _ = compute_gross(400_000, 4_800_000, README_TIERS, 800, [])
        self.assertEqual(gross, 22_000, "未配置折扣档时不应扣减，也不应判超范围")


class TestDiscountFactorHit(unittest.TestCase):
    """折扣档命中：取第一个 max_discount_bp >= discount_bp 的档位。"""

    def test_first_matching_tier_wins(self):
        tiers = [(0, 1_000)]  # 10%
        factors = [(300, 10_000), (800, 9_000), (1_000, 8_000)]
        gross_300, steps_300 = compute_gross(100_000, 0, tiers, 300, factors)
        self.assertEqual(gross_300, 10_000)          # 100000*10%*100%
        self.assertEqual(steps_300[0]["discount_factor_bp"], 10_000)

        gross_500, _ = compute_gross(100_000, 0, tiers, 500, factors)
        self.assertEqual(gross_500, 9_000)           # 命中 (800, 9000) -> *90%

        gross_1000, _ = compute_gross(100_000, 0, tiers, 1_000, factors)
        self.assertEqual(gross_1000, 8_000)          # 命中 (1000, 8000) -> *80%

    def test_factor_order_independent(self):
        tiers = [(0, 1_000)]
        shuffled = [(1_000, 8_000), (300, 10_000), (800, 9_000)]
        self.assertEqual(compute_gross(100_000, 0, tiers, 500, shuffled)[0], 9_000)


class TestTierSlicing(unittest.TestCase):
    def test_three_tiers_full_coverage(self):
        tiers = [(0, 500), (1_000_000, 600), (2_000_000, 700)]
        gross, steps = compute_gross(3_000_000, 0, tiers, 0, [])
        self.assertEqual(gross, 180_000)  # 50000 + 60000 + 70000
        self.assertEqual(len([s for s in steps if "segment_cents" in s]), 3)

    def test_base_within_single_tier(self):
        gross, steps = compute_gross(100_000, 0, README_TIERS, 0, [])
        segments = [s for s in steps if "segment_cents" in s]
        self.assertEqual(len(segments), 1)
        self.assertEqual(gross, 5_000)
        self.assertEqual(segments[0]["tier_to_cents"], 5_000_000)

    def test_cumulative_exactly_on_boundary(self):
        gross, steps = compute_gross(200_000, 5_000_000, README_TIERS, 0, [])
        segments = [s for s in steps if "segment_cents" in s]
        self.assertEqual(len(segments), 1, "起点正好在边界上时不应产生 0 金额的伪段")
        self.assertEqual(segments[0]["rate_bp"], 600)
        self.assertEqual(gross, 12_000)

    def test_highest_tier_has_no_upper_bound(self):
        gross, steps = compute_gross(10_000_000, 0, README_TIERS, 0, [])
        last = [s for s in steps if "segment_cents" in s][-1]
        self.assertIsNone(last["tier_to_cents"])
        self.assertEqual(last["segment_cents"], 5_000_000)
        self.assertEqual(gross, 550_000)  # [0,5e6)*500bp=250000 + [5e6,1e7)*600bp=300000

    def test_dict_style_tiers_accepted(self):
        tiers = [{"from_cents": 0, "rate_bp": 500}, {"from_cents": 5_000_000, "rate_bp": 600}]
        gross, _ = compute_gross(400_000, 4_800_000, tiers, 0, [{"max_discount_bp": 500, "factor_bp": 10_000}])
        self.assertEqual(gross, 22_000)


class TestRounding(unittest.TestCase):
    def test_half_up_rounds_up(self):
        # 1 分 * 50% = 0.5 分 -> ROUND_HALF_UP -> 1 分
        gross, _ = compute_gross(1, 0, [(0, 5_000)], 0, [])
        self.assertEqual(gross, 1)

    def test_below_half_rounds_down(self):
        # 1 分 * 0.01% = 0.0001 分 -> 0
        gross, _ = compute_gross(1, 0, [(0, 1)], 0, [])
        self.assertEqual(gross, 0)

    def test_rounding_applied_once_at_the_end(self):
        # 两段各 0.5 分：若逐段取整会得 1+1=2，正确应为 1.0 -> 1
        tiers = [(0, 5_000)]
        gross, steps = compute_gross(2, 0, tiers, 0, [(0, 5_000)])  # 2*50% = 1 分，再乘折扣 50% = 0.5 分
        self.assertEqual(gross, 1)
        self.assertEqual(steps[0]["commission_fraction"], "1/1")            # 折扣前
        self.assertEqual(steps[0]["commission_after_factor_fraction"], "1/2")  # 折扣后，唯一取整点

    def test_round_half_up_helper(self):
        self.assertEqual(round_half_up(Fraction(1, 2)), 1)
        self.assertEqual(round_half_up(Fraction(3, 2)), 2)
        self.assertEqual(round_half_up(Fraction(49, 100)), 0)
        self.assertEqual(round_half_up(Fraction(51, 100)), 1)
        self.assertEqual(round_half_up(Fraction(-1, 2)), -1)


class TestTierValidation(unittest.TestCase):
    def test_empty_tiers_rejected(self):
        with self.assertRaises(InvalidTierTable):
            compute_gross(100, 0, [], 0, [])

    def test_first_tier_must_start_at_zero(self):
        with self.assertRaises(InvalidTierTable):
            compute_gross(100, 0, [(1, 500)], 0, [])

    def test_tiers_must_be_ascending(self):
        with self.assertRaises(InvalidTierTable):
            compute_gross(100, 0, [(0, 500), (0, 600)], 0, [])

    def test_rate_out_of_range_rejected(self):
        with self.assertRaises(InvalidTierTable):
            compute_gross(100, 0, [(0, 20_000)], 0, [])


class TestNoFloatInEngineSource(unittest.TestCase):
    """验收 2：引擎源码中不得出现 float 字面量/构造/除法。"""

    def test_no_float_by_source_scan(self):
        import ast

        source = (ROOT / "commission" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"float 常量 line {node.lineno}: {node.value!r}")
            if isinstance(node, ast.Name) and node.id in {"float", "Decimal"}:
                offenders.append(f"{node.id} 引用 line {node.lineno}")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                offenders.append(f"真除法 / line {node.lineno}")
        self.assertEqual(offenders, [], "引擎代码不得出现 float/Decimal/真除法：" + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
