#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盤後籌碼延伸資料 extra_board.py
================================
上市＋上櫃，每天盤後整理：
  1. 融資融券：融資 5 日增減、券資比、融資增但股價跌
  2. 借券賣出：借券賣出餘額 5 日增減（金額）
  3. 注意股與處置股：今日注意、目前處置中
  4. 月營收：創 24 個月新高、年增率連續加速
  5. 集保大戶：千張大戶持股比例週變化（每週更新）
  6. 期貨選擇權：三大法人台指期未平倉、選擇權 Put/Call 比
  7. 美股財報行事曆：未來 14 天（美股成交前 100＋台灣供應鏈相關）

用法：
    python extra_board.py            全部（daily_job.py 每天 18:00 會執行）
    python extra_board.py --only margin,sbl     只跑指定項目
    python extra_board.py --demo     不連網，用快取或空資料產生頁面

輸出：extra_board.html（雙擊打開）、extra_board_fragment.html（手機版嵌進資金流向頁）
資料來源：臺灣證券交易所、證券櫃檯買賣中心、公開資訊觀測站、臺灣集中保管結算所、臺灣期貨交易所、Yahoo Finance
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import re
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
RAW = DATA / "extra"
FLOW = DATA / "flow"
log = logging.getLogger("extra")
warnings.filterwarnings("ignore")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TOP = 30

# 美股財報行事曆：除了成交前 100，也一定追蹤這些（和台灣供應鏈相關）
US_SUPPLY = ["NVDA", "AAPL", "AMD", "AVGO", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "QCOM", "MU", "INTC", "TSM",
             "ASML", "AMAT", "LRCX", "KLAC", "ARM", "MRVL", "DELL", "SMCI", "HPE", "ORCL", "CSCO", "ANET", "CRWV",
             "CRDO", "ALAB", "VRT", "CLS", "TXN", "ADI", "NXPI", "ON", "MCHP", "WDC", "STX", "SNDK", "COHR", "LITE"]


# =============================================================================
# 共用
# =============================================================================
def num(v) -> float:
    try:
        s = str(v).replace(",", "").replace("+", "").strip()
        return float(s) if s not in ("", "-", "--", "---", "X") else math.nan
    except (TypeError, ValueError):
        return math.nan


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def is_stock(c: str) -> bool:
    c = str(c).strip()
    return len(c) == 4 and c.isdigit() and not c.startswith("00")


def cached_json(name: str, fetch, keep_if=lambda js: True):
    """以檔名快取原始資料；今天的資料若不完整不存，下次再抓"""
    f = RAW / name
    if f.exists():
        return json.loads(f.read_text("utf-8"))
    js = fetch()
    if js is not None and keep_if(js):
        RAW.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(js, ensure_ascii=False), "utf-8")
    return js


def twse(path: str, d: date, **params):
    def fetch():
        rr = requests.get(f"https://www.twse.com.tw/rwd/zh/{path}",
                          params={**params, "date": f"{d:%Y%m%d}", "response": "json"}, headers=UA, timeout=30)
        rr.raise_for_status()
        time.sleep(3.5)  # 證交所限制查詢頻率
        return rr.json()
    tag = path.rsplit("/", 1)[-1]
    return cached_json(f"twse_{tag}_{d:%Y%m%d}.json", fetch,
                       keep_if=lambda js: js.get("stat") == "OK" or d < date.today())


def tpex(path: str, d: date, **params):
    def fetch():
        rr = requests.post(f"https://www.tpex.org.tw/www/zh-tw/{path}",
                           params={**params, "date": f"{d:%Y/%m/%d}", "response": "json"}, headers=UA, timeout=30)
        rr.raise_for_status()
        time.sleep(1.5)
        js = rr.json()
        for t in js.get("tables") or []:  # 只留普通股，權證等不存
            t["data"] = [row for row in t.get("data") or [] if row and is_stock(row[0])]
        return js
    tag = path.replace("/", "_")
    return cached_json(f"tpex_{tag}_{d:%Y%m%d}.json", fetch,
                       keep_if=lambda js: (js.get("tables") and js["tables"][0].get("data")) or d < date.today())


