#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主動式 ETF 持股追蹤 etf_board.py
================================
追蹤 00981A（統一台股增長）、00980A（野村臺灣智慧優選）、00985A（野村臺灣增強50）每日持股：
・今日加碼／減碼／新進／剔除（與前一個交易日比較，金額＝股數變化×股價）
・近 15 個交易日累計增減
・三檔共同加碼、共同減碼的股票
資料：統一投信 ezmoney 基金投資組合、野村投信 ETF 專區（每天傍晚公布當天持股）
野村可查歷史日期；統一只能抓最新一天，所以每天存一份，累積後才有 15 日資料。

用法：python etf_board.py          （daily 盤後工作會執行）
輸出：etf_board_fragment.html（嵌進資金流向頁「主動式ETF」分頁）、pulse_data/etf/latest.json
"""
from __future__ import annotations

import html
import json
import logging
import math
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
OUT = DATA / "etf"
log = logging.getLogger("etf")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
DAYS = 15
ETFS = [
    {"c": "00981A", "n": "主動統一台股增長", "src": "ez", "id": "49YTW"},
    {"c": "00980A", "n": "主動野村臺灣優選", "src": "nomura", "id": "00980A"},
    {"c": "00985A", "n": "主動野村台灣50", "src": "nomura", "id": "00985A"},
]


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return math.nan


def load_universe() -> dict:
    files = sorted((DATA / "flow").glob("stock_info_*.json"))
    if not files:
        return {}
    d = json.loads(files[-1].read_text("utf-8"))
    cols = {k: (list(v.values()) if isinstance(v, dict) else v) for k, v in d.items()}
    return {str(s): {"n": n, "i": i or ""} for s, n, i in zip(cols["stock_id"], cols["stock_name"], cols["industry_category"])}


# ---------------------------------------------------------------- 抓資料
def fetch_ez(e: dict) -> dict:
    t = requests.get(f"https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode={e['id']}", headers=UA, timeout=30).text
    m = re.search(r'id="DataAsset"[^>]*data-content="([^"]*)"', t) or re.search(r'data-content="([^"]*)"[^>]*id="DataAsset"', t)
    if not m:
        raise RuntimeError("統一投信頁面格式變了")
    assets = {a["AssetCode"]: a for a in json.loads(html.unescape(m.group(1)))}
    st = assets["ST"]["Details"] or []
    d = st[0]["TranDate"][:10]
    rows = [{"c": x["DetailCode"].strip(), "n": x["DetailName"].strip(), "sh": float(x["Share"]),
             "v": float(x["Amount"]), "w": float(x["NavRate"])} for x in st]
    fut = [{"n": x["DetailName"], "q": x["Share"], "w": x["NavRate"]} for x in (assets.get("GD", {}).get("Details") or [])]
    return {"date": d, "aum": assets.get("NAV", {}).get("Value"), "nav": assets.get("P_UNIT", {}).get("Value"),
            "units": assets.get("OUT_UNIT", {}).get("Value"), "rows": rows, "fut": fut,
            "cash": assets.get("CASH", {}).get("Value")}


def fetch_nomura(e: dict, d: date | None = None) -> dict | None:
    body = {"FundID": e["id"]}
    if d:
        body["SearchDate"] = f"{d:%Y-%m-%d}"
    js = requests.post("https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets", json=body, headers=UA, timeout=30).json()
    data = (js.get("Entries") or {}).get("Data") or {}
    fa = data.get("FundAsset") or {}
    if not fa.get("NavDate"):
        return None
    aum = num(fa.get("Aum"))
    rows, fut, cash = [], [], None
    for t in data.get("Table") or []:
        if t.get("TableTitle") == "股票":
            for c, n, sh, w in t["Rows"]:
                rows.append({"c": c.strip(), "n": n.strip(), "sh": num(sh), "w": num(w), "v": num(w) / 100 * aum})
        elif t.get("TableTitle") == "期貨":
            fut = [{"n": x[1], "q": num(x[2]), "w": num(x[3])} for x in t["Rows"]]
        else:
            for x in t.get("Rows") or []:
                if x and x[0] == "現金":
                    cash = num(x[-1])
    return {"date": fa["NavDate"].replace("/", "-"), "aum": aum, "nav": num(fa.get("Nav")), "units": num(fa.get("Units")),
            "rows": rows, "fut": fut, "cash": cash}


def snap_path(code: str, d: str) -> Path:
    return OUT / f"{code}_{d.replace('-', '')}.json"


def save_snap(code: str, s: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    snap_path(code, s["date"]).write_text(json.dumps(s, ensure_ascii=False), "utf-8")


def snaps(code: str) -> list[dict]:
    return [json.loads(f.read_text("utf-8")) for f in sorted(OUT.glob(f"{code}_20*.json"))]


def update(e: dict) -> None:
    if e["src"] == "ez":
        save_snap(e["c"], fetch_ez(e))
        return
    s = fetch_nomura(e)
    if s:
        save_snap(e["c"], s)
    have = {x["date"] for x in snaps(e["c"])}
    d, tries = date.today(), 0
    while len(have) < DAYS + 1 and tries < 30:  # 野村可以查歷史，補齊 16 個交易日
        d -= timedelta(days=1)
        tries += 1
        if d.weekday() >= 5 or f"{d:%Y-%m-%d}" in have:
            continue
        try:
            h = fetch_nomura(e, d)
        except Exception as ex:  # noqa: BLE001
            log.info("%s %s 抓取失敗：%s", e["c"], d, ex)
            continue
        if h and h["date"] == f"{d:%Y-%m-%d}" and h["rows"]:
            save_snap(e["c"], h)
            have.add(h["date"])
        time.sleep(0.6)


# ---------------------------------------------------------------- 計算
def analyze(e: dict, uni: dict) -> dict | None:
    ss = [s for s in snaps(e["c"]) if s.get("rows")]
    if not ss:
        return None
    ss = ss[-(DAYS + 1):]
    cur = ss[-1]
    prev = ss[-2] if len(ss) > 1 else None
    base = ss[0] if len(ss) > 1 else None
    pm = {x["c"]: x for x in prev["rows"]} if prev else {}
    bm = {x["c"]: x for x in base["rows"]} if base else {}
    cm = {x["c"]: x for x in cur["rows"]}

    def nm(c, fallback):
        u = uni.get(c)
        return (u["n"] if u else fallback), (u["i"] if u else "")

    def px(x):
        return x["v"] / x["sh"] if x.get("sh") else math.nan

    rows = []
    for c, x in cm.items():
        p = px(x)
        n, ind = nm(c, x["n"])
        d1 = x["sh"] - pm[c]["sh"] if c in pm else (x["sh"] if prev else None)
        d15 = x["sh"] - bm[c]["sh"] if c in bm else (x["sh"] if base else None)
        rows.append({"c": c, "n": n, "i": ind, "sh": x["sh"], "w": r(x["w"], 2), "p": r(p, 2),
                     "dw": r(x["w"] - pm[c]["w"], 2) if c in pm else None,
                     "d1": d1, "v1": r(d1 * p / 1e6, 1) if d1 is not None else None,
                     "d15": d15, "v15": r(d15 * p / 1e6, 1) if d15 is not None else None,
                     "new": bool(prev) and c not in pm})
    out = []
    for c, x in pm.items():  # 今天剔除
        if c not in cm:
            n, ind = nm(c, x["n"])
            out.append({"c": c, "n": n, "i": ind, "sh": 0, "w": 0, "p": r(px(x), 2), "d1": -x["sh"],
                        "v1": r(-x["v"] / 1e6, 1), "pw": r(x["w"], 2)})
    out15 = []
    for c, x in bm.items():
        if c not in cm:
            n, ind = nm(c, x["n"])
            out15.append({"c": c, "n": n, "i": ind, "d15": -x["sh"], "v15": r(-x["v"] / 1e6, 1)})
    rows.sort(key=lambda x: -x["w"])
    for i, x in enumerate(rows, 1):
        x["rk"] = i
    buy = sorted([x for x in rows if (x["v1"] or 0) > 0], key=lambda x: -x["v1"])
    sell = sorted([x for x in rows if (x["v1"] or 0) < 0] + out, key=lambda x: x["v1"])
    buy15 = sorted([x for x in rows if (x["v15"] or 0) > 0], key=lambda x: -x["v15"])
    sell15 = sorted([x for x in rows if (x["v15"] or 0) < 0] + out15, key=lambda x: x["v15"])
    nav_hist = [{"d": s["date"], "nav": s.get("nav"), "aum": r((s.get("aum") or 0) / 1e8, 1)} for s in ss]
    return {"c": e["c"], "n": e["n"], "date": cur["date"], "prev": prev["date"] if prev else None,
            "base": base["date"] if base else None, "ndays": len(ss) - 1,
            "nav": cur.get("nav"), "aum": r((cur.get("aum") or 0) / 1e8, 1), "cnt": len(rows),
            "units_ch": r((cur.get("units") or 0) - (prev.get("units") or 0), 0) if prev else None,
            "fut": cur.get("fut") or [], "cash_w": r((cur.get("cash") or 0) / cur["aum"] * 100, 2) if cur.get("aum") else None,
            "rows": rows, "buy": buy, "sell": sell, "new": [x for x in rows if x["new"]], "out": out,
            "buy15": buy15, "sell15": sell15, "hist": nav_hist}


def consensus(res: list[dict], key: str) -> dict:
    agg = {}
    for e in res:
        for x in e["rows"] + e.get("out" if key == "v1" else "_", []):
            v = x.get(key)
            if v is None or v == 0:
                continue
            g = agg.setdefault(x["c"], {"c": x["c"], "n": x["n"], "i": x.get("i", ""), "v": 0.0, "by": []})
            g["v"] += v
            g["by"].append({"e": e["c"], "v": v})
        if key == "v15":
            for x in e.get("sell15", []):
                if x["c"] not in {y["c"] for y in e["rows"]}:
                    g = agg.setdefault(x["c"], {"c": x["c"], "n": x["n"], "i": x.get("i", ""), "v": 0.0, "by": []})
                    g["v"] += x["v15"]
                    g["by"].append({"e": e["c"], "v": x["v15"]})
    for g in agg.values():
        g["v"] = r(g["v"], 1)
        g["nb"] = sum(1 for b in g["by"] if b["v"] > 0)
        g["ns"] = sum(1 for b in g["by"] if b["v"] < 0)
    buy = sorted([g for g in agg.values() if g["v"] > 0], key=lambda g: (-g["nb"], -g["v"]))[:30]
    sell = sorted([g for g in agg.values() if g["v"] < 0], key=lambda g: (-g["ns"], g["v"]))[:30]
    return {"buy": buy, "sell": sell}


def save(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    tpl = (BASE / "etf_board_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(res, ensure_ascii=False))
    (BASE / "etf_board_fragment.html").write_text(frag, "utf-8")
    (BASE / "etf_board.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>主動式 ETF 持股</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:1180px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")
    log.info("→ etf_board.html")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    uni = load_universe()
    res, fails = [], []
    for e in ETFS:
        try:
            update(e)
        except Exception as ex:  # noqa: BLE001
            log.warning("%s 抓取失敗：%s", e["c"], ex)
            fails.append(e["c"])
        a = analyze(e, uni)
        if a:
            res.append(a)
            log.info("%s %s：%d 檔，比較 %s，累積 %d 日", e["c"], a["date"], a["cnt"], a["prev"], a["ndays"])
    if not res:
        log.error("三檔都沒有資料")
        sys.exit(1)
    out = {"time": f"{datetime.now():%Y-%m-%d %H:%M}", "etfs": res,
           "con1": consensus(res, "v1"), "con15": consensus(res, "v15"), "fails": fails}
    save(out)
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
