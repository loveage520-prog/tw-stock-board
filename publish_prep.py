#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_prep.py：把本機產生的網頁轉成「手機公開版」，供 Claude 排程任務發布到 claude.ai
・去掉 <html>/<head>/<body> 外框（發布時會自動加上）
・拿掉持股相關標示（★、我的持股、持股異常事件），公開連結的人看不到你的持股
・三個頁面互相連結

用法：python publish_prep.py flow|pulse <來源 html> <輸出 index.html> [market_board_fragment.html] [post_board_fragment.html]
      第 4 個參數：把「成交排行與漲跌停」嵌進頁面（資金流向：新分頁；盤中脈動：指數區塊下方）
      第 5 個參數（只限 flow）：把「法人 15 日・美股」嵌成資金流向頁的另一個分頁
      第 6 個參數（只限 flow）：把「籌碼・營收・期權」嵌成資金流向頁的另一個分頁
      不需要的參數可以填 -
本機的 sector_flow.html、dashboard.html、market_board.html 不會被修改。
"""
from __future__ import annotations

import json
import os
import re
import sys

URL_FLOW = "https://claude.ai/artifact/1SLzTD3FqHs3nbqQ7nkQKr"
URL_PULSE = "https://claude.ai/artifact/WZyY55RMbMN76EagUXxguA"
if os.environ.get("SITE_LINKS") == "1":  # GitHub Pages 版：頁面互相用相對連結
    URL_FLOW, URL_PULSE = "index.html", "pulse.html"


def unwrap(h: str) -> str:
    h = h.replace("\r\n", "\n")
    h = re.sub(r"^\s*<!doctype html>\s*<html[^>]*>\s*<head>\s*", "", h, flags=re.I)
    h = re.sub(r'<meta charset="utf-8">\s*', "", h, count=1)
    h = re.sub(r'<meta name="viewport"[^>]*>\s*', "", h, count=1)
    h = re.sub(r'<meta http-equiv="refresh"[^>]*>\s*', "", h)
    h = re.sub(r"</head>\s*<body>\s*", "", h, count=1)
    h = re.sub(r"</body>\s*</html>\s*$", "\n", h)
    return h


def check(h: str) -> None:
    bad = re.findall(r'<script[^>]+src="(?!markets_data\.js")[^"]*"|fetch\(|XMLHttpRequest|WebSocket', h)
    if bad:
        raise SystemExit(f"發現不該有的外部載入或連線：{bad[:3]}，停止發布")


def scrub_mine(o):
    if isinstance(o, dict):
        return {k: (False if k == "mine" else scrub_mine(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub_mine(x) for x in o]
    return o


def flow(h: str) -> str:
    lines = h.split("\n")
    idx = next(i for i, l in enumerate(lines) if l.startswith("const D = ") and not l.startswith("const D = /*"))
    d = json.loads(lines[idx][len("const D = "):].rstrip().rstrip(";"))
    d = scrub_mine(d)
    d["stocks"]["mine"] = []
    d["missing_holdings"] = []
    for k, rows in d.get("members", {}).items():  # 成分股第 7 欄＝是否持股
        for r in rows:
            if len(r) > 6:
                r[6] = False
    lines[idx] = "const D = " + json.dumps(d, ensure_ascii=False) + ";"
    h = "\n".join(lines)
    h = h.replace('<div class="card"><h3>我的持股</h3>', '<div class="card" hidden><h3>我的持股</h3>', 1)
    h = h.replace('<option value="mine">含我的持股</option>', "")
    h = h.replace("黃框＝含你的持股。", "")
    links = f'<span class="meta"><a href="{URL_PULSE}" target="_blank" rel="noopener">盤中脈動 →</a></span>'
    h = h.replace('<span class="meta" id="meta"></span>', '<span class="meta" id="meta"></span>\n  ' + links, 1)
    return h


def pulse(h: str) -> str:
    h = h.replace("每 60 秒自動更新", "網頁每 5 分鐘更新" if os.environ.get("SITE_LINKS") == "1" else "手機版每小時更新")
    h = h.replace('<a href="sector_flow.html">資金流向・全球市場 →</a>',
                  f'<a href="{URL_FLOW}" target="_blank" rel="noopener">資金流向・全球市場 →</a>')
    h = re.sub(r"<tr class=\"mine\"><td>★</td>|<tr><td></td>", "<tr>", h)
    h = h.replace("<th>我的</th>", "")
    h = h.replace("★", "").replace('class="mine"', "")
    h = h.replace("（＝我的族群，依資金流入倍數排序）", "（依資金流入倍數排序）")
    h = h.replace("；＝我的族群", "").replace("＝我的族群。", "").replace("＝我的族群", "")
    h = re.sub(r"<li>[^<]*(持股|我的族群)[^<]*</li>", "", h)
    return h


def frag_of(path: str, tag: str = "MB") -> str:
    f = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    a, b = f.find(f"<!--{tag}-START-->"), f.find(f"<!--{tag}-END-->")
    if a < 0 or b < 0:
        raise SystemExit(f"{path} 格式不對")
    f = f[a:b + len(f"<!--{tag}-END-->")]
    check(f)
    return f


def embed_flow(h: str, frag: str) -> str:
    """資金流向頁：在「全球市場」旁邊加一個分頁"""
    tab = '<button class="tab" data-v="markets">全球市場</button>'
    assert tab in h, "資金流向頁面的分頁結構變了"
    h = h.replace(tab, tab + '\n      <button class="tab" data-v="board">成交排行・漲跌停</button>', 1)
    h = h.replace('    <div class="card" id="detail"></div>', f'    <div id="vBoard" hidden>\n{frag}\n    </div>\n\n    <div class="card" id="detail"></div>', 1)
    old = '$("#vMarkets").hidden = v !== "markets";'
    assert old in h
    h = h.replace(old, old + ' $("#vBoard").hidden = v !== "board";', 1)
    h = h.replace('$("#detail").style.display = v === "markets" ? "none"', '$("#detail").style.display = (v === "markets" || v === "board") ? "none"', 1)
    h = h.replace('else if (v === "markets") markets(); else chart();', 'else if (v === "markets") markets(); else if (v !== "board") chart();', 1)
    return h


def embed_post(h: str, frag: str) -> str:
    """資金流向頁：加「法人15日・美股」分頁"""
    tab = '<button class="tab" data-v="markets">全球市場</button>'
    assert tab in h
    h = h.replace(tab, tab + '\n      <button class="tab" data-v="post">法人15日・美股</button>', 1)
    h = h.replace('    <div class="card" id="detail"></div>', f'    <div id="vPost" hidden>\n{frag}\n    </div>\n\n    <div class="card" id="detail"></div>', 1)
    old = '$("#vMarkets").hidden = v !== "markets";'
    h = h.replace(old, old + ' $("#vPost").hidden = v !== "post";', 1)
    h = h.replace('(v === "markets" || v === "board")', '(v === "markets" || v === "board" || v === "post")', 1)
    h = h.replace('else if (v !== "board") chart();', 'else if (v !== "board" && v !== "post") chart();', 1)
    if '(v === "markets" || v === "board" || v === "post")' not in h:  # 沒有嵌成交排行時
        h = h.replace('$("#detail").style.display = v === "markets" ? "none"', '$("#detail").style.display = (v === "markets" || v === "post") ? "none"', 1)
        h = h.replace('else if (v === "markets") markets(); else chart();', 'else if (v === "markets") markets(); else if (v !== "post") chart();', 1)
    return h


def embed_extra(h: str, frag: str) -> str:
    """資金流向頁：加「籌碼・營收・期權」分頁"""
    tab = '<button class="tab" data-v="markets">全球市場</button>'
    assert tab in h
    h = h.replace(tab, tab + '\n      <button class="tab" data-v="extra">籌碼・營收・期權</button>', 1)
    h = h.replace('    <div class="card" id="detail"></div>', f'    <div id="vExtra" hidden>\n{frag}\n    </div>\n\n    <div class="card" id="detail"></div>', 1)
    old = '$("#vMarkets").hidden = v !== "markets";'
    h = h.replace(old, old + ' $("#vExtra").hidden = v !== "extra";', 1)
    h = re.sub(r'\$\("#detail"\)\.style\.display = \(?v === "markets"([^?]*?)\)? \? "none"',
               lambda m: f'$("#detail").style.display = (v === "markets"{m.group(1).rstrip(")")} || v === "extra") ? "none"', h, count=1)
    h = re.sub(r'else if \(v === "markets"\) markets\(\); else (if \(([^)]*)\) )?chart\(\);',
               lambda m: 'else if (v === "markets") markets(); else if (' + ((m.group(2) + " && ") if m.group(2) else "") + 'v !== "extra") chart();', h, count=1)
    return h


def embed_pulse(h: str, frag: str) -> str:
    """盤中脈動頁：放在加權指數走勢上面"""
    anchor = "<section><h2>加權指數盤中走勢</h2>"
    assert anchor in h, "盤中脈動頁面的結構變了"
    return h.replace(anchor, f'<section>\n{frag}\n</section>\n' + anchor, 1)


def main():
    kind, src, dst = sys.argv[1:4]
    h = unwrap(open(src, encoding="utf-8").read())
    check(h)
    h = {"flow": flow, "pulse": pulse}[kind](h)
    if len(sys.argv) > 4 and sys.argv[4] != "-":
        frag = frag_of(sys.argv[4])
        h = (embed_flow if kind == "flow" else embed_pulse)(h, frag)
    if kind == "flow" and len(sys.argv) > 5 and sys.argv[5] != "-":
        h = embed_post(h, frag_of(sys.argv[5], "PB"))
    if kind == "flow" and len(sys.argv) > 6 and sys.argv[6] != "-":
        h = embed_extra(h, frag_of(sys.argv[6], "XB"))
    open(dst, "w", encoding="utf-8").write(h)
    left = [w for w in ("★", "我的持股", "持股 ") if w in h and kind == "pulse"]
    print(f"{kind}: {len(h):,} 字元 → {dst}" + (f"（注意：仍含 {left}）" if left else ""))


if __name__ == "__main__":
    main()
