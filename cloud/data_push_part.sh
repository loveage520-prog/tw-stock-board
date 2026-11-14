#!/usr/bin/env bash
# 用法：data_push_part.sh "<pulse_data 底下的資料夾，空白分隔>" "<頁面檔，空白分隔>"
# 只更新 data 分支裡自己負責的部分（先取回最新的 data 分支再覆蓋），不會蓋掉其他工作的資料
set -euo pipefail
cd "$(dirname "$0")/.."
dirs="$1"; pages="${2:-}"
rm -rf .datapush && mkdir -p .datapush
if git fetch -q --depth 1 origin data 2>/dev/null; then
  git archive FETCH_HEAD pulse_data | tar -x -C .datapush
fi
mkdir -p .datapush/pulse_data/_pages
for d in $dirs; do
  if [ -d "pulse_data/$d" ]; then rm -rf ".datapush/pulse_data/$d"; cp -a "pulse_data/$d" .datapush/pulse_data/; fi
done
for f in $pages; do [ -f "$f" ] && cp -f "$f" .datapush/pulse_data/_pages/; done
bash cloud/git_push_dir.sh .datapush data
rm -rf .datapush
