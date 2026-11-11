#!/usr/bin/env python3
"""依檔名裡的日期清掉過舊的快取（git 取回的檔案修改時間都是當下，不能用 mtime 判斷）"""
import re
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "pulse_data"
RULES = [("flow/twse_*", 80), ("flow/tpex_*", 40), ("flow/fm_*", 20), ("extra/twse_*", 20), ("extra/tpex_*", 20),
         ("logs/*", 14), ("pulse_*.csv", 20), ("groups_*.csv", 20), ("baseline_*.json", 10), ("board/board_*", 30)]
today = date.today()
n = 0
for pat, days in RULES:
    for f in BASE.glob(pat):
        m = re.search(r"(20\d{6})", f.name)
        if m:
            d = date(int(m[1][:4]), int(m[1][4:6]), int(m[1][6:]))
            if today - d > timedelta(days=days):
                f.unlink()
                n += 1
print(f"清除 {n} 個過舊快取")
