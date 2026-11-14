#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股板塊資金流向 us_flow.py
============================
用產業／主題 ETF 判斷美股資金往哪裡走：
・相對輪動象限（類似 RRG）：橫軸＝相對 S&P 500 強弱（RS 比率），縱軸＝強弱動能
    右上「領先」、右下「轉弱」、左下「落後」、左上「改善」；每點拖出近 6 週軌跡
・資金流估算（Chaikin 資金流）：每天 (收盤在當日高低區間的位置) × 成交值，加總 5 日／20 日（億美元）
・報酬 1／5／20／60 日、相對大盤、量比、成分股（成交值前 100 中屬於該產業者）

用法：python us_flow.py          （盤後 daily 與美股收盤後都會執行）
輸出：us_flow_fragment.html、pulse_data/us/flow.json
"""
from __future__ import annotations

import json
import logging
import math
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
OUT = DATA / "us"
log = logging.getLogger("usflow")
warnings.filterwarnings("ignore")

BENCH = "SPY"
# (代號, 名稱, 類型, 對應的產業名稱（用來找成分股）或固定成分股)
ETFS = [
    ("XLK", "資訊科技", "產業", "資訊科技"), ("XLC", "通訊服務", "產業", "通訊服務"), ("XLY", "非必需消費", "產業", "非必需消費"),
    ("XLP", "必需消費", "產業", "必需消費"), ("XLV", "醫療保健", "產業", "醫療保健"), ("XLF", "金融", "產業", "金融"),
    ("XLI", "工業", "產業", "工業"), ("XLE", "能源", "產業", "能源"), ("XLB", "原物料", "產業", "原物料"),
    ("XLU", "公用事業", "產業", "公用事業"), ("XLRE", "不動產", "產業", "不動產"),
    ("SMH", "半導體", "主題", ["NVDA", "TSM", "AVGO", "AMD", "ASML", "MU", "QCOM", "INTC", "AMAT", "LRCX", "KLAC", "TXN", "ADI", "MRVL", "ARM"]),
    ("IGV", "軟體", "主題", ["MSFT", "ORCL", "CRM", "PLTR", "ADBE", "NOW", "INTU", "PANW", "CRWD", "SNOW", "DDOG", "ZS"]),
    ("XBI", "生技", "主題", ["VRTX", "REGN", "GILD", "AMGN", "MRNA", "BIIB", "ALNY"]),
    ("KRE", "區域銀行", "主題", ["USB", "PNC", "TFC", "MTB", "FITB", "HBAN", "RF", "KEY"]),
    ("ITA", "航太國防", "主題", ["GE", "RTX", "LMT", "BA", "NOC", "GD", "HWM", "TDG", "AXON"]),
    ("XHB", "住宅建商", "主題", ["DHI", "LEN", "PHM", "NVR", "HD", "LOW"]),
    ("TAN", "太陽能", "主題", ["FSLR", "ENPH", "NXT", "SEDG", "RUN"]),
    ("URA", "鈾・核能", "主題", ["CCJ", "OKLO", "SMR", "NXE", "LEU", "CEG", "VST"]),
    ("IBIT", "比特幣", "主題", ["MSTR", "COIN", "MARA", "RIOT", "HOOD", "IREN"]),
    ("IWM", "小型股", "風格", []), ("QQQ", "那斯達克 100", "風格", []), ("RSP", "S&P 等權", "風格", []),
]
TAIL = 6       # 軌跡點數（每 5 個交易日一點）
RS_N, MOM_N = 50, 10


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def download(syms: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    raw = yf.download(syms, period="1y", interval="1d", group_by="ticker", progress=False, threads=True, auto_adjust=False)
    out = {}
    for s in syms:
        try:
            df = raw[s][["Open", "High", "Low", "Close", "Volume"]].astype(float).dropna()
            if len(df) > RS_N + MOM_N + TAIL * 5:
                out[s] = df
        except Exception:  # noqa: BLE001
            pass
    miss = [s for s in syms if s not in out]
    for s in miss:  # 多執行緒偶爾失敗，單獨重抓
        try:
            df = yf.download(s, period="1y", interval="1d", progress=False, threads=False, auto_adjust=False)
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).dropna()
            if len(df) > RS_N + MOM_N + TAIL * 5:
                out[s] = df
        except Exception:  # noqa: BLE001
            pass
    return out


def members() -> dict[str, list[dict]]:
    f = DATA / "post" / "latest.json"
    top = ((json.loads(f.read_text("utf-8")).get("us") or {}).get("top") or []) if f.exists() else []
    by_sec, by_sym = {}, {}
    for x in top:
        m = {"s": x["s"], "n": x["n"], "p": x["p"], "ch": x["ch"], "dv": x["dv"], "r5": x.get("r5"), "r20": x.get("r20")}
        by_sec.setdefault(x["sec"], []).append(m)
        by_sym[x["s"]] = m
    return {"sec": by_sec, "sym": by_sym}


def quadrant(ratio: float, mom: float) -> str:
    if ratio >= 100:
        return "領先" if mom >= 100 else "轉弱"
    return "改善" if mom >= 100 else "落後"


def build() -> dict:
    syms = [BENCH] + [e[0] for e in ETFS]
    data = download(syms)
    if BENCH not in data:
        raise RuntimeError("抓不到 SPY")
    spy = data[BENCH]["Close"]
    mem = members()
    rows = []
    for code, name, kind, ref in ETFS:
        df = data.get(code)
        if df is None:
            continue
        c, h, lo, v = df["Close"], df["High"], df["Low"], df["Volume"]
        dv = c * v
        rng = (h - lo).replace(0, float("nan"))
        mfv = (((c - lo) - (h - c)) / rng).fillna(0) * dv
        rs = (c / spy.reindex(c.index).ffill())
        ratio = 100 * rs / rs.rolling(RS_N).mean()
        mom = 100 * ratio / ratio.shift(MOM_N)
        tail = [{"x": r(ratio.iloc[-1 - 5 * k], 2), "y": r(mom.iloc[-1 - 5 * k], 2)} for k in range(TAIL)][::-1]
        ret = lambda n: r(c.iloc[-1] / c.iloc[-1 - n] - 1, 4) if len(c) > n else None  # noqa: E731
        sret = lambda n: r(spy.iloc[-1] / spy.iloc[-1 - n] - 1, 4)  # noqa: E731
        if kind == "產業":
            mm = mem["sec"].get(ref, [])
        else:
            mm = [mem["sym"][s] for s in ref if s in mem["sym"]]
        x, y = tail[-1]["x"], tail[-1]["y"]
        rows.append({
            "s": code, "n": name, "k": kind, "p": r(c.iloc[-1]), "r1": ret(1), "r5": ret(5), "r20": ret(20), "r60": ret(60),
            "rel20": r((ret(20) or 0) - (sret(20) or 0), 4), "rel5": r((ret(5) or 0) - (sret(5) or 0), 4),
            "f5": r(mfv.tail(5).sum() / 1e8, 1), "f20": r(mfv.tail(20).sum() / 1e8, 1), "f1": r(mfv.iloc[-1] / 1e8, 1),
            "cmf": r(mfv.tail(20).sum() / dv.tail(20).sum(), 3), "vr": r(dv.iloc[-1] / dv.iloc[-21:-1].mean(), 2),
            "dv": r(dv.iloc[-1] / 1e8, 1), "x": x, "y": y, "q": quadrant(x or 100, y or 100), "tail": tail,
            "spark": [r(v_, 2) for v_ in (c.tail(60) / c.iloc[-60] * 100).tolist()] if len(c) >= 60 else [],
            "mem": sorted(mm, key=lambda m: -(m["dv"] or 0))[:12]})
    last = data[BENCH].index[-1]
    sp = {"r1": r(spy.iloc[-1] / spy.iloc[-2] - 1, 4), "r5": r(spy.iloc[-1] / spy.iloc[-6] - 1, 4), "r20": r(spy.iloc[-1] / spy.iloc[-21] - 1, 4)}
    cnt = {q: sum(1 for x in rows if x["q"] == q and x["k"] != "風格") for q in ("領先", "改善", "轉弱", "落後")}
    return {"date": str(last.date()), "time": f"{datetime.now(ZoneInfo('Asia/Taipei')):%Y-%m-%d %H:%M}", "spy": sp,
            "rows": rows, "cnt": cnt}


def save(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flow.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    tpl = (BASE / "us_flow_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(res, ensure_ascii=False))
    (BASE / "us_flow_fragment.html").write_text(frag, "utf-8")
    (BASE / "us_flow.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>美股板塊資金流向</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:1180px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    res = build()
    save(res)
    log.info("美股板塊 %s：%d 檔 ETF，領先 %d、改善 %d、轉弱 %d、落後 %d", res["date"], len(res["rows"]), *res["cnt"].values())


if __name__ == "__main__":
    main()
