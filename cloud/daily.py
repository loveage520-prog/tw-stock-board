#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雲端盤後工作（GitHub Actions 每個交易日 18:00 執行）
依序：資金流向＋全球市場 → 成交排行與漲跌停（收盤）→ 外資投信 15 日＋美股 → 籌碼營收期權
完成後用 Telegram 推播摘要。
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

STEPS = [
    ("資金流向＋全球市場", ["sector_flow.py", "--auto", "--no-browser"]),
    ("成交排行與漲跌停", ["market_board.py", "--close"]),
    ("外資投信 15 日＋美股", ["post_board.py"]),
    ("融資借券營收期權", ["extra_board.py"]),
]


def load(p: str):
    f = BASE / p
    return json.loads(f.read_text("utf-8")) if f.exists() else {}


def summary(results) -> str:
    ok = sum(1 for r in results if r[1])
    lines = [f"{'✅' if ok == len(results) else '⚠️'} 盤後資料 {datetime.now():%m/%d %H:%M}（{ok}/{len(results)} 成功）"]
    lines += [f"✗ {n}" for n, good, _ in results if not good]
    fl = load("pulse_data/flow/latest_sectors.json")
    secs = sorted((fl.get("sectors") or {}).items(), key=lambda kv: -kv[1]["x"])
    if secs:
        lines.append("法人 5 日買最多：" + "、".join(f"{k}({v['x']:+.0f}億)" for k, v in secs[:3]))
        lines.append("法人 5 日賣最多：" + "、".join(f"{k}({v['x']:+.0f}億)" for k, v in secs[-3:][::-1]))
    bd = load("pulse_data/board/latest.json")
    if bd:
        a = bd["count"]["all"]
        lines.append(f"漲停 {a['U']}、跌停 {a['D']}｜上漲 {a['up']}／下跌 {a['dn']}")
    pb = load("pulse_data/post/latest.json")
    for who in ("外資", "投信"):
        b = (((pb.get("tw") or {}).get("data") or {}).get(who) or {}).get("buy") or []
        if b:
            lines.append(f"{who} 15 日買超：" + "、".join(f"{x['n']}({x['sum']:+.0f}億)" for x in b[:3]))
    us = (pb.get("us") or {}).get("top") or []
    if us:
        lines.append("美股成交前 5：" + "、".join(f"{x['s']}({x['ch'] * 100:+.1f}%)" for x in us[:5]))
    ex = load("pulse_data/extra/latest.json")
    if ex.get("margin"):
        lines.append("融資增加：" + "、".join(x["n"] for x in ex["margin"]["inc"][:3]))
    if ex.get("sbl"):
        lines.append("借券賣出增加：" + "、".join(x["n"] for x in ex["sbl"]["inc"][:3]))
    if ex.get("warn"):
        lines.append(f"處置中 {len(ex['warn']['punish'])} 檔、今日注意 {len(ex['warn']['notice'])} 檔")
    fut = ((ex.get("fut") or {}).get("day") or {}).get("臺股期貨", {})
    pc = (ex.get("fut") or {}).get("pc") or []
    if fut:
        lines.append(f"外資台指期淨未平倉 {fut.get('外資及陸資', {}).get('oi', 0):+,} 口" + (f"｜P/C {pc[-1]['oi']:.0f}%" if pc else ""))
    earn = [x for x in (ex.get("earn") or {}).get("rows", []) if x.get("tw")][:4]
    if earn:
        lines.append("台灣供應鏈美股財報：" + "、".join(f"{x['s']} {x['d'][5:]}" for x in earn))
    return "\n".join(lines)


def main():
    results = []
    for name, cmd in STEPS:
        t0 = time.time()
        p = subprocess.run([sys.executable, *cmd], cwd=BASE)
        results.append((name, p.returncode == 0, time.time() - t0))
        print(f"{'✓' if p.returncode == 0 else '✗'} {name}（{time.time() - t0:.0f} 秒）", flush=True)
    msg = summary(results) + "\n手機版網頁已更新"
    print(msg)
    h = datetime.now().hour
    if h >= 21 or h < 7:  # 晚上補抓那一次、或排程延遲到半夜才執行時，不推播
        return
    try:
        from market_pulse import Notifier, load_cfg
        Notifier(load_cfg(BASE / "pulse_config.yaml")).send(msg)
    except Exception as e:  # noqa: BLE001
        print("推播略過：", e)


if __name__ == "__main__":
    main()
