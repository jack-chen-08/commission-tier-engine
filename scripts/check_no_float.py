#!/usr/bin/env python3
"""静态检查：提成引擎源码中不得出现浮点运算。

检查项（AST 级，比 grep 更可靠，能区分注释/文档字符串里的词）：
  FLOAT_CONST   浮点字面量（1.5 / 1e3）
  FLOAT_CALL    float(...) 调用
  DECIMAL_USE   Decimal(...) 或在计算中使用 decimal
  TRUE_DIV      / 真除法（应用 // 或 Fraction）
  ROUND_CALL    round(...) 内建取整（应用 round_half_up 统一处理）

用法：
  python3 scripts/check_no_float.py            # 检查 commission/ 下全部 .py
  python3 scripts/check_no_float.py --json     # 机器可读输出，便于 CI
退出码：0 = 通过，1 = 命中违规，2 = 参数/路径错误
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "commission"

RULE_DOC = {
    "FLOAT_CONST": "禁止浮点字面量（金额须为整数分或 Fraction）",
    "FLOAT_CALL": "禁止 float() 构造",
    "DECIMAL_USE": "禁止 Decimal（统一用 fractions.Fraction）",
    "TRUE_DIV": "禁止真除法 /（须用 // 整除或 Fraction 精确运算）",
    "ROUND_CALL": "禁止 round()（须走 round_half_up 统一 ROUND_HALF_UP）",
}


def scan_file(path: Path) -> list[dict]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[dict] = []
    for node in ast.walk(tree):
        rule = None
        detail = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            rule, detail = "FLOAT_CONST", repr(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "float":
                rule, detail = "FLOAT_CALL", "float(...)"
            elif node.func.id == "Decimal":
                rule, detail = "DECIMAL_USE", "Decimal(...)"
            elif node.func.id == "round":
                rule, detail = "ROUND_CALL", "round(...)"
        elif isinstance(node, ast.Name) and node.id == "Decimal":
            rule, detail = "DECIMAL_USE", "Decimal 引用"
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            rule, detail = "TRUE_DIV", "/ 运算符"
        if rule:
            findings.append(
                {
                    "file": str(path.relative_to(ROOT)),
                    "line": getattr(node, "lineno", 0),
                    "rule": rule,
                    "detail": detail,
                    "why": RULE_DOC[rule],
                }
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查引擎源码是否含浮点运算")
    parser.add_argument("target", nargs="?", default=str(DEFAULT_TARGET), help="文件或目录，默认 commission/")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"路径不存在：{target}", file=sys.stderr)
        return 2
    files = sorted(target.rglob("*.py")) if target.is_dir() else [target]

    all_findings: list[dict] = []
    for path in files:
        try:
            all_findings.extend(scan_file(path))
        except SyntaxError as exc:
            print(f"{path}: 语法错误 {exc}", file=sys.stderr)
            return 2

    report = {
        "scanned": [str(p.relative_to(ROOT)) for p in files],
        "file_count": len(files),
        "findings": all_findings,
        "passed": not all_findings,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 62)
        print("静态检查：引擎源码浮点运算扫描（AST 级）")
        print("=" * 62)
        print(f"检查文件 {len(files)} 个：")
        for rel in report["scanned"]:
            print(f"  - {rel}")
        print()
        if all_findings:
            print(f"❌ 命中 {len(all_findings)} 处违规：")
            for item in all_findings:
                print(f"  [{item['rule']}] {item['file']}:{item['line']}  {item['detail']}")
                print(f"           {item['why']}")
        else:
            print("✅ 通过：未发现 float 字面量、float()/Decimal() 调用、真除法或 round()")
            print("   金额运算全部走 int 与 fractions.Fraction，末尾统一 round_half_up 取整")
        print("=" * 62)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
