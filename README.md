# 提成计算引擎核心：超额累进阶梯 + 折扣系数

会员空间平台的业务员提成计算核心。纯函数、无 I/O、无第三方依赖，Python 3.11+（与项目统一技术约定一致）。

- **金额单位**：全部为「分」（`int`），不出现元、不出现浮点。
- **阶梯**：按业务员当月**累计可计提回款**做超额累进，跨档自动切段。
- **折扣**：按合同折扣率命中折扣系数档，系数在总额上一次生效。
- **取整**：全程 `int` + `fractions.Fraction` 精确运算，**函数末尾按 ROUND_HALF_UP 取整到分，只取整一次**。

## 快速验证

```bash
python3 -m unittest discover -s tests -v   # 25 个用例
python3 scripts/check_no_float.py          # 静态检查：无 float / Decimal / 真除法
```

## 接口

```python
from commission import compute_gross

gross_cents, steps = compute_gross(
    base_cents=400_000,            # 本笔可计提回款（分），押金/代收已在上游排除
    cumulative_before_cents=4_800_000,  # 本笔之前当月累计可计提回款（分）
    tiers=[(0, 500), (5_000_000, 600)], # (起征点分, 费率基点)
    discount_bp=0,                 # 合同折扣率，基点；0 = 无折扣
    discount_factors=[(500, 10_000)],   # (折扣率上限基点, 系数基点)
)
```

`tiers` / `discount_factors` 也接受 dict 形式：`{"from_cents": 0, "rate_bp": 500}`、`{"max_discount_bp": 500, "factor_bp": 10_000}`。

## 跨档样例（可复现，结果 22000 分）

阶梯：`0 分起 500 bp`、`5,000,000 分起 600 bp`。该业务员本月此前已累计 **4,800,000 分**，本笔 **400,000 分**。

| 段 | 区间（分） | 段金额 | 费率 | 段提成 |
|---|---|---:|---:|---:|
| 1 | 4,800,000 → 5,000,000 | 200,000 | 500 bp | 10,000.00 |
| 2 | 5,000,000 → 5,200,000 | 200,000 | 600 bp | 12,000.00 |
| **合计** | | 400,000 | | **22,000.00** |

```python
>>> compute_gross(400_000, 4_800_000, [(0, 500), (5_000_000, 600)], 0, [(500, 10_000)])[0]
22000
```

前 200,000 分落在 500 bp 档（因为累计起点 4,800,000 距 5,000,000 档界只剩 200,000），越过档界后的 200,000 分进入 600 bp 档 —— 这就是「超额累进」而非「全额跳档」。

## 语义与边界

### 阶梯

| 场景 | 行为 |
|---|---|
| 累计起点正好落在档界上 | 只产生后一档的段，不产生 0 金额伪段 |
| 单笔跨越多档 | 逐档切段，`steps` 每档一条 |
| 最高档 | 无上限，`tier_to_cents` 为 `null` |
| 阶梯表为空 / 首档不从 0 起 / 起征点非严格升序 / 费率超出 0..10000 bp | 抛 `InvalidTierTable` |

### 折扣

取**第一个** `max_discount_bp >= discount_bp` 的档位，系数 `= factor_bp / 10000`。折扣档按上限升序匹配，配置书写顺序不影响结果。

| 场景 | 行为 |
|---|---|
| `discount_bp` 命中某档 | 总提成乘以该档系数 |
| `discount_bp` 超出全部档位上限 | 提成 **0**，`steps` 每段带 `discount_out_of_range: true`，并追加一条 `type="discount_out_of_range"` 的记录，`flag` 为「折扣超出规则表范围」 |
| `discount_factors` 为空 | 视为无折扣规则，按 10000 bp（全额）计提，**不**判超范围 |

### 取整

`ROUND_HALF_UP`（四舍五入、远离零），只在返回值处执行一次：

| 精确值（分） | 返回 |
|---:|---:|
| 0.0001 | 0 |
| 0.5 | 1 |
| 1.5 | 2 |

实现为纯整数运算（`divmod` + `2r >= d` 判进位），不经过 `float`／`Decimal`，避免二进制浮点误差。

