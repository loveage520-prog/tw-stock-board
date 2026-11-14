#!/usr/bin/env bash
# 美股盤中工作：開盤前 5 分鐘開始，每 5 分鐘更新「美股即時」分頁，收盤後產生收盤重點
# GitHub 單次最多跑 6 小時，美股一盤 6.5 小時，所以分兩段觸發（21:25、03:00），後一段會取代前一段
set -uo pipefail
cd "$(dirname "$0")/.."
publish() {
  bash cloud/refresh_pages.sh us_live_fragment.html us_flow_fragment.html
  python cloud/build_site.py >/dev/null 2>&1 && bash cloud/git_push_dir.sh site gh-pages >/dev/null 2>&1 \
    && echo "$(date '+%H:%M') 網頁已更新" || echo "$(date '+%H:%M') 網頁更新失敗"
}
python us_live.py --wait
if [ $? -eq 2 ]; then
  echo "今天美股沒有盤或已收盤，只更新一次"
  python us_live.py --once; python us_flow.py; publish
  bash cloud/data_push_part.sh "us" "us_live_fragment.html us_flow_fragment.html"
  exit 0
fi
start=$(date +%s)
while true; do
  python us_live.py --once
  rc=$?
  publish
  if [ $rc -eq 3 ]; then
    echo "美股已收盤，收盤重點完成"
    python us_flow.py && publish
    break
  fi
  [ $(( $(date +%s) - start )) -gt 20100 ] && { echo "接近 6 小時上限，交給下一段"; break; }
  sleep 300
done
bash cloud/data_push_part.sh "us" "us_live_fragment.html us_flow_fragment.html"
