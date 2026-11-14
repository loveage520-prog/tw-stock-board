#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把產生好的頁面整理成 GitHub Pages 網站（site/ 資料夾）
  index.html  板塊資金流向（盤後：象限圖、排行、全球市場、籌碼營收期權、法人15日・美股、成交排行・漲跌停）
  pulse.html  台股盤中脈動（含盤中成交排行與漲跌停）
持股相關標示會由 publish_prep.py 拿掉。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SITE = BASE / "site"
SITE.mkdir(exist_ok=True)
os.environ["SITE_LINKS"] = "1"

HEAD = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex">
{extra}<style>[hidden]{{display:none!important}}body{{margin:0}}</style>
</head>
<body>
"""


def frag(name: str) -> str:
    f = BASE / name
    return str(f) if f.exists() else "-"


def prep(kind: str, src: str, args: list[str], out: str, extra_head: str = "") -> None:
    if not (BASE / src).exists():
        print(f"略過 {out}：找不到 {src}")
        return
    tmp = SITE / f"_{out}"
    subprocess.run([sys.executable, str(BASE / "publish_prep.py"), kind, str(BASE / src), str(tmp), *args], check=True)
    body = tmp.read_text("utf-8")
    tmp.unlink()
    (SITE / out).write_text(HEAD.format(extra=extra_head) + body + "\n</body>\n</html>\n", "utf-8")
    print("→", out)


prep("flow", "sector_flow.html",
     [frag("market_board_fragment.html"), frag("post_board_fragment.html"), frag("extra_board_fragment.html"),
      frag("etf_board_fragment.html"), frag("us_live_fragment.html"), frag("morning_fragment.html"),
      frag("us_flow_fragment.html")], "index.html")
# 盤中頁每 5 分鐘自動重新整理
# 盤中頁圖示：加到手機主畫面時顯示（iPhone 用 apple-touch-icon，Android 用 manifest）
PULSE_ICON = ('<link rel="icon" type="image/png" sizes="32x32" href="icons/pulse-32.png">\n'
              '<link rel="apple-touch-icon" href="icons/pulse-180.png">\n'
              '<link rel="manifest" href="pulse.webmanifest">\n'
              '<meta name="apple-mobile-web-app-title" content="盤中脈動">\n'
              '<meta name="theme-color" content="#050508">\n<title>盤中脈動</title>\n')
prep("pulse", "dashboard.html", [frag("market_board_fragment.html"), frag("us_live_fragment.html")], "pulse.html",
     '<meta http-equiv="refresh" content="300">\n' + PULSE_ICON)
(SITE / "icons").mkdir(exist_ok=True)
for f in (BASE / "cloud" / "icons").glob("*.png"):
    shutil.copyfile(f, SITE / "icons" / f.name)
(SITE / "pulse.webmanifest").write_text(json.dumps({
    "name": "盤中脈動", "short_name": "盤中", "start_url": "pulse.html", "display": "browser",
    "background_color": "#050508", "theme_color": "#050508",
    "icons": [{"src": "icons/pulse-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icons/pulse-512.png", "sizes": "512x512", "type": "image/png"}]}, ensure_ascii=False), "utf-8")
if (BASE / "markets_data.js").exists():
    shutil.copyfile(BASE / "markets_data.js", SITE / "markets_data.js")
(SITE / ".nojekyll").write_text("", "utf-8")
