#!/usr/bin/env bash
# 從 data 分支取回快取與上次產生的頁面，沒有的話就從空白開始
set -uo pipefail
cd "$(dirname "$0")/.."
if git fetch -q --depth 1 origin data 2>/dev/null; then
  git checkout -q FETCH_HEAD -- pulse_data 2>/dev/null && echo "已取回快取資料" || echo "data 分支是空的"
  git reset -q 2>/dev/null || true
else
  echo "還沒有 data 分支，從空白開始"
fi
mkdir -p pulse_data
[ -d pulse_data/_pages ] && cp -a pulse_data/_pages/. ./ || true
