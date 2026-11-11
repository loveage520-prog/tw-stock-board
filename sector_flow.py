#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板塊資金流向 sector_flow.py
==========================
盤後執行，用三大法人買賣超計算每個板塊（產業＋自訂概念族群）的資金流向，
產生互動網頁 sector_flow.html：象限泡泡圖、板塊排行、個股異常、我的持股。

用法：
    python sector_flow.py            # 抓資料並產生網頁（建議每天 17:00 後執行）
    python sector_flow.py --demo     # 用模擬資料產生網頁，看效果用
    python sector_flow.py --probe    # 檢查資料來源

泡泡圖怎麼看：
    橫軸  近 5 日法人淨買超金額（億）：越右買越多
    縱軸  近 5 日平均每天淨買 − 近 20 日平均每天淨買（億／天）：越上代表最近買得比平常更積極
    大小  近 20 日成交金額：越大代表板塊越熱門
    象限  右上 漲潮（加速流入）、右下 輪動（流入但放緩）、左上 觀望（流出但放緩）、左下 退潮（加速流出）

資料來源：
    上市  證交所「三大法人買賣超日報」與「每日收盤行情」（全市場，每天 2 次查詢）
    上櫃  FinMind（只抓概念族群裡的上櫃股，逐檔查詢）
    分類  FinMind 台股基本資料（產業別、上市或上櫃）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

BASE = Path(__file__).resolve().parent
CACHE = BASE / "pulse_data" / "flow"
OUT = BASE / "sector_flow.html"
log = logging.getLogger("flow")
NAN = float("nan")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def num(s) -> float:
    s = re.sub(r"<[^>]+>", "", str(s)).replace(",", "").strip()
    if s in ("", "--", "---", "X", "除權息", "除權", "除息"):
        return NAN
    try:
        return float(s)
    except ValueError:
        return NAN


def sign_of(s) -> int:
    s = re.sub(r"<[^>]+>", "", str(s))
    return -1 if "-" in s else 1 if "+" in s else 0


def is_stock(code: str) -> bool:
    return len(code) == 4 and code.isdigit() and code[0] != "0"


# =============================================================================
# 證交所（上市全市場）
# =============================================================================
def twse_tables(js: dict) -> list[tuple[str, list, list]]:
    """同時支援新版 tables 格式與舊版 fieldsN/dataN 格式"""
    out = []
    for t in js.get("tables") or []:
        if t.get("fields") and t.get("data"):
            out.append((t.get("title", ""), t["fields"], t["data"]))
    if js.get("fields") and js.get("data"):
        out.append((js.get("title", ""), js["fields"], js["data"]))
    for k in js:
        m = re.fullmatch(r"fields(\d+)", k)
        if m and js.get(f"data{m.group(1)}"):
            out.append((js.get(f"subtitle{m.group(1)}", ""), js[k], js[f"data{m.group(1)}"]))
    return out


def parse_mi(js: dict) -> tuple[pd.DataFrame | None, float]:
    """每日收盤行情 → 個股收盤、漲跌、成交金額；另回傳加權指數漲跌幅"""
    stocks, taiex = None, NAN
    for title, fields, data in twse_tables(js):
        ix = {f: i for i, f in enumerate(fields)}
        if "證券代號" in ix and "收盤價" in ix and stocks is None:
            rows = []
            for r in data:
                code = str(r[ix["證券代號"]]).strip()
                if not is_stock(code):
                    continue
                close = num(r[ix["收盤價"]])
                sgn = sign_of(r[ix["漲跌(+/-)"]]) if "漲跌(+/-)" in ix else 0
                diff = num(r[ix["漲跌價差"]]) if "漲跌價差" in ix else NAN
                ref = close - sgn * diff if diff == diff and close == close else NAN
                rows.append({"code": code, "name": str(r[ix.get("證券名稱", 1)]).strip(), "close": close,
                             "chg": (close / ref - 1) if ref and ref == ref else NAN,
                             "amount": num(r[ix["成交金額"]]) if "成交金額" in ix else NAN})
            stocks = pd.DataFrame(rows)
        if "指數" in ix and taiex != taiex:
            for r in data:
                if str(r[ix["指數"]]).strip() == "發行量加權股價指數":
                    p = next((i for f, i in ix.items() if "百分比" in f), None)
                    s = next((i for f, i in ix.items() if "漲跌(+/-)" in f), None)
                    if p is not None:
                        # 百分比欄位本身可能已帶正負號（例如 -1.81），所以取絕對值再套用漲跌符號
                        v = num(r[p])
                        sg = sign_of(r[s]) if s is not None else 0
                        taiex = abs(v) / 100 * sg if sg else v / 100
    return stocks, taiex


