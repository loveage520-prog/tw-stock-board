#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盤中行情儀表板 market_pulse.py
==============================
交易時間每分鐘更新一次，判斷「今天有沒有行情」與「資金集中在哪些族群」，
輸出自動更新的網頁儀表板，並在指定時間或發生異常時推播到手機。

用法：
    python market_pulse.py            # 正式執行（9:00 前啟動會自動等開盤，13:36 自動結束）
    python market_pulse.py --demo     # 模擬一整個交易日（約 2 分鐘跑完），測試儀表板與推播
    python market_pulse.py --once     # 只抓一次就結束
    python market_pulse.py --probe    # 檢查各資料來源與推播設定是否正常

資料來源：
    twse      證交所「基本市況報導」資料（免費，約 5 秒更新，非正式 API）
    shioaji   永豐金證券 API（即時、全市場、含台指期）
    基準量    FinMind（每天開盤前抓一次過去 20 日成交值）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import smtplib
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

BASE = Path(__file__).resolve().parent
DATA = BASE / "pulse_data"
log = logging.getLogger("pulse")
NAN = float("nan")

OPEN_T, CLOSE_T, END_T = dtime(9, 0), dtime(13, 30), dtime(13, 36)
# 台股成交量是 U 型分布：開盤和收盤量大、中午量小。這條曲線表示到第 N 分鐘時，通常已完成全日量的比例
VOL_CURVE = [(0, 0), (15, .15), (30, .24), (60, .36), (90, .46), (120, .54), (150, .62),
             (180, .70), (210, .78), (240, .86), (265, .94), (270, 1.0)]

DEMO_NAMES = {"4979": "華星光", "3081": "聯亞", "3163": "波若威", "6442": "光聖", "3363": "上詮", "4908": "前鼎",
              "3450": "聯鈞", "2455": "全新", "2383": "台光電", "6274": "台燿", "6213": "聯茂", "2368": "金像電",
              "3037": "欣興", "3044": "健鼎", "4958": "臻鼎-KY", "2313": "華通", "8046": "南電", "3189": "景碩",
              "3017": "奇鋐", "3324": "雙鴻", "3653": "健策", "6805": "富世達", "2421": "建準", "1519": "華城",
              "1513": "中興電", "1514": "亞力", "1504": "東元", "1503": "士電", "3711": "日月光投控", "2449": "京元電子",
              "6239": "力成", "6147": "頎邦", "2408": "南亞科", "2344": "華邦電", "2337": "旺宏", "2382": "廣達",
              "3231": "緯創", "6669": "緯穎", "2317": "鴻海", "2356": "英業達", "4938": "和碩", "2376": "技嘉",
              "2454": "聯發科", "3034": "聯詠", "2379": "瑞昱", "3661": "世芯-KY", "3443": "創意", "2330": "台積電",
              "2303": "聯電", "2345": "智邦", "3045": "台灣大", "2412": "中華電", "2327": "國巨", "2492": "華新科",
              "2375": "凱美", "2881": "富邦金", "2882": "國泰金", "2891": "中信金", "2886": "兆豐金", "2884": "玉山金",
              "2885": "元大金", "2892": "第一金", "5880": "合庫金", "2880": "華南金", "2887": "台新金", "2603": "長榮",
              "2609": "陽明", "2615": "萬海", "1301": "台塑", "1303": "南亞", "6505": "台塑化", "2002": "中鋼",
              "1101": "台泥", "1102": "亞泥", "2308": "台達電", "1216": "統一", "2357": "華碩", "2301": "光寶科",
              "3008": "大立光", "2395": "研華", "2207": "和泰車", "2912": "統一超", "1590": "亞德客-KY", "2360": "致茂",
              "2474": "可成", "9910": "豐泰", "2105": "正新", "1402": "遠東新", "2618": "長榮航", "2610": "華航",
              "2801": "彰銀", "2834": "臺企銀", "5871": "中租-KY"}

INDUSTRY = {"01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織", "05": "電機機械", "06": "電器電纜",
            "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造",
            "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "20": "其他", "21": "化學",
            "22": "生技醫療", "23": "油電燃氣", "24": "半導體", "25": "電腦週邊", "26": "光電",
            "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
            "32": "文化創意", "33": "農業科技", "35": "綠能環保", "36": "數位雲端", "37": "運動休閒",
            "38": "居家生活"}


def fnum(x) -> float:
    try:
        v = float(str(x).split("_")[0])
        return v if math.isfinite(v) and v > 0 else NAN
    except (TypeError, ValueError):
        return NAN


def session_frac(now: datetime) -> float:
    m = now.hour * 60 + now.minute + now.second / 60 - 9 * 60
    xs, ys = zip(*VOL_CURVE)
    return float(np.interp(min(max(m, 0), 270), xs, ys))


def pct(a: float, b: float) -> float:
    return a / b - 1 if a == a and b == b and b else NAN


@dataclass
class Quote:
    code: str
    name: str
    price: float
    ref: float          # 昨收（漲跌基準）
    open: float = NAN
    high: float = NAN
    low: float = NAN
    vol_lots: float = NAN   # 累積成交張數
    amount: float = NAN     # 累積成交值（元，證交所來源為估計值）
    up_limit: float = NAN
    down_limit: float = NAN
    category: str = ""

    @property
    def chg(self) -> float:
        return pct(self.price, self.ref)

    @property
    def at_up_limit(self) -> bool:
        return (self.up_limit == self.up_limit and self.price >= self.up_limit) or self.chg >= 0.095

    @property
    def at_down_limit(self) -> bool:
        return (self.down_limit == self.down_limit and self.price <= self.down_limit) or self.chg <= -0.095


# =============================================================================
# 資料來源
# =============================================================================
class TwseSource:
    """證交所基本市況報導（mis.twse.com.tw）。非正式 API：查太頻繁會被暫時封鎖"""

    URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    name = "證交所（約 5 秒更新）"

    def __init__(self, cfg: dict):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                               "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
        self.cache_file = DATA / "market_map.json"
        self.market = json.loads(self.cache_file.read_text("utf-8")) if self.cache_file.exists() else {}
        self.last_price: dict[str, float] = {}
        self.data_date = ""
        self._warm = False

    def now(self) -> datetime:
        return datetime.now()

    def _query(self, chans: list[str]) -> list[dict]:
        if not self._warm:
            try:
                self.s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
            except requests.RequestException:
                pass
            self._warm = True
        out = []
        for i in range(0, len(chans), 50):
            url = f"{self.URL}?ex_ch={'|'.join(chans[i:i + 50])}&json=1&delay=0&_={int(time.time() * 1000)}"
            r = self.s.get(url, timeout=15)
            r.raise_for_status()
            out += r.json().get("msgArray", [])
            time.sleep(0.8)
        return out

    def resolve(self, codes: list[str]) -> list[str]:
        """自動判斷每檔是上市（tse）還是上櫃（otc），結果存檔，下次不用再查"""
        unknown = [c for c in codes if c not in self.market]
        for ex in ("tse", "otc"):
            if not unknown:
                break
            got = {m.get("c") for m in self._query([f"{ex}_{c}.tw" for c in unknown])}
            for c in unknown:
                if c in got:
                    self.market[c] = ex
            unknown = [c for c in unknown if c not in self.market]
        DATA.mkdir(exist_ok=True)
        self.cache_file.write_text(json.dumps(self.market, ensure_ascii=False, indent=0), "utf-8")
        return unknown

    def _parse(self, m: dict) -> Quote:
        code = m.get("c", "")
        z = fnum(m.get("z"))
        if z != z:  # 這 5 秒沒有成交時 z 為 "-"
            z = self.last_price.get(code, NAN)
        if z != z:
            bid, ask = fnum(m.get("b")), fnum(m.get("a"))
            z = bid if bid == bid else ask
        if z != z:
            z = fnum(m.get("o"))
        if z == z:
            self.last_price[code] = z
        vol = fnum(m.get("v"))
        q = Quote(code=code, name=m.get("n", code), price=z, ref=fnum(m.get("y")), open=fnum(m.get("o")),
                  high=fnum(m.get("h")), low=fnum(m.get("l")), vol_lots=vol,
                  up_limit=fnum(m.get("u")), down_limit=fnum(m.get("w")))
        avg_px = np.nanmean([q.open, q.high, q.low, z]) if vol == vol else NAN
        q.amount = vol * 1000 * avg_px if vol == vol else NAN  # 張數 × 1000 股 × 當日均價（估計）
        self.data_date = m.get("d", self.data_date)
        return q

    def quotes(self, codes: list[str]) -> dict[str, Quote]:
        chans = [f"{self.market[c]}_{c}.tw" for c in codes if c in self.market]
        return {q.code: q for q in map(self._parse, self._query(chans)) if q.price == q.price}

    def indices(self) -> dict[str, Quote]:
        out = {}
        for m in self._query(["tse_t00.tw", "otc_o00.tw"]):
            key = "加權" if m.get("c") == "t00" else "櫃買"
            q = self._parse(m)
            q.name, q.amount, q.vol_lots = key, NAN, NAN
            out[key] = q
        return out

    def futures_basis(self) -> float:
        return NAN  # 免費來源沒有台指期

    def full_market(self) -> pd.DataFrame | None:
        return None


