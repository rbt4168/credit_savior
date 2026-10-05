# 常駐部署、觀測與故障恢復

本文件保留完整設計目標。目前程式、已驗證介面與未完成項目請以 [實作狀態](implementation.md) 為準。

[實作計畫](../plan.md) · [系統架構](architecture.md) · [資料模型](data-model.md) · [測試設計](testing.md)

## 部署目標

一台 Windows 主機、單一系統帳戶與一個 NTU COOL 帳號。主機持續開機、連網且停用自動睡眠。24×7 是此部署條件下的運行目標，平台停機、驗證等待與主機離線要顯示為服務缺口。

第一版 CLI 與腳本已實作；下列契約是完整設計目標，實際部署差異見 implementation.md。

## CLI 契約

| 指令 | 前置條件 | 行為 |
| --- | --- | --- |
| python -m credit_scammer auth | 本機登入設定與可見桌面。 | 可見瀏覽器完成驗證，保存狀態。 |
| python -m credit_scammer run | 課程設定、有效狀態或可恢復登入。 | 常駐巡檢與 worker。 |
| python -m credit_scammer status | 可讀取健康檔。 | 只讀顯示最近巡檢、pending job 與錯誤。 |
| python -m credit_scammer retry JOB_ID | run 未啟動、持有同一鎖。 | 驗證可重試狀態；pending submission 只允許核對，不清除 intent。 |
| python -m credit_scammer reconcile JOB_ID | 未決提交與平台可讀權限。 | 只讀核對回執，必要時修正本機完成狀態。 |
| python -m credit_scammer backup DEST | 可存取資料檔案。 | SQLite backup API 加上 immutable 任務產物，不含憑證。 |

規劃 exit code：0 正常結束、10 設定錯誤、20 驗證／互動等待、30 暫時性程序故障、40 資料庫／必要產物損毀。status 在 run 執行中只讀 health.json；retry／reconcile 等會改寫狀態的命令不得與 run 同時持有資料庫所有權。

## Windows 開機啟動

階段 E 實作 scripts/RunWorker.ps1 與工作排程器設定說明。工作於開機後、網路可用時以專用本機帳戶執行，設定 repository 工作目錄、虛擬環境 Python 的絕對路徑，禁止平行實例，常駐 runtime 不設短期執行上限。

RunWorker.ps1 根據 exit code 決定是否重啟：30 依 15、60、300 秒最多三次；0、10、20、40 直接停止並保留健康狀態。工作排程器不另套無上限重啟，避免兩層策略互相放大。

互動 auth 由使用者桌面單獨執行；常駐程序不用開啟 MFA 視窗來阻塞。登入狀態失效時保存 waiting_auth；完成 auth 後再啟動 run。

## 單實例與啟動恢復

單實例採作業系統持有的鎖，owner UUID 與 PID 只供診斷；PID 檔案存在不代表程序存活。程序退出或被終止時 OS 釋放鎖，遺留檔案不應永久阻擋啟動。

啟動時依序：

1. 驗證路徑與 .env；取得單實例鎖。
2. 開啟 Store，確認 schema version 與引用的 immutable 產物存在。
3. 把上次未完成 scan 標記 interrupted。
4. 回收已退出 owner 的 running job；一般階段排回 queued，未決提交排至 reconciling。
5. 建立 browser，檢查驗證。等待互動時寫健康狀態並以 20 退出，不運行網站 worker。
6. 啟動 worker；第一輪立即掃描，heartbeat 每 30 秒更新。

lease 到期不允許新 worker 與舊 worker 同時執行。Supervisor 必須先取消並等待舊 task 結束，或讓整個程序退出由啟動恢復接手。

## 故障處理矩陣

| 故障 | 保留資料 | 恢復 |
| --- | --- | --- |
| 列表／附件短暫網路失敗 | scan cursor、subject ID、error_code。 | 5／15／45 秒有限重試，受 scan budget 限制。 |
| 模型 timeout／限流 | 已用請求額度、題目快照。 | Solver 依同一三次額度處理。 |
| 已點提交但失去回應 | dispatch intent、answer_hash。 | 只讀 reconciliation；未知結果不重送。 |
| browser process crash | 各 job phase、影片 checkpoint。 | 重建一次 browser 與 generation；連續三次 crash 退出 30。 |
| worker 未處理例外 | error event、lease、最後 phase。 | 保存後重建該 worker；未決提交仍走核對。 |
| 磁碟滿 | 最後可寫的 health event。 | 停止新下載與提交，退出 40；不可只把寫檔失敗當成功。 |
| DB 損毀／較新 schema | 原資料庫與診斷。 | 退出 40，不刪除重建。 |
| 登入被拒／互動驗證 | waiting_auth 與未完成任務。 | 停止自動登入，auth 完成後再啟動。 |
| layout_changed | 去敏感診斷與失敗頁面類型。 | 停止該類寫入；fixture 修正通過後才恢復。 |

browser crash 計數以連續恢復失敗為準；成功完成一次角色頁面操作後清零。一般網路錯誤不觸發 browser 重建。

## 健康檔案

每 30 秒以暫存檔原子替換 data/health.json。以下為示例契約，不代表實際運行狀態：

```json
{
  "schema_version": 1,
  "process_state": "running",
  "heartbeat_at_ms": 1791158400000,
  "session_state": "ready",
  "last_scan_started_at_ms": 1791158400000,
  "last_scan_succeeded_at_ms": 1791158400000,
  "scan_state": "succeeded",
  "scan_duration_ms": 12000,
  "unswept_course_count": 0,
  "assignment_queue_count": 1,
  "video_queue_count": 2,
  "active_assignment_job_id": null,
  "active_video_job_id": null,
  "pending_submission_count": 0,
  "last_error_code": null
}
```

heartbeat 超過 90 秒為 stale；最後成功完整巡檢超過 1200 秒為 degraded。連續三輪 budget_exceeded 標記 scan_capacity_exceeded。個別作業 needs_input 不讓正常課程巡檢誤顯示失敗，但要在任務列表呈現。

## 日誌與保留

JSONL event 欄位為 timestamp、level、event、scan_id／job_id／attempt_id、course_id、phase、error_code、duration_ms。error_detail 只保留去敏感摘要，不含完整頁面、模型 request、憑證或答案正文。

日誌輪替為每檔 10 MiB、保留五檔；30 天後可清除未被 DB 引用的診斷與暫存檔。答案、題目快照、提交回執及 DB 引用的檔案預設保留，需由明確清理指令處理，不按檔案年齡直接刪除。

第一版以本機 status 與日誌觀測，不發送外部訊息。若後續要求通知，再定義渠道、觸發條件與去重。

## 正常關閉

收到停止事件後不再 claim 新 job，保存影片位置，讓正在核對的提交最多等待 30 秒。超時時保存 uncertain intent，再取消工作與關閉 browser；不能將取消視為平台未收到提交。SQLite 交易完整結束後關閉 connection，最後釋放單實例鎖。
