# TSMC Public Monitor

公開網站：

https://triathlan226.github.io/tsmc-public-monitor/

## 架構

```text
Google Sheet          = 人工審核後台
Yahoo Finance         = 股價來源
GitHub Actions        = 每天自動更新機器
data/data.json        = 正式乾淨資料檔
data/data.js          = 網站實際讀取資料檔
GitHub Pages          = 公開 dashboard 網站
```

## Google Sheet 後台

模板檔：

```text
google_sheet_template/tsmc_monitor_google_sheet_template.xlsx
```

主要會讀取兩個分頁：

```text
manual_events
watchlist
```

其他分頁是後續擴充與人工管理用：

```text
sources
rules
companies
README
```

## 接上 Google Sheet

1. 將 `google_sheet_template/tsmc_monitor_google_sheet_template.xlsx` 匯入 Google Sheet。
2. 在 Google Sheet 選擇「檔案」→「共用」→「發布到網路」。
3. 分別將 `manual_events` 與 `watchlist` 發布為 CSV。
4. 到 GitHub repo 的 Settings → Secrets and variables → Actions 新增：

```text
GOOGLE_SHEET_EVENTS_CSV_URL
GOOGLE_SHEET_WATCHLIST_CSV_URL
```

5. 手動執行 GitHub Actions 的 `Update dashboard data`，確認成功。

## 本機更新資料

```bash
python scripts/update_data.py
```

如果沒有設定 Google Sheet CSV URL，腳本仍會更新 Yahoo Finance 股價，並保留既有人工資料。

## 重新產生 Google Sheet 模板

```bash
node scripts/build_google_sheet_template.mjs
```
