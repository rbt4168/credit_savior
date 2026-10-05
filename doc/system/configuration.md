# 環境設定

[實作計畫](../plan.md) · [登入設計](authentication.md) · [常駐部署](operations.md)

## 現有檔案與載入規則

目前 repository 已有 [.env.example](../../.env.example)，本機已有被 Git 忽略的 .env。程式尚未實作；現有設定不會啟動任何操作。

規劃載入優先順序為程序環境變數 → repository 根目錄 .env → 程式預設值。.env parser 選用時必須支援 UTF-8、引號與含 # 的密碼，且不覆蓋已存在的程序環境變數。未知欄位保留不使用；必要欄位錯誤一次列出欄位名稱，不輸出值。

## 欄位規格

| 欄位 | 型別／預設 | 驗證與用途 |
| --- | --- | --- |
| COOL_BASE_URL | HTTPS URL；https://cool.ntu.edu.tw | 去除尾端 /；第一版只接受 NTU COOL origin。SSO 重新導向由平台勘查另確認。 |
| COOL_USERNAME | 非空字串 | auth／run 必填，用於核對實際登入身分。 |
| COOL_PASSWORD | secret；空 | auth 必填；run 有有效狀態可先讀取，需帳密恢復時才要求。不進 DTO、日誌、資料庫或模型輸入。 |
| COOL_COURSE_IDS | 逗號分隔字串 | run 必填；只接受正整數 ID、去重、保持原順序；空值不代表所有課程。 |
| CHECK_INTERVAL_SECONDS | 正整數；600 | 正式巡檢固定 600；測試用時鐘注入，不靠縮短正式設定。非 600 時指出與 10 分鐘需求不符。 |
| BROWSER_HEADLESS | bool；true | 只接受 true／false，不依字串 truthiness；auth 指令固定可見。 |
| AUTH_STATE_PATH | 相對路徑；playwright/.auth/state.json | 相對 repository 根目錄解析；父目錄按需建立。 |
| DATA_DIR | 相對路徑；data | 任務產物根目錄；第一版必須在 Git 忽略的 data 目錄內。 |
| DATABASE_PATH | 相對路徑；data/state.sqlite3 | 解析後必須位於 DATA_DIR 內。 |
| LLM_API_KEY | secret；空 | 開啟作業 solver 前必填；不寫入答案檔。 |
| LLM_BASE_URL | HTTPS URL；空 | 模型服務待選；不得由題目內容提供或改寫。 |
| LLM_MODEL | 非空字串；空 | 與服務搭配驗證，不預設不存在的模型名稱。 |
| TZ | IANA 時區；Asia/Taipei | 用於顯示；所有持久化時間為 UTC epoch milliseconds。 |

路徑解析後檢查是否仍在指定根目錄內；拒絕 .. 逃逸、symlink 跳出或指向 Git 追蹤的檔案。Windows 若缺 IANA 時區資料，實作時補入 timezone data 相依套件並驗證 Asia/Taipei 可載入。

## 第一版固定常數

先放在具型別的程式常數，不新增大量環境設定。若實测需調整，修改設計與測試後再改值。

| 常數 | 值 | 用途 |
| --- | --- | --- |
| navigation_timeout_s | 30 | 單次頁面導覽。 |
| action_timeout_s | 15 | 元素操作與等待。 |
| upload_timeout_s | 120 | 已選擇檔案後等待平台上傳完成。 |
| read_retry_delays_s | 5、15、45 | 首次嘗試外最多重試三次；最終提交點擊不適用。 |
| scan_budget_s | 480 | 單輪掃描上限，保留距下一輪的空間。 |
| login_timeout_s / login_attempts | 60 / 2 | 登入恢復上限。 |
| model_timeout_s / model_attempts | 180 / 3 | 模型首次嘗試外最多重試兩次。 |
| job_lease_s / lease_renew_s | 120 / 30 | worker 持有工作與更新 lease。 |
| playback_sample_s / playback_stall_s | 10 / 60 | 播放檢查與停滯判定。 |
| video_checkpoint_s | 30 | 保存播放位置。 |
| receipt_poll_delays_s | 2、5、10、20、30 | 最終提交後只讀核對節奏。 |
| progress_poll_delays_s | 5、15、30、60、120 | 播完後核對觀看紀錄。 |
| max_attachment_bytes / max_task_bytes | 25 MiB / 100 MiB | 單檔與單作業下載上限。 |
| shutdown_grace_s | 30 | 正常關閉等待。 |

## 設定方式

以下是現有範本的設定操作，不是系統啟動指令。在 repository 根目錄執行；若已有 .env，直接編輯即可。

```powershell
Copy-Item .env.example .env
notepad .env
```

密碼與金鑰填入本機 .env。含 #、空白或引號的值依 parser 語法處理；正式實作必須以這些字元的 fixture 驗證讀值，不拿真實密碼測試日誌輸出。

## 設定錯誤

CLI 規劃回報 config_invalid 與所有缺漏欄位名稱。登入可成功但模型設定缺漏時，巡檢與影片仍可運行；作業保留 needs_input，原因為 solver_not_configured。認證狀態與課程設定缺漏則不能執行 run。
