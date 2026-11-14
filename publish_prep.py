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
      第 7～9 個參數（只限 flow）：主動式 ETF、美股即時、晨報
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


def embed_tab(h: str, frag: str, view: str, label: str) -> str:
    """資金流向頁：在「全球市場」後面加一個分頁（通用版，可疊加在其他分頁之後）"""
    tab = '<button class="tab" data-v="markets">全球市場</button>'
    assert tab in h
    vid = "v" + view.capitalize()
    h = h.replace(tab, tab + f'\n      <button class="tab" data-v="{view}">{label}</button>', 1)
    h = h.replace('    <div class="card" id="detail"></div>', f'    <div id="{vid}" hidden>\n{frag}\n    </div>\n\n    <div class="card" id="detail"></div>', 1)
    old = '$("#vMarkets").hidden = v !== "markets";'
    h = h.replace(old, old + f' $("#{vid}").hidden = v !== "{view}";', 1)
    h = re.sub(r'\$\("#detail"\)\.style\.display = \(?v === "markets"([^?]*?)\)? \? "none"',
               lambda m: f'$("#detail").style.display = (v === "markets"{m.group(1).rstrip(")")} || v === "{view}") ? "none"', h, count=1)
    h = re.sub(r'else if \(v === "markets"\) markets\(\); else (if \(([^)]*)\) )?chart\(\);',
               lambda m: 'else if (v === "markets") markets(); else if (' + ((m.group(2) + " && ") if m.group(2) else "") + f'v !== "{view}") chart();', h, count=1)
    return h


HASH_JS = """<script>
/* 網址加 #分頁名稱（例：#morning、#us、#etf）可直接打開該分頁 */
(function(){ const v = location.hash.slice(1); const b = v && document.querySelector('.tab[data-v="' + v + '"]'); if (b) b.click(); })();
</script>
"""


MK_CSS = """<style>
/* 最上層：台股／美股切換＋頁面切換（兩個頁面共用） */
.mk-nav{position:sticky;top:0;z-index:30;background:var(--bg,#f4f5f7);padding:calc(8px + env(safe-area-inset-top,0px)) max(16px,env(safe-area-inset-right,0px)) 8px max(16px,env(safe-area-inset-left,0px));border-bottom:1px solid var(--line,#e3e6eb)}
.mk-in{max-width:1440px;margin:0 auto;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.mk-seg{display:inline-flex;padding:3px;border-radius:12px;background:var(--line,#e3e6eb);gap:3px}
.mk-seg button{border:0;background:none;color:var(--fg,#1c2230);font:inherit;font-weight:700;font-size:15px;padding:7px 18px;border-radius:9px;cursor:pointer}
.mk-seg button.on{background:var(--card,#fff);box-shadow:0 1px 3px rgba(0,0,0,.15)}
.mk-seg button:focus-visible,.mk-pg a:focus-visible,.mk-sub button:focus-visible{outline:2px solid var(--accent,#2f5fb3);outline-offset:1px}
.mk-pg{display:inline-flex;gap:4px}
.mk-pg a{padding:6px 12px;border-radius:9px;border:1px solid var(--line,#e3e6eb);color:var(--fg,#1c2230);text-decoration:none;font-size:13px;font-weight:600;background:var(--card,#fff)}
.mk-pg a.on{background:var(--fg,#1c2230);color:var(--card,#fff);border-color:var(--fg,#1c2230)}
.mk-us{max-width:1440px;margin:0 auto;padding:14px max(16px,env(safe-area-inset-right,0px)) 28px max(16px,env(safe-area-inset-left,0px))}
.mk-us h1{font-size:20px;margin:0 0 10px}
.mk-sub{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px}
.mk-sub button{padding:7px 14px;border-radius:9px;border:1px solid var(--line,#e3e6eb);background:var(--card,#fff);color:var(--fg,#1c2230);font:inherit;font-weight:600;cursor:pointer}
.mk-sub button.on{background:var(--fg,#1c2230);color:var(--card,#fff);border-color:var(--fg,#1c2230)}
@media (max-width:480px){.mk-seg button{padding:7px 13px}.mk-pg a{padding:6px 9px}}
</style>
"""


def mk_nav(page: str) -> str:
    pg = [("pulse", URL_PULSE, "盤中脈動"), ("flow", URL_FLOW, "盤後・資金流向")]
    links = "".join(f'<a class="{"on" if k == page else ""}" data-base="{u}" href="{u}">{t}</a>' for k, u, t in pg)
    return (MK_CSS + '<nav class="mk-nav" aria-label="市場與頁面"><div class="mk-in">'
            '<div class="mk-seg"><button data-mk="tw" class="on">🇹🇼 台股</button><button data-mk="us">🇺🇸 美股</button></div>'
            f'<div class="mk-pg">{links}</div></div></nav>\n')


