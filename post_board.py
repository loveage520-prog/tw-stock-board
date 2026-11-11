#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盤後籌碼與美股排行 post_board.py
================================
1. 外資、投信近 15 個交易日買賣超（上市，證交所三大法人買賣超日報）
   ・15 日累計買超金額前 30、賣超金額前 30
   ・每檔附：15 日內買超天數、目前連續買／賣幾天、今日買賣超、15 日漲跌
2. 美股成交值前 100 名（S&P 500＋Nasdaq-100＋自選清單），依產業分組
   ・價格、漲跌、成交值、量比、5／20 日與今年報酬、均線位置、距 52 週高點、RSI

用法：
    python post_board.py            兩部分都跑（daily_job.py 每天 18:00 會執行）
    python post_board.py --tw       只跑台股法人
    python post_board.py --us       只跑美股
    python post_board.py --demo     模擬資料
    python post_board.py --probe    檢查資料來源

輸出：post_board.html（雙擊打開）、post_board_fragment.html（手機版嵌進資金流向頁）
"""
from __future__ import annotations

import argparse
import warnings
import json
import logging
import math
import sys
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = Path(__file__).resolve().parent
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")
DATA = BASE / "pulse_data"
FLOW = DATA / "flow"
OUT = DATA / "post"
log = logging.getLogger("post")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DAYS, TOP = 15, 30

# 美股：S&P 500 以外（含 Nasdaq-100 中不在 S&P 500 的成分股），想一起比較的股票（ADR、熱門中小型股）可以加在這裡
US_EXTRA = ["MELI", "AZN", "CCEP", "GFS", "MDB", "ZS", "TEAM", "MRVL", "TSM", "ASML", "BABA", "PDD", "ARM", "MSTR", "COIN", "HOOD", "SOFI", "RKLB", "IONQ", "RGTI",
            "QBTS", "OKLO", "SMR", "NBIS", "CRWV", "CRCL", "HIMS", "NIO", "MARA", "RIOT", "APLD", "CIFR",
            "IREN", "SNOW", "SHOP", "SE", "MELI", "NU", "UBER", "SPOT", "RDDT", "ALAB", "CRDO", "CLS", "VRT"]
SECTOR_ZH = {"Information Technology": "資訊科技", "Technology": "資訊科技", "Communication Services": "通訊服務",
             "Consumer Discretionary": "非必需消費", "Consumer Cyclical": "非必需消費",
             "Consumer Staples": "必需消費", "Consumer Defensive": "必需消費", "Health Care": "醫療保健",
             "Healthcare": "醫療保健", "Financials": "金融", "Financial Services": "金融", "Industrials": "工業",
             "Energy": "能源", "Materials": "原物料", "Basic Materials": "原物料", "Real Estate": "不動產",
             "Utilities": "公用事業", "Telecommunications": "通訊服務"}


def num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return math.nan


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


# =============================================================================
# 台股：外資、投信 15 日
# =============================================================================
def load_universe() -> dict:
    files = sorted(FLOW.glob("stock_info_*.json"))
    if not files:
        return {}
    d = json.loads(files[-1].read_text("utf-8"))
    cols = {k: (list(v.values()) if isinstance(v, dict) else v) for k, v in d.items()}
    return {str(s): {"n": n, "i": i or "", "m": "上市" if t == "twse" else "上櫃" if t == "tpex" else ""}
            for s, n, i, t in zip(cols["stock_id"], cols["stock_name"], cols["industry_category"], cols["type"])}


def twse_json(kind: str, d: date) -> dict | None:
    f = FLOW / f"twse_{kind}_{d:%Y%m%d}.json"
    if f.exists():
        js = json.loads(f.read_text("utf-8"))
        return js if js.get("stat") == "OK" else None
    if d >= date.today() and datetime.now().hour < 16:
        return None  # 今天資料還沒公布，不要把空結果存進快取
    url, params = {"T86": ("https://www.twse.com.tw/rwd/zh/fund/T86", {"selectType": "ALLBUT0999"}),
                   "MI": ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"type": "ALLBUT0999"})}[kind]
    rr = requests.get(url, params={**params, "date": f"{d:%Y%m%d}", "response": "json"}, headers=UA, timeout=30)
    rr.raise_for_status()
    js = rr.json()
    FLOW.mkdir(parents=True, exist_ok=True)
    if js.get("stat") == "OK" or d < date.today():  # 今天還沒公布的空結果不存，晚點再抓
        f.write_text(json.dumps(js, ensure_ascii=False), "utf-8")
    time.sleep(3.5)  # 證交所限制查詢頻率
    return js if js.get("stat") == "OK" else None


def is_stock(c: str) -> bool:
    return len(c) == 4 and c.isdigit() and not c.startswith("00")


def parse_t86(js: dict) -> dict[str, tuple[float, float]]:
    f = js["fields"]
    ic = f.index("證券代號")
    fi = [i for i, x in enumerate(f) if "外陸資買賣超股數" in x or x.startswith("外資自營商買賣超")]
    ti = [i for i, x in enumerate(f) if x.startswith("投信買賣超")]
    out = {}
    for row in js["data"]:
        c = str(row[ic]).strip()
        if is_stock(c):
            out[c] = (sum(num(row[i]) for i in fi), sum(num(row[i]) for i in ti))
    return out


def parse_close(js: dict) -> dict[str, float]:
    for t in js.get("tables", []):
        if "每日收盤行情" in (t.get("title") or "") and "證券代號" in (t.get("fields") or []):
            ix = {k: i for i, k in enumerate(t["fields"])}
            return {row[ix["證券代號"]].strip(): num(row[ix["收盤價"]]) for row in t["data"]}
    return {}


def tpex_json(path: str, d: date, **params) -> dict | None:
    """櫃買中心歷史資料（依日期），有快取"""
    f = FLOW / f"tpex_{path.replace('/', '_')}_{d:%Y%m%d}.json"
    if f.exists():
        return json.loads(f.read_text("utf-8"))
    rr = requests.post(f"https://www.tpex.org.tw/www/zh-tw/{path}",
                       params={**params, "date": f"{d:%Y/%m/%d}", "response": "json"}, headers=UA, timeout=30)
    rr.raise_for_status()
    js = rr.json()
    for t in js.get("tables") or []:  # 只留普通股，權證等不存（檔案小很多）
        t["data"] = [row for row in t.get("data") or [] if row and is_stock(str(row[0]).strip())]
    ok = bool(js.get("tables") and js["tables"][0].get("data"))
    if ok or d < date.today():
        FLOW.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(js, ensure_ascii=False), "utf-8")
    time.sleep(1.5)
    return js


def tpex_day(d: date) -> tuple[dict, dict]:
    """上櫃：{代號: (外資及陸資合計買賣超股數, 投信買賣超股數)}、{代號: 收盤價}。失敗回傳空的"""
    ins, px = {}, {}
    try:
        for row in ((tpex_json("insti/dailyTrade", d, type="Daily", sect="EW") or {}).get("tables") or [{}])[0].get("data", []):
            c = str(row[0]).strip()
            if is_stock(c) and len(row) > 13:
                ins[c] = (num(row[10]), num(row[13]))
        for row in ((tpex_json("afterTrading/dailyQuotes", d, type="EW") or {}).get("tables") or [{}])[0].get("data", []):
            c = str(row[0]).strip()
            if is_stock(c):
                px[c] = num(row[2])
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        log.info("%s 櫃買資料抓取失敗：%s", d, e)
    return ins, px


def tw_institutions() -> dict:
    uni = load_universe()
    days, d = [], date.today()
    for _ in range(40):  # 往回找 15 個有資料的交易日
        if d.weekday() < 5:
            try:
                mi = twse_json("MI", d)
                t86 = twse_json("T86", d) if mi else None
            except requests.RequestException as e:
                log.info("%s 證交所資料抓取失敗：%s", d, e)
                mi = t86 = None
            if t86 and mi:
                ins, px = parse_t86(t86), parse_close(mi)
                otc_i, otc_p = tpex_day(d)  # 上櫃
                ins.update(otc_i)
                px.update(otc_p)
                days.append((d, ins, px))
                if len(days) == DAYS:
                    break
        d -= timedelta(days=1)
    if not days:
        raise RuntimeError("找不到證交所三大法人資料")
    days.reverse()  # 舊 → 新
    last_d, _, last_close = days[-1]
    log.info("台股法人：%s～%s，共 %d 個交易日", days[0][0], last_d, len(days))
    codes = set().union(*(set(x[1]) for x in days))
    res = {}
    for who, k in (("外資", 0), ("投信", 1)):
        rows = []
        for c in codes:
            amts = []
            for _, t86, close in days:
                sh, px = t86.get(c, (0.0, 0.0))[k], close.get(c, math.nan)
                amts.append(sh * px / 1e8 if px == px and sh == sh else 0.0)
            tot = sum(amts)
            if abs(tot) < 0.01:
                continue
            streak = 0
            for a in reversed(amts):
                if a > 0 and streak >= 0:
                    streak += 1
                elif a < 0 and streak <= 0:
                    streak -= 1
                else:
                    break
            p0, p1 = days[0][2].get(c, math.nan), last_close.get(c, math.nan)
            info = uni.get(c, {})
            rows.append({"c": c, "n": info.get("n", c), "i": info.get("i", ""), "m": info.get("m", ""), "sum": r(tot, 2),
                         "bd": sum(a > 0 for a in amts), "sd": sum(a < 0 for a in amts), "st": streak,
                         "today": r(amts[-1], 2), "ret": r(p1 / p0 - 1, 4) if p0 and p0 == p0 and p1 == p1 else None,
                         "p": r(p1), "spark": [r(a, 2) for a in amts]})
        buy = sorted([x for x in rows if x["sum"] > 0], key=lambda x: -x["sum"])[:TOP]
        sell = sorted([x for x in rows if x["sum"] < 0], key=lambda x: x["sum"])[:TOP]
        cbuy = sorted([x for x in rows if x["st"] >= 5], key=lambda x: (-x["st"], -x["sum"]))[:TOP]
        csell = sorted([x for x in rows if x["st"] <= -5], key=lambda x: (x["st"], x["sum"]))[:TOP]
        res[who] = {"buy": buy, "sell": sell, "cbuy": cbuy, "csell": csell,
                    "total": r(sum(x["sum"] for x in rows), 1),
                    "n_cbuy": sum(x["st"] >= 5 for x in rows), "n_csell": sum(x["st"] <= -5 for x in rows)}
    return {"from": days[0][0].isoformat(), "to": last_d.isoformat(), "days": len(days),
            "dates": [x[0].strftime("%m/%d") for x in days], "data": res,
            "scope": "上市＋上櫃股票（外資含外資自營商）；金額＝買賣超股數 × 當日收盤價，為估算值"}


# =============================================================================
# 美股：成交值前 100
# =============================================================================
def wiki_table(url: str, sym_cols=("Symbol", "Ticker")) -> pd.DataFrame:
    html = requests.get(url, headers=UA, timeout=30).text
    for t in pd.read_html(StringIO(html)):
        col = next((c for c in sym_cols if c in t.columns), None)
        sec_col = next((c for c in t.columns if str(c) in ("GICS Sector", "ICB Industry"))
                       , next((c for c in t.columns if "Sector" in str(c) or "Industry" in str(c)), None))
        if col and sec_col is not None and len(t) > 90:
            name_col = next((c for c in ("Security", "Company") if c in t.columns), None)
            return pd.DataFrame({"s": t[col].astype(str).str.replace(".", "-", regex=False),
                                 "n": t[name_col] if name_col else t[col], "sec": t[sec_col]})
    raise RuntimeError(f"讀不到表格：{url}")


def us_universe() -> pd.DataFrame:
    f = OUT / f"us_universe_{date.today():%Y%m}.json"
    if f.exists():
        return pd.read_json(StringIO(f.read_text("utf-8"))).drop_duplicates("s")
    parts = []
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",):
        try:
            parts.append(wiki_table(url))
        except Exception as e:  # noqa: BLE001
            log.info("美股清單抓取失敗（%s）：%s", url, e)
    uni = pd.concat(parts).drop_duplicates("s") if parts else pd.DataFrame(columns=["s", "n", "sec"])
    old = sorted(OUT.glob("us_universe_*.json"))
    if uni.empty and old:  # 抓不到就用上個月的清單
        return pd.read_json(StringIO(old[-1].read_text("utf-8"))).drop_duplicates("s")
    extra = [s for s in US_EXTRA if s not in set(uni["s"])]
    if extra:
        import yfinance as yf
        rows = []
        for s in extra:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    info = yf.Ticker(s).info
                rows.append({"s": s, "n": info.get("shortName", s), "sec": info.get("sector", "")})
            except Exception:  # noqa: BLE001
                rows.append({"s": s, "n": s, "sec": ""})
        uni = pd.concat([uni, pd.DataFrame(rows)])
    uni = uni.drop_duplicates("s")
    OUT.mkdir(parents=True, exist_ok=True)
    f.write_text(uni.to_json(orient="records", force_ascii=False), "utf-8")
    return uni


def rsi(c: pd.Series, n=14) -> float:
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] else 100.0


def us_board(demo=False) -> dict:
    if demo:
        rng = np.random.default_rng(5)
        uni = pd.DataFrame({"s": [f"DM{i}" for i in range(300)], "n": [f"Demo Corp {i}" for i in range(300)],
                            "sec": rng.choice(list(SECTOR_ZH)[:11], 300)})
        idx = pd.bdate_range(end=pd.Timestamp.today(), periods=260)
        frames = {}
        for s in uni["s"]:
            c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 260)))
            frames[s] = pd.DataFrame({"Close": c, "Volume": rng.lognormal(16, 1, 260)}, index=idx)
    else:
        import yfinance as yf
        uni = us_universe()
        syms = list(uni["s"])
        log.info("美股：下載 %d 檔一年日線", len(syms))
        frames = {}
        for i in range(0, len(syms), 200):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(syms[i:i + 200], period="1y", interval="1d", auto_adjust=False, group_by="ticker",
                                  progress=False, threads=True)
            for s in syms[i:i + 200]:
                try:
                    df = raw[s][["Close", "Volume"]].astype(float).dropna()
                    if len(df) > 30:
                        frames[s] = df
                except Exception:  # noqa: BLE001
                    pass
    meta = uni.drop_duplicates("s").set_index("s").to_dict("index")
    last = max(df.index[-1] for df in frames.values())
    rows = []
    for s, df in frames.items():
        if df.index[-1] != last:
            continue  # 當天沒交易（停牌等）
        c, v = df["Close"], df["Volume"]
        dv = c * v
        px = float(c.iloc[-1])
        ma20, ma50, ma200 = (float(c.tail(n).mean()) for n in (20, 50, 200))
        y0 = c[c.index.year == last.year]
        sec = meta.get(s, {}).get("sec", "") or ""
        rows.append({"s": s, "n": meta.get(s, {}).get("n", s), "sec": SECTOR_ZH.get(sec, sec or "其他"),
                     "p": r(px), "ch": r(px / c.iloc[-2] - 1, 4), "dv": r(dv.iloc[-1] / 1e8, 2),
                     "vr": r(dv.iloc[-1] / dv.iloc[-21:-1].mean(), 2),
                     "r5": r(px / c.iloc[-6] - 1, 4) if len(c) > 6 else None,
                     "r20": r(px / c.iloc[-21] - 1, 4) if len(c) > 21 else None,
                     "ytd": r(px / y0.iloc[0] - 1, 4) if len(y0) > 1 else None,
                     "a20": px > ma20, "a50": px > ma50, "a200": px > ma200,
                     "hi": r(px / c.tail(252).max() - 1, 4), "rsi": r(rsi(c), 0)})
    rows.sort(key=lambda x: -(x["dv"] or 0))
    top = rows[:100]
    for i, x in enumerate(top, 1):
        x["rk"] = i
    secs = {}
    for x in top:
        g = secs.setdefault(x["sec"], {"k": x["sec"], "cnt": 0, "dv": 0.0, "ch": []})
        g["cnt"] += 1
        g["dv"] += x["dv"] or 0
        g["ch"].append(x["ch"] or 0)
    sectors = sorted(({"k": g["k"], "cnt": g["cnt"], "dv": r(g["dv"], 1), "ch": r(sum(g["ch"]) / len(g["ch"]), 4)}
                      for g in secs.values()), key=lambda g: -g["dv"])
    log.info("美股：%s，共 %d 檔有資料，取前 100", last.date(), len(rows))
    return {"date": str(last.date()), "top": top, "sectors": sectors, "n": len(rows),
            "total_dv": r(sum(x["dv"] or 0 for x in rows), 0), "top_dv": r(sum(x["dv"] or 0 for x in top), 0)}


# =============================================================================
def demo_tw() -> dict:
    rng = np.random.default_rng(3)
    uni = list(load_universe().items())[:400] or [(str(1101 + i), {"n": f"示範{i}", "i": "示範業"}) for i in range(400)]
    def mk():
        out = []
        for c, info in uni:
            amts = list(rng.normal(0, 2, DAYS) + rng.normal(0, 1.5))
            st = 0
            for a in reversed(amts):
                if (a > 0 and st >= 0):
                    st += 1
                elif (a < 0 and st <= 0):
                    st -= 1
                else:
                    break
            out.append({"c": c, "n": info["n"], "i": info["i"], "sum": r(sum(amts)), "bd": sum(a > 0 for a in amts),
                        "sd": sum(a < 0 for a in amts), "st": st, "today": r(amts[-1]), "ret": r(rng.normal(0, .08), 4),
                        "p": r(rng.uniform(20, 900)), "spark": [r(a) for a in amts]})
        return {"buy": sorted([x for x in out if x["sum"] > 0], key=lambda x: -x["sum"])[:TOP],
                "sell": sorted([x for x in out if x["sum"] < 0], key=lambda x: x["sum"])[:TOP],
                "cbuy": sorted([x for x in out if x["st"] >= 5], key=lambda x: (-x["st"], -x["sum"]))[:TOP],
                "csell": sorted([x for x in out if x["st"] <= -5], key=lambda x: (x["st"], x["sum"]))[:TOP],
                "total": r(sum(x["sum"] for x in out), 1), "n_cbuy": sum(x["st"] >= 5 for x in out),
                "n_csell": sum(x["st"] <= -5 for x in out)}
    return {"from": "示範", "to": "示範", "days": DAYS, "dates": [f"D{i}" for i in range(DAYS)],
            "data": {"外資": mk(), "投信": mk()}, "scope": "示範資料"}


def save(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "latest.json"
    old = json.loads(f.read_text("utf-8")) if f.exists() else {}
    for k in ("tw", "us"):  # 只跑其中一部分時，保留另一部分上次的結果
        if res.get(k) is None and old.get(k):
            res[k] = old[k]
    f.write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    tpl = (BASE / "post_board_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(res, ensure_ascii=False))
    (BASE / "post_board_fragment.html").write_text(frag, "utf-8")
    (BASE / "post_board.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>法人與美股排行</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:1180px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")
    log.info("→ post_board.html")


def probe():
    print("1) 證交所三大法人（T86）")
    try:
        d = date.today() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        js = twse_json("T86", d)
        print("   ", d, "OK" if js else "無資料", (len(js["data"]) if js else ""))
    except Exception as e:  # noqa: BLE001
        print("   失敗：", e)
    print("2) 美股清單（Wikipedia）")
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",):
        try:
            print("   ", url.rsplit("/", 1)[-1], len(wiki_table(url)), "檔")
        except Exception as e:  # noqa: BLE001
            print("    失敗：", e, "（若顯示缺少 lxml，執行 pip install lxml）")
    print("3) yfinance")
    try:
        import yfinance as yf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(["AAPL", "NVDA"], period="5d", progress=False, group_by="ticker")
        print("    OK，最新日期", df.index[-1].date())
    except Exception as e:  # noqa: BLE001
        print("    失敗：", e)


def main():
    ap = argparse.ArgumentParser()
    for a in ("--tw", "--us", "--demo", "--probe"):
        ap.add_argument(a, action="store_true")
    args = ap.parse_args()
    if sys.platform == "win32" and sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    hs = [logging.FileHandler(DATA / "logs" / f"post_{date.today():%Y%m%d}.log", encoding="utf-8")]
    if sys.stdout is not None:
        hs.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", handlers=hs)
    if args.probe:
        probe()
        return
    do_tw, do_us = (args.tw or not args.us), (args.us or not args.tw)
    res = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "tw": None, "us": None, "demo": args.demo}
    ok = True
    if do_tw:
        try:
            res["tw"] = demo_tw() if args.demo else tw_institutions()
        except Exception as e:  # noqa: BLE001
            ok = False
            log.info("台股法人失敗：%s", e)
    if do_us:
        try:
            res["us"] = us_board(args.demo)
        except Exception as e:  # noqa: BLE001
            ok = False
            log.info("美股失敗：%s", e)
    save(res)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