def parse_t86(js: dict) -> pd.DataFrame | None:
    tabs = twse_tables(js)
    if not tabs:
        return None
    _, fields, data = tabs[0]

    def cols(pred):
        return [i for i, f in enumerate(fields) if pred(f)]

    i_code = fields.index("證券代號")
    fi = cols(lambda f: "外陸資買賣超股數" in f) + cols(lambda f: f.startswith("外資自營商買賣超"))
    if not fi:
        fi = cols(lambda f: f.startswith("外資") and "買賣超" in f)
    ti = cols(lambda f: f.startswith("投信買賣超"))
    di = cols(lambda f: f == "自營商買賣超股數") or cols(lambda f: f.startswith("自營商買賣超股數("))
    rows = []
    for r in data:
        code = str(r[i_code]).strip()
        if not is_stock(code):
            continue
        f_, t_, d_ = (sum(num(r[i]) for i in idx if num(r[i]) == num(r[i])) for idx in (fi, ti, di))
        rows.append({"code": code, "f": f_, "t": t_, "d": d_})
    return pd.DataFrame(rows)


class Twse:
    URLS = {"T86": ("https://www.twse.com.tw/rwd/zh/fund/T86", {"selectType": "ALLBUT0999"}),
            "MI": ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"type": "ALLBUT0999"})}

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": "https://www.twse.com.tw/"})
        self.last = 0.0

    def raw(self, kind: str, d: date) -> dict:
        f = CACHE / f"twse_{kind}_{d:%Y%m%d}.json"
        if f.exists():
            return json.loads(f.read_text("utf-8"))
        wait = 3.5 - (time.time() - self.last)  # 證交所限制查詢頻率，太快會被暫時封鎖
        if wait > 0:
            time.sleep(wait)
        url, extra = self.URLS[kind]
        r = self.s.get(url, params={"date": d.strftime("%Y%m%d"), "response": "json", **extra}, timeout=30)
        self.last = time.time()
        r.raise_for_status()
        js = r.json()
        stat = str(js.get("stat", ""))
        if stat == "OK" or ("沒有符合" in stat and d < date.today()):
            f.write_text(json.dumps(js, ensure_ascii=False), "utf-8")
        elif stat != "OK" and "沒有符合" not in stat:
            raise RuntimeError(f"證交所回應：{stat}")
        return js


