#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股盤中＋收盤重點 us_live.py
==============================
・盤中（台灣時間約 22:30～05:00，夏令 21:30～04:00）：指數、期貨匯率、成交值前 100（依產業分組）、台灣供應鏈
・收盤後：自動整理「收盤重點」（指數、強弱族群、漲跌幅最大、台積電 ADR 溢價、今晚財報）

用法：
    python us_live.py --once      抓一次並輸出（盤中每 5 分鐘由 cloud/us.sh 呼叫）
    python us_live.py --wait      等到開盤前 5 分鐘；今天不開盤或已收盤就回傳 2
回傳碼：0＝盤中、3＝已收盤（已寫入收盤重點）、2＝今天沒有盤
輸出：us_live_fragment.html（嵌進資金流向頁「美股即時」分頁）、pulse_data/us/latest.json、pulse_data/us/close_YYYYMMDD.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
import warnings
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
OUT = DATA / "us"
ET = ZoneInfo("America/New_York")
TW = ZoneInfo("Asia/Taipei")
log = logging.getLogger("us")
warnings.filterwarnings("ignore")

INDICES = [("^GSPC", "S&P 500"), ("^IXIC", "那斯達克"), ("^DJI", "道瓊"), ("^SOX", "費城半導體"), ("^RUT", "羅素 2000"), ("^VIX", "VIX 恐慌")]
MACRO = [("^TNX", "美 10 年債", "y"), ("DX-Y.NYB", "美元指數", ""), ("TWD=X", "美元/台幣", ""), ("JPY=X", "美元/日圓", ""),
         ("GC=F", "黃金", ""), ("CL=F", "原油 WTI", ""), ("BTC-USD", "比特幣", "")]
TWCHAIN = [("TSM", "台積電 ADR"), ("UMC", "聯電 ADR"), ("ASX", "日月光 ADR"), ("NVDA", "輝達"), ("AMD", "超微"), ("AVGO", "博通"),
           ("MU", "美光"), ("AAPL", "蘋果"), ("MSFT", "微軟"), ("GOOGL", "Google"), ("AMZN", "亞馬遜"), ("META", "Meta"),
           ("TSLA", "特斯拉"), ("ASML", "艾司摩爾"), ("ARM", "安謀"), ("QCOM", "高通"), ("INTC", "英特爾"), ("SMCI", "美超微"),
           ("DELL", "戴爾"), ("ORCL", "甲骨文"), ("MRVL", "邁威爾"), ("ANET", "Arista"), ("VRT", "Vertiv"), ("CRWV", "CoreWeave")]
TW_PAIR = {"TSM": ("2330.TW", 5), "UMC": ("2303.TW", 5), "ASX": ("3711.TW", 2)}  # ADR 1 股＝幾股台股


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def now_et() -> datetime:
    return datetime.now(ET)


def session(d: datetime) -> tuple[datetime, datetime]:
    return d.replace(hour=9, minute=30, second=0, microsecond=0), d.replace(hour=16, minute=0, second=0, microsecond=0)


def universe() -> tuple[list[dict], dict]:
    """昨天盤後的成交值前 100 ＋ 美股清單（產業）"""
    f = DATA / "post" / "latest.json"
    top = ((json.loads(f.read_text("utf-8")).get("us") or {}).get("top") or []) if f.exists() else []
    meta = {x["s"]: {"n": x["n"], "sec": x["sec"]} for x in top}
    uf = sorted((DATA / "post").glob("us_universe_*.json"))
    if uf:
        from post_board import SECTOR_ZH
        for x in json.loads(uf[-1].read_text("utf-8")):
            meta.setdefault(x["s"], {"n": x["n"], "sec": SECTOR_ZH.get(x.get("sec") or "", x.get("sec") or "其他")})
    return top, meta


def download(syms: list[str], **kw) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out = {}
    for i in range(0, len(syms), 150):
        part = syms[i:i + 150]
        raw = yf.download(part, group_by="ticker", progress=False, threads=True, auto_adjust=False, **kw)
        for s in part:
            try:
                df = (raw[s] if len(part) > 1 else raw)[["Close", "Volume"]].astype(float).dropna(subset=["Close"])
                if len(df):
                    out[s] = df
            except Exception:  # noqa: BLE001
                pass
    return out


def quote(df: pd.DataFrame, today) -> dict | None:
    """日線：最後一列是今天（盤中為即時價）→ 漲跌用前一列收盤"""
    if df is None or len(df) < 2:
        return None
    last_d = df.index[-1].date()
    p, pc = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
    return {"p": p, "pc": pc, "ch": p / pc - 1 if pc else None, "v": float(df["Volume"].iloc[-1] or 0), "live": last_d == today,
            "d": str(last_d)}


