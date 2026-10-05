# credit_scammer

NTU COOL 瀏覽器自動化系統的實作計畫與詳細設計，涵蓋每 10 分鐘巡檢課程、作業解題與提交、影片連續播放及平台進度確認。目前尚未實作執行程式。

## 計畫

[實作計畫與驗收](doc/plan.md) 定義需求、前置資訊、工作拆解、相依順序與完成條件。

## 系統詳細設計

| 文件 | 內容 |
| --- | --- |
| [系統架構](doc/system/architecture.md) | 模組介面、資源所有權、並行與啟停流程。 |
| [NTU COOL 整合](doc/system/platform-integration.md) | 頁面契約、定位器、資料擷取與平台證據。 |
| [資料模型](doc/system/data-model.md) | DTO、SQLite DDL、版本雜湊、去重與交易。 |
| [登入與憑證](doc/system/authentication.md) | 驗證狀態、狀態保存、互動登入與恢復。 |
| [課程巡檢](doc/system/scanner.md) | 600 秒排程、掃描 budget、入列與完整性。 |
| [作業處理](doc/system/assignments.md) | 解題契約、驗證、提交 intent、回執與重啟恢復。 |
| [影片處理](doc/system/videos.md) | 播放控制、續播、停滯恢復與完成核對。 |
| [環境設定](doc/system/configuration.md) | .env 欄位、載入規則、固定常數與錯誤。 |
| [常駐運行](doc/system/operations.md) | Windows 啟動、CLI、健康狀態、日誌與故障處理。 |
| [測試設計](doc/system/testing.md) | 驗收矩陣、故障注入與 24 小時量測。 |

## 設定

範本為 [.env.example](.env.example)，真實帳密填入本機 .env。登入狀態與本機產物已由 .gitignore 排除。現有設定不會啟動巡檢、提交或播放。