# =============================================================================
# FinMind（上櫃個股、產業分類）
# =============================================================================
class FinMind:
    URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, token: str):
        self.token = token
        self.s = requests.Session()

    def get(self, dataset: str, data_id: str | None = None, start: str | None = None) -> pd.DataFrame:
        params = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = start
        headers = {}
        if self.token:
            params["token"] = self.token
            headers["Authorization"] = f"Bearer {self.token}"
        r = self.s.get(self.URL, params=params, headers=headers, timeout=40)
        if r.status_code == 402:
            raise RuntimeError("FinMind 額度用完，請填 token 或稍後再試")
        js = r.json()
        if js.get("status") != 200:
            raise RuntimeError(js.get("msg"))
        time.sleep(0.3)
        return pd.DataFrame(js.get("data", []))

    def info(self) -> pd.DataFrame:
        f = CACHE / f"stock_info_{date.today():%Y%m}.json"
        if f.exists():
            return pd.read_json(f)
        df = self.get("TaiwanStockInfo")
        df.to_json(f, force_ascii=False)
        return df

    def otc_stock(self, code: str, start: date) -> pd.DataFrame:
        f = CACHE / f"fm_{code}_{date.today():%Y%m%d}.json"
        if f.exists():
            return pd.read_json(f, dtype={"code": str})
        inst = self.get("TaiwanStockInstitutionalInvestorsBuySell", code, start.isoformat())
        px = self.get("TaiwanStockPrice", code, start.isoformat())
        if px.empty:
            return pd.DataFrame()
        px = px[px["close"] > 0].copy()
        px["ref"] = px["close"] - px["spread"]
        out = pd.DataFrame({"date": pd.to_datetime(px["date"]), "code": code, "close": px["close"].astype(float),
                            "chg": (px["close"] / px["ref"] - 1).astype(float),
                            "amount": px["Trading_money"].astype(float)})
        if not inst.empty:
            inst["net"] = inst["buy"].astype(float) - inst["sell"].astype(float)
            p = inst.pivot_table(index="date", columns="name", values="net", aggfunc="sum").fillna(0)
            g = lambda *ns: p[[n for n in ns if n in p]].sum(axis=1) if any(n in p for n in ns) else 0  # noqa: E731
            net = pd.DataFrame({"f": g("Foreign_Investor", "Foreign_Dealer_Self"), "t": g("Investment_Trust"),
                                "d": g("Dealer_self", "Dealer_Hedging")})
            net.index = pd.to_datetime(net.index)
            out = out.merge(net, left_on="date", right_index=True, how="left")
        for c in ("f", "t", "d"):
            out[c] = out.get(c, 0)
            out[c] = out[c].fillna(0)
        out.to_json(f, force_ascii=False, date_format="iso")
        return out


# =============================================================================
# 組資料
# =============================================================================
def load_cfg() -> tuple[dict, dict, str, dict]:
    fc = yaml.safe_load((BASE / "flow_config.yaml").read_text("utf-8")) or {}
    themes = {}
    pc = BASE / "pulse_config.yaml"
    if pc.exists():
        for k, v in ((yaml.safe_load(pc.read_text("utf-8")) or {}).get("groups") or {}).items():
            themes[k] = [str(x) for x in v]
    for k, v in (fc.get("themes") or {}).items():
        themes[k] = [str(x) for x in v]
    token, holdings = "", {}
    mc = BASE / "config.yaml"
    if mc.exists():
        c = yaml.safe_load(mc.read_text("utf-8")) or {}
        token = c.get("finmind_token") or ""
        holdings = {str(h["symbol"]): h.get("name", str(h["symbol"])) for h in c.get("holdings") or []
                    if str(h.get("market", "")).upper() == "TW"}
    return fc, themes, token, holdings


