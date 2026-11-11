#!/usr/bin/env bash
# 雲端盤中工作：盤中脈動（每分鐘）＋全市場成交排行與漲跌停（每 5 分鐘），每 5 分鐘更新一次網頁
set -uo pipefail
cd "$(dirname "$0")/.."
python market_pulse.py --auto --no-browser &
P1=$!
python market_board.py --auto &
P2=$!
publish() {
  python cloud/build_site.py >/dev/null 2>&1 && bash cloud/git_push_dir.sh site gh-pages >/dev/null 2>&1 \
    && echo "$(date '+%H:%M') 網頁已更新" || echo "$(date '+%H:%M') 網頁更新失敗"
}
sleep 120
while kill -0 $P1 2>/dev/null || kill -0 $P2 2>/dev/null; do
  publish
  sleep 300
done
publish
bash cloud/data_push.sh
