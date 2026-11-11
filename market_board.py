#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成交值排行與漲跌停 market_board.py
==================================
上市＋上櫃全市場（普通股，不含 ETF、權證、興櫃）：
  ・成交值前 100 名
  ・漲停、跌停名單與家數（盤中另外列出「曾漲停但已打開」）
  ・上漲／下跌／平盤家數

用法：
    python market_board.py --auto     盤中：9:00～13:30 每 5 分鐘掃一次全市場，13:31 做收盤掃描後結束
    python market_board.py --once     立刻掃一次（盤中或收盤後都可以）
    python market_board.py --close    盤後：上市用證交所每日收盤行情（官方成交金額與漲跌停家數），
                                       上櫃用櫃買中心 OpenAPI；daily_job.py 會自動執行
    python market_board.py --demo     用模擬資料產生網頁，不連網路
    python market_board.py --probe    檢查證交所、櫃買中心資料是否正常

輸出：market_board.html（雙擊打開）、pulse_data\\board\\board_日期.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
OUT_DIR = DATA / "board"
FLOW = DATA / "flow"
HTML_OUT = BASE / "market_board.html"
log = logging.getLogger("board")

SCAN_EVERY_MIN = 5
OPEN_T, CLOSE_T, FINAL_T = dtime(9, 0), dtime(13, 30), dtime(13, 31)
TOP_N = 100
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# -----------------------------------------------------------------------------
# 小工具
# -----------------------------------------------------------------------------
def fnum(v) -> float:
    try:
        s = str(v).replace(",", "").strip()
        if s in ("", "-", "--", "---", "None", "nan", "除權息", "除權", "除息"):
            return math.nan
        return float(s)
    except (TypeError, ValueError):
        return math.nan


def ok(v: float) -> bool:
    return v == v and v is not None


def tick(p: float) -> float:
    """台股升降單位（普通股）"""
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def limit_prices(prev: float) -> tuple[float, float]:
    """依前一日收盤（參考價）推算漲停、跌停價（±10%，依升降單位取到可成交的價位）"""
    up_raw, dn_raw = prev * 1.1, prev * 0.9
    tu, td = tick(up_raw), tick(dn_raw)
    up = math.floor(up_raw / tu + 1e-9) * tu
    dn = math.ceil(dn_raw / td - 1e-9) * td
    return round(up, 2), round(dn, 2)


def load_universe() -> dict[str, dict]:
    """上市、上櫃普通股清單（沿用 sector_flow.py 快取的 FinMind 台股基本資料）"""
    files = sorted(FLOW.glob("stock_info_*.json"))
    if not files:
        raise RuntimeError("找不到 pulse_data\\flow\\stock_info_*.json，請先執行一次 sector_flow.py")
    d = json.loads(files[-1].read_text("utf-8"))
    cols = {k: (list(v.values()) if isinstance(v, dict) else v) for k, v in d.items()}
    out = {}
    for sid, nm, ty, ind in zip(cols["stock_id"], cols["stock_name"], cols["type"], cols["industry_category"]):
        sid = str(sid)
        if ty not in ("twse", "tpex") or len(sid) != 4 or not sid.isdigit() or sid.startswith("00"):  # 00 開頭為 ETF
            continue
        out.setdefault(sid, {"n": nm, "m": "上市" if ty == "twse" else "上櫃", "i": ind or ""})
    return out


