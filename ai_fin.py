#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 相關公司營收與獲利 ai_fin.py（盤後 daily 執行）
==================================================
・月營收：近 36 個月（可算 24 個月的年增率）
・季報：近 12 季營收、毛利、營業利益、稅後淨利、EPS
資料來源：FinMind（TaiwanStockMonthRevenue、TaiwanStockFinancialStatements）
只在「應該有新資料、但快取裡還沒有」時才重抓，一天的請求數很少。
輸出：pulse_data/aifin/<代號>.json（快取）、pulse_data/aifin/all.json（給網頁）
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "pulse_data" / "aifin"
URL = "https://api.finmindtrade.com/api/v4/data"
log = logging.getLogger("aifin")

# 族群 → [(代號, 名稱)]
GROUPS = [
    ("晶圓代工・IC 設計", [("2330", "台積電"), ("2454", "聯發科"), ("3661", "世芯-KY"), ("3443", "創意"), ("5274", "信驊"), ("3035", "智原"), ("2379", "瑞昱")]),
    ("先進封裝・測試", [("3711", "日月光投控"), ("2449", "京元電子"), ("6515", "穎崴"), ("6223", "旺矽"), ("6510", "精測"), ("3131", "弘塑"), ("6187", "萬潤"), ("3583", "辛耘"), ("6640", "均華")]),
    ("伺服器組裝", [("2317", "鴻海"), ("2382", "廣達"), ("3231", "緯創"), ("6669", "緯穎"), ("2356", "英業達"), ("2376", "技嘉"), ("3706", "神達")]),
    ("散熱", [("3017", "奇鋐"), ("3324", "雙鴻"), ("3653", "健策"), ("6805", "富世達"), ("8996", "高力")]),
    ("電源・重電", [("2308", "台達電"), ("2301", "光寶科"), ("1519", "華城"), ("1503", "士電"), ("1513", "中興電")]),
    ("PCB・載板・CCL", [("2383", "台光電"), ("6274", "台燿"), ("6213", "聯茂"), ("2368", "金像電"), ("3037", "欣興"), ("8046", "南電"), ("3189", "景碩")]),
    ("玻纖布", [("1802", "台玻"), ("1815", "富喬"), ("5475", "德宏")]),
    ("網通・光通訊", [("2345", "智邦"), ("3081", "聯亞"), ("4979", "華星光"), ("3363", "上詮"), ("3163", "波若威"), ("3450", "聯鈞")]),
    ("記憶體", [("2408", "南亞科"), ("2344", "華邦電"), ("8299", "群聯")]),
    ("機構件・連接器", [("2059", "川湖"), ("8210", "勤誠"), ("3533", "嘉澤")]),
]
Q_TYPES = {"Revenue": "rev", "GrossProfit": "gp", "OperatingIncome": "op", "IncomeAfterTaxes": "ni",
           "EquityAttributableToOwnersOfParent": "nip", "EPS": "eps"}


class FM:
    def __init__(self):
        self.token = os.environ.get("FINMIND_TOKEN", "")
        self.n = 0

    def get(self, dataset: str, code: str, start: str) -> list[dict]:
        p = {"dataset": dataset, "data_id": code, "start_date": start}
        if self.token:
            p["token"] = self.token
        for i in range(3):
            try:
                self.n += 1
                js = requests.get(URL, params=p, timeout=30).json()
                if js.get("status") == 402:
                    raise RuntimeError("FinMind 本小時額度用完")
                return js.get("data") or []
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                log.info("%s %s 第 %d 次失敗：%s", dataset, code, i + 1, e)
                time.sleep(3)
        return []


def expected_month(today: date) -> str:
    """應該已公布的最新月份：每月 10 日前公布上個月"""
    m = today.replace(day=1) - timedelta(days=1)
    return f"{m.year}-{m.month:02d}"


def expected_quarter(today: date) -> str:
    """應該已公布的最新一季（季報：Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31；之前就先公布的公司也會抓到）"""
    y, m = today.year, today.month
    q = (m - 1) // 3  # 上一個已結束的季
    if q == 0:
        return f"{y - 1}Q4"
    return f"{y}Q{q}"


def load(code: str) -> dict:
    f = OUT / f"{code}.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else {"m": {}, "q": {}, "tm": "", "tq": ""}


def update(fm: FM, code: str, today: date, force: bool = False) -> dict:
    c = load(code)
    em, eq = expected_month(today), expected_quarter(today)
    # 月營收：快取沒有最新月份才抓（同一天不重複抓）
    if force or (em not in c["m"] and c.get("tm") != str(today)):
        start = (today.replace(day=1) - timedelta(days=37 * 31)).strftime("%Y-%m-01") if not c["m"] else (today - timedelta(days=200)).isoformat()
        for x in fm.get("TaiwanStockMonthRevenue", code, start):
            ym = f"{int(x['revenue_year'])}-{int(x['revenue_month']):02d}"
            c["m"][ym] = int(x["revenue"])
        c["tm"] = str(today)
    # 季報
    if force or (eq not in c["q"] and c.get("tq") != str(today)):
        start = f"{today.year - 3}-01-01" if not c["q"] else f"{today.year - 1}-01-01"
        rows = fm.get("TaiwanStockFinancialStatements", code, start)
        for x in rows:
            k = Q_TYPES.get(x.get("type"))
            if not k:
                continue
            d = datetime.strptime(x["date"], "%Y-%m-%d").date()
            yq = f"{d.year}Q{(d.month - 1) // 3 + 1}"
            c["q"].setdefault(yq, {})[k] = x["value"]
        c["tq"] = str(today)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{code}.json").write_text(json.dumps(c, ensure_ascii=False), "utf-8")
    return c


def build_all(caches: dict) -> dict:
    out = []
    for g, items in GROUPS:
        for code, name in items:
            c = caches.get(code)
            if not c:
                continue
            months = sorted(c["m"])[-36:]
            qs = sorted(c["q"])[-12:]
            out.append({"c": code, "n": name, "g": g,
                        "m": [[k, c["m"][k]] for k in months],
                        "q": [[k] + [c["q"][k].get(f) for f in ("rev", "gp", "op", "ni", "nip", "eps")] for k in qs]})
    return {"time": f"{datetime.now():%Y-%m-%d %H:%M}", "groups": [g for g, _ in GROUPS], "cos": out}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    force = "--force" in sys.argv
    fm, today, caches = FM(), date.today(), {}
    try:
        for g, items in GROUPS:
            for code, _ in items:
                caches[code] = update(fm, code, today, force)
    except RuntimeError as e:  # 額度用完：用目前有的資料先產生網頁，明天再補
        log.warning("%s，先用已有資料", e)
        for g, items in GROUPS:
            for code, _ in items:
                caches.setdefault(code, load(code))
    res = build_all(caches)
    (OUT / "all.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), "utf-8")
    n_m = sum(1 for x in res["cos"] if x["m"])
    n_q = sum(1 for x in res["cos"] if x["q"])
    log.info("AI 營收：%d 家（月營收 %d、季報 %d），FinMind 請求 %d 次", len(res["cos"]), n_m, n_q, fm.n)


if __name__ == "__main__":
    main()