def mk_js(default: str) -> str:
    """default：'tw' 或 'time'（晚上 9 點到早上 6 點自動顯示美股）"""
    return """<script>
/* 台股／美股切換：網址 #us（或 #us-live）、#tw；切換頁面時會帶著目前的市場 */
(function(){
  const tw = document.getElementById("mkTw"), us = document.getElementById("mkUs");
  const show = v => {
    tw.hidden = v !== "tw"; us.hidden = v !== "us";
    document.querySelectorAll(".mk-seg button").forEach(b => b.classList.toggle("on", b.dataset.mk === v));
    document.querySelectorAll(".mk-pg a").forEach(a => a.href = a.dataset.base + (v === "us" ? "#us" : "#tw"));
    if (v === "tw") dispatchEvent(new Event("resize"));
  };
  document.querySelectorAll(".mk-seg button").forEach(b => b.onclick = () => {
    show(b.dataset.mk);
    try { history.replaceState(null, "", "#" + b.dataset.mk); } catch(e) {}
    scrollTo(0, 0);
  });
  const h = location.hash.slice(1);
  let v = h.startsWith("us") ? "us" : h ? "tw" : null;
  if (!v) {
    const hr = Number(new Intl.DateTimeFormat("en-US", {timeZone: "Asia/Taipei", hour: "numeric", hour12: false}).format(new Date())) % 24;
    v = ("__DEF__" === "time" && (hr >= 21 || hr < 6)) ? "us" : "tw";
  }
  show(v);
  const subs = document.querySelectorAll(".mk-sub button");
  const sub = k => { subs.forEach(b => b.classList.toggle("on", b.dataset.sub === k)); document.querySelectorAll("[data-subv]").forEach(d => d.hidden = d.dataset.subv !== k); };
  subs.forEach(b => b.onclick = () => { sub(b.dataset.sub); try { history.replaceState(null, "", "#" + (b.dataset.sub === "live" ? "us-live" : "us")); } catch(e) {} });
  if (subs.length) sub(h === "us-live" ? "live" : "flow");
})();
</script>
""".replace("__DEF__", default)


def embed_pulse_us(h: str, frag: str) -> str:
    """盤中脈動頁：最上層加「台股／美股」切換，美股盤中放在美股畫面"""
    assert "<main>" in h and "</main>" in h, "盤中脈動頁面的結構變了"
    h = h.replace("<main>", mk_nav("pulse") + '<div id="mkTw">\n<main>', 1)
    i = h.rfind("</main>") + len("</main>")
    h = h[:i] + '\n</div>\n<div id="mkUs" class="mk-us" hidden>\n<h1>美股盤中脈動</h1>\n' + frag + "\n</div>\n" + h[i:]
    return h + mk_js("time")


def embed_flow_us(h: str, live: str | None, flow: str | None) -> str:
    """資金流向頁：最上層加「台股／美股」切換；美股畫面＝美股板塊資金流向＋盤中・收盤重點"""
    assert '<div class="wrap">' in h
    h = h.replace('<div class="wrap">', mk_nav("flow") + '<div id="mkTw">\n<div class="wrap">', 1)
    subs, views = [], []
    if flow:
        subs.append('<button data-sub="flow" class="on">板塊資金流向</button>')
        views.append(f'<div data-subv="flow">\n{flow}\n</div>')
    if live:
        subs.append('<button data-sub="live">盤中・收盤重點</button>')
        views.append(f'<div data-subv="live" hidden>\n{live}\n</div>')
    return (h + '\n</div>\n<div id="mkUs" class="mk-us" hidden>\n<h1>美股板塊資金流向</h1>\n'
            f'<div class="mk-sub">{"".join(subs)}</div>\n' + "\n".join(views) + "\n</div>\n" + mk_js("tw"))


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
    if kind == "pulse" and len(sys.argv) > 5 and sys.argv[5] != "-":  # 盤中脈動第 5 個參數：美股即時
        h = embed_pulse_us(h, frag_of(sys.argv[5], "UB"))
    if kind == "flow" and len(sys.argv) > 5 and sys.argv[5] != "-":
        h = embed_post(h, frag_of(sys.argv[5], "PB"))
    if kind == "flow" and len(sys.argv) > 6 and sys.argv[6] != "-":
        h = embed_extra(h, frag_of(sys.argv[6], "XB"))
    # 第 7～9 個參數（只限 flow）：主動式 ETF、美股即時、晨報（後加的排在前面）
    arg = lambda i: sys.argv[i] if len(sys.argv) > i and sys.argv[i] != "-" else None  # noqa: E731
    for i, (tag, view, label) in ((7, ("EB", "etf", "主動式ETF")), (9, ("NB", "morning", "晨報"))):
        if kind == "flow" and arg(i):
            h = embed_tab(h, frag_of(arg(i), tag), view, label)
    if kind == "flow":
        h += HASH_JS
        # 第 8 個參數＝美股即時、第 10 個＝美股板塊資金流向 → 放進最上層「美股」畫面
        live, flw = arg(8), arg(10)
        if live or flw:
            h = embed_flow_us(h, frag_of(live, "UB") if live else None, frag_of(flw, "SB") if flw else None)
    open(dst, "w", encoding="utf-8").write(h)
    left = [w for w in ("★", "我的持股", "持股 ") if w in h and kind == "pulse"]
    print(f"{kind}: {len(h):,} 字元 → {dst}" + (f"（注意：仍含 {left}）" if left else ""))


if __name__ == "__main__":
    main()
