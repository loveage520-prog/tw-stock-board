#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全球市場 global_markets.py
=========================
抓各國指數、匯率、利率、原物料的日線資料，產生 markets_data.js，
給 sector_flow.html 的「全球市場」分頁使用。

用法：
    python global_markets.py          # 更新資料（隨時都可以跑，約 10～20 秒）
    python global_markets.py --demo   # 用模擬資料
另外：sector_flow.py 執行時會自動更新一次；market_pulse.py 盤中每 10 分鐘自動更新一次。

要調整顯示哪些指數，編輯 markets_config.yaml。
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

BASE = Path(__file__).resolve().parent
OUT = BASE / "markets_data.js"
log = logging.getLogger("markets")

DEFAULT_GROUPS = {
    "台灣": [["^TWII", "台灣加權"], ["^TWOII", "櫃買指數"], ["TSM", "台積電 ADR"], ["2330.TW", "台積電"]],
    "美國": [["^GSPC", "S&P 500"], ["^DJI", "道瓊工業"], ["^IXIC", "那斯達克"], ["^SOX", "費城半導體"],
             ["^VIX", "VIX 恐慌指數"]],
    "亞洲": [["^N225", "日經 225"], ["^KS11", "韓國綜合"], ["^HSI", "香港恆生"], ["000001.SS", "上證指數"]],
    "歐洲": [["^GDAXI", "德國 DAX"], ["^FTSE", "英國 FTSE"], ["^STOXX50E", "歐洲 STOXX 50"]],
    "匯率・利率・原物料": [["TWD=X", "美元兌台幣"], ["DX-Y.NYB", "美元指數"], ["^TNX", "美債 10 年殖利率"],
                           ["CL=F", "WTI 原油"], ["BZ=F", "布蘭特原油"], ["GC=F", "黃金"], ["BTC-USD", "比特幣"]],
}
# 這些越高代表越避險，漲跌顏色反過來看
INVERSE = {"^VIX", "^TNX", "DX-Y.NYB", "TWD=X"}


def load_groups() -> dict:
    f = BASE / "markets_config.yaml"
    if f.exists():
        g = (yaml.safe_load(f.read_text("utf-8")) or {}).get("groups")
        if g:
            return {k: [[str(a), str(b)] for a, b in v] for k, v in g.items()}
    return DEFAULT_GROUPS


def rsi(c: pd.Series, n=14) -> float:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    v = (100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1]
    return float(v) if v == v else float("nan")


def r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


def summarize(sym: str, name: str, df: pd.DataFrame) -> dict | None:
    df = df.dropna(subset=["Close"])
    if len(df) < 2:
        return None
    if sym == "^TNX" and df["Close"].iloc[-1] > 20:  # 有些版本是殖利率 × 10
        df = df.copy()
        df[["Open", "High", "Low", "Close"]] /= 10
    c = df["Close"]
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    ma = {n: float(c.tail(n).mean()) if len(c) >= n else float("nan") for n in (5, 20, 60, 120)}
    hi52, lo52 = float(df["High"].tail(252).max()), float(df["Low"].tail(252).min())
    if last > ma[20] > ma[60]:
        trend = "多頭"
    elif last < ma[20] < ma[60]:
        trend = "空頭"
    else:
        trend = "整理"
    tail = df.tail(160)
    return {
        "s": sym, "n": name, "last": r(last, 4), "chg": r(last - prev, 4), "pct": r(last / prev - 1, 5),
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "r5": r(last / float(c.iloc[-6]) - 1, 4) if len(c) > 5 else None,
        "r20": r(last / float(c.iloc[-21]) - 1, 4) if len(c) > 20 else None,
        "ytd": r(last / float(c[c.index.year == df.index[-1].year].iloc[0]) - 1, 4),
        "ma20": r(ma[20], 4), "ma60": r(ma[60], 4), "above20": last > ma[20], "above60": last > ma[60],
        "hi52": r(hi52, 4), "lo52": r(lo52, 4), "dd": r(last / hi52 - 1, 4), "rsi": r(rsi(c), 1),
        "trend": trend, "inv": sym in INVERSE,
        "spark": [r(v, 4) for v in c.tail(60).tolist()],
        "k": [[d.strftime("%Y-%m-%d"), r(o, 4), r(h, 4), r(l, 4), r(cl, 4)]
              for d, o, h, l, cl in zip(tail.index, tail["Open"], tail["High"], tail["Low"], tail["Close"])],
    }