class ShioajiSource:
    """永豐金證券 Shioaji API。只用行情，不會下單；不需要憑證，只要 API 金鑰"""

    name = "永豐 Shioaji（即時）"

    def __init__(self, cfg: dict):
        import shioaji as sj  # pip install shioaji
        sc = cfg.get("shioaji") or {}
        if not sc.get("api_key"):
            raise RuntimeError("pulse_config.yaml 的 shioaji.api_key 尚未填寫")
        self.api = sj.Shioaji()
        self.api.login(api_key=sc["api_key"], secret_key=sc["secret_key"], fetch_contract=True)
        self.full = bool(sc.get("full_market", True))
        self.data_date = date.today().strftime("%Y%m%d")
        self._all = None

    def now(self) -> datetime:
        return datetime.now()

    def resolve(self, codes):
        return [c for c in codes if self._contract(c) is None]

    def _contract(self, code):
        try:
            return self.api.Contracts.Stocks[code]
        except Exception:  # noqa: BLE001
            return None

    def _snap(self, contracts) -> list:
        out = []
        for i in range(0, len(contracts), 400):
            out += list(self.api.snapshots(contracts[i:i + 400]))
            time.sleep(0.3)
        return out

    @staticmethod
    def _q(s, name="", category="") -> Quote:
        g = lambda k: getattr(s, k, NAN)  # noqa: E731
        close, chg = float(g("close")), float(g("change_price") or 0)
        return Quote(code=s.code, name=name or s.code, price=close, ref=close - chg, open=float(g("open")),
                     high=float(g("high")), low=float(g("low")), vol_lots=float(g("total_volume")),
                     amount=float(g("total_amount")), category=category)

    def quotes(self, codes):
        cons = {c: self._contract(c) for c in codes}
        cons = {k: v for k, v in cons.items() if v is not None}
        snaps = self._snap(list(cons.values()))
        return {s.code: self._q(s, cons[s.code].name, getattr(cons[s.code], "category", "")) for s in snaps}

    def indices(self):
        tse, otc = self.api.Contracts.Indexs.TSE["001"], self.api.Contracts.Indexs.OTC["101"]
        a, b = self.api.snapshots([tse, otc])
        return {"加權": self._q(a, "加權"), "櫃買": self._q(b, "櫃買")}

    def futures_basis(self) -> float:
        try:
            fut = self.api.Contracts.Futures.TXF.TXFR1
            f = self.api.snapshots([fut])[0]
            return float(f.close) - float(self.indices()["加權"].price)
        except Exception as e:  # noqa: BLE001
            log.debug("台指期取得失敗：%s", e)
            return NAN

    def full_market(self) -> pd.DataFrame | None:
        if not self.full:
            return None
        if self._all is None:
            cs = []
            for ex in ("TSE", "OTC"):
                for c in getattr(self.api.Contracts.Stocks, ex):
                    if len(c.code) == 4 and c.code.isdigit() and getattr(c, "category", "") not in ("", "00"):
                        cs.append(c)
            self._all = {c.code: c for c in cs}
        snaps = self._snap(list(self._all.values()))
        rows = []
        for s in snaps:
            q = self._q(s, self._all[s.code].name, self._all[s.code].category)
            rows.append({"code": q.code, "category": q.category, "chg": q.chg, "amount": q.amount,
                         "up_limit": q.at_up_limit, "down_limit": q.at_down_limit,
                         "vol_ratio": float(getattr(s, "volume_ratio", NAN) or NAN)})
        return pd.DataFrame(rows)


class DemoSource:
    """模擬一整個交易日：每次更新時間前進 5 分鐘，用來測試儀表板與推播"""

    name = "示範資料（模擬）"

    def __init__(self, cfg: dict, step_min: int = 5):
        self.t = datetime.combine(date.today(), dtime(8, 58))
        self.step = timedelta(minutes=step_min)
        self.data_date = date.today().strftime("%Y%m%d")
        self.state: dict[str, dict] = {}
        self.rng = np.random.default_rng(int(date.today().strftime("%Y%m%d")))
        self.mood = self.rng.normal(0.0002, 0.0003)  # 今天的多空基調

    def now(self) -> datetime:
        return self.t

    def tick(self):
        self.t += self.step

    def resolve(self, codes):
        return []

    def _seed(self, code: str) -> dict:
        h = int(hashlib.md5(code.encode()).hexdigest()[:6], 16)
        ref = 50 + h % 2000
        return {"ref": ref, "price": ref, "open": NAN, "high": NAN, "low": NAN, "vol": 0.0,
                "base_vol": 2000 + h % 30000, "beta": 0.6 + (h % 100) / 60, "theme": (h % 7 - 3) * 0.0004}

    def _step(self, st: dict, frac_prev: float, frac_now: float, vol: float = 0.004):
        drift = self.mood * st["beta"] + st["theme"]
        r = self.rng.normal(drift, vol * st["beta"])
        p = st["price"] * (1 + r)
        p = min(max(p, st["ref"] * 0.9), st["ref"] * 1.1)
        if st["open"] != st["open"]:
            st["open"] = st["high"] = st["low"] = p
        st["price"], st["high"], st["low"] = p, max(st["high"], p), min(st["low"], p)
        st["vol"] += st["base_vol"] * (frac_now - frac_prev) * (1.2 + 0.4 * abs(r) * 100) * math.exp(st["theme"] * 600)

    def quotes(self, codes):
        f_now = session_frac(self.t)
        f_prev = session_frac(self.t - self.step)
        out = {}
        for c in codes:
            st = self.state.setdefault(c, self._seed(c))
            if self.t.time() >= OPEN_T:
                self._step(st, f_prev, f_now)
            q = Quote(code=c, name=c, price=st["price"], ref=st["ref"], open=st["open"], high=st["high"],
                      low=st["low"], vol_lots=st["vol"], amount=st["vol"] * 1000 * st["price"],
                      up_limit=round(st["ref"] * 1.1, 2), down_limit=round(st["ref"] * 0.9, 2))
            out[c] = q
        return out

    def indices(self):
        out = {}
        for key, ref, beta in (("加權", 46041.0, 1.0), ("櫃買", 280.0, 1.3)):
            st = self.state.setdefault(key, {"ref": ref, "price": ref, "open": NAN, "high": NAN, "low": NAN,
                                             "vol": 0, "base_vol": 0, "beta": beta, "theme": 0})
            if self.t.time() >= OPEN_T:
                self._step(st, 0, 0, vol=0.0012)
            out[key] = Quote(code=key, name=key, price=st["price"], ref=st["ref"], open=st["open"],
                             high=st["high"], low=st["low"])
        return out

    def futures_basis(self):
        return float(self.rng.normal(-20, 15))

    def full_market(self):
        return None

    def baseline(self, codes) -> dict:
        out = {}
        for c in codes:
            st = self.state.setdefault(c, self._seed(c))
            amt = st["base_vol"] * 1000 * st["ref"]
            out[c] = {"amt20": amt, "amt5": amt * 1.05, "close": st["ref"]}
        return out


# =============================================================================
# 基準資料：過去 20 日平均成交值（每天抓一次並快取）
# =============================================================================
def baseline_from_flow_cache(codes: list[str]) -> dict:
    """sector_flow.py 已經下載過證交所每日收盤（全部上市股），直接拿來算 20 日均量，省下 FinMind 查詢"""
    files = sorted((DATA / "flow").glob("twse_MI_*.json"))
    if not files:
        return {}
    try:
        from sector_flow import parse_mi
    except Exception:  # noqa: BLE001
        return {}
    want, hist, ok_days = set(codes), {}, 0
    today = date.today().strftime("%Y%m%d")
    for fp in reversed(files):
        if fp.stem.endswith(today):
            continue
        try:
            js = json.loads(fp.read_text("utf-8"))
            if js.get("stat") != "OK":
                continue
            st, _ = parse_mi(js)
        except Exception:  # noqa: BLE001
            continue
        if st is None:
            continue
        for r in st[st["code"].isin(want)].itertuples():
            hist.setdefault(r.code, []).append((r.amount, r.close))
        ok_days += 1
        if ok_days >= 20:
            break
    out = {}
    for c, rows in hist.items():  # rows 由新到舊
        amts = [a for a, _ in rows if a == a]
        if len(amts) >= 5:
            out[c] = {"amt20": float(np.mean(amts[:20])), "amt5": float(np.mean(amts[:5])), "close": float(rows[0][1])}
    if out:
        log.info("基準量：%d 檔取自盤後資金流向的證交所資料", len(out))
    return out


