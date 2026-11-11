#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
執行前產生設定檔（只存在 GitHub 的執行環境裡，不會被 commit）
・config.yaml：若有 Secret「CONFIG_YAML」就整份使用（可放持股，網頁仍會拿掉持股標示）；
               沒有的話只放 FinMind token、持股留空
・pulse_config.yaml：把 Telegram token／chat_id 從 Secrets 填進去
"""
import os
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent

cfg_text = os.environ.get("CONFIG_YAML", "").strip()
if cfg_text:
    cfg = yaml.safe_load(cfg_text) or {}
else:
    cfg = {"holdings": [], "watchlist": []}
if os.environ.get("FINMIND_TOKEN"):
    cfg["finmind_token"] = os.environ["FINMIND_TOKEN"]
(BASE / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), "utf-8")

pc_path = BASE / "pulse_config.yaml"
pc = yaml.safe_load(pc_path.read_text("utf-8")) or {}
n = pc.setdefault("notify", {})
n["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
n["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
if os.environ.get("FINMIND_TOKEN"):
    pc["finmind_token"] = os.environ["FINMIND_TOKEN"]
pc_path.write_text(yaml.safe_dump(pc, allow_unicode=True, sort_keys=False), "utf-8")

print("設定檔已產生：持股 %d 檔、Telegram %s、FinMind %s" % (
    len(cfg.get("holdings") or []), "有" if n["telegram_bot_token"] else "無", "有" if cfg.get("finmind_token") else "無"))
