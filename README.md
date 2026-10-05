<p align="center">
  <img src="assets/credit-savior-banner.png" alt="Credit Savior — NTU COOL course automation" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square" alt="Python 3.10 or newer">
  <img src="https://img.shields.io/badge/Playwright-1.63.0-2EAD33?style=flat-square" alt="Playwright 1.63.0">
  <img src="https://img.shields.io/badge/Codex%20CLI-0.160.0-111827?style=flat-square" alt="Codex CLI 0.160.0 verified">
  <img src="https://img.shields.io/badge/Model-GPT--6.1--Sol-0D9488?style=flat-square" alt="GPT-6.1-Sol">
  <br>
  <img src="https://img.shields.io/badge/python--dotenv-1.2.4-7C3AED?style=flat-square" alt="python-dotenv 1.2.4">
  <img src="https://img.shields.io/badge/pypdf-6.19.0-2563EB?style=flat-square" alt="pypdf 6.19.0">
  <img src="https://img.shields.io/badge/ReportLab-5.0.1-DC2626?style=flat-square" alt="ReportLab 5.0.1">
  <img src="https://img.shields.io/badge/tzdata-2026.5-64748B?style=flat-square" alt="tzdata 2026.5">
</p>

# Credit Savior

Credit Savior 是 NTU COOL 的全天候課程助手，讓作業與課程影片 **24/7 自動接著做**。從查看課程、發現待交作業，到產生答案、提交與接續播放影片，都能持續處理，並透過 Discord 回報進度與結果。

**把設定與操作交給 coding agent。** 複製本頁最後的訊息給 Codex 或你使用的 coding agent，由它完成安裝、登入與啟動。設定完成後，你可以在 Discord 看處理結果；遇到缺題目、缺主題等需要你補充的內容，也會一起收到提醒。

## 設定一次，持續幫你處理課程

### 全天候巡課，自動找出待交作業

每 10 分鐘查看你在 NTU COOL 加入的所有學生課程，找出還沒到期、仍可繳交且尚未提交的作業，再接著處理。助教身份的課程、已提交與已截止的作業會略過，讓新作業出現後也能自動接上。

### 從讀題到提交，自動完成能處理的作業

找到待交作業後，使用 **GPT-6.1-Sol** 閱讀題目、要求與支援的附件，產生文字答案、PDF 或文字檔案。答案通過檢查後自動提交，確認網站收到才回報「已提交」，並將寫好的內容送到 Discord。提交途中斷線時，會先查網站上的結果，避免直接重複繳交。

### 做不完的先記下，其他工作繼續

題目不完整、報告還沒選主題，或附件目前無法處理時，會先略過這份作業，記下缺少什麼、為什麼不能完成，以及之後需要詢問的內容，讓其他工作繼續。有可交付的部分答案時會附上，明確標為「未提交草稿」。程式作業目前還無法自動驗證執行結果；圖片、Word 等內容也可能需要另外處理，不能保證所有題型都能完成。

### 接續播放課程影片，中斷後自動重接

依網站觀看紀錄接續未看的部分，以正常 1 倍速播放支援的 NTU COOL 課程影片。播放器失效或播放停住會自動重接；連續失敗後等待 10 分鐘再試，讓影片能持續往下看。

播完也會通知你，並再次核對網站進度，分別回報「平台已確認完成」或「已播完，進度尚待確認」。如果網站的影片長度與最後一秒紀錄有差異，會保留原因供核對。

### 在 Discord 看完成了什麼、交到哪裡

持續處理的同時，把寫好的作業、草稿、提交結果、影片播完結果與需要處理的問題送到你指定的 Discord 頻道。每則通知包含 **課程名稱、正式課號、作業或影片標題、主要結果與網站連結**，方便你查看完成了什麼、交到哪裡，以及哪些地方還需要補充。

課號取自課程資訊的「課號」欄位；缺少資料時會明示待取得。同一課程的同類影片問題會合併，至少間隔一小時才再通知；已確認送達的相同結果會保存紀錄，避免一般重啟造成洗版。網路中斷時仍可能出現送達回應遺失而重複通知的情況。

### 登入資料在自己的電腦上設定

由 agent 開啟你電腦上的對話框，直接詢問帳號、密碼與選填的 Discord 通知網址。密碼欄會遮蔽，資料保存在你的電腦；保存後清空欄位並關閉視窗，帳密不需要貼進聊天，也不會加入公開專案。已貼進聊天的資料仍需由本人管理，agent 無法保證刪除聊天紀錄或上下文。

### 停止後能接續，換電腦也能保留紀錄

保留已完成的答案、待補資料、影片位置與提交紀錄，讓 agent 能在停止後接續工作。同帳號換電腦時，agent 會先停用舊電腦，再搬移工作與通知紀錄，避免兩台同時處理同一門課。

電腦登入後可自動開始運行。要維持 24/7 處理，執行它的電腦需要保持開機、登入、連網且不睡眠；關機期間會暫停，重新啟動後再接續。

## 複製這段訊息給你的 coding agent

```text
請幫我把 Credit Savior 在這台電腦上設定好，讓它 24/7 自動處理我的 NTU COOL 作業與課程影片。
專案：https://github.com/rbt4168/credit_savior

先閱讀專案的 doc/installation-guide.md，再實際完成安裝、登入、驗證與持續運行的設定。
請用本機對話框直接詢問我的 NTU 帳號、密碼及 Discord 通知網址，
保存後清空欄位、關閉對話框；不要要求我把帳密貼進聊天，
也不要把它們放入記憶、文件或公開紀錄。

每 10 分鐘查看我所有學生課程，處理仍可繳交且尚未提交的作業，
使用 GPT-6.1-Sol 產生答案；通過檢查後自動提交並確認網站收到。
缺題目、缺主題或無法處理時先略過，把原因和之後要問的事項告訴我。

正常播放支援的課程影片，斷線時自動接續，播完也要通知我。
把答案、草稿、提交到哪門課、影片結果與問題送到我的 Discord，
包含課程名稱、課程資訊中的正式課號、作業或影片標題與網站連結。
平台尚未確認完成時請明確說明，不把草稿說成已交或把播完說成進度已滿。

如果是從舊版本更新或換電腦，先保留現有工作與通知紀錄，
確認同一帳號的舊電腦已停止，再接續；不要刪掉資料來排除問題。
最後用簡單的話告訴我哪些功能已經確認可用、還有哪些需要我處理。
```
