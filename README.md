# 台南不動產資訊查詢平台

以地圖為核心入口，整合 **使用執照、實價登錄、生活機能、學校、預售屋與預售屋價格摘要**，讓使用者可以從一個地址或一個地圖中心點，快速掌握周邊房地產資訊。

![專案首頁視覺](apps/web/image/pic2.jpg)

## 線上展示

- 專案入口（Projects Hub）：<https://cindyho.work/>
- 專案首頁（tnhouse）：<https://cindyho.work/tnhouse.html>
- 地圖查詢：<https://cindyho.work/map.html>

## 專案亮點

- **地址即入口**：輸入地址後，可同步查看周邊成交、生活機能、預售屋與使用執照資訊。
- **地圖式探索**：透過 Leaflet 地圖、藍色拖曳中心點與半徑查詢，快速切換不同區位。
- **使用執照查詢整合**：支援執照號碼查詢、地址查詢、候選地址摘要與地圖定位。
- **預售屋資訊更完整**：除了建案基本資料，也整合成交筆數、最近成交日期、最近單價與平均單價。
- **生活機能分類清楚**：便利商店、加油站、公園、醫院、學校等資訊可用同一張地圖瀏覽。
- **適合部署與延伸**：前端靜態頁面 + FastAPI + PostgreSQL/PostGIS，結構清楚，方便部署到 EC2。

## 功能展示

### 1. Projects Hub 與專案首頁

- `index.html` 為 Projects Hub，統一整理對外展示作品。
- `tnhouse.html` 為本專案首頁，提供專案入口與導向地圖查詢。

![首頁畫面](apps/web/image/pic2.jpg)

### 2. 地圖整合查詢

- 地址查詢後，會同步更新：
  - 成交摘要
  - 周邊生活機能
  - 周邊預售屋
  - 使用執照查詢結果
- 可切換查詢半徑，例如 `500m / 1000m / 1500m`
- 可依生活機能類別篩選結果

![地圖查詢視覺](apps/web/image/pic1.jpg)

### 3. 使用執照查詢

- 支援：
  - 執照號碼查詢
  - 地址查詢
- 查詢後可顯示：
  - 使用執照基本資料
  - 候選地址摘要
  - 可定位時同步移動地圖中心

### 4. 預售屋查詢

- 支援：
  - 區域下拉選單
  - 建案名稱下拉選單
- 查到建案後可顯示：
  - 建案名稱、建商、建照、戶數
  - 成交筆數
  - 最近成交日期
  - 最近單價
  - 平均單價

### 5. 周邊生活機能

- 目前已整合：
  - 便利商店
  - 加油站
  - 公園
  - 醫院
  - 學校
- 每筆資料可顯示名稱、距離與地址，適合地圖式探索。

## 技術棧

### Backend Stack

- FastAPI
- Python 3.11
- Uvicorn
- Psycopg (psycopg[binary])
- python-dotenv
- PostgreSQL + PostGIS
- Docker + Docker Compose

### Frontend Stack

- HTML5
- CSS3
- Vanilla JavaScript
- Leaflet

## 專案結構

```text
tn-house-mvp/
├─ apps/
│  ├─ api/                 # FastAPI 後端與資料匯入腳本
│  └─ web/                 # 前端靜態頁面
├─ etl/                    # ETL / 匯入工具
├─ infra/initdb/           # DB 初始化 SQL
├─ data/                   # 原始資料（通常不進公開 repo）
├─ scripts/                # 輔助腳本
├─ docker-compose.yml
└─ README.md
```

## 主要頁面

### `apps/web/index.html`

- Projects Hub 首頁
- 統一整理所有專案入口
- 連到 `tnhouse.html` 與 `map.html`

### `apps/web/tnhouse.html`

- 本專案首頁
- 提供專案導覽與入口

### `apps/web/map.html`

- 核心查詢頁
- 整合：
  - 地址查詢
  - 使用執照查詢
  - 周邊成交
  - 生活機能
  - 預售屋

### `apps/web/permits.html`

- 使用執照專用查詢頁
- 適合做建照查詢的單一入口

## 主要 API

### `/nearby_txn_by_address`

- 用地址查詢附近實價登錄
- 回傳：
  - 定位中心點
  - 成交清單
  - 成交摘要

### `/nearby_by_address`

- 用地址查詢附近使用執照
- 回傳：
  - 定位中心點
  - 使用執照結果列表

### `/nearby_poi`

- 查詢周邊生活機能
- 支援類別：
  - convenience
  - gas_station
  - park
  - hospital

### `/nearby_schools`

- 查詢周邊學校

### `/nearby_presale`

- 查詢周邊預售屋
- 整合建案資料與價格摘要

### `/presale_search`

- 用區域與建案名稱直接查預售屋

### `/permit_address_summary`

- 查詢使用執照候選地址摘要
- 適合處理只有地號、沒有完整門牌的建照資料

## 核心資料表

目前前端與 API 正常運作時，最核心的資料表包含：

- `use_permits`
- `real_price_txn`
- `address_points_base`
- `poi`
- `schools`
- `presale_projects`
- `presale_price_summary`
- `permit_address_summary_top3`

## 本機啟動方式

### 1. 建立環境變數

請在專案根目錄建立 `.env`。

常見欄位例如：

```env
POSTGRES_DB=tn_house
POSTGRES_USER=tn
POSTGRES_PASSWORD=tn
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DSN=postgresql://tn:tn@db:5432/tn_house
```

### 2. 啟動 DB 與 API

```bash
docker compose up -d db api
```

### 3. 檢查 API 是否正常

```bash
curl http://127.0.0.1:8000/health
```

預期回傳：

```json
{"ok": true}
```

### 4. 開啟前端頁面

可用任一靜態伺服器開啟 `apps/web/`，例如：

```bash
cd apps/web
python -m http.server 5173
```

接著打開：

- `http://127.0.0.1:5173/index.html`
- `http://127.0.0.1:5173/map.html`

## 部署建議

### 建議模式

- **模式 A：直接帶既有資料庫上 EC2**

這是目前最穩、最省時間的部署方式：

- 上傳必要程式檔
- 還原既有 PostgreSQL / PostGIS 資料庫
- 啟動 API 與靜態前端

### EC2 必要檔案

- `docker-compose.yml`
- `infra/initdb/001_init.sql`
- `apps/api/**`
- `apps/web/**`

### 不建議公開的內容

- `.env`
- `data/**`
- `exports/**`
- `apps/api/*.csv`
- SQL dump
- 手動補點座標檔

## README 截圖建議

如果你想讓 GitHub Repository 更完整，建議另外建立 `docs/screenshots/`，加入這些實際畫面截圖：

- 首頁
- `map.html` 主查詢頁
- 使用執照查詢結果
- 預售屋查詢結果
- 生活機能與成交卡片

之後只要把本 README 內的圖片路徑換掉，就能變成更完整的圖文版介紹。

## 適用情境

- 房地產資訊整合展示
- 地圖式資料探索
- 使用執照與實價登錄結合查詢
- 預售屋建案與價格摘要展示
- 台南地區不動產資料分析與內部工具

## 備註

- 使用執照查詢結果中，部分案件可能只有地號、沒有可直接定位的門牌點位。
- 預售屋價格摘要採建案名稱與區域對照，因此部分建案可能沒有價格摘要。
- 生活機能與地址定位高度依賴 `address_points_base` 與既有資料品質。









