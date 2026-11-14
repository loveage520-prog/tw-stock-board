#!/usr/bin/env python3
"""暫時用：測試新資料來源在 GitHub 雲端是否連得到"""
import html
import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def t(name, fn):
    try:
        print("OK ", name, fn())
    except Exception as e:  # noqa
        print("ERR", name, repr(e)[:200])


def nomura():
    r = requests.post("https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets",
                      json={"FundID": "00980A", "SearchDate": "2026-11-12"}, headers=UA, timeout=20)
    d = r.json()["Entries"]["Data"]
    return r.status_code, d["FundAsset"], d["Table"][0]["Rows"][:2]


def ez():
    r = requests.get("https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW", headers=UA, timeout=20)
    m = re.search(r'id="DataAsset"[^>]*data-content="([^"]*)"', r.text) or re.search(r'data-content="([^"]*)"[^>]*id="DataAsset"', r.text)
    a = json.loads(html.unescape(m.group(1)))
    st = next(x for x in a if x["AssetCode"] == "ST")
    return r.status_code, len(st["Details"]), st["Details"][0]["TranDate"], st["Details"][0]["DetailName"]


def cnyes(cat):
    def f():
        r = requests.get(f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}?limit=5", headers=UA, timeout=20)
        items = r.json()["items"]["data"]
        return r.status_code, [(x["newsId"], x["title"][:30], x.get("publishAt")) for x in items[:3]]
    return f


def rss(url):
    def f():
        r = requests.get(url, headers=UA, timeout=20)
        return r.status_code, re.findall(r"<title>(.*?)</title>", r.text)[1:4]
    return f


def yf_live():
    import yfinance as yf
    df = yf.download(["NVDA", "TSM", "^SOX"], period="2d", interval="5m", progress=False, group_by="ticker")
    return df.shape, str(df.index[-1])


def taifex_mis():
    r = requests.post("https://mis.taifex.com.tw/futures/api/getQuoteList",
                      json={"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"},
                      headers=UA, timeout=20)
    return r.status_code, r.text[:300]


t("nomura", nomura)
t("ezmoney", ez)
for c in ["headline", "tw_stock", "tw_stock_news", "us_stock", "wd_stock", "tech", "industry", "tw_macro", "forex"]:
    t("cnyes " + c, cnyes(c))
t("gnews", rss("https://news.google.com/rss/search?q=%E5%8F%B0%E8%82%A1+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"))
t("yahoo", rss("https://tw.stock.yahoo.com/rss?category=tw-market"))
t("yf", yf_live)
t("taifex_mis", taifex_mis)