def load_baseline(codes: list[str], token: str) -> dict:
    DATA.mkdir(exist_ok=True)
    f = DATA / f"baseline_{date.today():%Y%m%d}.json"
    base = json.loads(f.read_text("utf-8")) if f.exists() else {}
    todo = [c for c in codes if c not in base]
    if todo:
        base.update(baseline_from_flow_cache(todo))
        todo = [c for c in codes if c not in base]
    if todo:
        log.info("建立基準量：%d 檔（每天第一次執行約需 1～2 分鐘）", len(todo))
        s = requests.Session()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        start = (date.today() - timedelta(days=45)).isoformat()
        for i, c in enumerate(todo, 1):
            params = {"dataset": "TaiwanStockPrice", "data_id": c, "start_date": start}
            if token:
                params["token"] = token
            try:
                r = s.get("https://api.finmindtrade.com/api/v4/data", params=params, headers=headers, timeout=20)
                js = r.json()
                df = pd.DataFrame(js.get("data", []))
                df = df[pd.to_datetime(df["date"]).dt.date < date.today()] if not df.empty else df
                if len(df) >= 5:
                    amt = df["Trading_money"].astype(float)
                    base[c] = {"amt20": float(amt.tail(20).mean()), "amt5": float(amt.tail(5).mean()),
                               "close": float(df["close"].iloc[-1])}
            except Exception as e:  # noqa: BLE001
                log.warning("基準量 %s 失敗：%s", c, e)
            if i % 20 == 0:
                log.info("  …%d/%d", i, len(todo))
            time.sleep(0.25)
        f.write_text(json.dumps(base, ensure_ascii=False), "utf-8")
    return base


# =============================================================================
# 行情計算
# =============================================================================
def intraday_state(vr: float, chg: float) -> str:
    """盤中資金狀態：資金流入倍數（今日成交比重 ÷ 平常比重）× 漲跌"""
    if vr != vr or chg != chg:
        return "—"
    if 0.93 < vr < 1.07 and abs(chg) < 0.003:
        return "盤整"
    if vr >= 1.0:
        return "資金進攻" if chg >= 0 else "資金出逃"
    return "惜售上漲" if chg >= 0 else "冷門觀望"


def combined_view(state: str, inst: str | None) -> str:
    """盤中狀態 × 昨日盤後法人象限 → 規則判讀（不是買賣建議）"""
    buy, sell = inst in ("漲潮", "輪動"), inst in ("觀望", "退潮")
    table = {
        ("資金進攻", "漲潮"): "✅ 順勢：法人加速買＋盤中放量上攻",
        ("資金進攻", "輪動"): "🟡 偏多：法人仍買但放緩，盤中轉強",
        ("資金進攻", "觀望"): "👀 轉強訊號：法人賣壓減輕、盤中放量上攻，看明天法人是否轉買",
        ("資金進攻", "退潮"): "⚠️ 反彈：法人仍加速賣，追價風險高",
        ("惜售上漲", "漲潮"): "🟢 籌碼穩：法人買、量縮上漲",
        ("惜售上漲", "輪動"): "🟢 量縮上漲，法人仍偏買",
    }
    if (state, inst) in table:
        return table[(state, inst)]
    if state == "資金出逃":
        return "⚠️ 法人在買但盤中放量下跌：換手或獲利了結" if buy else "⛔ 弱勢：法人賣＋盤中放量下跌" if sell else "⚠️ 放量下跌"
    if state == "惜售上漲":
        return "〰️ 量縮反彈，力道有限" if sell else "〰️ 量縮上漲"
    if state == "冷門觀望":
        return "👀 法人買但盤中冷清，可等拉回" if buy else "〰️ 冷門，觀望" if sell else "〰️ 量縮下跌"
    if state == "資金進攻":
        return "🔥 盤中放量上攻"
    return "〰️ 盤整" if state == "盤整" else "—"

def compute(quotes: dict[str, Quote], idx: dict[str, Quote], base: dict, cfg: dict, now: datetime,
            names: dict[str, str], basis: float, full: pd.DataFrame | None) -> dict:
    th = cfg.get("thresholds") or {}
    frac = session_frac(now)
    q = {c: v for c, v in quotes.items() if v.price == v.price}
    with_base = [c for c in q if c in base and q[c].amount == q[c].amount]
    amt_now = sum(q[c].amount for c in with_base)
    amt_base = sum(base[c]["amt20"] for c in with_base)
    pace = amt_now / (amt_base * frac) if frac > 0.02 and amt_base else NAN

    if full is not None and not full.empty:   # 全市場（永豐）
        up, down = int((full["chg"] > 0).sum()), int((full["chg"] < 0).sum())
        lim_up, lim_dn = int(full["up_limit"].sum()), int(full["down_limit"].sum())
        breadth_src, eq_chg = f"全市場 {len(full)} 檔", float(full["chg"].mean())
    else:                                       # 樣本估計（證交所）
        chs = [v.chg for v in q.values() if v.chg == v.chg]
        up, down = sum(c > 0 for c in chs), sum(c < 0 for c in chs)
        lim_up = sum(v.at_up_limit for v in q.values())
        lim_dn = sum(v.at_down_limit for v in q.values())
        breadth_src, eq_chg = f"樣本 {len(chs)} 檔", float(np.mean(chs)) if chs else NAN
    up_ratio = up / (up + down) if up + down else NAN

    tx, otc = idx.get("加權"), idx.get("櫃買")
    tx_chg = tx.chg if tx else NAN
    otc_chg = otc.chg if otc else NAN
    pos = NAN
    if tx and tx.high == tx.high and tx.high > tx.low:
        pos = (tx.price - tx.low) / (tx.high - tx.low)
    amp = (tx.high - tx.low) / tx.ref if tx and tx.high == tx.high else NAN

    # 方向分數（-5 ～ +5）
    d = 0
    if tx_chg == tx_chg:
        d += 2 if tx_chg > 0.01 else 1 if tx_chg > 0.003 else -2 if tx_chg < -0.01 else -1 if tx_chg < -0.003 else 0
    if up_ratio == up_ratio:
        d += 2 if up_ratio > 0.65 else 1 if up_ratio > 0.55 else -2 if up_ratio < 0.35 else -1 if up_ratio < 0.45 else 0
    if pos == pos:
        d += 1 if pos > 0.75 else -1 if pos < 0.25 else 0

    hot, cold = th.get("pace_hot", 1.15), th.get("pace_cold", 0.85)
    if pace != pace:
        label, desc = "⏳ 資料累積中", "開盤初期量能資料不足，稍後判斷"
    elif pace >= hot and d >= 3:
        label, desc = "🔥 有量上攻", "量增價揚、多數個股上漲，屬於趨勢多頭日"
    elif pace >= hot and d <= -3:
        label, desc = "🧊 有量下殺", "量增價跌、賣壓沉重，屬於趨勢空頭日"
    elif tx_chg == tx_chg and tx_chg > 0.003 and up_ratio == up_ratio and up_ratio < 0.45:
        label, desc = "⚖️ 權值撐盤", "指數漲但多數個股跌，選股難度高"
    elif tx_chg == tx_chg and tx_chg < -0.003 and up_ratio == up_ratio and up_ratio > 0.55:
        label, desc = "🌱 個股活躍", "指數被權值股拖累，但多數個股上漲"
    elif pace <= cold:
        label, desc = "😴 量縮觀望", "成交清淡，行情有限，不宜追價"
    elif d >= 2:
        label, desc = "📈 溫和偏多", "量能普通、方向偏多"
    elif d <= -2:
        label, desc = "📉 溫和偏空", "量能普通、方向偏空"
    else:
        label, desc = "〰️ 震盪整理", "多空不明，等待方向"
    has_move = (pace == pace and pace >= hot) or abs(d) >= 3

    # 自訂族群資金流向
    groups, my = cfg.get("groups") or {}, set(cfg.get("my_groups") or [])
    all_base = sum(base[c]["amt20"] for c in with_base) or NAN
    grows = []
    for g, members in groups.items():
        ms = [c for c in members if c in q]
        if not ms:
            continue
        chs = [q[c].chg for c in ms if q[c].chg == q[c].chg]
        amt = sum(q[c].amount for c in ms if q[c].amount == q[c].amount)
        b20 = sum(base[c]["amt20"] for c in ms if c in base)
        share = amt / amt_now if amt_now else NAN
        bshare = b20 / all_base if all_base == all_base and b20 else NAN
        lead = max(ms, key=lambda c: q[c].chg if q[c].chg == q[c].chg else -9)
        grows.append({"族群": g, "我的": "★" if g in my else "", "平均漲跌": float(np.mean(chs)) if chs else NAN,
                      "上漲比例": sum(c > 0 for c in chs) / len(chs) if chs else NAN,
                      "成交值(億)": amt / 1e8, "成交比重": share,
                      "資金流入倍數": share / bshare if bshare == bshare and bshare else NAN,
                      "量比": amt / (b20 * frac) if b20 and frac > 0.02 else NAN,
                      "領漲": f"{names.get(lead, lead)} {q[lead].chg:+.1%}" if q[lead].chg == q[lead].chg else ""})
        st_, inst = intraday_state(grows[-1]["資金流入倍數"], grows[-1]["平均漲跌"]), (cfg.get("_inst") or {}).get(g)
        grows[-1].update({"盤中狀態": st_, "法人(5日)": inst["q"] if inst else "—",
                          "法人5日(億)": inst["x"] if inst else NAN, "綜合判讀": combined_view(st_, inst["q"] if inst else None)})
    gdf = pd.DataFrame(grows)
    if not gdf.empty:
        gdf["_s"] = gdf["資金流入倍數"].fillna(0) * np.sign(gdf["平均漲跌"].fillna(0)).replace(0, 1)
        gdf = gdf.sort_values(["資金流入倍數"], ascending=False).drop(columns="_s").reset_index(drop=True)

    # 產業資金流向（全市場才有）
    idf = pd.DataFrame()
    if full is not None and not full.empty:
        tot = full["amount"].sum()
        idf = (full.groupby("category").agg(平均漲跌=("chg", "mean"), 成交值=("amount", "sum"),
                                            檔數=("code", "count"), 量比=("vol_ratio", "median")).reset_index())
        idf["產業"] = idf["category"].map(INDUSTRY).fillna(idf["category"])
        idf["成交比重"] = idf["成交值"] / tot
        idf["成交值(億)"] = idf["成交值"] / 1e8
        idf = idf.sort_values("成交值", ascending=False)[["產業", "平均漲跌", "成交值(億)", "成交比重", "量比", "檔數"]]

    # 個股排行
    srows = []
    min_amt = th.get("hot_stock_min_amount", 1.0) * 1e8
    for c, v in q.items():
        b = base.get(c, {})
        srows.append({"代號": c, "名稱": names.get(c, v.name), "現價": v.price, "漲跌": v.chg,
                      "成交值(億)": v.amount / 1e8 if v.amount == v.amount else NAN,
                      "量比": v.amount / (b["amt20"] * frac) if b.get("amt20") and frac > 0.02 and b["amt20"] >= min_amt else NAN})
    sdf = pd.DataFrame(srows)
    top_amt = sdf.sort_values("成交值(億)", ascending=False).head(10) if not sdf.empty else sdf
    top_hot = sdf.dropna(subset=["量比"]).sort_values("量比", ascending=False).head(10) if not sdf.empty else sdf

    return {"time": now.strftime("%H:%M"), "frac": frac, "pace": pace, "label": label, "desc": desc,
            "has_move": has_move, "dir": d, "tx": tx, "otc": otc, "tx_chg": tx_chg, "otc_chg": otc_chg,
            "eq_chg": eq_chg, "up": up, "down": down, "up_ratio": up_ratio, "lim_up": lim_up, "lim_dn": lim_dn,
            "breadth_src": breadth_src, "pos": pos, "amp": amp, "basis": basis, "groups": gdf, "industry": idf,
            "top_amt": top_amt, "top_hot": top_hot, "stocks": sdf}


