#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用已存的資料、套最新的網頁樣板重新產生各區塊（不重新抓資料）
改了樣板想馬上看到效果時使用：GitHub Actions「重新產生網頁」
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
D = BASE / "pulse_data"


def load(p):
    f = D / p
    return json.loads(f.read_text("utf-8")) if f.exists() else None


def run(name, fn):
    try:
        fn()
        print("✓", name)
    except Exception as e:  # noqa: BLE001
        print("✗", name, e)


def board():
    import market_board
    lt = load("board/latest.json")
    full = load(f"board/board_{lt['date'].replace('-', '')}.json") if lt else None
    if full:
        market_board.save(full)


def simple(mod, path):
    def f():
        import importlib
        res = load(path)
        if res:
            importlib.import_module(mod).save(res)
    return f


run("成交排行", board)
run("法人15日・美股", simple("post_board", "post/latest.json"))
run("籌碼營收期權", simple("extra_board", "extra/latest.json"))
run("主動式ETF", simple("etf_board", "etf/latest.json"))
run("美股即時", simple("us_live", "us/latest.json"))
run("美股板塊", simple("us_flow", "us/flow.json"))
run("晨報", simple("morning_brief", "news/latest.json"))