### 异常

| 异常 | 触发条件 |
|---|---|
| `CannotComputeCommission`（`ValueError` 子类） | `base_cents < 0`、`cumulative_before_cents < 0`、`discount_bp` 超出 `0..10000`、参数非整数 |
| `InvalidTierTable`（`ValueError` 子类） | 阶梯表不合规（见上表） |

`base_cents == 0` **不是**异常：返回 `(0, [])`，`steps` 为空列表。

## `steps` 结构

每个切段一条记录：

| 字段 | 含义 |
|---|---|
| `tier_index` | 档位序号（从 0 起） |
| `tier_from_cents` / `tier_to_cents` | 该档起征点 / 下一档起征点（最高档为 `null`） |
| `segment_from_cents` / `segment_to_cents` | 本笔落在该档的实际段区间 |
| `segment_cents` | 段金额 |
| `rate_bp` / `rate_fraction` | 费率基点 / 分数形式 `"500/10000"` |
| `commission_fraction` | 折扣前段提成的精确分数 `"10000/1"` |
| `commission_after_factor_fraction` | 折扣后段提成的精确分数 |
| `commission_display` | 折扣后段提成的两位小数展示，`"10000.00"` |
| `discount_factor_bp` | 命中的系数基点 |

折扣超范围时，另有一条 `type="discount_out_of_range"` 记录（含 `flag`、`discount_bp`、`max_known_discount_bp`、`gross_cents`），便于对账系统识别与告警。

## 关于「不出现 float」的验证

`scripts/check_no_float.py` 做 AST 级扫描（比 grep 更准，不会把文档字符串里的 "float" 误报），检查五类：浮点字面量、`float()`、`Decimal`、真除法 `/`、`round()`。实测输出：

```
$ python3 scripts/check_no_float.py
==============================================================
静态检查：引擎源码浮点运算扫描（AST 级）
==============================================================
检查文件 2 个：
  - commission/__init__.py
  - commission/engine.py

✅ 通过：未发现 float 字面量、float()/Decimal() 调用、真除法或 round()
   金额运算全部走 int 与 fractions.Fraction，末尾统一 round_half_up 取整
==============================================================
```

## 测试覆盖

25 个用例，`python3 -m unittest discover -s tests` 全绿。实测输出：

```
$ python3 -m unittest discover -s tests -v
...
test_zero_base_with_zero_cumulative (test_engine.TestZeroAndNegativeBase.test_zero_base_with_zero_cumulative) ... ok
----------------------------------------------------------------------
Ran 25 tests in 0.006s

OK
```

同一次运行的终端记录（静态检查 + 全部用例）见 [`docs/verify_output.png`](docs/verify_output.png)。

分组：

| 组 | 覆盖 |
|---|---|
| `TestReadmeSample` | 跨档样例 = 22000、段切分正确、`steps` 字段完整 |
| `TestZeroAndNegativeBase` | 基数 0 → 0；基数负数 → 抛异常 |
| `TestDiscountOutOfRange` | 超范围 → 0 且带标记；空系数表 → 不判超范围 |
| `TestDiscountFactorHit` | 首个命中档生效、与配置顺序无关 |
| `TestTierSlicing` | 三档全覆盖、单档、正好压边界、最高档无上限、dict 入参 |
| `TestRounding` | HALF_UP 进位/舍去、末尾只取整一次、辅助函数 |
| `TestTierValidation` | 空表、首档非 0、非升序、费率越界 |
| `TestNoFloatInEngineSource` | AST 扫描引擎源码无 float/Decimal/真除法 |

## 已知边界（不在本函数职责内）

- 不做**月份归属**判定：`cumulative_before_cents` 由调用方按「回款到账日」归集后传入；跨月清零、退单回冲需上游处理。
- 不做**负数回冲**：本函数对负基数直接抛异常，退款场景应由上游生成独立的冲正单据，而非用负数调用。
- 不做**提成上下限封顶**：如业务上存在保底/封顶，须在调用侧处理，避免与阶梯语义混淆。
