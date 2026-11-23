#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
個股／大盤日 K 資料 kline.py（盤後 daily 執行一次）
=====================================================
抓上市櫃普通股＋加權指數、櫃買指數近一年日 K，每檔存成一個小 JSON：
    kline_out/k/2330.json、kline_out/k/TAIEX.json、kline_out/k/OTC.json
內容：{"c":代號,"n":名稱,"d":[日期...],"o":[...],"h":[...],"l":[...],"c_":[收盤...],"v":[成交張數...]}
之後由 cloud/daily.py 推到 GitHub 的 kline 分支，網頁點個股時再讀取（只讀那一檔，不會拖慢網頁）。
另外附上 ETF：0050、0056、00878、00919、00981A、00980A、00985A。

用法：python kline.py
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
OUT = BASE / "kline_out" / "k"
log = logging.getLogger("kline")
warnings.filterwarnings("ignore")
EXTRA = {"0050": ("元大台灣50", "上市"), "0056": ("元大高股息", "上市"), "00878": ("國泰永續高股息", "上市"),
         "00919": ("群益台灣精選高息", "上市"), "00981A": ("主動統一台股增長", "上市"), "00980A": ("主動野村臺灣優選", "上市"),
         "00985A": ("主動野村台灣50", "上市")}
MKT: dict[str, str] = {}
INDEX = {"TAIEX": ("^TWII", "加權指數"), "OTC": ("^TWOII", "櫃買指數")}


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def dump(code: str, name: str, df, vol_div: float, vu: str = "張", fname: str | None = None, m: str | None = None) -> bool:
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    if len(df) < 5:
        return False
    nd = 0 if df["Close"].iloc[-1] >= 1000 and vu == "張" else 2
    o = {"c": code, "n": name, "m": m if m is not None else MKT.get(code, ""), "vu": vu, "t": f"{datetime.now():%Y-%m-%d %H:%M}",
         "d": [f"{x:%Y-%m-%d}" for x in df.index],
         "o": [r(x, nd) for x in df["Open"]], "h": [r(x, nd) for x in df["High"]],
         "l": [r(x, nd) for x in df["Low"]], "c_": [r(x, nd) for x in df["Close"]],
         "v": [int(x / vol_div) if x == x else 0 for x in df["Volume"]]}
    (OUT / f"{fname or code}.json").write_text(json.dumps(o, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return True


US_INDEX = {"^GSPC": "S&P 500", "^IXIC": "那斯達克", "^DJI": "道瓊", "^SOX": "費城半導體", "^RUT": "羅素 2000", "^VIX": "VIX 恐慌指數"}


def us_file(sym: str) -> str:
    """美股檔名：us_NVDA、us__GSPC（^ 換成 _）"""
    return "us_" + sym.replace("^", "_").replace("/", "-")


def us_kline(yf) -> int:
    """美股：S&P 500＋額外清單＋產業／主題 ETF＋主要指數；成交量單位為萬股"""
    names = {}
    uf = sorted((BASE / "pulse_data" / "post").glob("us_universe_*.json"))
    if uf:
        names.update({x["s"]: x["n"] for x in json.loads(uf[-1].read_text("utf-8"))})
    try:
        from post_board import US_EXTRA
        for x in US_EXTRA:
            names.setdefault(x, x)
    except Exception:  # noqa: BLE001
        pass
    try:
        from us_flow import ETFS, BENCH
        names.setdefault(BENCH, "S&P 500 ETF")
        for e in ETFS:
            names.setdefault(e[0], e[1] + " ETF")
    except Exception:  # noqa: BLE001
        pass
    try:
        from us_live import TWCHAIN
        for s_, n in TWCHAIN:
            names.setdefault(s_, n)
    except Exception:  # noqa: BLE001
        pass
    syms, ok = list(names), 0
    for i in range(0, len(syms), 200):
        part = syms[i:i + 200]
        try:
            raw = yf.download(part, period="1y", interval="1d", group_by="ticker", progress=False, threads=True, auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.info("美股第 %d 批失敗：%s", i // 200 + 1, e)
            continue
        for s_ in part:
            try:
                if dump(s_, names[s_], raw[s_][["Open", "High", "Low", "Close", "Volume"]].astype(float), 1e4, vu="萬股",
                        fname=us_file(s_), m="美股"):
                    ok += 1
            except Exception:  # noqa: BLE001
                pass
    for s_, n in US_INDEX.items():
        try:
            df = yf.download(s_, period="1y", interval="1d", progress=False, auto_adjust=False)
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if dump(s_, n, df[["Open", "High", "Low", "Close", "Volume"]].astype(float), 1e8, vu="", fname=us_file(s_), m="美股指數"):
                ok += 1
        except Exception as e:  # noqa: BLE001
            log.info("%s 失敗：%s", n, e)
    log.info("美股日 K：%d 檔", ok)
    return ok


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    import yfinance as yf
    from market_board import load_universe
    uni = load_universe()
    for c, (n, m) in EXTRA.items():
        uni.setdefault(c, {"n": n, "m": m, "i": "ETF"})
    OUT.mkdir(parents=True, exist_ok=True)
    MKT.update({c: v["m"] for c, v in uni.items()})
    sym = {c: f"{c}.{'TW' if v['m'] == '上市' else 'TWO'}" for c, v in uni.items()}
    codes = list(sym)
    ok, t0 = 0, time.time()
    for i in range(0, len(codes), 200):
        part = codes[i:i + 200]
        for attempt in range(2):
            try:
                raw = yf.download([sym[c] for c in part], period="1y", interval="1d", group_by="ticker",
                                  progress=False, threads=True, auto_adjust=False)
                break
            except Exception as e:  # noqa: BLE001
                log.info("第 %d 批失敗：%s", i // 200 + 1, e)
                time.sleep(5)
        else:
            continue
        for c in part:
            try:
                if dump(c, uni[c]["n"], raw[sym[c]][["Open", "High", "Low", "Close", "Volume"]].astype(float), 1000):
                    ok += 1
            except Exception:  # noqa: BLE001
                pass
        log.info("進度 %d/%d（成功 %d）", min(i + 200, len(codes)), len(codes), ok)
    for code, (s, n) in INDEX.items():
        try:
            df = yf.download(s, period="1y", interval="1d", progress=False, auto_adjust=False)
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            if dump(code, n, df[["Open", "High", "Low", "Close", "Volume"]].astype(float), 1e8, vu=""):
                ok += 1
        except Exception as e:  # noqa: BLE001
            log.info("%s 失敗：%s", n, e)
    ok += us_kline(yf)
    (OUT.parent / "index.json").write_text(json.dumps({"t": f"{datetime.now():%Y-%m-%d %H:%M}", "n": ok}), "utf-8")
    log.info("日 K 完成：%d 檔，耗時 %.0f 秒", ok, time.time() - t0)
    if ok < 100:
        sys.exit(1)


if __name__ == "__main__":
    main()
