# 第一版實作與驗收紀錄

[計畫](../plan.md) · [README](../../README.md)

本文件記錄 2026-10-05 已實作的執行契約。其他系統文件保留完整設計與後續驗收目標；若原設計的介面名稱、CLI 或部署方式與本文件不同，以本文件及目前程式為準。尚未完成的能力列在下方，不以設計文件作為完成證據。

## 實作對照

| 模組 | 已實作行為 | 程式 |
| --- | --- | --- |
| Config | dotenv 與環境優先序、欄位錯誤不輸出值、NTU origin、ID 或 all、模型固定 6.1-Sol、路徑防逃逸。 | config.py |
| BrowserSession | Chromium、NTU ADFS 登入、Cookie/IndexedDB 原子保存、身分核對、角色頁面、reader/writer gate、提交互斥、影片頁及 browser/context/driver 重接。 | browser.py |
| CoolClient | Canvas GET、附件快照、文字/檔案提交表單、NTU LTI 與跨 iframe HTML5/YouTube 播放。 | cool.py |
| Store | SQLite WAL、schema v1、唯一 job、120 秒 lease/30 秒續租、原子提交 intent、崩潰恢復、核對優先。 | store.py、schema.sql |
| Scanner | 即刻開始、monotonic 600 秒時間格、480 秒 budget、學生角色篩選、精確 due/unlock/lock、版本去重。 | scanner.py |
| Solver | Codex CLI gpt-6.1-sol、JSON schema、最多三次請求、180 秒 timeout、文字/CJK PDF/源碼產物。 | solver.py |
| Validator | 非空、格式、大小、SHA-256、PDF 可讀性；程式題因 runner 未提供而停止提交。 | validation.py |
| AssignmentWorker | 點擊前重讀、gate → submission lock、prepared → dispatching → confirmed/uncertain、文字與下載檔案雜湊回執。 | assignments.py |
| VideoWorker | 平台已看區間、最早缺漏前 5 秒續播、30 秒 checkpoint、60 秒停滯、5/15/45 秒重接、失敗延後 10 分鐘自動再試、平台確認。 | videos.py、progress.py |
| CLI/Supervisor | auth、scan、run、run --once、status、retry、backup、OS 鎖、健康檔、JSONL 輪替、例外去敏感。 | main.py、workers.py |
| Windows | 隱藏視窗、登入時啟動、同名工作所有權检查、暫時故障最多三次重啟。 | scripts/*.ps1 |
| Discord | 答案附件、提交課程及位置、未完成原因與待詢問事項；15 秒檢查、持久化去重、wait=true 確認、憑證遮蔽。 | discord.py |

## 已確認的平台契約

登入入口 https://cool.ntu.edu.tw/login/portal 的「以計中帳號登入」導向 adfs.ntu.edu.tw；帳密僅填入已驗證的 ADFS UsernameTextBox/PasswordTextBox。成功返回 COOL 後核對 Canvas ENV.current_user_id，保存私有狀態及 metadata。

課程列表讀 /courses 的目前與過往表格；只處理學生 enrollment。作業頁是 /courses/{course_id}/assignments，不公開該導覽的課程目前略過。browser context Cookie 下的同 origin 只讀請求，沒有另存 Canvas token：

```text
GET /api/v1/courses/{course_id}/assignments?include[]=submission&per_page=100
GET /api/v1/courses/{course_id}/assignments/{assignment_id}?include[]=submission
GET /api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self
GET /api/v1/files/{file_id}
GET /courses/{course_id}/files/{file_id}/download
GET /api/v1/courses/{course_id}/modules?include[]=items&per_page=100
GET /api/v1/courses/{course_id}/modules/{module_id}/items?per_page=100
```

分頁按 Link header 的 next 跟進，重複 next 中止。ISO 日期使用目前學生的截止日，不從相對日期文字猜測。過期、已繳交、無線上提交方式的 gradebook item 不建立解題任務。小組、測驗與特殊外部工具不自動提交。

直接引用的公開 NTU PDF 由獨立無 Cookie 下載器取得；限 NTU 子網域、無 query 的直接 PDF，redirect 也驗證 origin；25 MiB 單檔、100 MiB 題目上限。PDF Symbol 字型按字型編碼轉換希臘符號；含圖片或影像 PDF 尚不支援視覺理解。

## 模型與產物

使用已登入本機 Codex CLI，不猜 API endpoint。模型輸入只有題目 JSON，沒有 COOL 憑證。CLI 採 ephemeral、忽略使用者 config/rules、read-only sandbox，停用 shell/browser/apps/plugins/skills/多代理/影像工具；題目送 stdin。Windows 直接以 node 啟動 Codex JS，避免 shell 展開題目。

答案 manifest 保存模型、路徑、雜湊；timeout/cancel 終止子程序樹。報告主題、個資、實驗數據與引用不能捏造；必交源碼須獨立檔案。格式驗證不保證解答正確。缺資料時 model-response.json 保存缺漏，job 留 needs_input，註記之後要詢問，不反覆解題。

## 提交與恢復

寫入採 browser assignment 表單，不直接呼叫 submission POST。文字用 TinyMCE 或 textarea；檔案用 file input，額外檔案須匹配新增控制項；無匹配則停止。先持久化 dispatching intent，再點一次提交。

回執須在本次準備之後提交；文字解 HTML 後比對，檔案比對名稱與 SHA-256。unknown、網路例外或點擊失敗先核對。重啟/retry 遇 dispatching/uncertain 只能讀回執；prepared 中斷可重用答案，避免額外模型請求。

## 影片證據

目前辨識 modules ExternalTool，其 URL 是 cool-video.dlc.ntu.edu.tw/ltiv1p1/launch/videos/{source_id}。經 /courses/{course_id}/modules/items/{item_id} 正常啟動 LTI，不自行產生 token。

監聽前端正常 GET 的 JSON，不截取或輸出 bearer token：

```text
/api/users/current
/api/courses/{course_id}/videos/{course_video_id}/view
/api/courses/{course_id}/course-videos/{course_video_id}/viewing-records/summaries
```

只讀當前影片使用者的 records，合併 start/end 區間，計算 [0,length] 補集；由最早缺口正常 1 倍速播放，不 POST 觀看紀錄。ended 不等於 complete。播完透過正常「觀看紀錄」tab 刷新；仍有缺口或讀不到時留 played_unverified。證據存 tasks/{job_id}/video-evidence.json。

真實 YouTube 實測：播放器 1425.641 秒，平台 length=1426，records 到 1425，completionRate 約 99.93%；播到 ended 仍差最後一秒。系統記錄差異，不改成 complete。

## 驗證證據

截至 2026-10-05：

- 真實 .env 登入及狀態重用成功，憑證未加入 repository。
- 真實全課程巡檢成功，能區分學生與助教 enrollment、建立未到期且未交的作業及影片任務；私人課程名單與任務數量不收錄於文件。
- 真實 Codex gpt-6.1-sol JSON schema smoke 成功。
- 真實 NTU LTI/YouTube 播放到 ended，可檢出平台末秒量化差異。
- 47 個自動化測試通過：本機 HTTP 站真實 Chromium 提交/讀回執、提交前後斷電、unknown retry 不重送、跨連線 claim、失效 lease、版本變更、提交前截止日更新、session gate、區間合併/缺口、PDF 渲染、竄改產物、runner 阻擋、播放順序、CLI 狀態、Discord 去重/隱私/embed/冷卻、自動重接、延後重試、關閉 page/context/browser 後恢復、取消保存位置、舊頁回應不覆蓋新證據、播放器外層啟動及完成通知。
- ruff check src tests 通過。
- Windows 登入排程已實際安裝及啟動，heartbeat 可讀；正常停止也已驗證。
- 真實 Discord 通知已有伺服器確認回執；未完成作業已傳課程、作業連結與缺漏原因，沒有把未提交的作業標為已提交。
- 真實 NTU COOL 瀏覽器關閉後，已重建 Chromium、重用本機登入狀態並核對相同身分，課程讀取恢復；輸出不含帳戶資料。
- 真實 NTU LTI/YouTube 播放中關閉 Chromium 後，重接並重讀觀看紀錄，恢復至最早缺口前 5 秒；採樣位置由約 1420 秒正常前進至 1422 秒。未初始化的播放器先點外層 Play Video，等待媒體 metadata 載入再續播，避免載入與 play 請求互相中斷。

測試站使用虛構帳戶；真實課程頁面、憑證與答案不進 Git。真實作業提交、各種上傳控制項與 24 小時 soak 仍需個別證據。

## 尚未完成

| 能力 | 目前行為／完成觸發條件 |
| --- | --- |
| 隔離程式 runner | 產生源碼後留 runner_unavailable；需固定 runtime、無網路隔離及題目測試。 |
| 圖片、Word/LaTeX | 留不支援原因；需視覺或文書處理。 |
| 報告主題、未附完整題目 | 暫時略過，註記之後詢問，補資料後 retry。 |
| 非 modules 影片、其他 LTI | 未提供 adapter。 |
| 平台末秒誤差 | 保存缺口，不宣稱 100%。 |
| 24×7 | 有常駐及登入啟動；需持續開機、登入、連網、不睡眠；尚未做 24 小時 soak。 |
| 無人登入 Windows service | 目前是 AtLogOn/Interactive 工作。 |
| 無法恢復的影片服務 | 影片可自動重接；連續失敗後延後 10 分鐘繼續嘗試。需要互動登入、來源變更或已播放但進度無法確認時，仍保留具體待處理原因。 |
| reconcile CLI | 使用 retry 保留核對語意，沒有獨立指令。 |
| status 告警 | 有 stale/blocked_jobs；完整 degraded/容量告警仍待做。 |

這是可運行的第一版，完整 A–E 退出條件尚未全部通過。

## Discord 交付契約

使用者要求傳的是作業產物與提交位置，並要求不能完成的作業列出原因；系統設計文件不傳送。Webhook 只在本機 .env 中，模型子程序不取得該值。[Discord 官方 Webhook 規格](https://docs.discord.com/developers/resources/webhook)

成功作業訊息附答案、課程名稱、作業標題與平台 URL，明確寫「已提交並確認回執」。needs_input/failed 的作業只寫暫時略過，列缺漏或處理事項，若有答案檔案則標未提交草稿。當前没有答案檔案時直接說明，不造附件。影片與巡檢問題傳問題類型，不夾带登入或帳戶資料。

傳送用 wait=true，附件名稱與伺服器回執核對，禁用 mentions；確認後才記 notifications.json 去重。失敗保留本機並至少等待 60 秒後重試，不阻塞巡檢。網路中斷在伺服器已收到、但回應未收到的邊界可能出現重複通知；不能宣稱 exactly-once。文字遮蔽帳密、Webhook 與本機路徑；PDF/源碼附件先比雜湊並檢查是否含上述資訊，未通過不傳。答案個資不自動加上，課程與作業位置則為使用者明確要求的交付資訊。

使用者指定 embed 格式：標題為「課程名稱（COOL course_id）－作業/影片/其他」，description 起首為子標題，接主要訊息、平台位置與後續事項。影片問題按課程及錯誤類型合併，至少冷卻 60 分鐘；完整影片標題與連結太長時附清單。影片 succeeded 或 played_unverified 逐支傳結果通知，明確區分「平台已確認完成」與「已播放到結束，但平台進度尚未確認」，附課程、標題、連結及核對事項；以 job_id、state 及格式版本保存送達去重，重啟不重送，後續確認完成仍可再通知。Discord 429 及成功回應的 rate-limit headers 用於節流；不把每支影片的同一錯誤逐支發送。

notify --resend 明確重發一次，並更新正常去重基線，防止下次常駐啟動又自動重發。模型產生但缺必要資料的部分答案，在獨立 delivery-drafts 目錄渲染供傳送；不放進 worker 的答案 manifest，避免 retry 誤用草稿直接提交。
