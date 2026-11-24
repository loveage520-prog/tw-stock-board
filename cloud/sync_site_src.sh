#!/usr/bin/env bash
# 長時間執行的工作（台股盤中、美股盤中）每次更新網頁前執行：
# 1. 取回 main 分支最新的「網站產生程式與樣板」，盤中改了網頁不會被舊程式蓋掉
# 2. 取回 data 分支裡其他工作產生的資料（AI 營收），收盤推回資料時也不會把它洗掉
set -uo pipefail
cd "$(dirname "$0")/.."
if git fetch -q --depth 1 origin main 2>/dev/null; then
  for f in cloud/build_site.py publish_prep.py kchart.js chain_data.json chain_template.html ai_template.html cloud/icons; do
    git checkout -q FETCH_HEAD -- "$f" 2>/dev/null || true
  done
fi
tmp="$(mktemp -d)"
if git fetch -q --depth 1 origin data 2>/dev/null && git archive FETCH_HEAD pulse_data/aifin 2>/dev/null | tar -x -C "$tmp" 2>/dev/null; then
  if [ -d "$tmp/pulse_data/aifin" ]; then rm -rf pulse_data/aifin; mv "$tmp/pulse_data/aifin" pulse_data/aifin; fi
fi
rm -rf "$tmp"