def tpex_rows(js) -> list:
    try:
        return js["tables"][0]["data"] if js and str(js.get("stat", "")).lower() == "ok" else []
    except (KeyError, IndexError):
        return []


def trading_days(n: int) -> list[date]:
    """最近 n 個交易日（舊→新），用證交所每日收盤行情判斷是否開市"""
    sys.path.insert(0, str(BASE))
    from post_board import twse_json
    out, d = [], date.today()
    for _ in range(n * 3 + 10):
        if d.weekday() < 5:
            try:
                if twse_json("MI", d):
                    out.append(d)
            except requests.RequestException:
                pass
            if len(out) == n:
                break
        d -= timedelta(days=1)
    return sorted(out)


def load_names() -> dict:
    files = sorted(FLOW.glob("stock_info_*.json"))
    if not files:
        return {}
    d = json.loads(files[-1].read_text("utf-8"))
    cols = {k: (list(v.values()) if isinstance(v, dict) else v) for k, v in d.items()}
    return {str(s): {"n": n, "i": i or "", "m": "上市" if t == "twse" else "上櫃" if t == "tpex" else "興櫃"}
            for s, n, i, t in zip(cols["stock_id"], cols["stock_name"], cols["industry_category"], cols["type"])}


NAMES: dict = {}


def info(c: str, name: str = "") -> dict:
    x = NAMES.get(c, {})
    return {"c": c, "n": x.get("n") or name.strip(), "i": x.get("i", ""), "m": x.get("m", "")}


def closes(d: date) -> dict[str, float]:
    """上市＋上櫃收盤價"""
    sys.path.insert(0, str(BASE))
    from post_board import parse_close, twse_json
    out = {}
    try:
        js = twse_json("MI", d)
        if js:
            out.update(parse_close(js))
    except requests.RequestException:
        pass
    try:
        for row in tpex_rows(tpex("afterTrading/dailyQuotes", d, type="EW")):
            out[str(row[0]).strip()] = num(row[2])
    except requests.RequestException as e:
        log.info("櫃買行情 %s 失敗：%s", d, e)
    return out


def ret(c, p0: dict, p1: dict):
    a, b = p0.get(c, math.nan), p1.get(c, math.nan)
    return r(b / a - 1, 4) if a and a == a and b == b else None


# =============================================================================
# 1. 融資融券
# =============================================================================
def margin_day(d: date) -> dict[str, tuple[float, float]]:
    """{代號: (融資餘額張, 融券餘額張)}"""
    out = {}
    js = twse("marginTrading/MI_MARGN", d, selectType="ALL")
    for t in (js or {}).get("tables", []):
        if "融資融券彙總" in (t.get("title") or ""):
            for row in t["data"]:
                if is_stock(row[0]):
                    out[row[0].strip()] = (num(row[6]), num(row[12]))
    for row in tpex_rows(tpex("margin/balance", d)):
        if is_stock(row[0]):
            out[str(row[0]).strip()] = (num(row[6]), num(row[14]))
    return out


def latest_pair(days: list[date], fn) -> tuple[date, date, dict, dict]:
    """找最近一個「已經公布」的交易日（融資券、借券約晚上 9 點後才公布），和它往前 5 個交易日"""
    for i in range(len(days) - 1, 4, -1):
        cur = fn(days[i])
        if len(cur) > 500:
            return days[i - 5], days[i], fn(days[i - 5]), cur
    raise RuntimeError("找不到已公布的資料")


