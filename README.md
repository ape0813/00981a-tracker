# 00981A ETF Holdings Tracker

每日自動抓取 **中信關鍵半導體ETF正2（00981A）** 持股明細，以 Cyberpunk 霓虹風格網頁呈現。

![screenshot](https://placehold.co/1200x600/04040f/00fff9?text=00981A+ETF+TRACKER)

## 功能

- **自動抓取**：GitHub Actions 在每個交易日 15:40（台灣時間）執行，取得當日持股資料
- **Cyberpunk UI**：掃描線動畫背景、霓虹發光效果、即時動畫長條圖
- **指標卡片**：規模、淨值、年初至今報酬、持股數
- **前 20 大持股**：帶發光效果的水平長條圖 + 持股比例變化
- **異動日誌**：新增建倉 / 完全清倉 / 加碼 / 減碼 四類標記
- **產業分布**：自動分類（半導體、電子、金融…）並視覺化
- **安全回退**：抓取失敗時保留前一天的資料，絕不寫入空資料

---

## 快速部署到 GitHub Pages

### 1. 建立 Repository

```bash
git init
git remote add origin https://github.com/<YOUR_USER>/00981a-tracker.git
git add .
git commit -m "init: 00981A ETF tracker"
git push -u origin main
```

### 2. 啟用 GitHub Pages

1. 前往 **Settings → Pages**
2. Source 選 **Deploy from a branch**
3. Branch 選 `main`，目錄選 `/ (root)`
4. 儲存後等約 1 分鐘，網址為 `https://<YOUR_USER>.github.io/00981a-tracker/`

### 3. 賦予 Actions 寫入權限

1. **Settings → Actions → General**
2. Workflow permissions 選 **Read and write permissions**
3. 儲存

### 4. 產生初始資料

手動觸發一次 Workflow：

**Actions → Daily ETF Holdings Update → Run workflow**

或在本機執行：

```bash
pip install -r requirements.txt
python scraper.py
git add data/holdings.json
git commit -m "data: initial holdings"
git push
```

### 5. 確認

開啟 `https://<YOUR_USER>.github.io/00981a-tracker/`，應可看到 Cyberpunk 風格頁面。

---

## 本機開發

```bash
pip install -r requirements.txt
python scraper.py           # 抓取資料 → data/holdings.json
python -m http.server 8080  # 啟動靜態伺服器
# 開啟 http://localhost:8080
```

---

## 目錄結構

```
00981a-tracker/
├── scraper.py                    # 資料抓取腳本
├── index.html                    # Cyberpunk 前端頁面
├── requirements.txt              # Python 套件
├── data/
│   └── holdings.json             # 每日自動更新（Git 追蹤）
└── .github/
    └── workflows/
        └── daily-update.yml      # GitHub Actions 排程
```

---

## 資料格式（data/holdings.json）

```jsonc
{
  "date": "2024-05-07",
  "fetched_at": "2024-05-07T15:45:12",
  "holdings": [
    {
      "code": "2330",
      "name": "台積電",
      "weight": 22.50,    // 持股比例 (%)
      "change": 0.30,     // 與前日比較 (+/-)
      "sector": "半導體"
    }
  ],
  "metrics": {
    "scale": "15.2億",
    "nav": "18.52",
    "return_ytd": "N/A",
    "return_1y": "N/A",
    "holdings_count": 20
  },
  "changes": {
    "added":     [/* 新增建倉 */],
    "removed":   [/* 完全清倉 */],
    "increased": [/* 加碼（含 change 欄位）*/],
    "decreased": [/* 減碼（含 change 欄位）*/]
  }
}
```

---

## 資料來源說明

`scraper.py` 依優先順序嘗試以下來源，任一成功即停止：

| 優先 | 來源 | 說明 |
|------|------|------|
| 1 | TWSE OpenAPI | `openapi.twse.com.tw/v1/ETFdividend/ETFcomponent` |
| 2 | TWSE HTML | `www.twse.com.tw/fund/ETF_tf.html` |
| 3 | 中信投信官網 | `ctbcasset.com.tw` ETF 持股頁 |
| 4 | 保留前一天資料 | 任何錯誤時的安全回退 |

若 TWSE 或官網改版導致抓取失敗，可在 `scraper.py` 中調整對應 `fetch_*` 函式的 URL 與解析邏輯。

---

## 更新週期

GitHub Actions 設定為 **每週一至週五 15:40 台灣時間（07:40 UTC）** 自動執行。  
若當日非交易日（颱風假、國定假日等），台股未開盤時資料可能無更新，腳本會保留前一天的資料。

---

## License

MIT