# =============================================================================
# 推播
# =============================================================================
class Notifier:
    def __init__(self, cfg: dict, dry: bool = False):
        self.n = cfg.get("notify") or {}
        self.dry = dry
        self.sent: set[str] = set()
        self.history: list[str] = []

    @property
    def enabled(self) -> list[str]:
        n = self.n
        out = []
        if n.get("telegram_bot_token") and n.get("telegram_chat_id"):
            out.append("Telegram")
        if n.get("discord_webhook"):
            out.append("Discord")
        if n.get("email_smtp_user") and n.get("email_app_password") and n.get("email_to"):
            out.append("Email")
        return out

    def send(self, text: str, key: str | None = None) -> bool:
        if key and key in self.sent:
            return False
        if key:
            self.sent.add(key)
        self.history.append(text.splitlines()[0])
        log.info("推播：%s", text.splitlines()[0])
        if self.dry or not self.enabled:
            with (DATA / "notify_log.txt").open("a", encoding="utf-8") as fh:
                fh.write(f"\n-----\n{text}\n")
            return True
        n, ok = self.n, True
        try:
            if "Telegram" in self.enabled:
                requests.post(f"https://api.telegram.org/bot{n['telegram_bot_token']}/sendMessage",
                              data={"chat_id": n["telegram_chat_id"], "text": text}, timeout=15).raise_for_status()
            if "Discord" in self.enabled:
                requests.post(n["discord_webhook"], json={"content": text[:1900]}, timeout=15).raise_for_status()
            if "Email" in self.enabled:
                msg = MIMEText(text, "plain", "utf-8")
                msg["Subject"], msg["From"], msg["To"] = text.splitlines()[0], n["email_smtp_user"], n["email_to"]
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
                    s.login(n["email_smtp_user"], n["email_app_password"])
                    s.send_message(msg)
        except Exception as e:  # noqa: BLE001
            log.warning("推播失敗：%s", e)
            ok = False
        return ok


def fmt_pct(x, digits=1) -> str:
    return "—" if x != x else f"{x:+.{digits}%}"


def summary_text(m: dict, title: str) -> str:
    tx, otc = m["tx"], m["otc"]
    g = m["groups"]
    lines = [f"📊 台股{title} {m['time']}",
             f"行情：{m['label']}（{'有行情' if m['has_move'] else '行情有限'}）",
             m["desc"]]
    if tx:
        lines.append(f"加權 {tx.price:,.0f}（{fmt_pct(m['tx_chg'], 2)}）｜櫃買 {otc.price:,.2f}（{fmt_pct(m['otc_chg'], 2)}）"
                     if otc else f"加權 {tx.price:,.0f}（{fmt_pct(m['tx_chg'], 2)}）")
    pace = "—" if m["pace"] != m["pace"] else f"{m['pace']:.2f} 倍"
    upr = "—" if m["up_ratio"] != m["up_ratio"] else f"{m['up_ratio']:.0%}"
    lines.append(f"推估量能 {pace}｜上漲比例 {upr}｜漲停 {m['lim_up']}／跌停 {m['lim_dn']}")
    if m["basis"] == m["basis"]:
        lines.append(f"台指期價差 {m['basis']:+.0f} 點")
    if not g.empty:
        inflow = g.dropna(subset=["資金流入倍數"]).head(3)
        lines.append("資金流入：" + "、".join(f"{r.族群}（{r.資金流入倍數:.1f}x，{fmt_pct(r.平均漲跌)}）"
                                         for r in inflow.itertuples()))
        rest = g.dropna(subset=["資金流入倍數"])
        outflow = rest[(~rest["族群"].isin(inflow["族群"])) & (rest["資金流入倍數"] < 1)].tail(2)
        if not outflow.empty:
            lines.append("資金流出：" + "、".join(f"{r.族群}（{r.資金流入倍數:.1f}x，{fmt_pct(r.平均漲跌)}）"
                                             for r in outflow.itertuples()))
        if "綜合判讀" in g:
            good = g[g["綜合判讀"].str.startswith("✅")]
            risk = g[g["綜合判讀"].str.startswith(("⚠️", "⛔"))]
            if not good.empty:
                lines.append("順勢（法人＋盤中同步買）：" + "、".join(good["族群"].head(4)))
            if not risk.empty:
                lines.append("注意（反彈追價或放量下跌）：" + "、".join(risk["族群"].head(4)))
        mine = g[g["我的"] == "★"]
        if not mine.empty:
            lines.append("我的族群：")
            for _, r in mine.iterrows():
                lines.append(f"・{r['族群']} {fmt_pct(r['平均漲跌'])}｜{r.get('盤中狀態', '')}"
                             + (f"｜法人{r['法人(5日)']}" if r.get("法人(5日)", "—") != "—" else "")
                             + (f"\n　{r['綜合判讀']}" if r.get("綜合判讀", "—") not in ("—", "") else ""))
    return "\n".join(lines)