def margin(days: list[date]) -> dict:
    d0, d1, m0, m1 = latest_pair(days, margin_day)
    p0, p1 = closes(d0), closes(d1)
    rows = []
    for c, (fin, short) in m1.items():
        f0 = m0.get(c, (math.nan, math.nan))[0]
        if not (fin == fin and f0 == f0) or fin < 500:  # 融資餘額太小的不看
            continue
        px = p1.get(c, math.nan)
        rows.append({**info(c), "fin": int(fin), "dfin": int(fin - f0), "pfin": r((fin - f0) / f0, 4) if f0 else None,
                     "afin": r((fin - f0) * 1000 * px / 1e8, 2) if px == px else None,
                     "short": int(short) if short == short else 0,
                     "sr": r(short / fin, 4) if fin else None, "ret": ret(c, p0, p1), "p": r(px)})
    up = [x for x in rows if x["pfin"] is not None]
    return {
        "from": d0.isoformat(), "to": d1.isoformat(),
        "inc": sorted([x for x in up if x["afin"] and x["afin"] > 0], key=lambda x: -x["afin"])[:TOP],
        "dec": sorted([x for x in up if x["afin"] and x["afin"] < 0], key=lambda x: x["afin"])[:TOP],
        "trap": sorted([x for x in up if x["pfin"] > 0.05 and (x["ret"] or 0) < 0], key=lambda x: -x["pfin"])[:TOP],
        "sr": sorted([x for x in rows if x["sr"] and x["fin"] >= 1000], key=lambda x: -x["sr"])[:TOP],
    }


# =============================================================================
# 2. 借券賣出
# =============================================================================
def sbl_day(d: date) -> dict[str, float]:
    """{代號: 借券賣出餘額（股）}"""
    out = {}
    js = twse("marginTrading/TWT93U", d)
    for row in (js or {}).get("data", []):
        if is_stock(row[0]):
            out[row[0].strip()] = num(row[12])
    for row in tpex_rows(tpex("margin/sbl", d)):
        if is_stock(row[0]):
            out[str(row[0]).strip()] = num(row[12])
    return out


def sbl(days: list[date]) -> dict:
    d0, d1, s0, s1 = latest_pair(days, sbl_day)
    p0, p1 = closes(d0), closes(d1)
    rows = []
    for c, bal in s1.items():
        b0, px = s0.get(c, math.nan), p1.get(c, math.nan)
        if not (bal == bal and b0 == b0 and px == px):
            continue
        rows.append({**info(c), "bal": r(bal * px / 1e8, 2), "chg": r((bal - b0) * px / 1e8, 2),
                     "lots": int(bal / 1000), "dlots": int((bal - b0) / 1000), "ret": ret(c, p0, p1), "p": r(px)})
    return {"from": d0.isoformat(), "to": d1.isoformat(),
            "inc": sorted([x for x in rows if x["chg"] > 0], key=lambda x: -x["chg"])[:TOP],
            "dec": sorted([x for x in rows if x["chg"] < 0], key=lambda x: x["chg"])[:TOP],
            "big": sorted(rows, key=lambda x: -x["bal"])[:TOP]}


# =============================================================================
# 3. 注意股、處置股
# =============================================================================
def roc(s: str) -> date | None:
    m = re.findall(r"\d+", s)
    if len(m) >= 3:
        return date(int(m[0]) + 1911, int(m[1]), int(m[2]))
    if len(m) == 1 and len(m[0]) == 7:
        return date(int(m[0][:3]) + 1911, int(m[0][3:5]), int(m[0][5:7]))
    return None


def short_reason(t: str) -> str:
    t = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", t))).strip()
    return t[:90] + ("…" if len(t) > 90 else "")