def collect_live(n_days: int, themes: dict, token: str) -> tuple[pd.DataFrame, dict, dict, dict, list]:
    CACHE.mkdir(parents=True, exist_ok=True)
    tw, fm = Twse(), FinMind(token)
    # 1) 找最近 n_days 個交易日（每日收盤行情有資料的日子）
    days, mi, taiex = [], {}, {}
    d = date.today()
    log.info("讀取證交所每日資料（第一次約需 3～5 分鐘，之後只抓新的一天）")
    for _ in range(n_days * 2 + 20):
        if d.weekday() < 5:
            try:
                js = tw.raw("MI", d)
                if js.get("stat") == "OK":
                    stocks, tx = parse_mi(js)
                    if stocks is not None and not stocks.empty:
                        days.append(d)
                        mi[d], taiex[d] = stocks, tx
                        if len(days) % 5 == 0:
                            log.info("  已取得 %d／%d 個交易日", len(days), n_days)
            except Exception as e:  # noqa: BLE001
                log.warning("每日收盤 %s 失敗：%s", d, e)
        if len(days) >= n_days:
            break
        d -= timedelta(days=1)
    if not days:
        raise RuntimeError("抓不到證交所每日收盤資料，請執行 --probe 檢查")
    days.sort()
    # 2) 三大法人
    frames = []
    for d in days:
        try:
            t86 = parse_t86(tw.raw("T86", d))
        except Exception as e:  # noqa: BLE001
            log.warning("三大法人 %s 失敗：%s", d, e)
            t86 = None
        df = mi[d].copy()
        df["date"] = pd.Timestamp(d)
        if t86 is not None and not t86.empty:
            df = df.merge(t86, on="code", how="left")
        for c in ("f", "t", "d"):
            df[c] = df[c].fillna(0) if c in df else 0.0
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    names = dict(zip(panel["code"], panel["name"]))

    # 3) 產業分類與上櫃股
    info = fm.info()
    info = info[info["stock_id"].astype(str).map(is_stock)]
    info["stock_id"] = info["stock_id"].astype(str)
    industries: dict[str, list[str]] = {}
    for cat, g in info[info["type"] == "twse"].groupby("industry_category"):
        members = sorted(set(g["stock_id"]))
        if 2 <= len(members) <= 300 and cat not in ("", "None"):
            industries[str(cat)] = members
    otc_set = set(info.loc[info["type"] == "tpex", "stock_id"])
    names.update(dict(zip(info["stock_id"], info["stock_name"])))
    otc_need = sorted({c for v in themes.values() for c in v if c in otc_set})
    log.info("上櫃概念股 %d 檔（FinMind 逐檔查詢）", len(otc_need))
    otc_frames = []
    for i, c in enumerate(otc_need, 1):
        try:
            o = fm.otc_stock(c, days[0])
            if not o.empty:
                o["name"] = names.get(c, c)
                otc_frames.append(o[o["date"].isin([pd.Timestamp(x) for x in days])])
        except Exception as e:  # noqa: BLE001
            log.warning("上櫃 %s 失敗：%s", c, e)
        if i % 10 == 0:
            log.info("  …%d/%d", i, len(otc_need))
    if otc_frames:
        panel = pd.concat([panel] + otc_frames, ignore_index=True)
    return panel, industries, names, taiex, days


def collect_demo(n_days: int, themes: dict) -> tuple[pd.DataFrame, dict, dict, dict, list]:
    try:
        from market_pulse import DEMO_NAMES
    except Exception:  # noqa: BLE001
        DEMO_NAMES = {}
    rng = np.random.default_rng(20261022)
    inds = ["半導體業", "電子零組件業", "電腦及週邊設備業", "光電業", "通信網路業", "金融保險業", "航運業",
            "鋼鐵工業", "塑膠工業", "建材營造業", "生技醫療業", "食品工業", "紡織纖維", "汽車工業", "電機機械"]
    industries = {k: [f"{9000 - i * 20 - j:04d}" for j in range(rng.integers(6, 18))] for i, k in enumerate(inds)}
    for k, members in themes.items():  # 把概念股也分一些到產業裡
        industries[inds[hash(k) % 5]] = industries[inds[hash(k) % 5]] + members[:2]
    codes = sorted({c for v in list(industries.values()) + list(themes.values()) for c in v})
    bias = {}
    for g, members in list(themes.items()) + list(industries.items()):
        b = rng.normal(0, 1)
        for c in members:
            bias.setdefault(c, []).append(b)
    days = list(pd.bdate_range(end=pd.Timestamp("2026-10-22"), periods=n_days).date)
    rows = []
    for c in codes:
        h = int(hashlib.md5(c.encode()).hexdigest()[:6], 16)
        b = float(np.mean(bias.get(c, [0])))
        accel = rng.normal(0, 1)
        px = 30 + h % 1500
        size = 10 ** rng.uniform(7.5, 9.8)
        for k, d in enumerate(days):
            trend = b + accel * (k - n_days * 0.6) / n_days
            r = rng.normal(0.0008 * trend, 0.02)
            ref, px = px, px * (1 + r)
            amt = size * rng.uniform(0.6, 1.6)
            net_val = amt * rng.normal(0.08 * trend, 0.12)
            rows.append({"date": pd.Timestamp(d), "code": c, "name": DEMO_NAMES.get(c, f"示範{c}"), "close": px,
                         "chg": px / ref - 1, "amount": amt, "f": net_val * 0.7 / px, "t": net_val * 0.2 / px,
                         "d": net_val * 0.1 / px})
    panel = pd.DataFrame(rows)
    names = dict(zip(panel["code"], panel["name"]))
    taiex = {d: float(rng.normal(-0.002, 0.01)) for d in days}
    taiex[days[-1]] = -0.0181
    return panel, industries, names, taiex, days