def check_events(m: dict, cfg: dict, nt: Notifier, prev_label: str | None, holdings: dict[str, str]):
    n = cfg.get("notify") or {}
    now_t = m["time"]
    for lvl in n.get("alert_index_moves", [1.0, 2.0]):
        if m["tx_chg"] == m["tx_chg"] and abs(m["tx_chg"]) >= lvl / 100:
            side = "上漲" if m["tx_chg"] > 0 else "下跌"
            nt.send(f"⚠️ 加權指數{side}超過 {lvl:g}%（{m['time']}）\n目前 {m['tx'].price:,.0f}（{fmt_pct(m['tx_chg'], 2)}）\n"
                    f"行情：{m['label']}", key=f"idx_{side}_{lvl}")
    gm = n.get("alert_group_move", 3.0) / 100
    for r in m["groups"].itertuples():
        if r.我的 == "★" and r.平均漲跌 == r.平均漲跌 and abs(r.平均漲跌) >= gm:
            side = "大漲" if r.平均漲跌 > 0 else "大跌"
            nt.send(f"⚠️ 我的族群「{r.族群}」{side} {fmt_pct(r.平均漲跌)}（{now_t}）\n領漲：{r.領漲}｜資金流入 {r.資金流入倍數:.1f}x",
                    key=f"grp_{r.族群}_{side}")
    hm = n.get("alert_holding_move", 5.0) / 100
    sdf = m["stocks"]
    for code, name in holdings.items():
        row = sdf[sdf["代號"] == code] if not sdf.empty else sdf
        if row.empty:
            continue
        c = float(row["漲跌"].iloc[0])
        if c == c and abs(c) >= hm:
            side = "大漲" if c > 0 else "大跌"
            nt.send(f"⚠️ 持股 {name}({code}) {side} {fmt_pct(c)}（{now_t}）\n現價 {float(row['現價'].iloc[0]):,.2f}",
                    key=f"hold_{code}_{side}")