def build(state: str) -> dict:
    t = now_et()
    today = t.date()
    top, meta = universe()
    stocks = list(dict.fromkeys([x["s"] for x in top] + [s for s, _ in TWCHAIN]))
    syms = [s for s, _ in INDICES] + [s for s, *_ in MACRO] + stocks + [p[0] for p in TW_PAIR.values()] + ["ES=F", "NQ=F"]
    daily = download(syms, period="7d", interval="1d")
    if "^GSPC" in daily:  # 用指數最後一個交易日當「今天」（盤前、週末時就是上一個交易日）
        today = daily["^GSPC"].index[-1].date()
    q = {s: quote(daily.get(s), today) for s in syms}
    # 指數走勢（5 分鐘）
    spark = {}
    try:
        intr = download(["^GSPC", "^IXIC", "^SOX", "TSM"], period="1d", interval="5m")
        for s, df in intr.items():
            spark[s] = [r(v, 2) for v in df["Close"].tolist()]
    except Exception as e:  # noqa: BLE001
        log.info("走勢圖抓取失敗：%s", e)

    def item(s, n, extra=None):
        x = q.get(s)
        if not x:
            return None
        o = {"s": s, "n": n, "p": r(x["p"], 2), "ch": r(x["ch"], 4), "live": x["live"]}
        if extra:
            o.update(extra)
        return o

    idx = [i for i in (item(s, n, {"sp": spark.get(s)}) for s, n in INDICES) if i]
    fut = [i for i in (item(s, n) for s, n in (("ES=F", "S&P 期貨"), ("NQ=F", "那斯達克期貨"))) if i]
    mac = [i for i in (item(s, n, {"u": u}) for s, n, u in MACRO) if i]
    usd = (q.get("TWD=X") or {}).get("p")
    chain = []
    for s, n in TWCHAIN:
        it = item(s, n)
        if not it:
            continue
        x = q[s]
        it["dv"] = r(x["p"] * x["v"] / 1e8, 1) if x["live"] else None
        if s in TW_PAIR and usd and q.get(TW_PAIR[s][0]):
            tw = q[TW_PAIR[s][0]]
            it["prem"] = r(x["p"] * usd / (tw["p"] * TW_PAIR[s][1]) - 1, 4)  # ADR 溢價＝ADR 換算台幣 ÷ 台股收盤
            it["twp"] = r(tw["p"], 1)
            it["twch"] = r(tw["ch"], 4)
            it["eqv"] = r(x["p"] * usd / TW_PAIR[s][1], 1)
        chain.append(it)
    rows = []
    for s in stocks:
        x = q.get(s)
        if not x or not x["live"]:
            continue
        m = meta.get(s, {})
        rows.append({"s": s, "n": m.get("n", s), "sec": m.get("sec") or "其他", "p": r(x["p"], 2), "ch": r(x["ch"], 4),
                     "dv": r(x["p"] * x["v"] / 1e8, 2)})
    rows.sort(key=lambda x: -(x["dv"] or 0))
    rows = rows[:100]
    for i, x in enumerate(rows, 1):
        x["rk"] = i
    secs = {}
    for x in rows:
        g = secs.setdefault(x["sec"], {"k": x["sec"], "cnt": 0, "dv": 0.0, "chs": []})
        g["cnt"] += 1
        g["dv"] += x["dv"] or 0
        g["chs"].append(x["ch"] or 0)
    sectors = sorted(({"k": g["k"], "cnt": g["cnt"], "dv": r(g["dv"], 1), "ch": r(sum(g["chs"]) / len(g["chs"]), 4),
                       "up": sum(c > 0 for c in g["chs"])} for g in secs.values()), key=lambda g: -g["dv"])
    o, c = session(t)
    res = {"state": state, "et": f"{t:%m/%d %H:%M}", "tw": f"{datetime.now(TW):%m/%d %H:%M}", "date": str(today),
           "sess": f"{today:%m/%d}",
           "open_tw": f"{o.astimezone(TW):%H:%M}", "close_tw": f"{c.astimezone(TW):%H:%M}",
           "idx": idx, "fut": fut, "mac": mac, "chain": chain, "top": rows, "sectors": sectors,
           "up": sum((x["ch"] or 0) > 0 for x in rows), "dn": sum((x["ch"] or 0) < 0 for x in rows)}
    return res


def earnings_today() -> list[dict]:
    f = DATA / "extra" / "latest.json"
    if not f.exists():
        return []
    rows = (json.loads(f.read_text("utf-8")).get("earn") or {}).get("rows") or []
    tw_today = datetime.now(TW).date()
    keep = {str(tw_today), str(tw_today + timedelta(days=1))}
    return [x for x in rows if x.get("d") in keep][:12]