# =============================================================================
# 指標
# =============================================================================
def quadrant(x: float, y: float) -> str:
    if x > 0:
        return "漲潮" if y > 0 else "輪動"
    return "觀望" if y > 0 else "退潮"


def compute(panel: pd.DataFrame, sectors: dict[str, tuple[str, list]], names: dict, holdings: dict,
            days: list, replay: int, fc: dict) -> dict:
    panel = panel.copy()
    panel["tot"] = panel["f"] + panel["t"] + panel["d"]
    for c in ("tot", "f", "t"):
        panel[f"v_{c}"] = panel[c] * panel["close"]   # 張數換金額：股數 × 收盤價（元）
    P = {k: panel.pivot_table(index="date", columns="code", values=k, aggfunc="last").sort_index()
         for k in ("v_tot", "v_f", "v_t", "amount", "close", "chg")}
    for k in ("v_tot", "v_f", "v_t"):
        P[k] = P[k].where(P["close"].notna())
    n = len(P["close"])
    frames = []
    for k in range(max(n - replay, 19), n):
        rows = []
        for sname, (kind, members) in sectors.items():
            cols = [c for c in members if c in P["close"].columns and P["close"][c].iloc[k] == P["close"][c].iloc[k]]
            if len(cols) < fc.get("min_members", 2):
                continue
            v = P["v_tot"][cols]
            x5 = float(np.nansum(v.iloc[k - 4:k + 1].values)) / 1e8
            avg20 = float(np.nansum(v.iloc[k - 19:k + 1].values)) / 20 / 1e8
            y = x5 / 5 - avg20
            size = float(np.nansum(P["amount"][cols].iloc[k - 19:k + 1].values)) / 1e8
            today = v.iloc[k]
            top = today.idxmax() if today.notna().any() else None
            c5 = (P["close"][cols].iloc[k] / P["close"][cols].iloc[k - 5] - 1)
            rows.append({"n": sname, "k": kind, "x": round(x5, 2), "y": round(y, 3), "s": round(size, 1),
                         "q": quadrant(x5, y), "tn": round(float(np.nansum(today.values)) / 1e8, 2),
                         "ct": round(float(np.nanmean(P["chg"][cols].iloc[k].values)), 4),
                         "c5": round(float(np.nanmean(c5.values)), 4),
                         "f5": round(float(np.nansum(P["v_f"][cols].iloc[k - 4:k + 1].values)) / 1e8, 2),
                         "t5": round(float(np.nansum(P["v_t"][cols].iloc[k - 4:k + 1].values)) / 1e8, 2),
                         "b": int((today > 0).sum()), "m": len(cols),
                         "top": names.get(top, top) if top else "", "mine": any(c in holdings for c in cols)})
        frames.append({"date": P["close"].index[k].strftime("%Y-%m-%d"), "sectors": rows})

    # 成分股明細（最新一天）
    k = n - 1
    members_out = {}
    for sname, (kind, members) in sectors.items():
        lst = []
        for c in members:
            if c not in P["close"].columns or P["close"][c].iloc[k] != P["close"][c].iloc[k]:
                continue
            lst.append([c, names.get(c, c), round(float(P["v_tot"][c].iloc[k]) / 1e8, 2),
                        round(float(np.nansum(P["v_tot"][c].iloc[k - 4:k + 1])) / 1e8, 2),
                        round(float(P["chg"][c].iloc[k]), 4),
                        round(float(P["close"][c].iloc[k] / P["close"][c].iloc[k - 5] - 1), 4),
                        c in holdings])
        members_out[sname] = sorted(lst, key=lambda r: -r[3])

    # 個股
    v = P["v_tot"]
    srows = []
    ratio, min_yi = fc.get("anomaly_ratio", 3.0), fc.get("anomaly_min_yi", 1.0)
    for c in v.columns:
        if P["close"][c].iloc[k] != P["close"][c].iloc[k]:
            continue
        today = float(v[c].iloc[k]) / 1e8
        hist = v[c].iloc[k - 20:k].abs().mean() / 1e8
        s, sign = 0, 0
        for val in reversed(v[c].iloc[:k + 1].tolist()):
            sg = 1 if val > 0 else -1 if val < 0 else 0
            if sg == 0 or (sign and sg != sign):
                break
            sign, s = sg, s + 1
        flag = ""
        if hist and abs(today) >= ratio * hist and abs(today) >= min_yi:
            flag = f"法人{'買' if today > 0 else '賣'}超暴增 {abs(today) / hist:.1f} 倍"
        elif sign * s >= 5:
            flag = f"法人連買 {s} 天"
        elif sign * s <= -5:
            flag = f"法人連賣 {s} 天"
        srows.append({"c": c, "nm": names.get(c, c), "tv": round(today, 2),
                      "v5": round(float(np.nansum(v[c].iloc[k - 4:k + 1])) / 1e8, 2),
                      "f5": round(float(np.nansum(P["v_f"][c].iloc[k - 4:k + 1])) / 1e8, 2),
                      "t5": round(float(np.nansum(P["v_t"][c].iloc[k - 4:k + 1])) / 1e8, 2),
                      "ct": round(float(P["chg"][c].iloc[k]), 4),
                      "c5": round(float(P["close"][c].iloc[k] / P["close"][c].iloc[k - 5] - 1), 4),
                      "st": sign * s, "fl": flag, "mine": c in holdings})
    sdf = pd.DataFrame(srows)
    in_sec = {}
    for sname, (_, members) in sectors.items():
        for c in members:
            in_sec.setdefault(c, []).append(sname)
    stocks = {
        "buy": sdf.sort_values("tv", ascending=False).head(15).to_dict("records"),
        "sell": sdf.sort_values("tv").head(15).to_dict("records"),
        "anomaly": sdf[sdf["fl"] != ""].assign(a=lambda d: d["tv"].abs()).sort_values("a", ascending=False)
                      .drop(columns="a").head(40).to_dict("records"),
        "mine": [dict(r, secs=in_sec.get(r["c"], [])) for r in sdf[sdf["mine"]].to_dict("records")],
    }
    missing = [f"{nm}({c})" for c, nm in holdings.items() if sdf.empty or c not in set(sdf["c"])]
    return {"frames": frames, "members": members_out, "stocks": stocks, "missing_holdings": missing}


