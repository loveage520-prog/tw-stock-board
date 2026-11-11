#!/usr/bin/env bash
# 清掉過舊的快取後，把 pulse_data 和產生好的頁面推回 data 分支
set -euo pipefail
cd "$(dirname "$0")/.."
python cloud/prune.py
mkdir -p pulse_data/_pages
for f in sector_flow.html dashboard.html market_board_fragment.html post_board_fragment.html extra_board_fragment.html markets_data.js; do
  [ -f "$f" ] && cp "$f" pulse_data/_pages/
done
rm -rf .datapush && mkdir -p .datapush/pulse_data
cp -a pulse_data/. .datapush/pulse_data/
bash cloud/git_push_dir.sh .datapush data
rm -rf .datapush
