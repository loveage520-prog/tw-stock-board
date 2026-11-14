#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
開盤前晨報 morning_brief.py（每個交易日 07:30）
================================================
整理前一天到今天早上的重要資訊，推播到 Telegram，並放進網頁「晨報」分頁：
・美股收盤重點（us_live.py 收盤時存的）、台積電 ADR 溢價
・台指期夜盤
・昨天台股：加權指數、漲跌家數、法人與資金流向、主動式 ETF 共同加碼
・重要新聞（鉅亨網台股／國際／科技產業／總經匯市，近 24 小時，依重要性挑選）
・今天要注意：美股財報

用法：python morning_brief.py [--no-push]
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
DATA = BASE / "pulse_data"
OUT = DATA / "news"
TW = ZoneInfo("Asia/Taipei")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
log = logging.getLogger("brief")
WEEK = "一二三四五六日"

CATS = [("headline", "頭條"), ("tw_stock", "台股"), ("wd_stock", "國際股市"), ("tech", "科技產業"), ("tw_macro", "台灣經濟"), ("forex", "匯市")]
SECTIONS = [("台股", ["tw_stock"], 8), ("國際・美股", ["wd_stock"], 6), ("科技・產業", ["tech"], 6), ("總經・匯市", ["tw_macro", "forex"], 4)]
HOT = ["台積電", "輝達", "NVIDIA", "Fed", "聯準會", "升息", "降息", "CPI", "非農", "關稅", "AI", "法說", "營收", "財報", "外資", "出口",
       "半導體", "川普", "央行", "GDP", "PMI", "記憶體", "HBM", "伺服器", "蘋果", "降評", "升評", "創高", "崩", "暴", "漲停", "跌停"]


def r(v, n=2):
    return None if v is None or v != v or math.isinf(v) else round(float(v), n)