def fetch_live(groups: dict) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    syms = [s for v in groups.values() for s, _ in v]
    raw = yf.download(syms, period="1y", interval="1d", auto_adjust=False, group_by="ticker",
                      progress=False, threads=True)
    out = {}
    for s in syms:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close"]].astype(float)
            df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz is not None else pd.to_datetime(df.index)
            if df["Close"].notna().sum() >= 2:
                out[s] = df
        except Exception as e:  # noqa: BLE001
            log.warning("%s 失敗：%s", s, e)
    return out


def fetch_demo(groups: dict) -> dict[str, pd.DataFrame]:
    base = {"^TWII": 46041, "^TWOII": 280, "TSM": 418.5, "2330.TW": 2385, "^GSPC": 7623.7, "^DJI": 52215,
            "^IXIC": 25955, "^SOX": 10779, "^VIX": 19.45, "^N225": 62690, "^KS11": 5550, "^HSI": 27000,
            "000001.SS": 3900, "^GDAXI": 25000, "^FTSE": 10200, "^STOXX50E": 6000, "TWD=X": 32.57,
            "DX-Y.NYB": 101.75, "^TNX": 4.96, "CL=F": 103.27, "BZ=F": 116.7, "GC=F": 4110, "BTC-USD": 120000}
    out = {}
    idx = pd.bdate_range(end=pd.Timestamp("2026-10-22"), periods=250)
    for k, (s, _) in enumerate([x for v in groups.values() for x in v]):
        rng = np.random.default_rng(k + 11)
        vol = 0.02 if s in ("^VIX", "BTC-USD") else 0.009
        path = np.exp(np.cumsum(rng.normal(0.0004, vol, 250)))
        close = base.get(s, 100) * path / path[-1]
        o = close * (1 + rng.normal(0, vol / 3, 250))
        out[s] = pd.DataFrame({"Open": o, "High": np.maximum(o, close) * (1 + abs(rng.normal(0, vol / 2, 250))),
                               "Low": np.minimum(o, close) * (1 - abs(rng.normal(0, vol / 2, 250))), "Close": close},
                              index=idx)
    return out


def margin_maintenance(token: str) -> dict | None:
    """大盤融資維持率（FinMind）。抓不到就略過"""
    try:
        params = {"dataset": "TaiwanTotalExchangeMarginMaintenance",
                  "start_date": (date.today() - timedelta(days=120)).isoformat()}
        headers = {}
        if token:
            params["token"] = token
            headers["Authorization"] = f"Bearer {token}"
        js = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, headers=headers, timeout=20).json()
        df = pd.DataFrame(js.get("data", []))
        if df.empty:
            return None
        col = next(c for c in df.columns if c not in ("date", "stock_id") and pd.api.types.is_numeric_dtype(df[c]))
        df = df.sort_values("date")
        v = df[col].astype(float)
        return {"date": str(df["date"].iloc[-1]), "last": r(v.iloc[-1], 1),
                "chg": r(v.iloc[-1] - v.iloc[-2], 1) if len(v) > 1 else None, "spark": [r(x, 1) for x in v.tail(60)]}
    except Exception as e:  # noqa: BLE001
        log.info("融資維持率略過：%s", e)
        return None


