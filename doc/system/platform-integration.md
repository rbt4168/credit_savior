# NTU COOL 平台整合契約

本文件保留完整設計目標。目前程式、已驗證介面與未完成項目請以 [實作狀態](implementation.md) 為準。

[實作計畫](../plan.md) · [系統架構](architecture.md) · [測試設計](testing.md)

## 已知依據與實測界線

NTU COOL 基於 Canvas LMS，另有自訂影片與學習足跡模組。影片可來自校方上傳或匯入 YouTube。[官方平台介紹](https://www.dlc.ntu.edu.tw/ntu-cool/)

尚未使用真實帳號勘查，因此本文件定義資料與操作契約，不提供未驗證的 CSS selector、影片 API endpoint 或繳交成功文字。不得以 Canvas 預設行為推定 NTU COOL 所有頁面相同。

## 頁面勘查交付

| 頁面 | 要保存的去識別化 fixture | 要確認的契約 |
| --- | --- | --- |
| 登入與 dashboard | 登入前、成功、過期、錯誤。 | 登入入口、身分識別、重新導向、額外驗證。 |
| 課程首頁與列表 | 單頁、多頁、空列表、無權限。 | course ID、課程名稱、當期範圍。 |
| 作業列表與詳細 | 文字／檔案作業、關閉、已繳交。 | 題目、rubric、期限、提交型態、附件。 |
| 提交表單與回執 | 上傳中、明確拒絕、提交成功。 | 上傳完成與最終提交的差異、回執可讀欄位。 |
| 影片列表與播放器 | 自有影片、YouTube、載入失敗。 | video ID、iframe、播放控制、進度紀錄。 |
| 學習足跡 | 未觀看、部分、已完成。 | 涵蓋率或完成標記、更新延遲、可見權限。 |

fixture 不包含真實姓名、題目全文、登入狀態或未公開課程內容；以結構等價的測試文字取代。

## 定位器與讀取規則

定位器優先採 role、accessible name、label，再使用經 fixture 驗證的穩定屬性；避免座標與易變動的 DOM 層級。Playwright 的 locator 會在操作時重新解析元素，並支援透過 frame locator 操作 iframe。[官方定位器文件](https://playwright.dev/python/docs/locators)

- 點擊前必須唯一定位且可操作；零個或多個目標皆為 layout_changed。
- 列表翻頁直到終點，保存已見 ID；同一分頁重複出現代表分頁異常，停止該頁擷取。
- 無期限回傳 null；無法解析的期限回傳 date_unparseable，不能默默當成無期限。
- 日期轉換使用頁面明示時區；沒有時區時先依帳戶設定確認，再轉 UTC。
- 檔案 ID、作業 ID、影片 ID 優先從已確認的連結或穩定屬性取得；取不到穩定 ID 時不以標題直接去重。
- 空列表必須有明確空狀態或完成載入的證據；載入 spinner 尚在時不得當成沒有作業。

## 標準化輸出

| DTO | 必要欄位 | 缺漏處理 |
| --- | --- | --- |
| CourseSnapshot | course_id、name、url、access_state。 | ID 缺漏使課程掃描失敗。 |
| AssignmentSnapshot | course_id、assignment_id、prompt、constraints、attachments、期限、submission_observation。 | 題目或格式不明則 needs_input。 |
| SubmissionObservation | presence、ownership、submitted_at_ms、artifact metadata、evidence_path。 | 無法判斷 presence 時為 unknown。 |
| VideoSnapshot | course_id、video_id、url、player_kind、duration_s。 | 長度未知可等待 metadata，逾時則 needs_input。 |
| ProgressObservation | completion、coverage_ratio、observed_at_ms、evidence_path。 | 不可見紀錄為 unknown，不能視為 0 或 100%。 |
| PlaybackObservation | position_s、duration_s、paused、ended、buffering。 | 讀不到則先重建該影片頁，仍失敗則 needs_input。 |

presence 為 present／absent／unknown；ownership 為 ours／other／unknown。presence=present 即避免再次自動提交，即使 ownership 無法辨識。

## 下載與上傳契約

附件透過已登入頁面的下載機制取得，保存平台 ID、原始檔名、大小與 SHA-256。輸出檔名由本機生成，不直接使用包含路徑的伺服器檔名；重新導向到未確認的外部服務時標記待整合。

上傳需等待檔案在表單中顯示完成且無格式錯誤，之後才允許最終提交。回執優先使用平台 attempt ID；若無此欄位，核對提交時間、檔名與大小，文字回答核對可見正文。證據不足時保留 unknown，不宣稱已提交。

## 錯誤分類

| error_code | 意義 | worker 行為 |
| --- | --- | --- |
| auth_expired | 回到登入頁或明確驗證失效。 | 呼叫 BrowserSession 恢復。 |
| course_forbidden | 已登入但無課程權限。 | 課程標記 needs_input。 |
| layout_changed | 定位器失效、欄位語意不明。 | 保存診斷，停止相關寫入。 |
| network_transient | 讀取、下載或載入短暫失敗。 | 有限重試。 |
| submission_rejected | 平台明確拒絕且未繳交。 | 依原因修正或 needs_input。 |
| submission_uncertain | 最終提交送出後結果不明。 | 只讀核對，不直接再送出。 |
| progress_unavailable | 無法取得觀看完成證據。 | 標記 played_unverified。 |

不因一次 timeout 就把它分類為驗證失效；必須有登入頁或身分檢查證據。