def clean(o):
    """把 NaN 換成 None，讓 JSON 合法"""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def build(args) -> Path:
    fc, themes, token, holdings = load_cfg()
    replay = int(fc.get("replay_days", 10))
    n_days = replay + 20
    if args.demo:
        panel, industries, names, taiex, days = collect_demo(n_days, themes)
    else:
        panel, industries, names, taiex, days = collect_live(n_days, themes, token)
    names.update(holdings)
    sectors = {k: ("產業", list(dict.fromkeys(v))) for k, v in industries.items()}
    sectors.update({k: ("概念", list(dict.fromkeys(v))) for k, v in themes.items()})
    if len(days) < 21:
        raise RuntimeError(f"交易日只有 {len(days)} 天，至少需要 21 天")
    res = compute(panel, sectors, names, holdings, days, replay, fc)
    meta = {"date": str(days[-1]), "taiex": {str(d): taiex.get(d) for d in days[-replay:]},
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "demo": bool(args.demo),
            "n_stocks": int(panel["code"].nunique())}
    data = clean({"meta": meta, **res})
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if not args.demo:  # 給盤中儀表板用的法人背景
        latest = {r["n"]: {"q": r["q"], "x": r["x"], "y": r["y"], "tn": r["tn"]} for r in res["frames"][-1]["sectors"]}
        (CACHE / "latest_sectors.json").write_text(json.dumps(clean({"date": meta["date"], "sectors": latest}),
                                                            ensure_ascii=False), "utf-8")
    tpl = (BASE / "sector_flow_template.html").read_text("utf-8")
    OUT.write_text(tpl.replace("/*__DATA__*/null", payload), "utf-8")
    log.info("完成：%s（%d 個板塊、%d 檔股票、資料日期 %s）", OUT, len(res["frames"][-1]["sectors"]),
             meta["n_stocks"], meta["date"])
    if res["missing_holdings"]:
        log.warning("這些持股沒有資料（可能是上櫃股但沒放進任何概念族群）：%s", "、".join(res["missing_holdings"]))
    return OUT