def risk_gauge(items: dict[str, dict]) -> dict:
    """用 VIX、美股趨勢、美債、美元、油價打分數：正＝風險偏好，負＝避險"""
    score, notes = 0, []

    def g(s):
        return items.get(s)

    v = g("^VIX")
    if v:
        if v["last"] < 15:
            score += 1; notes.append(f"VIX {v['last']:.1f} 偏低，市場樂觀")
        elif v["last"] > 30:
            score -= 2; notes.append(f"VIX {v['last']:.1f} 恐慌")
        elif v["last"] > 22:
            score -= 1; notes.append(f"VIX {v['last']:.1f} 偏高")
    for s, nm in (("^GSPC", "S&P 500"), ("^SOX", "費半")):
        x = g(s)
        if x:
            score += 1 if x["above20"] else -1
            notes.append(f"{nm}{'站上' if x['above20'] else '跌破'}月線")
    t = g("^TNX")
    if t and t.get("r20") is not None and t["last"]:
        bp = (t["last"] - t["last"] / (1 + t["r20"])) * 100
        if bp > 20:
            score -= 1; notes.append(f"美債 10 年 20 日升 {bp:.0f} 個基點")
        elif bp < -20:
            score += 1; notes.append(f"美債 10 年 20 日降 {-bp:.0f} 個基點")
    d = g("DX-Y.NYB")
    if d and d.get("r20") is not None and abs(d["r20"]) > 0.02:
        score += -1 if d["r20"] > 0 else 1
        notes.append(f"美元 20 日{'走強' if d['r20'] > 0 else '走弱'} {abs(d['r20']):.1%}")
    o = g("CL=F")
    if o and o.get("r20") is not None and o["r20"] > 0.15:
        score -= 1; notes.append(f"油價 20 日漲 {o['r20']:.0%}，通膨壓力")
    eq = [g(s) for s in ("^TWII", "^GSPC", "^IXIC", "^SOX", "^N225", "^KS11", "^HSI", "^GDAXI") if g(s)]
    ups = sum(1 for x in eq if x["pct"] and x["pct"] > 0)
    label = "風險偏好" if score >= 2 else "避險" if score <= -2 else "中性"
    return {"score": score, "label": label, "notes": notes, "eq_up": ups, "eq_n": len(eq)}


def build(demo: bool = False) -> Path:
    groups = load_groups()
    data = fetch_demo(groups) if demo else fetch_live(groups)
    items, out_groups = {}, []
    for gname, lst in groups.items():
        rows = []
        for s, n in lst:
            if s in data:
                it = summarize(s, n, data[s])
                if it:
                    items[s] = it
                    rows.append(it)
            else:
                rows.append({"s": s, "n": n, "missing": True})
        out_groups.append({"g": gname, "items": rows})
    adr = None
    if "TSM" in items and "2330.TW" in items and "TWD=X" in items:
        local = items["2330.TW"]["last"]
        adr = {"prem": r(items["TSM"]["last"] * items["TWD=X"]["last"] / 5 / local - 1, 4),
               "adr": items["TSM"]["last"], "fx": items["TWD=X"]["last"], "local": local,
               "adr_date": items["TSM"]["date"], "local_date": items["2330.TW"]["date"]}
    token = ""
    mc = BASE / "config.yaml"
    if mc.exists():
        token = (yaml.safe_load(mc.read_text("utf-8")) or {}).get("finmind_token") or ""
    payload = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "demo": demo, "groups": out_groups,
               "risk": risk_gauge(items), "adr": adr, "margin": None if demo else margin_maintenance(token)}
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=lambda o: None)
    OUT.write_text(f"window.MARKETS = {js};\n", "utf-8")
    miss = [i["n"] for g in out_groups for i in g["items"] if i.get("missing")]
    log.info("全球市場已更新：%d 項%s", len(items), f"（抓不到：{'、'.join(miss)}）" if miss else "")
    return OUT


def main():
    ap = argparse.ArgumentParser(description="全球市場資料")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    print(build(args.demo))


if __name__ == "__main__":
    main()