# =============================================================================
# 網頁儀表板
# =============================================================================
def svg_line(series: list[tuple[str, float]], ref: float, w=640, h=210) -> str:
    pts = [(t, v) for t, v in series if v == v]
    if len(pts) < 2:
        return '<div class="empty">資料累積中…</div>'
    if ref != ref:
        ref = pts[0][1]
    mins = [int(t[:2]) * 60 + int(t[3:5]) - 540 for t, _ in pts]
    vals = [v for _, v in pts] + [ref]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    X = lambda m: 62 + (w - 72) * min(max(m, 0), 270) / 270  # noqa: E731
    Y = lambda v: 12 + (h - 36) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    path = " ".join(f"{X(m):.1f},{Y(v):.1f}" for m, (_, v) in zip(mins, pts))
    last = pts[-1][1]
    color = "var(--up)" if last >= ref else "var(--down)"
    ticks = "".join(f'<text x="{X(mm):.0f}" y="{h - 4}" class="tk" text-anchor="{an}">{lab}</text>'
                    for mm, lab, an in ((0, "09:00", "start"), (90, "10:30", "middle"), (180, "12:00", "middle"),
                                        (270, "13:30", "end")))
    ys, ylab = [], ""
    for v in (ref, hi - pad, lo + pad):   # 昨收優先，其他標籤和已畫的距離太近就略過
        if all(abs(Y(v) - y0) >= 14 for y0 in ys):
            ys.append(Y(v))
            ylab += f'<text x="56" y="{Y(v) + 4:.0f}" class="tk" text-anchor="end">{v:,.0f}</text>'
    return (f'<div class="tw"><svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="加權指數盤中走勢">'
            f'<line x1="62" x2="{w - 10}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}" class="refline"/>'
            f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<circle cx="{X(mins[-1]):.1f}" cy="{Y(last):.1f}" r="3.5" fill="{color}"/>{ticks}{ylab}</svg></div>')


STATE_COLOR = {"資金進攻": "var(--q-up)", "資金出逃": "var(--q-down)", "惜售上漲": "var(--q-rot)",
               "冷門觀望": "var(--q-wait)", "盤整": "var(--q-wait)", "—": "var(--q-wait)"}
INST_COLOR = {"漲潮": "#d6453d", "輪動": "#e0a020", "觀望": "#8d9bb5", "退潮": "#2b9a62"}


def svg_bubbles(g: pd.DataFrame, w=900, h=480) -> str:
    """橫軸：量比（log 刻度，1＝和平常一樣）；縱軸：族群平均漲跌；大小：成交值；外圈顏色：昨日法人象限"""
    if g is None or g.empty or g["資金流入倍數"].dropna().empty:
        return '<div class="empty">開盤後約 5 分鐘開始顯示</div>'
    d = g.dropna(subset=["資金流入倍數", "平均漲跌"]).copy()
    span = max(0.5, min(2.0, float(np.log2(d["資金流入倍數"].clip(0.25, 4)).abs().max()) * 1.15))
    lx = lambda v: math.log2(min(max(v, 2 ** -span), 2 ** span))  # noqa: E731
    ymax = max(0.03, float(d["平均漲跌"].abs().max()) * 1.15)
    smax = float(d["成交值(億)"].max()) or 1
    PL, PR, PT, PB = 56, 16, 14, 40
    X = lambda v: PL + (lx(v) + span) / (2 * span) * (w - PL - PR)  # noqa: E731
    Y = lambda v: PT + (1 - (v + ymax) / (2 * ymax)) * (h - PT - PB)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" class="bub" role="img" aria-label="盤中族群資金地圖">']
    ticks = [t for t in (0.25, 0.5, 0.7, 0.85, 1, 1.2, 1.4, 2, 4) if abs(math.log2(t)) <= span + 1e-9]
    for v, lab in ((t, "1x 平常" if t == 1 else f"{t:g}x") for t in ticks):
        out.append(f'<line x1="{X(v):.0f}" x2="{X(v):.0f}" y1="{PT}" y2="{h - PB}" class="{"ax" if v == 1 else "gl"}"/>'
                   f'<text x="{X(v):.0f}" y="{h - PB + 16}" class="tk" text-anchor="middle">{lab}</text>')
    step = 0.01 if ymax <= 0.04 else 0.02 if ymax <= 0.08 else 0.05
    t = -math.floor(ymax / step) * step
    while t <= ymax + 1e-9:
        out.append(f'<line x1="{PL}" x2="{w - PR}" y1="{Y(t):.0f}" y2="{Y(t):.0f}" class="{"ax" if abs(t) < 1e-9 else "gl"}"/>'
                   f'<text x="{PL - 6}" y="{Y(t) + 4:.0f}" class="tk" text-anchor="end">{t:+.0%}</text>')
        t += step
    out.append(f'<text x="{w - PR}" y="{h - 6}" class="tk" text-anchor="end">資金流入倍數（今日成交比重 ÷ 平常比重）→ 資金流入</text>'
               f'<text x="{PL}" y="{h - 6}" class="tk">← 資金流出</text>')
    # 只標重點：我的族群、成交值前 8、漲跌最大與最小各 3
    key = set(d.loc[d["我的"] == "★", "族群"]) | set(d.nlargest(8, "成交值(億)")["族群"]) \
        | set(d.nlargest(3, "平均漲跌")["族群"]) | set(d.nsmallest(3, "平均漲跌")["族群"])
    for txt, x, y, an, col in (("資金進攻", w - PR - 8, PT + 16, "end", "var(--q-up)"),
                               ("資金出逃", w - PR - 8, h - PB - 8, "end", "var(--q-down)"),
                               ("惜售上漲", PL + 8, PT + 16, "start", "var(--q-rot)"),
                               ("冷門觀望", PL + 8, h - PB - 8, "start", "var(--q-wait)")):
        out.append(f'<text x="{x}" y="{y}" class="ql" text-anchor="{an}" fill="{col}">{txt}</text>')
    for _, r in d.sort_values("成交值(億)", ascending=False).iterrows():
        amt, inst, mine = float(r["成交值(億)"]), r.get("法人(5日)", "—"), r.get("我的") == "★"
        rad = 6 + 26 * math.sqrt(max(amt, 0) / smax)
        ring = INST_COLOR.get(inst, "var(--card)")
        cx, cy = X(r["資金流入倍數"]), Y(r["平均漲跌"])
        name = (r["族群"] if len(r["族群"]) <= 5 else r["族群"][:5] + "…") if r["族群"] in key else ""
        tip = (f"{r['族群']}｜{r.get('盤中狀態', '')}｜漲跌 {r['平均漲跌']:+.2%}｜資金流入 {r['資金流入倍數']:.2f}x｜"
               f"量比 {r['量比']:.2f}x｜"
               f"成交 {amt:.0f} 億｜法人 {inst}｜{r.get('綜合判讀', '')}")
        out.append(f'<g><title>{escape(tip)}</title><circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" '
                   f'fill="{STATE_COLOR.get(r.get("盤中狀態", "—"), "var(--q-wait)")}" fill-opacity=".8" stroke="{ring}" '
                   f'stroke-width="{3 if inst in INST_COLOR else 1.5}"/>'
                   f'<text x="{cx:.1f}" y="{cy - rad - 3:.1f}" class="bl" text-anchor="middle"'
                   f'{" font-weight=800" if mine else ""}>{"★" if mine else ""}{escape(name)}</text></g>')
    out.append("</svg>")
    return f'<div class="tw">{"".join(out)}</div>'


def cls(x) -> str:
    return "" if x != x else ("up" if x > 0 else "down" if x < 0 else "")


def html_table(df: pd.DataFrame, cols: dict[str, str], bar_col: str | None = None) -> str:
    if df is None or df.empty:
        return '<div class="empty">無資料</div>'
    head = "".join(f"<th>{escape(c)}</th>" for c in cols)
    has_bar = bool(bar_col and bar_col in df and df[bar_col].notna().any())
    bmax = float(df[bar_col].max()) if has_bar else 0
    bmin = float(df[bar_col].min()) if has_bar else 0
    span = bmax - bmin
    body = []
    for _, r in df.iterrows():
        tds = []
        for c, kind in cols.items():
            v = r.get(c, "")
            if kind == "pct":
                tds.append(f'<td class="num {cls(v)}">{fmt_pct(v)}</td>')
            elif kind == "ratio":
                txt = "—" if v != v else f"{v:.2f}x"
                hot = " hot" if v == v and v >= 1.3 else " cold" if v == v and v <= 0.7 else ""
                wpct = 15 + 85 * (v - bmin) / span if span > 1e-9 else 60
                bar = (f'<span class="bar" style="width:{wpct:.0f}%"></span>'
                       if c == bar_col and v == v and bmax else "")
                tds.append(f'<td class="num{hot}"><div class="cell">{bar}<span>{txt}</span></div></td>')
            elif kind == "share":
                tds.append(f'<td class="num">{"—" if v != v else f"{v:.1%}"}</td>')
            elif kind == "num":
                tds.append(f'<td class="num">{"—" if v != v else f"{v:,.2f}"}</td>')
            elif kind == "num1":
                tds.append(f'<td class="num">{"—" if v != v else f"{v:,.1f}"}</td>')
            else:
                tds.append(f"<td>{escape(str(v))}</td>")
        mine = ' class="mine"' if str(r.get("我的", "")) == "★" else ""
        body.append(f"<tr{mine}>{''.join(tds)}</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def write_dashboard(m: dict, hist: list[dict], events: list[str], src_name: str, interval: int, final: bool) -> Path:
    out = BASE / "dashboard.html"
    tx, otc = m["tx"], m["otc"]
    series = [(h["time"], h["taiex"]) for h in hist]
    tone = "hot" if m["label"].startswith(("🔥", "📈", "🌱")) else "cold" if m["label"].startswith(("🧊", "📉")) else "flat"

    def tile(title, val, sub="", c=""):
        return f'<div class="tile"><div class="t">{title}</div><div class="v {c}">{val}</div><div class="s">{sub}</div></div>'

    pace = m["pace"]
    tiles = "".join([
        tile("加權指數", f"{tx.price:,.0f}" if tx else "—", fmt_pct(m["tx_chg"], 2), cls(m["tx_chg"])),
        tile("櫃買指數", f"{otc.price:,.2f}" if otc else "—", fmt_pct(m["otc_chg"], 2), cls(m["otc_chg"])),
        tile("推估量能", "—" if pace != pace else f"{pace:.2f}x", "對 20 日均量（≥1.15 有量）",
             "up" if pace == pace and pace >= 1.15 else "down" if pace == pace and pace <= 0.85 else ""),
        tile("上漲比例", "—" if m["up_ratio"] != m["up_ratio"] else f"{m['up_ratio']:.0%}",
             f"漲 {m['up']}／跌 {m['down']}（{m['breadth_src']}）", cls((m['up_ratio'] or 0.5) - 0.5)),
        tile("漲停／跌停", f"{m['lim_up']}／{m['lim_dn']}", "樣本內" if "樣本" in m["breadth_src"] else "全市場"),
        tile("等權平均漲跌", fmt_pct(m["eq_chg"], 2), "和加權比：判斷權值或中小撐盤", cls(m["eq_chg"])),
        tile("指數位置", "—" if m["pos"] != m["pos"] else f"{m['pos']:.0%}", "0%＝今日最低、100%＝今日最高"),
        tile("台指期價差", "—" if m["basis"] != m["basis"] else f"{m['basis']:+.0f}", "正價差偏多／逆價差偏空",
             cls(m["basis"])),
    ])
    gcols = {"我的": "txt", "族群": "txt", "盤中狀態": "txt", "法人(5日)": "txt", "綜合判讀": "txt",
             "平均漲跌": "pct", "量比": "ratio", "資金流入倍數": "ratio", "成交比重": "share", "上漲比例": "share",
             "成交值(億)": "num1", "領漲": "txt"}
    has_inst = not m["groups"].empty and (m["groups"].get("法人(5日)", pd.Series(dtype=str)) != "—").any()
    inst_note = ("外圈顏色＝昨日盤後法人象限：<b style='color:#d6453d'>漲潮</b>、<b style='color:#e0a020'>輪動</b>、"
                 "<b style='color:#8d9bb5'>觀望</b>、<b style='color:#2b9a62'>退潮</b>。" if has_inst else
                 "尚未有盤後法人資料：前一天收盤後執行 sector_flow.py，這裡就會加上法人背景與綜合判讀。")
    ind = ""
    if not m["industry"].empty:
        ind = ("<section><h2>產業資金流向（全市場）</h2>"
               + html_table(m["industry"].head(15), {"產業": "txt", "平均漲跌": "pct", "成交值(億)": "num1",
                                                     "成交比重": "share", "量比": "ratio", "檔數": "txt"}) + "</section>")
    ev = "".join(f"<li>{escape(e)}</li>" for e in events[-12:][::-1]) or "<li>尚無事件</li>"
    refresh = "" if final else f'<meta http-equiv="refresh" content="{max(interval, 20)}">'
    status = "今日已收盤，以下為最終結果" if final else f"每 {interval} 秒自動更新"
    html = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>台股盤中脈動</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#6b7280;--line:#e5e7eb;--up:#d63c3c;--down:#1e8a4c;
--hot:#fde7e7;--cold:#e3f4ea;--flat:#eef1f5;--accent:#2c5aa0;--mine:#fff8e1;
--q-up:#d6453d;--q-down:#2b9a62;--q-rot:#e0a020;--q-wait:#8d9bb5;--grid:#e8ebf0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11151c;--card:#1a2029;--fg:#e6e9ef;--muted:#9aa3b2;--line:#2a313c;
--up:#ff6b6b;--down:#3ccf7f;--hot:#3a1f22;--cold:#173026;--flat:#222a35;--accent:#7aa7ff;--mine:#2e2a1a;
--q-up:#ef6a5f;--q-down:#3fbf7f;--q-rot:#e8b443;--q-wait:#7d8aa3;--grid:#232a34}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;font-size:14px}}
main{{max-width:1180px;margin:0 auto;padding:16px}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}}
h1{{font-size:20px;margin:0}}.meta{{color:var(--muted);font-size:12px}}
.verdict{{margin:12px 0;padding:16px;border-radius:12px;background:var(--{tone})}}
.verdict .l{{font-size:26px;font-weight:700}}.verdict .d{{color:var(--muted);margin-top:4px}}
.badge{{display:inline-block;margin-left:8px;padding:2px 10px;border-radius:99px;font-size:13px;
background:var(--card);border:1px solid var(--line);vertical-align:middle}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.tile .t{{color:var(--muted);font-size:12px}}.tile .v{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}}
.tile .s{{color:var(--muted);font-size:12px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-top:12px}}
h2{{font-size:15px;margin:0 0 8px}}.grid2{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}}
@media (max-width:760px){{.grid2{{grid-template-columns:minmax(0,1fr)}}}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{color:var(--muted);font-weight:500;font-size:12px}}td.num{{text-align:right}}
tr.mine td{{background:var(--mine)}}.up{{color:var(--up)}}.down{{color:var(--down)}}
td.hot{{color:var(--up);font-weight:700}}td.cold{{color:var(--muted)}}
.cell{{position:relative;display:flex;justify-content:flex-end}}.cell span{{position:relative}}
.bar{{position:absolute;left:0;top:2px;bottom:2px;background:var(--accent);opacity:.18;border-radius:3px}}
.chart{{width:100%;min-width:520px;height:auto;display:block}}.chart .tk{{font-size:13px;fill:var(--muted)}}
.refline{{stroke:var(--muted);stroke-dasharray:4 4}}.empty{{color:var(--muted);padding:12px}}
svg.bub{{width:100%;min-width:620px;height:auto;display:block;background:var(--bg);border-radius:10px}}
svg.bub .gl{{stroke:var(--grid)}}svg.bub .ax{{stroke:var(--muted);stroke-width:1.2}}
svg.bub .tk{{font-size:12px;fill:var(--muted)}}svg.bub .ql{{font-size:14px;font-weight:700;opacity:.45}}
svg.bub .bl{{font-size:12px;fill:var(--fg);paint-order:stroke;stroke:var(--card);stroke-width:3px}}
svg.bub g:hover circle{{stroke:var(--fg);stroke-width:3}}
ul.ev{{margin:0;padding-left:18px;max-height:220px;overflow:auto}}ul.ev li{{margin:3px 0}}
.note{{color:var(--muted);font-size:12px;margin-top:14px;line-height:1.6}}
</style></head><body><main>
<header><h1>台股盤中脈動 {date.today():%m/%d}</h1>
<div class="meta">更新 {m['time']}｜{status}｜來源：{escape(src_name)}｜<a href="sector_flow.html">資金流向・全球市場 →</a></div></header>
<div class="verdict"><div class="l">{escape(m['label'])}<span class="badge">{'有行情' if m['has_move'] else '行情有限'}</span>
<span class="badge">方向分數 {m['dir']:+d}</span></div><div class="d">{escape(m['desc'])}</div></div>
<div class="tiles">{tiles}</div>
<section><h2>加權指數盤中走勢</h2>{svg_line(series, tx.ref if tx else NAN)}</section>
<section><h2>盤中資金地圖</h2>{svg_bubbles(m['groups'])}
<div class="note">橫軸看資金「相對」往哪裡去：今天這個族群佔大盤成交的比重，比平常高就在右邊（就算整體量縮也看得出輪動）。
右上＝<b style="color:var(--q-up)">資金進攻</b>（資金流入、上漲）、右下＝<b style="color:var(--q-down)">資金出逃</b>（資金湧入但下跌，換手或出貨）、
左上＝<b style="color:var(--q-rot)">惜售上漲</b>（交易冷清但上漲）、左下＝<b style="color:var(--q-wait)">冷門觀望</b>（資金離開、下跌）。
泡泡越大＝成交值越大；★＝我的族群。{inst_note}滑鼠移到泡泡上看數字。開盤 15 分鐘內量比誤差大。</div></section>
<section><h2>族群資金流向（★＝我的族群，依資金流入倍數排序）</h2>{html_table(m['groups'], gcols, '資金流入倍數')}
<div class="note">資金流入倍數＝今天該族群佔成交值的比重 ÷ 過去 20 日的平均比重。大於 1 代表資金比平常更集中在這個族群；
搭配平均漲跌一起看：流入又上漲＝資金進場，流入但下跌＝有人在出貨。量比＝推估全日成交值 ÷ 20 日均量。</div></section>
{ind}
<div class="grid2">
<section><h2>成交值前 10 名</h2>{html_table(m['top_amt'], {'名稱': 'txt', '代號': 'txt', '現價': 'num', '漲跌': 'pct', '成交值(億)': 'num1'})}</section>
<section><h2>爆量股（量比前 10 名）</h2>{html_table(m['top_hot'], {'名稱': 'txt', '代號': 'txt', '現價': 'num', '漲跌': 'pct', '量比': 'ratio'})}</section>
</div>
<section><h2>今日事件</h2><ul class="ev">{ev}</ul></section>
<div class="note">推估量能以「到目前為止通常完成全日量的比例」換算，開盤 15 分鐘內誤差較大。
證交所來源的成交值為「張數 × 當日均價」估算，漲跌家數為樣本估計；改用永豐來源可取得全市場精確數字。本頁為資料整理，不構成投資建議。</div>
</main>
<script>
try{{const y=sessionStorage.getItem("pulseY");if(y)window.scrollTo(0,+y);
addEventListener("beforeunload",()=>sessionStorage.setItem("pulseY",String(scrollY)));}}catch(e){{}}
</script></body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