def probe():
    CACHE.mkdir(parents=True, exist_ok=True)
    tw = Twse()
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    for kind in ("MI", "T86"):
        print(f"=== 證交所 {kind}（{d}）===")
        for back in range(6):
            dd = d - timedelta(days=back)
            if dd.weekday() >= 5:
                continue
            try:
                js = tw.raw(kind, dd)
            except Exception as e:  # noqa: BLE001
                print("  ⚠ 失敗：", e)
                break
            if js.get("stat") != "OK":
                print(f"  {dd} 沒有資料（{js.get('stat')}）")
                continue
            print(f"  {dd} ✓ 有資料。最上層欄位：{list(js.keys())[:10]}")
            for title, fields, data in twse_tables(js)[:12]:
                print(f"   表格「{title[:30]}」{len(data)} 列；欄位：{fields[:8]}")
            if kind == "MI":
                st, tx = parse_mi(js)
                print(f"  解析結果：個股 {0 if st is None else len(st)} 檔，加權指數漲跌 {tx:+.2%}" if tx == tx
                      else f"  解析結果：個股 {0 if st is None else len(st)} 檔，加權指數漲跌：未取得")
                if st is not None and not st.empty:
                    print("  範例：", st[st["code"] == "2330"].to_dict("records"))
            else:
                t = parse_t86(js)
                print(f"  解析結果：{0 if t is None else len(t)} 檔")
                if t is not None and not t.empty:
                    print("  範例（股數）：", t[t["code"] == "2330"].to_dict("records"))
            break
    _, _, token, _ = load_cfg()
    print("=== FinMind 產業分類 ===")
    try:
        info = FinMind(token).info()
        print(f"  ✓ {len(info)} 筆，欄位 {list(info.columns)}")
    except Exception as e:  # noqa: BLE001
        print("  ⚠ 失敗：", e)


def main():
    ap = argparse.ArgumentParser(description="板塊資金流向")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    if args.probe:
        probe()
        return
    out = build(args)
    try:
        import global_markets
        global_markets.build(demo=args.demo)
    except Exception as e:  # noqa: BLE001
        log.warning("全球市場資料更新失敗（不影響資金流向）：%s", e)
    if not args.no_browser and not args.auto:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
