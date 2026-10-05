# credit_scammer

NTU COOL 課程巡檢與瀏覽器工作流程。每 600 秒掃描所有已選課中的學生課程，找出未到期、尚未繳交且仍可提交的作業，使用 `gpt-6.1-sol` 產生答案。支援文字、PDF 和文字檔案；提交成功必須以平台回執確認。影片使用正常 1 倍速播放，再核對 NTU COOL 觀看紀錄。

目前支援範圍、實測證據與限制見 [實作狀態](doc/system/implementation.md)。程式作業尚無隔離 runner；缺題目、缺報告主題、無法判讀的附件與未決提交會留下 `needs_input` 與待詢問事項，暫時略過。不能保證任意題型正確或取得滿分。

## 安裝

Windows、Python 3.10 以上，另需已安裝且登入的 Codex CLI：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
codex login
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

帳密只填本機 `.env`。`COOL_COURSE_IDS=all` 代表所有學生課程，助教課程會略過；也可指定逗號分隔 ID。模型固定 `LLM_MODEL=gpt-6.1-sol`，使用 Codex CLI 自己的登入，無須把 API 金鑰交給程式。[Codex 官方文件](https://developers.openai.com/codex/noninteractive)

## 操作

```powershell
# 只巡檢並建立本機任務，不執行答案提交或影片播放
.venv\Scripts\python.exe -m credit_scammer scan

# 掃描一次並處理可執行的作業（會自動提交通過驗證的答案）
.venv\Scripts\python.exe -m credit_scammer run --once

# 每 10 分鐘巡檢，同時處理作業與影片
.venv\Scripts\python.exe -m credit_scammer run

# 查看健康檔與待補資料的任務
.venv\Scripts\python.exe -m credit_scammer status
```

額外登入驗證用 `auth` 開啟可見瀏覽器。運行中的單實例鎖會阻擋另一個 `run`、`scan` 或 `auth`。先停止常駐程序，才能執行 `retry JOB_ID` 或 `backup DEST`；未決提交的 retry 只核對回執。

本機 `.env` 可選填 `DISCORD_WEBHOOK_URL`。常駐程序每 15 秒查看結果，以 embed 傳送「課程名稱（課號）－類別／子標題／主要訊息」：成功提交會附答案與平台位置；無法完成時列原因與之後要詢問的事項，有草稿就附上並註明未提交。影片播放結束也逐支通知，區分平台已確認完成與已播完但平台進度尚未確認，附課程、標題、連結及待核對原因。相同結果確認後不重複傳送。影片同一課程的同類問題合併，至少冷卻 60 分鐘，長清單用附件。Webhook、帳密及本機路徑會遮蔽，檔案未通過完整性或隱私檢查時只保留本機。`notify` 可在停止常駐後補送，`notify --resend` 可明確重發一次。系統設計文件不送 Discord。

```powershell
# 登入後自動啟動，現在立即在背景執行
.\scripts\InstallTask.ps1 -StartNow

# 停止排程中的程序，資料與提交紀錄仍保留
.\scripts\StopWorker.ps1
```

排程工作名稱為 `NTU-COOL-credit-scammer`，以目前 Windows 帳戶執行，視窗隱藏。持續運行需要保持登入、開機、連網且不進入睡眠；目前的登入觸發不等於無人登入的 Windows 服務。暫時性程序故障最多重啟三次，驗證或設定錯誤直接停止。日誌在 `data/logs/`，狀態在 `data/health.json`。

影片斷線、播放器失效或停滯會在 5、15、45 秒後自動重接：優先重開影片頁，瀏覽器/context 已關閉時重建並核對登入身分，重新讀取平台觀看紀錄後從最早缺口續播。連續三次重接失敗則延後 10 分鐘自動再試，其他影片仍可繼續處理；Discord 會說明正在自動重試，維持課程/影片標題及通知冷卻。

## 設計與驗證

- [詳細計畫](doc/plan.md)
- [實作、已驗證介面及限制](doc/system/implementation.md)
- [架構](doc/system/architecture.md)、[平台整合](doc/system/platform-integration.md)、[資料模型](doc/system/data-model.md)
- [登入](doc/system/authentication.md)、[巡檢](doc/system/scanner.md)、[作業](doc/system/assignments.md)、[影片](doc/system/videos.md)
- [設定](doc/system/configuration.md)、[運行](doc/system/operations.md)、[測試設計](doc/system/testing.md)

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check src tests
```

`.env`、登入 Cookie、題目、答案、SQLite 與日誌都在 Git 排除範圍內。
