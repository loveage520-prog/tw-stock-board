# 台股盤中・盤後資料（雲端版）

GitHub Actions 依排程自動抓資料，網頁放在 GitHub Pages，手機打開網址就能看，電腦不用開機。

| 網頁 | 內容 | 更新時間 |
|---|---|---|
| `index.html` 板塊資金流向 | 法人象限圖、板塊排行、全球市場、籌碼・營收・期權、法人 15 日・美股、成交排行・漲跌停 | 交易日 18:00 後 |
| `pulse.html` 台股盤中脈動 | 行情判斷、族群資金、全市場成交前 100、漲跌停 | 交易日 9:00～13:35，每 5 分鐘 |

## 排程（`.github/workflows/`）
- **intraday.yml**：台灣時間 08:40 啟動，盤中持續執行，13:36 結束
- **daily.yml**：台灣時間 18:00，跑盤後資料並推播 Telegram 摘要
- 兩個都可以在 GitHub 的 **Actions** 頁面按 **Run workflow** 手動執行

## Secrets（Settings → Secrets and variables → Actions）
| 名稱 | 用途 | 必填 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram 機器人 token | 要推播才需要 |
| `TELEGRAM_CHAT_ID` | Telegram chat_id | 要推播才需要 |
| `FINMIND_TOKEN` | FinMind token（額度較高） | 建議 |
| `CONFIG_YAML` | 整份 config.yaml（含持股），用來推播持股異常 | 選填 |

Secrets 只在執行時寫入設定檔，不會出現在程式碼或網頁上。網頁會自動拿掉持股相關標示。

## 分支
- `main`：程式
- `data`：快取資料（每次覆蓋，不保留歷史）
- `gh-pages`：網頁

資料來源：臺灣證券交易所、證券櫃檯買賣中心、公開資訊觀測站、臺灣集中保管結算所、臺灣期貨交易所、Yahoo Finance、FinMind。本專案只整理資料，不構成投資建議。
