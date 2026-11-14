#!/usr/bin/env bash
# 用法：refresh_pages.sh <自己負責的頁面檔...>
# 長時間執行的工作（美股盤中）每次更新網頁前，先從 data 分支取回其他工作剛產生的頁面，避免用舊資料蓋掉
set -uo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
if git fetch -q --depth 1 origin data 2>/dev/null && git archive FETCH_HEAD pulse_data/_pages 2>/dev/null | tar -x -C "$tmp" 2>/dev/null; then
  for f in "$tmp"/pulse_data/_pages/*; do
    b="$(basename "$f")"
    skip=0
    for k in "$@"; do [ "$b" = "$k" ] && skip=1; done
    [ $skip -eq 0 ] && cp -f "$f" "./$b"
  done
fi
rm -rf "$tmp"