def load(p: str) -> dict:
    f = DATA / p
    try:
        return json.loads(f.read_text("utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 新聞
def cnyes(cat: str, limit=40) -> list[dict]:
    js = requests.get(f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}?limit={limit}", headers=UA, timeout=20).json()
    out = []
    for x in js["items"]["data"]:
        out.append({"id": x["newsId"], "t": html.unescape(x["title"]).strip(), "ts": x.get("publishAt") or 0,
                    "sum": re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", x.get("summary") or ""))).strip()[:120],
                    "u": f"https://news.cnyes.com/news/id/{x['newsId']}", "src": "鉅亨網"})
    return out


def yahoo_rss() -> list[dict]:
    t = requests.get("https://tw.stock.yahoo.com/rss?category=tw-market", headers=UA, timeout=20).text
    out = []
    for it in re.findall(r"<item>(.*?)</item>", t, re.S):
        g = lambda k: (re.search(rf"<{k}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{k}>", it, re.S) or [None, ""])[1].strip()  # noqa: E731
        try:
            ts = datetime.strptime(g("pubDate")[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=ZoneInfo("UTC")).timestamp()
        except ValueError:
            ts = 0
        out.append({"id": g("link"), "t": html.unescape(g("title")), "ts": ts, "sum": "", "u": g("link"), "src": "Yahoo股市"})
    return out


def news(since: float) -> dict:
    pool, seen_in = {}, {}
    for cat, _ in CATS:
        try:
            items = cnyes(cat)
        except Exception as e:  # noqa: BLE001
            log.info("鉅亨網 %s 抓取失敗：%s", cat, e)
            continue
        for x in items:
            if x["ts"] < since:
                continue
            pool.setdefault(x["id"], x)
            seen_in.setdefault(x["id"], set()).add(cat)
    if not pool:  # 鉅亨網都失敗時改用 Yahoo
        try:
            for x in yahoo_rss():
                if x["ts"] >= since:
                    pool[x["id"]] = x
                    seen_in[x["id"]] = {"tw_stock"}
        except Exception as e:  # noqa: BLE001
            log.info("Yahoo RSS 失敗：%s", e)

    def score(x):
        cs = seen_in[x["id"]]
        s = 3 * ("headline" in cs) + (len(cs) - 1)
        s += min(2, sum(k.lower() in x["t"].lower() for k in HOT))
        if x["t"].startswith(("〈熱門股〉", "〈焦點股〉")):
            s -= 1
        return s

    used, out = set(), {}
    for name, cats, n in SECTIONS:
        cand = [x for x in pool.values() if seen_in[x["id"]] & set(cats) and x["id"] not in used]
        cand.sort(key=lambda x: (-score(x), -x["ts"]))
        pick = []
        for x in cand:
            if any(similar(x["t"], y["t"]) for y in pick):
                continue
            pick.append(x)
            if len(pick) >= n:
                break
        for x in pick:
            used.add(x["id"])
            x["tm"] = datetime.fromtimestamp(x["ts"], TW).strftime("%m/%d %H:%M") if x["ts"] else ""
            x["hot"] = score(x) >= 3
        out[name] = pick
    return {"sections": out, "n": len(pool)}


def similar(a: str, b: str) -> bool:
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, min(len(sa), len(sb))) > 0.7


# ---------------------------------------------------------------- 市場數據
def night_futures() -> dict | None:
    """台指期夜盤（期交所 MIS，近月）"""
    js = requests.post("https://mis.taifex.com.tw/futures/api/getQuoteList",
                       json={"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
                             "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}, headers=UA, timeout=20).json()
    for q in (js.get("RtData") or {}).get("QuoteList") or []:
        if q.get("SymbolID", "").endswith("-P") or not q.get("CLastPrice"):
            continue
        last, ref = float(q["CLastPrice"]), float(q.get("CRefPrice") or 0)
        return {"n": q.get("DispCName", "台指期"), "p": last, "ref": ref, "ch": last - ref if ref else None,
                "pct": (last / ref - 1) if ref else None, "t": q.get("CTime", "")}
    return None


def us_close() -> dict:
    files = sorted((DATA / "us").glob("close_*.json"))
    if files:
        c = json.loads(files[-1].read_text("utf-8"))
        if datetime.now(TW).date() - datetime.strptime(c["date"], "%Y-%m-%d").date() <= timedelta(days=4):
            return c
    try:  # 沒有收盤檔（例如美股那段沒執行）就現抓一次
        import us_live
        res = us_live.build("收盤")
        res["summary"] = us_live.summarize(res)
        res["earn"] = us_live.earnings_today()
        return res
    except Exception as e:  # noqa: BLE001
        log.info("美股收盤資料抓取失敗：%s", e)
        return {}


def tw_yesterday() -> dict:
    out = {}
    try:
        import yfinance as yf
        df = yf.download(["^TWII", "^TWOII"], period="7d", interval="1d", progress=False, group_by="ticker")
        for s, n in (("^TWII", "加權指數"), ("^TWOII", "櫃買指數")):
            c = df[s]["Close"].dropna()
            if len(c) >= 2:
                out.setdefault("idx", []).append({"n": n, "p": r(c.iloc[-1], 2), "ch": r(c.iloc[-1] - c.iloc[-2], 2),
                                                  "pct": r(c.iloc[-1] / c.iloc[-2] - 1, 4), "d": str(c.index[-1].date())})
    except Exception as e:  # noqa: BLE001
        log.info("台股指數抓取失敗：%s", e)
    bd = load("board/latest.json")
    if bd.get("count"):
        a = bd["count"]["all"]
        out["breadth"] = {"d": bd.get("date"), "U": a.get("U"), "D": a.get("D"), "up": a.get("up"), "dn": a.get("dn")}
        out["top"] = [{"n": x.get("n"), "c": x.get("c"), "ch": x.get("ch")} for x in (bd.get("top") or [])[:5]]
    fl = load("flow/latest_sectors.json")
    secs = sorted((fl.get("sectors") or {}).items(), key=lambda kv: -kv[1]["x"])
    if secs:
        out["flow_in"] = [f"{k}({v['x']:+.0f}億)" for k, v in secs[:3]]
        out["flow_out"] = [f"{k}({v['x']:+.0f}億)" for k, v in secs[-3:][::-1]]
    pb = load("post/latest.json")
    for who in ("外資", "投信"):
        b = ((((pb.get("tw") or {}).get("data") or {}).get(who) or {}).get("buy") or [])[:3]
        if b:
            out.setdefault("inst", {})[who] = [f"{x['n']}({x['sum']:+.0f}億)" for x in b]
    et = load("etf/latest.json")
    if et.get("con1"):
        out["etf_buy"] = [f"{x['n']}({x['nb']}檔)" for x in et["con1"]["buy"][:4]]
        out["etf_sell"] = [f"{x['n']}({x['ns']}檔)" for x in et["con1"]["sell"][:3]]
        out["etf_date"] = (et.get("etfs") or [{}])[0].get("date")
    ex = load("extra/latest.json")
    fut = ((ex.get("fut") or {}).get("day") or {}).get("臺股期貨", {})
    if fut:
        out["fi_oi"] = fut.get("外資及陸資", {}).get("oi")
    return out


def earnings(today) -> list[dict]:
    rows = (load("extra/latest.json").get("earn") or {}).get("rows") or []
    keep = {str(today), str(today + timedelta(days=1))}
    return [x for x in rows if x.get("d") in keep][:10]


# ---------------------------------------------------------------- 組合
def build() -> dict:
    now = datetime.now(TW)
    since = (now - timedelta(hours=26)).timestamp()
    res = {"time": f"{now:%Y-%m-%d %H:%M}", "title": f"晨報 {now:%m/%d}（{WEEK[now.weekday()]}）"}
    try:
        res["news"] = news(since)
    except Exception as e:  # noqa: BLE001
        log.info("新聞失敗：%s", e)
        res["news"] = {"sections": {}, "n": 0}
    us = us_close()
    res["us"] = {k: us.get(k) for k in ("date", "summary", "idx", "chain", "mac", "sectors")} if us else {}
    try:
        res["night"] = night_futures()
    except Exception as e:  # noqa: BLE001
        log.info("夜盤失敗：%s", e)
        res["night"] = None
    res["tw"] = tw_yesterday()
    res["earn"] = earnings(now.date())
    return res


def telegram_text(b: dict) -> str:
    e = lambda s: html.escape(str(s), quote=False)  # noqa: E731
    pct = lambda v: "—" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    L = [f"☀️ <b>{e(b['title'])}</b>"]
    us = b.get("us") or {}
    if us.get("summary"):
        L.append(f"\n🇺🇸 <b>美股收盤（{e(us['date'][5:].replace('-', '/'))}）</b>")
        L += [f"• {e(s)}" for s in us["summary"][:6]]
    n = b.get("night")
    if n:
        L.append(f"\n🌙 <b>台指期夜盤</b> {n['p']:,.0f}（{n['ch']:+,.0f}，{pct(n['pct'])}）" if n.get("ch") is not None else f"\n🌙 台指期夜盤 {n['p']:,.0f}")
    tw = b.get("tw") or {}
    seg = []
    for x in tw.get("idx", []):
        seg.append(f"{x['n']} {x['p']:,.2f}（{x['ch']:+,.2f}，{pct(x['pct'])}）")
    if tw.get("breadth"):
        a = tw["breadth"]
        seg.append(f"漲停 {a['U']}／跌停 {a['D']}，上漲 {a['up']}／下跌 {a['dn']}")
    if tw.get("flow_in"):
        seg.append("資金流入：" + "、".join(tw["flow_in"]))
    for who, v in (tw.get("inst") or {}).items():
        seg.append(f"{who} 15 日買超：" + "、".join(v))
    if tw.get("etf_buy"):
        seg.append("主動 ETF 共同加碼：" + "、".join(tw["etf_buy"]))
    if seg:
        L.append("\n🇹🇼 <b>昨日台股</b>")
        L += [f"• {e(s)}" for s in seg]
    secs = (b.get("news") or {}).get("sections") or {}
    for name, items in secs.items():
        if not items:
            continue
        L.append(f"\n📰 <b>{e(name)}</b>")
        for x in items[:5]:
            L.append(f"• {'🔥' if x.get('hot') else ''}<a href=\"{e(x['u'])}\">{e(x['t'])}</a>")
    if b.get("earn"):
        L.append("\n📅 <b>美股財報（今晚／明晚）</b>")
        L.append("、".join(f"{x['s']}{'⭐' if x.get('tw') else ''}" for x in b["earn"]))
    L.append("\n完整內容：https://loveage520-prog.github.io/tw-stock-board/#morning")
    text = "\n".join(L)
    return text[:4000]


def send(text: str) -> None:
    import yaml
    cfg = yaml.safe_load((BASE / "pulse_config.yaml").read_text("utf-8")) or {}
    n = cfg.get("notify") or {}
    if not (n.get("telegram_bot_token") and n.get("telegram_chat_id")):
        log.info("沒有 Telegram 設定，略過推播")
        return
    requests.post(f"https://api.telegram.org/bot{n['telegram_bot_token']}/sendMessage",
                  data={"chat_id": n["telegram_chat_id"], "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
                  timeout=20).raise_for_status()
    log.info("晨報已推播")


def save(b: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"brief_{b['time'][:10].replace('-', '')}.json").write_text(json.dumps(b, ensure_ascii=False), "utf-8")
    (OUT / "latest.json").write_text(json.dumps(b, ensure_ascii=False), "utf-8")
    tpl = (BASE / "morning_template.html").read_text("utf-8")
    frag = tpl.replace("/*__DATA__*/null", json.dumps(b, ensure_ascii=False))
    (BASE / "morning_fragment.html").write_text(frag, "utf-8")
    (BASE / "morning.html").write_text(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>晨報</title>\n'
        '<style>:root{--bg:#f4f5f7}@media (prefers-color-scheme:dark){:root{--bg:#0f1318}}'
        'body{margin:0;background:var(--bg)}.wrap{max-width:900px;margin:0 auto;padding:14px 16px 28px}</style>\n'
        f'</head><body><div class="wrap">\n{frag}\n</div></body></html>\n', "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    b = build()
    save(b)
    text = telegram_text(b)
    print(re.sub("<[^>]+>", "", text))
    if not a.no_push:
        try:
            send(text)
        except Exception as e:  # noqa: BLE001
            log.warning("推播失敗：%s", e)


if __name__ == "__main__":
    main()
