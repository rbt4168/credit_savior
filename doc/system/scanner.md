# 課程巡檢詳細設計

[實作計畫](../plan.md) · [資料模型](data-model.md) · [平台整合](platform-integration.md)

## 輸入、輸出與限制

輸入為指定 course ID、已登入 scanner page、Store、600 秒排程與 480 秒單輪 budget。輸出為 scan_runs、各課程 scan_course_results、更新後的快照及新 job。

scanner 只做擷取、版本判斷與入列；不等待模型解題、答案提交或影片播放。完整巡檢包含指定課程全部分頁、作業題目與必要附件版本確認；附件較大或課程較多可能超出 budget，必須在健康狀態顯示容量不足，不能把未掃描課程宣稱完成。

## 排程演算法

1. 啟動後立即執行第一輪，記錄 monotonic 基準 t0。
2. 正常輪次的開始目標是 t0 + n × 600 秒，避免以「上輪完成後再等 600 秒」累積漂移。
3. 上輪未完成不另啟一輪；480 秒 budget 到期取消尚未完成的讀取，保存 partial 與未掃描範圍。
4. 若主機睡眠或程序暫停錯過多輪，立即合併補掃一次，再跳至下一個尚未過期的時間格；不大量補跑。
5. 排程保存 scheduled_at_ms、started_at_ms 與 completed_at_ms。UTC 只用於記錄，系統時鐘校正不改變 monotonic 等待。
6. run 啟動時建立新基準；離線期間沒有掃描紀錄，健康檔須顯示間隔缺口。

此排程保證正常條件下每 600 秒開始一輪，不保證在登入失效、睡眠或平台停機時仍成功完成。

## 每輪擷取步驟

| 步驟 | 讀取或寫入 | 失敗處理 |
| --- | --- | --- |
| 1 | ensure_ready，取得 scanner PageHandle。 | 驗證失效走登入恢復；互動驗證結束本輪。 |
| 2 | 依指定 ID 查課程可存取性與名稱。 | 單課程 forbidden 記錄錯誤，繼續其他課程。 |
| 3 | 列出作業，走完分頁與單元中相關項目。 | 分頁不完整時不推定既有作業已刪除。 |
| 4 | 讀完整題目、期限、格式、rubric、既有提交。 | 欄位不明只保存診斷，不建立可提交 job。 |
| 5 | 確認附件版本，按需下載並計算 content_hash。 | 超過大小限制、下載失敗或解析不支援，記錄原因。 |
| 6 | 原子保存快照，短交易 upsert assignment 並 enqueue。 | unique 衝突視為已入列，不重新解題。 |
| 7 | 列出影片，確認穩定 ID、來源 revision 與可見完成狀態。 | 看不到進度仍可建立播放任務，完成後需核對。 |
| 8 | 保存課程結果與整輪狀態。 | 任一課程失敗則整輪 partial；全部失敗則 failed。 |

附件 cache 以 platform_file_id + 可靠 source_version 對應 SHA-256；無可靠版本時重新取得 bytes。每次讀取與下載仍受剩餘 budget 限制，不能讓大附件佔住下一輪排程。

## 入列規則

| 觀察 | 作業處理 |
| --- | --- |
| presence=present | 更新平台提交紀錄，未提交的本機 job 改 cancelled；未決 attempt 交給 reconciliation。 |
| presence=unknown | 保存快照並建立 needs_input/submission_state_unknown，不自動提交。 |
| 尚未開放 | 建立 retry_wait，retry_after_ms=unlock_at_ms。 |
| 已關閉 | 保存資料並 needs_input/assignment_closed。 |
| 相同 content_hash 已有 job | 更新 last_seen，不重新入列。 |
| 新版本、舊 job 尚未送出 | 原 job cancelled/superseded，新 job 入列；進行中 worker 在 checkpoint 辨識版本失效。 |
| 新版本、舊 job 已送出或結果未決 | 先核對平台，不允許新版繞過 pending submission 限制。 |
| 未支援提交類型 | 建立 needs_input/unsupported_submission_type。 |
| 支援且可提交 | queued/discovered，due_at_ms 作為排序依據。 |

每輪成功掃描對既有 dispatching／uncertain attempt 排程一次原 job 的 reconciling；只把 needs_input 狀態改回 queued，不建立新 intent，也不重新解題。已 running 的核對任務不重複排程。prepared attempt 的舊題目被取代時可安全 abandoned，未送出前才可釋放 pending 限制。

影片已由平台證實 complete 時不播放；來源 revision 改變才建立新 job。同一 source 的 played_unverified 不因每輪巡檢重複入列，只更新平台進度觀察；若平台後來確認 complete，更新原 job 為 succeeded/completed。

## 掃描完整性與公平性

單輪按設定課程順序遍歷。budget 不足時保存下一門／下一項目的 cursor；下輪從未完成範圍開始，再回頭掃其他課程，避免列表末端永久飢餓。cursor 是恢復提示，不替代每輪所見 ID 集合。

完整成功輪次應包含所有指定課程。若連續三輪 budget_exceeded，健康狀態標記 scan_capacity_exceeded，列出耗時與未掃描數量；此時「下一輪發現」驗收不通過，需縮減課程範圍或優化已量測的瓶頸。

## 不把缺漏當成刪除

一次完整掃描沒有看到某 ID 時，標記 missing_observation，下一輪再確認；只有明確頁面顯示已刪除／無權限才取消尚未提交的工作。分頁錯誤、timeout 與空白畫面不取消任務。

## 驗收

注入可控制的 clock 與平台 adapter：驗證固定週期、單輪不重疊、漏掉多輪只補一次、長影片與模型不阻塞掃描、分頁重複能被辨識、相同版本只入列一次、附件變更產生新版本、budget 到期保存 cursor，以及單課程錯誤不影響其他結果。[完整測試矩陣](testing.md)