def warn(days: list[date]) -> dict:
    d1 = days[-1]
    notice, punish = [], []
    js = requests.get("https://www.twse.com.tw/rwd/zh/announcement/notice", headers=UA, timeout=30,
                      params={"startDate": f"{d1:%Y%m%d}", "endDate": f"{d1:%Y%m%d}", "response": "json"}).json()
    for row in js.get("data", []):
        if is_stock(row[1]):
            notice.append({**info(row[1].strip(), row[2]), "times": row[3], "why": short_reason(row[4]), "m": "上市"})
    time.sleep(3.5)
    js = requests.get("https://www.twse.com.tw/rwd/zh/announcement/punish", headers=UA, timeout=30,
                      params={"startDate": f"{d1 - timedelta(days=20):%Y%m%d}", "endDate": f"{d1:%Y%m%d}",
                              "response": "json"}).json()
    for row in js.get("data", []):
        c = str(row[2]).strip()
        per = str(row[6]).split("～")
        end = roc(per[-1]) if per else None
        if is_stock(c) and end and end >= d1:
            punish.append({**info(c, row[3]), "period": row[6], "cond": row[5], "lv": row[7], "m": "上市"})
    try:
        tw = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information", headers=UA, timeout=30).json()
        for x in tw:
            if is_stock(x.get("SecuritiesCompanyCode", "")) and roc(x.get("Date", "")) == d1:
                notice.append({**info(x["SecuritiesCompanyCode"], x.get("CompanyName", "")), "times": "",
                               "why": short_reason(x.get("TradingInformation", "")), "m": "上櫃"})
        tp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", headers=UA, timeout=30).json()
        for x in tp:
            per = str(x.get("DispositionPeriod", "")).split("~")
            end = roc(per[-1]) if per else None
            if is_stock(x.get("SecuritiesCompanyCode", "")) and end and end >= d1:
                a, b = roc(per[0]), end
                punish.append({**info(x["SecuritiesCompanyCode"], x.get("CompanyName", "")),
                               "period": f"{a:%m/%d}～{b:%m/%d}" if a else x.get("DispositionPeriod"),
                               "cond": x.get("DispositionReasons", ""), "lv": "", "m": "上櫃"})
    except (requests.RequestException, ValueError) as e:
        log.info("櫃買注意／處置失敗：%s", e)
    return {"date": d1.isoformat(), "notice": notice, "punish": punish}