def summarize(res: dict) -> list[str]:
    """收盤重點（純規則整理，數字都來自上面抓到的資料）"""
    pct = lambda v: "—" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    out = []
    ix = {x["s"]: x for x in res["idx"]}
    if ix:
        out.append("四大指數：" + "、".join(f"{x['n']} {pct(x['ch'])}" for x in res["idx"] if x["s"] != "^VIX"
                                         and x["s"] != "^RUT"))
    if "^VIX" in ix:
        v = ix["^VIX"]
        out.append(f"VIX {v['p']}（{pct(v['ch'])}）" + ("，市場偏緊張" if (v["p"] or 0) >= 20 else "，情緒平穩"))
    ch = {x["s"]: x for x in res["chain"]}
    if "TSM" in ch:
        t = ch["TSM"]
        s = f"台積電 ADR {t['p']}（{pct(t['ch'])}）"
        if t.get("prem") is not None:
            s += f"，換算台股約 {t['eqv']} 元、溢價 {t['prem'] * 100:.1f}%（台股收 {t['twp']}）"
        out.append(s)
    if res["sectors"]:
        ss = sorted(res["sectors"], key=lambda g: -(g["ch"] or 0))
        out.append("強勢族群：" + "、".join(f"{g['k']} {pct(g['ch'])}" for g in ss[:3]))
        out.append("弱勢族群：" + "、".join(f"{g['k']} {pct(g['ch'])}" for g in ss[-3:][::-1]))
    if res["top"]:
        t5 = res["top"][:5]
        out.append("成交值最大：" + "、".join(f"{x['s']} {pct(x['ch'])}" for x in t5))
        g = sorted(res["top"], key=lambda x: -(x["ch"] or 0))
        out.append("前 100 大漲最多：" + "、".join(f"{x['s']} {pct(x['ch'])}" for x in g[:5]))
        out.append("前 100 大跌最多：" + "、".join(f"{x['s']} {pct(x['ch'])}" for x in g[-5:][::-1]))
        out.append(f"前 100 大：上漲 {res['up']} 檔、下跌 {res['dn']} 檔")
    semi = [ch[s] for s in ("NVDA", "AMD", "AVGO", "MU", "ASML", "ARM", "QCOM", "INTC") if s in ch]
    if semi:
        out.append("半導體：" + "、".join(f"{x['n']} {pct(x['ch'])}" for x in semi))
    m = {x["s"]: x for x in res["mac"]}
    seg = []
    if "^TNX" in m:
        seg.append(f"10 年債 {m['^TNX']['p']:.2f}%")
    if "DX-Y.NYB" in m:
        seg.append(f"美元指數 {m['DX-Y.NYB']['p']:.2f}（{pct(m['DX-Y.NYB']['ch'])}）")
    if "TWD=X" in m:
        seg.append(f"美元/台幣 {m['TWD=X']['p']:.3f}")
    if "CL=F" in m:
        seg.append(f"原油 {pct(m['CL=F']['ch'])}")
    if "GC=F" in m:
        seg.append(f"黃金 {pct(m['GC=F']['ch'])}")
    if seg:
        out.append("利率匯率原物料：" + "、".join(seg))
    return out


def save(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if res["state"] == "收盤":
        res["summary"] = summarize(res)
        res["earn"] = earnings_today()
        (OUT / f"close_{res['date'].replace('-', '')}.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    else:  # 盤中沿用最近一次收盤重點，讓網頁一直有東西看
        last = sorted(OUT.glob("close_*.json"))
        if last:
            c = json.loads(last[-1].read_text("utf-8"))
            res["last_close"] = {"date": c["date"], "summary": c.get("summary", []), "earn": c.get("earn", [])}
    (OUT / "latest.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    tpl = (BASE / "us_live_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(res, ensure_ascii=False))
    (BASE / "us_live_fragment.html").write_text(frag, "utf-8")
    (BASE / "us_live.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>美股即時</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:1180px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")


def is_trading_day(d) -> bool:
    if d.weekday() >= 5:
        return False
    try:  # 交易所休市（例：感恩節）：pandas_market_calendars 沒裝就只判斷週末
        import pandas_market_calendars as pmc
        return len(pmc.get_calendar("NYSE").valid_days(d, d)) > 0
    except Exception:  # noqa: BLE001
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--close", action="store_true", help="強制產生收盤重點（測試用）")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    t = now_et()
    o, c = session(t)
    if a.wait:
        if not is_trading_day(t.date()) or t > c + timedelta(minutes=20):
            log.info("美股今天（%s）沒有盤或已收盤", t.date())
            sys.exit(2)
        while now_et() < o - timedelta(minutes=5):
            time.sleep(30)
        sys.exit(0)
    if a.close:
        state = "收盤"
    elif not is_trading_day(t.date()):
        state = "休市"
    elif t < o:
        state = "盤前"
    elif t <= c + timedelta(minutes=5):
        state = "盤中"
    else:
        state = "收盤"
    res = build(state)
    if state == "休市":  # 週末：顯示上一個交易日收盤
        res["state"] = "收盤"
        state = "收盤"
    save(res)
    log.info("美股 %s：%s，前 100 有 %d 檔", res["et"], state, len(res["top"]))
    sys.exit(3 if state == "收盤" else 0)


if __name__ == "__main__":
    main()