# =============================================================================
# 主流程
# =============================================================================
def load_cfg(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text("utf-8")) or {}
    groups = {k: [str(x) for x in v] for k, v in (cfg.get("groups") or {}).items()}
    fc = BASE / "flow_config.yaml"
    if fc.exists():  # 盤後資金流向的概念族群也一起盤中追蹤
        for k, v in ((yaml.safe_load(fc.read_text("utf-8")) or {}).get("themes") or {}).items():
            groups.setdefault(k, [str(x) for x in v])
    cfg["groups"] = groups
    if not cfg.get("finmind_token"):
        mc = BASE / "config.yaml"
        if mc.exists():
            cfg["finmind_token"] = (yaml.safe_load(mc.read_text("utf-8")) or {}).get("finmind_token", "")
    return cfg


def load_inst_context(demo: bool, groups: list[str]) -> dict:
    """讀 sector_flow.py 盤後產生的各板塊法人象限（前一個交易日）"""
    if demo:
        rng = np.random.default_rng(7)
        qs = ["漲潮", "輪動", "觀望", "退潮"]
        return {g: {"q": qs[int(rng.integers(0, 4))], "x": float(rng.normal(0, 30))} for g in groups}
    f = DATA / "flow" / "latest_sectors.json"
    if not f.exists():
        log.info("找不到盤後法人資料（執行 sector_flow.py 後就會有）")
        return {}
    js = json.loads(f.read_text("utf-8"))
    log.info("法人背景：%s 盤後資料，%d 個板塊", js.get("date"), len(js.get("sectors", {})))
    return js.get("sectors", {})


def holdings_tw() -> dict[str, str]:
    mc = BASE / "config.yaml"
    if not mc.exists():
        return {}
    c = yaml.safe_load(mc.read_text("utf-8")) or {}
    return {str(h["symbol"]): h.get("name", str(h["symbol"])) for h in c.get("holdings") or []
            if str(h.get("market", "")).upper() == "TW"}