# =============================================================================
# 4. 月營收
# =============================================================================
def mops_month(kind: str, y: int, m: int, final: bool) -> dict[str, list]:
    """kind: sii 上市、otc 上櫃。回傳 {代號: [名稱, 當月營收, 上月, 去年當月, 月增%, 年增%, 累計年增%]}（千元）"""
    f = RAW / f"mops_{kind}_{y}{m:02d}.json"
    if f.exists():
        return json.loads(f.read_text("utf-8"))
    url = f"https://mopsov.twse.com.tw/nas/t21/{kind}/t21sc03_{y - 1911}_{m}_0.html"
    rr = None
    for attempt in range(4):  # 公開資訊觀測站查太快會斷線，間隔拉長重試
        try:
            rr = requests.get(url, headers=UA, timeout=30)
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    time.sleep(4)
    if rr is None or rr.status_code != 200:
        return {}
    html = rr.content.decode("cp950", errors="replace")
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [re.sub(r"\s+", "", unescape(re.sub(r"<[^>]+>", "", td)))
                 for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(cells) >= 10 and is_stock(cells[0]):
            out[cells[0]] = [cells[1], num(cells[2]), num(cells[3]), num(cells[4]), num(cells[5]), num(cells[6]), num(cells[9])]
    if out and final:
        RAW.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    return out


def revenue() -> dict:
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(27):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append((y, m))
    # 最新月份：上個月的資料在每月 10 日左右公布完，之前先用上上個月
    latest = months[0] if today.day > 10 else months[1]
    hist = [x for x in months if x <= latest][:25]
    data = {}
    for (yy, mm) in hist:
        final = (yy, mm) != latest or today.day > 15
        for kind in ("sii", "otc"):
            try:
                for c, v in mops_month(kind, yy, mm, final).items():
                    data.setdefault(c, {})[(yy, mm)] = v
            except requests.RequestException as e:
                log.info("營收 %s %d/%d 失敗：%s", kind, yy, mm, e)
    rows = []
    for c, by in data.items():
        if latest not in by:
            continue
        cur = by[latest]
        past = [by[k][1] for k in hist[1:] if k in by and by[k][1] == by[k][1]]
        yoy = [by[k][5] for k in hist[:3] if k in by]
        if len(past) < 12 or cur[1] != cur[1]:
            continue
        rows.append({**info(c, cur[0]), "rev": r(cur[1] / 1e5, 2), "mom": r(cur[4], 1), "yoy": r(cur[5], 1),
                     "cum": r(cur[6], 1), "high": cur[1] > max(past) and cur[1] >= 2e5, "months": len(past) + 1,
                     "accel": len(yoy) == 3 and all(v == v for v in yoy) and yoy[0] > yoy[1] > yoy[2] and yoy[0] > 20 and cur[1] >= 2e5,
                     "y3": [r(v, 1) for v in yoy]})
    return {"month": f"{latest[0]}/{latest[1]:02d}", "n": len(rows),
            "high": sorted([x for x in rows if x["high"]], key=lambda x: -(x["yoy"] or 0))[:TOP * 2],
            "accel": sorted([x for x in rows if x["accel"]], key=lambda x: -(x["yoy"] or 0))[:TOP],
            "n_high": sum(x["high"] for x in rows)}


# =============================================================================
# 5. 集保大戶（每週）
# =============================================================================
def tdcc() -> dict:
    rr = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=UA, timeout=60)
    rr.raise_for_status()
    text = rr.content.decode("utf-8-sig", errors="replace")
    cur, dd = {}, ""
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or not row[0].strip().isdigit():
            continue
        dd, c, lv = row[0].strip(), row[1].strip(), int(row[2])
        if not is_stock(c):
            continue
        x = cur.setdefault(c, {"k1000": 0.0, "k400": 0.0, "holders": 0})
        pct = num(row[5])
        if lv == 15:
            x["k1000"] = pct
        if 12 <= lv <= 15:
            x["k400"] += pct
        if lv == 17:
            x["holders"] = int(num(row[3]))
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"tdcc_{dd}.json").write_text(json.dumps(cur), "utf-8")
    old = sorted(p for p in RAW.glob("tdcc_*.json") if p.stem != f"tdcc_{dd}")
    prev, prev_d = ({}, "")
    if old:
        prev, prev_d = json.loads(old[-1].read_text("utf-8")), old[-1].stem[5:]
    for p in sorted(RAW.glob("tdcc_*.json"))[:-10]:
        p.unlink(missing_ok=True)
    rows = []
    for c, x in cur.items():
        if c not in NAMES or NAMES[c].get("m") not in ("上市", "上櫃"):
            continue
        p = prev.get(c)
        rows.append({**info(c), "k1000": r(x["k1000"], 2), "k400": r(x["k400"], 2), "h": x["holders"],
                     "d1000": r(x["k1000"] - p["k1000"], 2) if p else None,
                     "dh": x["holders"] - p["holders"] if p else None})
    has = [x for x in rows if x["d1000"] is not None]
    return {"date": dd, "prev": prev_d, "first": not has,
            "inc": sorted([x for x in has if x["d1000"] > 0], key=lambda x: -x["d1000"])[:TOP],
            "dec": sorted([x for x in has if x["d1000"] < 0], key=lambda x: x["d1000"])[:TOP],
            "top": sorted([x for x in rows if x["h"] >= 1000 and (x["k1000"] or 0) < 99],
                          key=lambda x: -(x["k1000"] or 0))[:TOP]}