# -----------------------------------------------------------------------------
# 盤中：證交所基本市況報導（mis.twse.com.tw）
# -----------------------------------------------------------------------------
class Mis:
    URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({**UA, "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
        self.warm = False

    def query(self, chans: list[str], batch: int = 50, pause: float = 0.8) -> list[dict]:
        if not self.warm:
            try:
                self.s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
            except requests.RequestException:
                pass
            self.warm = True
        out = []
        for i in range(0, len(chans), batch):
            url = f"{self.URL}?ex_ch={'|'.join(chans[i:i + batch])}&json=1&delay=0&_={int(time.time() * 1000)}"
            for attempt in range(2):
                try:
                    r = self.s.get(url, timeout=15)
                    r.raise_for_status()
                    out += r.json().get("msgArray", [])
                    break
                except (requests.RequestException, ValueError) as e:
                    if attempt:
                        log.info("  第 %d 批查詢失敗：%s", i // batch + 1, e)
                    time.sleep(3)
            time.sleep(pause)
        return out


def scan_intraday(uni: dict[str, dict]) -> dict:
    mis = Mis()
    chans = [f"{'tse' if v['m'] == '上市' else 'otc'}_{c}.tw" for c, v in uni.items()]
    t0 = time.time()
    rows, data_date = [], ""
    for m in mis.query(chans):
        c = m.get("c", "")
        if c not in uni:
            continue
        data_date = m.get("d", data_date)
        ref, up, dn = fnum(m.get("y")), fnum(m.get("u")), fnum(m.get("w"))
        o, h, l, z, v = fnum(m.get("o")), fnum(m.get("h")), fnum(m.get("l")), fnum(m.get("z")), fnum(m.get("v"))
        bids = [fnum(x) for x in str(m.get("b", "")).split("_") if x]
        asks = [fnum(x) for x in str(m.get("a", "")).split("_") if x]
        if not ok(z):  # 這 5 秒沒有成交：用最佳買價／賣價／開盤價代替
            z = next((x for x in (bids[:1] or [math.nan]) + (asks[:1] or [math.nan]) + [o] if ok(x)), math.nan)
        if not ok(z) or not ok(ref) or not ok(v) or v <= 0:
            continue
        px = [x for x in (o, h, l, z) if ok(x)]
        amt = v * 1000 * (sum(px) / len(px))  # 張數 × 1000 股 × 當日均價（估計）
        at_up = ok(up) and z >= up - 1e-9
        at_dn = ok(dn) and z <= dn + 1e-9
        lock_up = at_up and not any(ok(a) for a in asks)          # 委賣掛光＝鎖漲停
        lock_dn = at_dn and not any(ok(b) for b in bids)          # 委買掛光＝鎖跌停
        touched_up = ok(up) and ok(h) and h >= up - 1e-9 and not at_up
        touched_dn = ok(dn) and ok(l) and l <= dn + 1e-9 and not at_dn
        mkt = {"tse": "上市", "otc": "上櫃"}.get(m.get("ex", ""))
        rows.append(row(c, uni[c], z, ref, amt, v, at_up, at_dn, lock_up, lock_dn, touched_up, touched_dn, mkt))
    log.info("全市場掃描 %d 檔，耗時 %.0f 秒", len(rows), time.time() - t0)
    if not data_date:
        raise RuntimeError("證交所沒有回傳資料（可能被暫時封鎖或網路問題）")
    d = datetime.strptime(data_date, "%Y%m%d").date()
    return build(rows, d, "盤中", datetime.now().strftime("%H:%M"), amount_note="估算", official=None)


def row(c, info, z, ref, amt, vol, at_up, at_dn, lock_up=None, lock_dn=None, t_up=False, t_dn=False, mkt=None) -> dict:
    return {"c": c, "n": info["n"], "m": mkt or info["m"], "i": info["i"], "p": round(z, 2),
            "ch": round(z / ref - 1, 5) if ref else 0.0, "a": round(amt / 1e8, 3), "v": int(vol),
            "U": bool(at_up), "D": bool(at_dn), "LU": lock_up, "LD": lock_dn, "TU": bool(t_up), "TD": bool(t_dn)}


# -----------------------------------------------------------------------------
# 盤後：證交所每日收盤行情（官方）＋櫃買中心 OpenAPI
# -----------------------------------------------------------------------------
def twse_mi(d: date) -> dict | None:
    f = FLOW / f"twse_MI_{d:%Y%m%d}.json"
    if f.exists() and f.stat().st_size > 1000:
        return json.loads(f.read_text("utf-8"))
    r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                     params={"date": f"{d:%Y%m%d}", "type": "ALLBUT0999", "response": "json"}, headers=UA, timeout=30)
    r.raise_for_status()
    js = r.json()
    if js.get("stat") != "OK":
        return None
    FLOW.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(js, ensure_ascii=False), "utf-8")
    return js


def parse_twse(js: dict, uni: dict) -> tuple[list[dict], dict]:
    rows, official = [], {}
    for t in js.get("tables", []):
        title, fields = t.get("title") or "", t.get("fields") or []
        if "漲跌證券數合計" in title:
            for r in t.get("data", []):
                k, stock = r[0], r[2] if len(r) > 2 else ""
                n, lim = (stock.replace(",", "").replace(")", "").split("(") + [""])[:2]
                official[k[:2]] = {"n": int(fnum(n) or 0), "lim": int(fnum(lim)) if ok(fnum(lim)) else None}
        if "每日收盤行情" in title and "證券代號" in fields:
            ix = {f: i for i, f in enumerate(fields)}
            for r in t.get("data", []):
                c = r[ix["證券代號"]].strip()
                if c not in uni:
                    continue
                z, diff = fnum(r[ix["收盤價"]]), fnum(r[ix["漲跌價差"]])
                sign = r[ix["漲跌(+/-)"]]
                if not ok(z):
                    continue
                s = -1 if "-" in sign and "+" not in sign else (1 if "+" in sign else 0)
                ref = z - s * (diff if ok(diff) else 0)
                up, dn = limit_prices(ref) if ref > 0 else (math.nan, math.nan)
                amt, vol = fnum(r[ix["成交金額"]]), fnum(r[ix["成交股數"]]) / 1000
                if not ok(amt) or amt <= 0:
                    continue
                rows.append(row(c, uni[c], z, ref, amt, vol, z >= up - 1e-9, z <= dn + 1e-9, mkt="上市"))
    return rows, official


TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def pick(r: dict, *keys):
    for k in keys:
        if k in r and str(r[k]).strip() not in ("", "-", "---"):
            return r[k]
    return None


def tpex_daily(d: date, uni: dict) -> list[dict] | None:
    r = requests.get(TPEX_URL, headers={**UA, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    rd = str(pick(js[0], "Date", "date") or "")
    if rd and len(rd) == 7:  # 民國年 1151110
        rd_date = date(int(rd[:3]) + 1911, int(rd[3:5]), int(rd[5:7]))
        if rd_date != d:
            log.info("櫃買中心資料日期 %s，不是 %s，略過", rd_date, d)
            return None
    rows = []
    for x in js:
        c = str(pick(x, "SecuritiesCompanyCode", "Code", "代號") or "").strip()
        if c not in uni:
            continue
        z = fnum(pick(x, "Close", "收盤"))
        chg = fnum(pick(x, "Change", "漲跌"))
        amt = fnum(pick(x, "TransactionAmount", "TradingAmount", "成交金額(元)", "成交金額"))
        vol = fnum(pick(x, "TradingShares", "TradeVolume", "成交股數"))
        if not ok(z) or not ok(amt) or amt <= 0:
            continue
        ref = z - (chg if ok(chg) else 0)
        up, dn = limit_prices(ref) if ref > 0 else (math.nan, math.nan)
        rows.append(row(c, uni[c], z, ref, amt, vol / 1000 if ok(vol) else 0, z >= up - 1e-9, z <= dn + 1e-9, mkt="上櫃"))
    return rows


def scan_close(uni: dict, d: date) -> dict:
    js = twse_mi(d)
    if not js:
        raise RuntimeError(f"證交所 {d} 沒有收盤行情（休市或尚未公布）")
    rows, official = parse_twse(js, uni)
    log.info("上市 %d 檔（證交所官方）", len(rows))
    otc = None
    try:
        otc = tpex_daily(d, uni)
        if otc:
            log.info("上櫃 %d 檔（櫃買中心）", len(otc))
    except Exception as e:  # noqa: BLE001
        log.info("櫃買中心資料抓取失敗：%s", e)
    note = "上市為證交所官方成交金額"
    if not otc:  # 改用今天 13:31 盤中收盤掃描的上櫃資料
        f = OUT_DIR / f"board_{d:%Y%m%d}.json"
        if f.exists():
            prev = json.loads(f.read_text("utf-8"))
            otc = [r for r in prev.get("rows", []) if r["m"] == "上櫃"] if prev.get("mode") == "盤中" else []
            if otc:
                log.info("上櫃改用盤中收盤掃描資料 %d 檔", len(otc))
                note += "；上櫃為收盤掃描估算值"
    else:
        note += "、上櫃為櫃買中心官方成交金額"
    return build(rows + (otc or []), d, "收盤", datetime.now().strftime("%H:%M"), amount_note=note, official=official)


# -----------------------------------------------------------------------------
# 彙總與輸出
# -----------------------------------------------------------------------------
def build(rows: list[dict], d: date, mode: str, at: str, amount_note: str, official: dict | None) -> dict:
    total = sum(r["a"] for r in rows)
    top = sorted(rows, key=lambda r: -r["a"])[:TOP_N]
    for i, r in enumerate(top, 1):
        r["rk"] = i

    def cnt(m=None):
        rr = [r for r in rows if m is None or r["m"] == m]
        return {"up": sum(r["ch"] > 0 for r in rr), "dn": sum(r["ch"] < 0 for r in rr),
                "flat": sum(r["ch"] == 0 for r in rr), "U": sum(r["U"] for r in rr), "D": sum(r["D"] for r in rr),
                "TU": sum(r["TU"] for r in rr), "TD": sum(r["TD"] for r in rr),
                "amt": round(sum(r["a"] for r in rr), 1), "n": len(rr)}

    by_ind: dict[str, list] = {}
    for r in rows:
        if r["U"]:
            by_ind.setdefault(r["i"] or "其他", []).append(r["c"])
    return {
        "date": d.isoformat(), "mode": mode, "at": at, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "amount_note": amount_note, "official": official,
        "count": {"all": cnt(), "上市": cnt("上市"), "上櫃": cnt("上櫃")},
        "top": top, "top_share": round(sum(r["a"] for r in top) / total, 4) if total else 0,
        "limit_up": sorted([r for r in rows if r["U"]], key=lambda r: -r["a"]),
        "limit_dn": sorted([r for r in rows if r["D"]], key=lambda r: -r["a"]),
        "opened_up": sorted([r for r in rows if r["TU"]], key=lambda r: -r["a"]),
        "opened_dn": sorted([r for r in rows if r["TD"]], key=lambda r: -r["a"]),
        "up_ind": sorted(by_ind.items(), key=lambda kv: -len(kv[1]))[:8],
        "rows": rows,
    }


def save(res: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    d = res["date"].replace("-", "")
    f = OUT_DIR / f"board_{d}.json"
    if res["mode"] == "收盤" or not f.exists() or json.loads(f.read_text("utf-8")).get("mode") != "收盤":
        f.write_text(json.dumps(res, ensure_ascii=False), "utf-8")
    (OUT_DIR / "latest.json").write_text(json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False), "utf-8")
    for old in sorted(OUT_DIR.glob("board_*.json"))[:-20]:
        old.unlink(missing_ok=True)
    page = {k: v for k, v in res.items() if k != "rows"}
    tpl = (BASE / "market_board_template.html").read_text("utf-8")
    HTML_OUT.write_text(tpl.replace("/*__DATA__*/null", json.dumps(page, ensure_ascii=False)), "utf-8")
    c = res["count"]["all"]
    log.info("%s %s｜漲停 %d、跌停 %d｜上漲 %d／下跌 %d｜前 100 名佔成交 %.0f%%｜→ %s",
             res["mode"], res["at"], c["U"], c["D"], c["up"], c["dn"], res["top_share"] * 100, HTML_OUT.name)


def demo(uni: dict | None) -> dict:
    rng = random.Random(11)
    names = list((uni or {}).items())[:1600] or [(f"{1101 + i}", {"n": f"示範{i}", "m": "上市" if i % 2 else "上櫃", "i": "示範業"}) for i in range(1600)]
    rows = []
    for c, info in names:
        ref = round(rng.uniform(15, 900), 1)
        chg = max(-0.1, min(0.1, rng.gauss(0.003, 0.03)))
        z = ref * (1 + chg)
        up, dn = limit_prices(ref)
        if rng.random() < 0.02:
            z = up
        elif rng.random() < 0.006:
            z = dn
        amt = math.exp(rng.gauss(17.5, 1.6))
        rows.append(row(c, info, z, ref, amt, amt / z / 1000, z >= up - 1e-9, z <= dn + 1e-9,
                        rng.random() < .7, rng.random() < .7, rng.random() < .01, rng.random() < .005))
    res = build(rows, date.today(), "盤中", datetime.now().strftime("%H:%M"), "示範資料", None)
    res["demo"] = True
    return res


def probe(uni: dict) -> None:
    print("1) 證交所盤中（mis.twse.com.tw）")
    try:
        m = Mis().query(["tse_2330.tw", "otc_6488.tw"])
        for x in m:
            print("   ", x.get("c"), x.get("n"), "現價", x.get("z"), "漲停", x.get("u"), "跌停", x.get("w"), "日期", x.get("d"))
    except Exception as e:  # noqa: BLE001
        print("   失敗：", e)
    print("2) 證交所每日收盤行情（MI_INDEX）")
    try:
        d = date.today()
        for _ in range(6):
            js = twse_mi(d)
            if js:
                rows, off = parse_twse(js, uni)
                print(f"    {d}：{len(rows)} 檔，官方家數 {off}")
                break
            d -= timedelta(days=1)
    except Exception as e:  # noqa: BLE001
        print("   失敗：", e)
    print("3) 櫃買中心 OpenAPI")
    try:
        r = requests.get(TPEX_URL, headers={**UA, "Accept": "application/json"}, timeout=30)
        js = r.json()
        print(f"    {len(js)} 筆，欄位：{list(js[0].keys()) if js else '空'}")
        if js:
            print("    第一筆：", js[0])
    except Exception as e:  # noqa: BLE001
        print("   失敗：", e)
    print("把以上畫面貼給 Claude，就能確認或修正欄位對應。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--close", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--date", help="--close 指定日期 YYYYMMDD（預設今天）")
    args = ap.parse_args()
    if sys.platform == "win32" and sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(DATA / "logs" / f"board_{date.today():%Y%m%d}.log", encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", handlers=handlers)

    if args.demo:
        try:
            uni = load_universe()
        except RuntimeError:
            uni = None
        save(demo(uni))
        return
    uni = load_universe()
    log.info("股票清單：上市 %d、上櫃 %d 檔", sum(v["m"] == "上市" for v in uni.values()), sum(v["m"] == "上櫃" for v in uni.values()))
    if args.probe:
        probe(uni)
        return
    if args.close:
        d = datetime.strptime(args.date, "%Y%m%d").date() if args.date else date.today()
        save(scan_close(uni, d))
        return
    if args.once or not args.auto:
        save(scan_intraday(uni))
        return

    # --auto：盤中迴圈
    if date.today().weekday() >= 5:
        log.info("週末休市，結束")
        return
    while datetime.now().time() < OPEN_T:
        time.sleep(20)
    last = None
    while True:
        now = datetime.now()
        if now.time() >= FINAL_T:
            log.info("收盤掃描")
            try:
                save(scan_intraday(uni))
            except Exception as e:  # noqa: BLE001
                log.info("收盤掃描失敗：%s", e)
            break
        slot = (now.hour * 60 + now.minute) // SCAN_EVERY_MIN
        if slot != last and now.time() < CLOSE_T:
            last = slot
            try:
                res = scan_intraday(uni)
                if res["date"] != date.today().isoformat() and now.time() >= dtime(9, 10):
                    log.info("證交所資料日期 %s 不是今天，判斷為休市，結束", res["date"])
                    return
                save(res)
            except Exception as e:  # noqa: BLE001
                log.info("掃描失敗：%s", e)
        time.sleep(15)
    log.info("結束")


if __name__ == "__main__":
    main()