def run(args):
    cfg = load_cfg(Path(args.config))
    DATA.mkdir(exist_ok=True)
    interval = 2 if args.demo else int(cfg.get("interval_seconds", 60))
    if args.demo:
        src = DemoSource(cfg)
    elif cfg.get("source") == "shioaji":
        src = ShioajiSource(cfg)
    else:
        src = TwseSource(cfg)
    log.info("資料來源：%s", src.name)
    if not args.demo and not args.once and date.today().weekday() >= 5:
        log.info("今天是週末，不是交易日，程式結束。（想看最近一個交易日的資料可以加 --once）")
        return None

    groups = cfg.get("groups") or {}
    hold = holdings_tw()
    universe = list(dict.fromkeys([str(c) for c in (cfg.get("market_sample") or [])]
                                  + [str(c) for g in groups.values() for c in g] + list(hold)))
    missing = src.resolve(universe)
    if missing:
        log.warning("查不到這些代號（已略過）：%s", ", ".join(missing))
        universe = [c for c in universe if c not in missing]
    base = src.baseline(universe) if args.demo else load_baseline(universe, cfg.get("finmind_token", ""))
    log.info("監控 %d 檔，基準量 %d 檔", len(universe), len(base))

    cfg["_inst"] = load_inst_context(demo=args.demo, groups=list(groups))
    names: dict[str, str] = {**(DEMO_NAMES if args.demo else {}), **hold}
    nt = Notifier(cfg, dry=args.demo or args.once)  # --once 只寫紀錄，不真的推播
    if args.demo:
        (DATA / "notify_log.txt").write_text(f"示範模式推播紀錄 {datetime.now():%F %T}\n", "utf-8")
    sum_times = list((cfg.get("notify") or {}).get("summary_times", ["09:30", "11:00", "13:00", "13:35"]))
    hist_file = DATA / f"pulse_{date.today():%Y%m%d}{'_demo' if args.demo else ''}.csv"
    hist: list[dict] = [] if args.demo or not hist_file.exists() else pd.read_csv(hist_file).to_dict("records")
    events: list[str] = []
    opened, last_m = False, None
    last_mk = 0.0  # 全球市場上次更新時間
    stable, cand, cand_since = None, None, None
    confirm_min = float((cfg.get("notify") or {}).get("label_confirm_minutes", 5))
    dash = BASE / "dashboard.html"

    while True:
        now = src.now()
        t = now.time()
        if t < dtime(8, 59) and not args.once:
            wait = (datetime.combine(now.date(), dtime(8, 59)) - now).total_seconds()
            log.info("尚未開盤，%.0f 分鐘後開始", wait / 60)
            if args.demo:
                src.tick()
                continue
            time.sleep(min(wait, 600))
            continue
        final = t >= END_T
        try:
            quotes = src.quotes(universe)
            idx = src.indices()
            basis = src.futures_basis()
            full = src.full_market()
        except Exception as e:  # noqa: BLE001
            log.warning("抓取失敗：%s（%d 秒後重試）", e, interval)
            time.sleep(interval)
            continue

        if not args.demo and src.data_date and src.data_date != date.today().strftime("%Y%m%d"):
            if args.once:  # 非交易日用 --once：顯示最近一個交易日的收盤結果
                log.warning("今天不是交易日，以下為 %s 的收盤資料", src.data_date)
                now = datetime.combine(now.date(), CLOSE_T)
                t, final = now.time(), True
            elif t >= dtime(9, 10):
                log.warning("資料日期是 %s，不是今天：今天可能不是交易日。程式結束。", src.data_date)
                break
            else:
                time.sleep(interval)
                continue

        for c, qq in quotes.items():
            if qq.name and qq.name != c:
                names.setdefault(c, qq.name)
        m = compute(quotes, idx, base, cfg, now, names, basis, full)
        last_m = m
        if not opened and t >= OPEN_T:
            opened = True
            if not args.once and not args.no_browser:
                write_dashboard(m, hist, events, src.name, interval, final)
                webbrowser.open(dash.as_uri())

        if t >= OPEN_T:
            hist.append({"time": m["time"], "taiex": m["tx"].price if m["tx"] else NAN,
                         "otc": m["otc"].price if m["otc"] else NAN, "pace": m["pace"], "up_ratio": m["up_ratio"],
                         "label": m["label"]})
            pd.DataFrame(hist).to_csv(hist_file, index=False, encoding="utf-8-sig")
            # 行情判斷要維持 confirm_min 分鐘以上才算數，避免在邊界來回跳動
            if m["label"] != cand:
                cand, cand_since = m["label"], now
            if cand != stable and (now - cand_since).total_seconds() >= confirm_min * 60 - 1:
                if stable and not cand.startswith("⏳") and m["time"] >= "09:15":
                    events.append(f"{m['time']} 行情判斷：{stable} → {cand}")
                    if (cfg.get("notify") or {}).get("alert_label_change", True):
                        nt.send(f"🔄 行情判斷改變（{m['time']}）：{stable} → {cand}\n{m['desc']}", key=f"lbl_{m['time']}")
                stable = cand
            m["stable"] = stable or m["label"]
            n_before = len(nt.history)
            check_events(m, cfg, nt, None, hold)
            for msg in nt.history[n_before:]:
                events.append(f"{m['time']} " + msg.replace("⚠️ ", "").replace("🔄 ", "").replace(f"（{m['time']}）", ""))
            # 程式晚啟動時好幾個摘要時間會同時到，只推最新的一則，其餘標記為已處理（避免同樣內容推好幾次）
            due = [st for st in sum_times if m["time"] >= st and f"sum_{st}" not in nt.sent and (st < "13:30" or final)]
            if due:
                for st in due[:-1]:
                    nt.sent.add(f"sum_{st}")
                st = due[-1]
                title = "收盤總結" if st >= "13:30" else "盤中摘要"
                nt.send(summary_text(m, title), key=f"sum_{st}")
                nt.history.pop()
                events.append(f"{m['time']} 已推播{title}")

        write_dashboard(m, hist, events, src.name, interval, final)
        if not args.demo and time.time() - last_mk >= 600:  # 每 10 分鐘更新一次全球市場
            last_mk = time.time()
            try:
                import global_markets
                global_markets.build()
            except Exception as e:  # noqa: BLE001
                log.info("全球市場更新略過：%s", e)
        log.info("%s %s｜加權 %s｜量能 %s｜上漲 %s", m["time"], m["label"], fmt_pct(m["tx_chg"], 2),
                 "—" if m["pace"] != m["pace"] else f"{m['pace']:.2f}x",
                 "—" if m["up_ratio"] != m["up_ratio"] else f"{m['up_ratio']:.0%}")
        if final or args.once:
            if not m["groups"].empty:
                m["groups"].to_csv(DATA / f"groups_{date.today():%Y%m%d}{'_demo' if args.demo else ''}.csv",
                                   index=False, encoding="utf-8-sig")
            break
        if args.demo:
            src.tick()
            time.sleep(0.2)
        else:
            time.sleep(interval)

    print(f"\n儀表板：{dash}")
    if args.demo:
        print(f"推播內容（示範模式不會真的送出）：{DATA / 'notify_log.txt'}")
    return last_m


def probe(args):
    """逐一檢查資料來源與推播"""
    cfg = load_cfg(Path(args.config))
    print("=== 1. 證交所即時資料 ===")
    try:
        ts = TwseSource(cfg)
        rows = ts._query(["tse_t00.tw", "otc_o00.tw", "tse_2330.tw", "otc_6274.tw"])
        for r in rows:
            print(f"  {r.get('c')} {r.get('n')}  日期={r.get('d')} 時間={r.get('t')} 成交={r.get('z')} 昨收={r.get('y')} 量={r.get('v')}")
        print("  ✓ 正常" if rows else "  ⚠ 沒有回傳資料")
        if rows:
            print("  原始欄位（第一筆）：", json.dumps(rows[0], ensure_ascii=False)[:400])
    except Exception as e:  # noqa: BLE001
        print("  ⚠ 失敗：", e)
    print("\n=== 2. FinMind 基準量 ===")
    try:
        b = load_baseline(["2330"], cfg.get("finmind_token", ""))
        print("  ✓ 台積電 20 日均成交值 %.0f 億" % (b["2330"]["amt20"] / 1e8) if "2330" in b else "  ⚠ 沒有資料")
    except Exception as e:  # noqa: BLE001
        print("  ⚠ 失敗：", e)
    if cfg.get("source") == "shioaji":
        print("\n=== 3. 永豐 Shioaji ===")
        try:
            sj = ShioajiSource(cfg)
            print("  ✓ 登入成功，台積電", sj.quotes(["2330"]).get("2330"))
        except Exception as e:  # noqa: BLE001
            print("  ⚠ 失敗：", e)
    print("\n=== 4. 推播 ===")
    n = cfg.get("notify") or {}
    if n.get("telegram_bot_token") and not n.get("telegram_chat_id"):
        try:
            r = requests.get(f"https://api.telegram.org/bot{n['telegram_bot_token']}/getUpdates", timeout=15).json()
            ids = {u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u}
            print("  你的 Telegram chat_id：", ", ".join(map(str, ids)) if ids else
                  "（找不到：請先在 Telegram 對你的機器人傳一句話，再執行一次 --probe）")
        except Exception as e:  # noqa: BLE001
            print("  ⚠ 查詢 chat_id 失敗：", e)
    nt = Notifier(cfg)
    if nt.enabled:
        ok = nt.send(f"✅ 盤中脈動推播測試 {datetime.now():%H:%M}\n收到這則訊息代表設定成功。")
        print(f"  {'✓' if ok else '⚠'} 已送出測試訊息到：{', '.join(nt.enabled)}")
    else:
        print("  尚未設定推播（pulse_config.yaml 的 notify 區塊）")


def main():
    ap = argparse.ArgumentParser(description="台股盤中行情儀表板")
    ap.add_argument("--config", default=str(BASE / "pulse_config.yaml"))
    ap.add_argument("--demo", action="store_true", help="模擬一整個交易日")
    ap.add_argument("--once", action="store_true", help="只抓一次")
    ap.add_argument("--probe", action="store_true", help="檢查資料來源與推播設定")
    ap.add_argument("--auto", action="store_true", help="排程執行用")
    ap.add_argument("--no-browser", action="store_true", help="不要自動開啟瀏覽器")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    handlers = None
    if sys.stdout is None or args.auto:  # 排程（背景）執行時，紀錄寫到檔案
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(DATA / "logs" / f"pulse_{date.today():%Y%m%d}.log", encoding="utf-8")]
        if sys.stdout is not None:
            handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S", handlers=handlers)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    if args.probe:
        probe(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