# =============================================================================
# 6. 期貨、選擇權
# =============================================================================
def futures() -> dict:
    base = "https://openapi.taifex.com.tw/v1/"

    def get(path):
        rr = requests.get(base + path, headers={**UA, "Accept": "application/json"}, timeout=30)
        try:
            return rr.json()
        except ValueError:
            raise RuntimeError(f"期交所回應不是 JSON（HTTP {rr.status_code}）：{rr.text[:120]!r}")
    fut = get("MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    pcr = get("PutCallRatio")
    day = {}
    for x in fut:
        k = x.get("ContractCode", "")
        if k in ("臺股期貨", "小型臺指期貨", "微型臺指期貨"):
            day.setdefault(k, {})[x.get("Item", "")] = {"oi": int(num(x.get("OpenInterest(Net)"))),
                                                         "tv": int(num(x.get("TradingVolume(Net)")))}
    dd = fut[0]["Date"] if fut else ""
    RAW.mkdir(parents=True, exist_ok=True)
    if dd:
        (RAW / f"taifex_{dd}.json").write_text(json.dumps(day, ensure_ascii=False), "utf-8")
    hist = []
    for p in sorted(RAW.glob("taifex_*.json"))[-20:]:
        js = json.loads(p.read_text("utf-8"))
        tx = js.get("臺股期貨", {})
        hist.append({"d": p.stem[7:], **{who: tx.get(who, {}).get("oi") for who in ("外資及陸資", "投信", "自營商")}})
    pc = sorted(({"d": x["Date"], "oi": num(x.get("PutCallOIRatio%")), "vol": num(x.get("PutCallVolumeRatio%"))}
                 for x in pcr), key=lambda x: x["d"])
    return {"date": dd, "day": day, "hist": hist, "pc": pc}


# =============================================================================
# 7. 美股財報行事曆
# =============================================================================
def earnings() -> dict:
    import yfinance as yf
    post = DATA / "post" / "latest.json"
    top = []
    if post.exists():
        js = json.loads(post.read_text("utf-8"))
        top = [(x["s"], x.get("n", x["s"]), x.get("sec", ""), x.get("rk")) for x in ((js.get("us") or {}).get("top") or [])]
    seen = {s for s, *_ in top}
    syms = top + [(s, s, "", None) for s in US_SUPPLY if s not in seen]
    today = date.today()
    rows = []
    for s, n, sec, rk in syms:
        try:
            cal = yf.Ticker(s).calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            ed = ed[0] if isinstance(ed, (list, tuple)) and ed else ed
            if ed is None:
                continue
            ed = ed if isinstance(ed, date) else datetime.fromisoformat(str(ed)[:10]).date()
            if today - timedelta(days=1) <= ed <= today + timedelta(days=14):
                eps = cal.get("Earnings Average")
                rev = cal.get("Revenue Average")
                rows.append({"s": s, "n": n, "sec": sec, "rk": rk, "d": ed.isoformat(), "tw": s in US_SUPPLY,
                             "eps": r(eps, 2) if isinstance(eps, (int, float)) else None,
                             "rev": r(rev / 1e8, 1) if isinstance(rev, (int, float)) else None})
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda x: (x["d"], x["rk"] or 999))
    return {"from": today.isoformat(), "rows": rows}


# =============================================================================
JOBS = {"margin": ("融資融券", margin, True), "sbl": ("借券賣出", sbl, True), "warn": ("注意／處置", warn, True),
        "rev": ("月營收", revenue, False), "tdcc": ("集保大戶", tdcc, False), "fut": ("期貨選擇權", futures, False),
        "earn": ("美股財報", earnings, False)}


def save(res: dict) -> None:
    f = RAW / "latest.json"
    old = json.loads(f.read_text("utf-8")) if f.exists() else {}
    for k in JOBS:
        if res.get(k) is None and old.get(k) is not None:
            res[k] = old[k]
    RAW.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    tpl = (BASE / "extra_board_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(res, ensure_ascii=False))
    (BASE / "extra_board_fragment.html").write_text(frag, "utf-8")
    (BASE / "extra_board.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>籌碼營收期權</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:1180px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")
    log.info("→ extra_board.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="逗號分隔：" + ",".join(JOBS))
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if sys.platform == "win32" and sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    hs = [logging.FileHandler(DATA / "logs" / f"extra_{date.today():%Y%m%d}.log", encoding="utf-8")]
    if sys.stdout is not None:
        hs.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", handlers=hs)
    NAMES.update(load_names())
    res = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if args.demo:
        save(res)
        return
    only = set(args.only.split(",")) if args.only else set(JOBS)
    days = None
    fails = 0
    for k, (title, fn, need_days) in JOBS.items():
        if k not in only:
            continue
        t0 = time.time()
        try:
            if need_days and days is None:
                days = trading_days(9)
                log.info("交易日：%s～%s", days[0], days[-1])
            res[k] = fn(days) if need_days else fn()
            log.info("✓ %s（%.0f 秒）", title, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            fails += 1
            log.info("✗ %s 失敗：%s", title, e)
    save(res)
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
