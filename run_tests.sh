#!/usr/bin/env bash
# 一键验证：单元测试 + 无浮点静态检查
set -euo pipefail
cd "$(dirname "$0")"
echo "== 单元测试 =="
python3 -m unittest discover -s tests -v
echo
echo "== 无浮点静态检查 =="
python3 scripts/check_no_float.py
