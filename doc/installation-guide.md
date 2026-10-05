# Installation Guide：交給另一台電腦的 Codex

本指南讓新電腦上的 Codex 從 clone 到常駐啟動完成部署。以 **Windows 原生 PowerShell** 為已驗證路徑；其他作業系統需要另外整合常駐服務。依照目前程式與 [.env.example](../.env.example) 操作；完整設計文件中的未實作命令不適用。功能與限制見 [實作狀態](system/implementation.md)。

啟動 `run` 後會自動處理並提交通過驗證的作業、正常播放影片，以及發送已設定的 Discord 通知。只有 `scan` 是巡檢並建立任務，不會執行作業提交或影片播放。

## 1. 可直接貼給新電腦 Codex 的指令

在新電腦開啟 repository 作為 Codex 工作目錄，貼上以下內容即可。尚未 clone 時先依第 3 節取得程式。

```text
請閱讀 doc/installation-guide.md，實際把這個專案部署到目前電腦，
完成依賴、Chromium、Codex CLI、NTU COOL 登入、模型及 Discord 驗證，
最後安裝登入排程並啟動常駐 run。這項要求包含 run 的自動作業提交、
正常影片播放及 Discord 結果通知。不要只提供計畫。

使用本機 .env；若已存在就保留，必要時只補缺少欄位。
請用 scripts/ConfigureEnv.ps1 開本機對話框，直接問我帳號、密碼，
以及選填的 Discord Webhook。密碼與 Webhook 欄位要遮蔽。
由對話框直接保存到 .env，不要要我把秘密貼進 Codex 聊天，
不要讀取或截圖對話框來取得值，不把值寫進 memory、文件或交接摘要。
保存後關閉對話框，清空輸入欄位並結束輸入程序。
Codex OAuth 與 NTU 額外登入驗證由我在瀏覽器完成。
等待這些必要輸入時先完成其他不依賴登入的安裝與檢查。

巡檢 COOL_COURSE_IDS=all，CHECK_INTERVAL_SECONDS=600，
模型固定 gpt-6.1-sol；模型無權限時報告阻礙，不自行換模型。
如果這是同帳號換機，先依第 10 節停止舊機並還原任務與通知紀錄；
舊機停機尚未確認前，不啟動新機的 run。

逐項核對驗收條件；回報 commit、版本、測試結果、排程狀態、
heartbeat、掃描狀態、模型及 Discord 驗證結果，以及尚未解決的事項。
不要輸出帳密、Webhook、Cookie、Codex 登入憑證或私人課程清單。
保留現有工作與資料，不刪除資料庫來排除錯誤。
只做部署；不要把 .env、data、auth、題目或答案加入 Git。
```

新電腦的 Codex 必須能執行本機 shell、寫入專案、連網下載依賴、啟動 Chromium 與管理目前帳戶的排程。若執行環境明確不允許某項操作，回報被擋的操作與原因，讓使用者在本機執行該步。

## 2. 環境與帳戶

| 項目 | 要求／已驗證版本 |
| --- | --- |
| 執行環境 | Windows 桌面、原生 PowerShell；以未來執行排程的同一 Windows 帳戶安裝與登入。 |
| Git | 可執行 `git`，可以取得 repository。 |
| Python | 專案要求 3.10 以上；已驗證 Python 3.10.11。選擇該版本可重現現有環境。 |
| Node.js／npm | 必須在該 Windows 帳戶的 PATH；目前驗證 Node 24.21.0、npm 12.2.0。 |
| Codex CLI | 已驗證 `@openai/codex@0.160.0` 的 npm 安裝，且登入帳戶可實際使用 `gpt-6.1-sol`。 |
| Chromium | 由虛擬環境的 Playwright 安裝；系統有 Chrome 不代表此步已完成。 |
| NTU COOL | 目標使用者的帳號、密碼與課程權限，直接在新機本機設定。 |
| Discord | 要啟用通知時，在本機設定自己的 Webhook；空白會停用通知。 |

先安裝缺少的 Git、Python 與 Node.js，再重新開啟 PowerShell／Codex，使新的 PATH 生效。此部署不需要開放入站埠；需能連出套件來源、NTU COOL／ADFS、影片服務、Codex 與 Discord。

```powershell
git --version
python --version
node --version
npm --version
```

若 `python` 指到 Windows Store 或錯誤版本，用 `py -3.10 --version` 確認可用版本，並在建立虛擬環境時改用 `py -3.10 -m venv .venv`。後續命令全部使用 `.venv\Scripts\python.exe`，不依賴 shell 的 Python 選擇。

## 3. 取得程式與安裝依賴

專案放在可寫入的固定本機目錄，例如 `C:\codex\credit_scammer`。以下路徑是範例，可換成自己的位置。已有 checkout 時先看 `git status --short`，保留未提交變更；更新前先停止該 checkout 的常駐程序。

```powershell
New-Item -ItemType Directory -Path C:\codex -Force | Out-Null
Set-Location C:\codex
git clone https://github.com/rbt4168/credit_scammer.git
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
Set-Location C:\codex\credit_scammer
git rev-parse --short HEAD

python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
.venv\Scripts\python.exe -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
.venv\Scripts\python.exe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }

npm install -g @openai/codex@0.160.0
if ($LASTEXITCODE -ne 0) { throw 'Codex CLI installation failed.' }
codex --version
```

上述版本是本專案的相容性基準。若本機已有不同 Codex CLI 版本，先記錄版本並檢查第 5 節，避免在另一個正在執行的 Codex 工作中直接覆蓋全域安裝。其他版本必須通過相同模型 smoke，不能只憑 `codex --version` 宣稱相容。官方亦提供 npm 安裝方式：[OpenAI Codex CLI 安裝命令](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)。

Windows solver 會由 PATH 找到 `codex`，再執行相鄰的 `node_modules/@openai/codex/bin/codex.js`。只有 Codex 桌面／IDE 已登入、單獨下載的原生執行檔，或只有 WSL 內的 CLI，不能直接滿足目前 Windows launcher 的要求。

不必 activate 虛擬環境，直接使用以上完整路徑。若 PowerShell 擋住 npm/codex 的 `.ps1` shim，可先用 `npm.cmd`／`codex.cmd`；專案 Python solver 直接呼叫 Node launcher。

## 4. 本機設定與登入

### 用本機對話框設定帳號、密碼

已經啟動的 checkout 先正常停止常駐，再修改設定；新的值會在下次啟動時載入。

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\scripts\ConfigureEnv.ps1
if ($LASTEXITCODE -ne 0) { throw 'Local credential setup cancelled or incomplete.' }
```

Codex 執行此命令後，使用者在自己電腦的 Windows 對話框填 NTU account、NTU password 與選填的 Discord Webhook，按 Save locally。密碼與 Webhook 欄位遮蔽；輸入不經聊天、命令列參數或 terminal 輸出。保存程式由 stdin 接收值，驗證後原子替換 UTF-8 `.env`，自動處理引號、`#` 與空白。保存成功只輸出成功狀態，關閉視窗並清空欄位；取消不改設定。

已有 `.env` 時保留其他設定，留空欄位保留原值；第一次安裝則從 `.env.example` 建立，必須填帳號與密碼。變更已有帳號時必須同時填新密碼。預設 all、600 秒與固定模型不需額外填寫；其他非秘密選項可在本機編輯。要停用已設定的 Discord，需直接在本機把 `DISCORD_WEBHOOK_URL` 清空，留空對話框欄位會保留原值。

此方式讓秘密不進入 Codex 對話或記憶。清空欄位與結束程序不等於保證所有作業系統記憶體已覆寫；Codex 也無法保證刪除已進入聊天的紀錄或上下文。若先前已貼進聊天，不宣稱「記憶已刪除」；帳號本人可依產品提供的功能管理紀錄及必要時更換秘密。

| 設定 | 正式值 |
| --- | --- |
| COOL_BASE_URL | `https://cool.ntu.edu.tw` |
| COOL_COURSE_IDS | `all`：所有已加入的學生課程；助教 enrollment 略過。也可填逗號分隔的數字 course ID。 |
| CHECK_INTERVAL_SECONDS | `600`；目前程式拒絕其他值。 |
| BROWSER_HEADLESS | `true`；互動 `auth` 仍會開可見瀏覽器。 |
| LLM_MODEL | `gpt-6.1-sol`；目前程式拒絕其他模型。 |
| AUTH_STATE_PATH | `playwright/.auth/state.json` |
| DATA_DIR／DATABASE_PATH | `data`／`data/state.sqlite3` |
| TZ | `Asia/Taipei` |
| DISCORD_WEBHOOK_URL | 本機填入完整 Webhook；空白會關閉 Discord。 |

此版本使用 Codex CLI 的登入，不讀取 `LLM_API_KEY` 或 `LLM_BASE_URL`。程序環境變數會覆蓋 `.env`，所以另一台電腦若結果與檔案設定不同，檢查同名環境變數即可；只回報欄位名稱，不印出 secret 值。

確認 Git 排除設定有效：

```powershell
git check-ignore .env data/state.sqlite3 playwright/.auth/state.json
git ls-files -- .env data playwright/.auth
```

第一個命令應顯示被忽略的路徑，第二個應無輸出。下載程式不會取得其他電腦的帳密、答案或登入狀態。

### Codex 登入

```powershell
codex login
codex login status
```

在瀏覽器完成自己的 ChatGPT 登入；CLI 登入與 NTU COOL 登入是兩件事。登入成功仍需要第 5 節的實際模型請求來驗證 `gpt-6.1-sol` 權限。瀏覽器 callback 無法使用時，可嘗試 `codex login --device-auth`，前提是帳戶／工作區允許 device code 登入。[OpenAI 登入文件](https://learn.chatgpt.com/docs/auth)

### 同帳號換機先還原資料

若要接續舊電腦的任務，**此時先執行第 10 節還原，再執行以下 auth／scan**。不同使用者部署從空白資料開始，不複製另一人的資料庫或任務。

### NTU COOL 登入

```powershell
.venv\Scripts\python.exe -m credit_scammer auth
if ($LASTEXITCODE -ne 0) { throw 'COOL authentication not complete.' }
```

程式開啟可見 Chromium，使用本機設定登入；遇到額外驗證由使用者在視窗完成。成功輸出 `session_state: ready`，並保存本機 auth state 與 `.meta.json`。之後常駐會先重用並核對身分，狀態失效時嘗試登入恢復。

## 5. 驗證 CLI、依賴與指定模型

所有命令都在 repository 根目錄執行。尚未啟動常駐時完成以下檢查。

```powershell
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m credit_scammer --help
.venv\Scripts\python.exe -c "from credit_scammer.solver import codex_command; codex_command(); print('Codex launcher OK')"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests
```

指南建立時的基準是 52 個測試通過、ruff 通過，包含本機設定保存、既有值保留、輸入檢查與 PowerShell→Python 的 Unicode／隱私測試。新 commit 的測試數可能改變，以該 checkout 的實際結果為準。測試使用虛構題目與本機瀏覽器 fixture；不提交真實課程作業。

以下 smoke 使用**專案實際 solver 與固定模型**，解一個虛構算式，只在暫存目錄產生答案；沒有 NTU 網路請求，也不提交作業。會使用該 Codex 帳戶的模型額度。

```powershell
@'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from credit_scammer.models import Assignment
from credit_scammer.solver import CodexSolver

async def smoke():
    with TemporaryDirectory(prefix='cool-model-smoke-') as directory:
        root = Path(directory)
        assignment = Assignment(
            '1', '1', 'https://example.invalid/installation-smoke',
            'Installation smoke', 'Compute 2 + 2. Return a text answer.',
            ('online_text_entry',))
        answer = await CodexSolver(root, 'gpt-6.1-sol').solve(assignment, root / 'answer')
        if answer.kind != 'text' or '4' not in (answer.text or ''):
            raise ValueError('Unexpected smoke answer')
        print('gpt-6.1-sol solver smoke OK; no course submission')

try:
    asyncio.run(smoke())
except Exception as error:
    print('Model smoke failed:', getattr(error, 'code', type(error).__name__))
    raise SystemExit(1)
'@ | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw 'Model smoke failed; do not mark solver ready.' }
```

`codex_launcher_unavailable` 代表 Windows 的 npm launcher／Node 路徑不相容；`model_request_failed` 可能是 CLI 旗標、登入、模型權限、網路或額度問題，不能直接猜成其中一種。先比對 CLI 版本與 `codex exec --help`；保留模型原名，回報還缺哪項證據。

## 6. 巡檢與 Discord 連線驗證

先只掃描，不開始解題提交或播放：

```powershell
.venv\Scripts\python.exe -m credit_scammer scan
if ($LASTEXITCODE -ne 0) { throw 'Scan command failed.' }
.venv\Scripts\python.exe -m credit_scammer status
```

掃描輸出的 `state` 應是 `succeeded`。`partial` 也可能以 0 結束，必須另讀 `errors` 與 `last_scan`；逐項處理失敗課程或容量問題，不能只看 exit code。掃描已建立本機 queued jobs，但尚未執行。

Discord 以下只發送一次不含私人資訊的安裝測試 embed。若預計啟用 Discord，空白 Webhook 不能視為驗證通過。

```powershell
@'
from pathlib import Path
from credit_scammer.config import Config
from credit_scammer.discord import DiscordTransport

try:
    config = Config.load(Path.cwd())
    if not config.discord_webhook:
        print('Discord disabled: local Webhook not configured')
        raise SystemExit(1)
    receipt = DiscordTransport(config.discord_webhook).post(
        'Installation (0) - Other\n**Discord connection test**\n'
        'Webhook delivery confirmed by this installation test; no course data attached.')
    print('Discord test receipt confirmed:', bool(receipt.get('message_id')))
except Exception as error:
    print('Discord test failed:', getattr(error, 'code', type(error).__name__))
    raise SystemExit(1)
'@ | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw 'Discord verification incomplete.' }
```

使用者應可在目標頻道看到測試 embed。`notify` 只會傳現有任務結果，空佇列時沒有訊息，不能用它作為新機連線已成功的證據。正式通知附課程、課號、作業／影片標題與連結；答案或草稿依狀態附上，系統文件不發送。

## 7. 啟動常駐與登入排程

通過前述檢查後，以目前 Windows 帳戶安裝並啟動：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\InstallTask.ps1 -StartNow
if ($LASTEXITCODE -ne 0) { throw 'Scheduled task installation failed.' }
Get-ScheduledTask -TaskName NTU-COOL-credit-scammer | Select-Object TaskName,State
```

`-ExecutionPolicy Bypass` 僅套用這次子程序，不改整台電腦的執行原則；組織政策仍可能阻擋。安裝程式保留同名、不同 checkout 的排程並回報錯誤。每台電腦目前只支援這個固定名稱的一個安裝，不能讓多個 checkout 搶同一排程。

排程以目前帳戶、Interactive／Limited、隱藏 PowerShell 視窗執行；登入時啟動，`-StartNow` 立即啟動。需持續開機、登入、連網且不睡眠；是否調整睡眠由使用者的電源需求決定。目前不是開機後無人登入也會執行的 Windows service。[OpenAI Windows 原生環境說明](https://learn.chatgpt.com/docs/windows/windows-sandbox)

排程的 Python 會繼承該帳戶的環境。若手動可以找到 Node／Codex，排程卻找不到，檢查是否只在目前 shell 暫時設定 PATH，或 Node 版本管理器尚未為登入帳戶設定固定版本；修正後重新啟動排程驗證。

需要前景診斷時，先停止排程，再執行：

```powershell
.venv\Scripts\python.exe -m credit_scammer run
```

前景程式用 Ctrl+C 或另一個 shell 的 `python -m credit_scammer stop` 正常停止。`run --once` 只掃描並處理作業，不播放影片，也不安裝排程。不要以 `Start-Process` 隨意另起一個 worker，造成無法追蹤的背景實例。

## 8. 運行驗收

啟動後等候新的 health 檔，並觀察至少兩次 heartbeat（間隔約 30 秒）：

```powershell
.venv\Scripts\python.exe -m credit_scammer status
Get-ScheduledTask -TaskName NTU-COOL-credit-scammer | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName NTU-COOL-credit-scammer |
    Select-Object LastRunTime,LastTaskResult
```

| 驗收項目 | 完成證據 |
| --- | --- |
| 工具與依賴 | 記錄 git commit、Python／Node／npm／Codex 版本；pip check、pytest、ruff 通過。 |
| COOL 登入 | auth 的 `session_state=ready`，scan 可讀取自己的學生課程。 |
| 模型 | 第 5 節固定 `gpt-6.1-sol` solver smoke 成功；不可由登入成功推論。 |
| 巡檢 | 手動 scan 成功；常駐的 `last_scan` 持續更新。至少跨過下一個 600 秒排程，才宣稱已驗證定期巡檢。 |
| 常駐 | 排程 `Running`；health 的 `process_state=running`、`session_state=ready`、`stale=false`，heartbeat 持續更新。 |
| Discord | 若有啟用，安裝測試收到 server receipt，status 的 `discord_error` 為 null，頻道實際可見 embed。 |
| 影片 | 有任務時可見 playing／verifying 等 phase；真實正常播放累積平台紀錄。沒有影片任務時標記「尚無可驗證任務」。 |
| 交付 | 只有提交回執確認後才說作業已交；影片播完會通知，平台尚未確認則明示待核對。尚無真實結果時不宣稱已驗證交付。 |

`LastTaskResult` 不是唯一成功標準，執行中的工作可能回報執行中狀態碼。先看 task state 與新鮮 heartbeat。`status` 輸出可能包含私人課程／任務資料；Codex 的最終回報只摘要上述驗收欄位。

影片斷線、播放器失效或 60 秒停滯後，在 5／15／45 秒重接；讀取平台最早缺口續播，三次失敗則 10 分鐘後自動再試。問題通知按課程／原因合併冷卻 60 分鐘；完成結果逐支通知並去重。播放器 `ended` 與平台已確認完成不同，不能宣稱所有影片必達 100%。

檔案用途：

| 路徑 | 用途 |
| --- | --- |
| `data/health.json` | 每約 30 秒更新的健康狀態。 |
| `data/follow-ups.json` | 暫時略過的任務與之後詢問／處理事項。 |
| `data/state.sqlite3` | 工作佇列、lease、提交 intent 與回執狀態。 |
| `data/notifications.json` | Discord 送達去重與最近通知錯誤。 |
| `data/logs/worker.jsonl`／`supervisor.log` | 程式事件與排程 wrapper 記錄。 |
| `data/tasks/`／`delivery-drafts/` | 題目處理產物、答案、證據與未提交草稿。 |
| `playwright/.auth/` | 私人的登入 state 與身分 metadata。 |

## 9. 停止、更新與常見問題

正常停止排程：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\StopWorker.ps1
```

腳本先要求正常結束、保存進度與提交狀態，最多等待約 40 秒再停止排程。若原本是自己啟動的前景程序，使用 `stop` 並確認該程序已退出；StopWorker 的排程檢查不能證明任意前景程序已停止。不要刪除 `worker.lock` 或 SQLite 來停止程序。

更新已存在、沒有未提交程式變更的 checkout：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\StopWorker.ps1
git status --short
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw 'Update needs investigation; preserve local changes.' }
.venv\Scripts\python.exe -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Dependency update failed.' }
.venv\Scripts\python.exe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Browser update failed.' }
# Run the checks in section 5 before restarting.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\InstallTask.ps1 -StartNow
```

| 現象／error_code | 處理 |
| --- | --- |
| `codex_not_installed`／`codex_launcher_unavailable` | npm 安裝 CLI，核對 Node、PATH、相鄰的 codex.js；確認排程使用同一 Windows 帳戶。 |
| `config_invalid` | 只看回報的 fields；補本機設定，檢查環境變數覆蓋與固定模型／600 秒限制。 |
| `interactive_required`／`password_required`／`identity_unverified` | 停常駐，檢查本機憑證，執行可見 `auth`；完成額外驗證後再啟動。 |
| `instance_running` | 查找同一 checkout 的程序／排程並正常停止；不能同時執行 run、scan、auth、notify、retry、backup。status 可在運行中讀取。 |
| `local_data_unavailable` | 初次 auth／scan 前沒有 health 檔屬正常；已運行過則檢查檔案、路徑與權限，保留原資料。 |
| `video_reconnect_delayed` | 正在 10 分鐘延後重試；先看網路、影片來源與登入，不需逐支手動 retry。 |
| `played_unverified`／`progress_unavailable` | 已播完但平台尚未確認；看影片證據與缺口，不能改 DB 冒充完成。 |
| `notification_delivery_unconfirmed` | 檢查本機 Webhook、網路與頻道；重試可能發生伺服器已收但回應遺失的重複，不能宣稱 exactly-once。 |
| `missing_problem_material`／`runner_unavailable` | 安裝不會補出缺題目或程式 runner；保留待詢問事項，取得資料後才 retry。 |
| `partial` scan | 核對 errors／last_scan；大課程量可能達 480 秒 scan budget，不能把不完整巡檢說成成功。 |
| 同名排程屬於另一個 checkout | 保留該排程，選用原 checkout，或由使用者決定搬移；不要直接強制覆蓋。 |

目前 CLI 以 10 回報設定錯誤；20 回報互動驗證類別；30 回報 transient WorkflowError；40 回報其他工作流程或本機資料錯誤。排程 wrapper 僅對 exit 30 在 15／60／300 秒有限重啟，其他代碼結束。查看具體 error_code，不能只憑退出碼判斷原因。

補送、重發與個別重試都先停止常駐：

```powershell
.venv\Scripts\python.exe -m credit_scammer notify
# Explicitly resend existing results once only when requested.
.venv\Scripts\python.exe -m credit_scammer notify --resend
# Replace the placeholder with an actual job_id from local status.
.venv\Scripts\python.exe -m credit_scammer retry JOB_ID
```

目前沒有獨立 `reconcile` 命令。未決提交的 `retry` 會保留 intent 並核對回執，不能藉由刪除任務重新提交。

## 10. 同帳號換機／備份還原

本機單實例鎖不跨電腦。**同一 NTU 帳號同時只讓一台電腦執行 worker**；兩台各有自己的 SQLite 時無法互相排除提交或觀看操作。不同使用者則各自建立 .env、登入與資料。

### 舊機：停止、停用排程、備份

以下範例使用預設 `data/`，備份放在 repository 外的私有本機目錄。備份含私人課程與答案，不透過公開 Git 或 Discord 傳送。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\StopWorker.ps1
Disable-ScheduledTask -TaskName NTU-COOL-credit-scammer | Out-Null
$migrationBackup = Join-Path 'C:\NTU-COOL-backup' (Get-Date -Format 'yyyyMMdd-HHmmss')
.venv\Scripts\python.exe -m credit_scammer backup $migrationBackup
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; do not migrate.' }
if (Test-Path -LiteralPath data/notifications.json) {
    Copy-Item -LiteralPath data/notifications.json -Destination $migrationBackup
}
```

`backup` 使用 SQLite backup API，包含 `state.sqlite3`、tasks、snapshots、attachments、videos；**目前不包含 notifications.json**，上方額外複製它才能保留送達去重。此步不包含 .env、COOL Cookie 或 Codex OAuth。透過使用者選定的私人檔案管道搬移備份，另在新機使用本機對話框填設定及登入。

### 新機：在 auth／scan 前還原到空白 data

先完成第 3 節安裝與第 4 節本機設定。下例把搬過來的備份假定放在 `C:\NTU-COOL-backup\incoming`；改成真實位置。新機 worker 尚未啟動，目標 data 必須是空的，避免合併兩份不同的提交紀錄。

```powershell
$migrationSource = (Resolve-Path -LiteralPath 'C:\NTU-COOL-backup\incoming').Path
if (-not (Test-Path -LiteralPath (Join-Path $migrationSource 'state.sqlite3'))) {
    throw 'Backup database missing.'
}
if ((Test-Path -LiteralPath data) -and (Get-ChildItem -LiteralPath data -Force | Select-Object -First 1)) {
    throw 'Destination data is not empty; preserve it and investigate before restoring.'
}
New-Item -ItemType Directory -Path data -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $migrationSource 'state.sqlite3') -Destination data/state.sqlite3
foreach ($artifactFolder in @('tasks','snapshots','attachments','videos')) {
    $migrationArtifact = Join-Path $migrationSource $artifactFolder
    if (Test-Path -LiteralPath $migrationArtifact) {
        Copy-Item -LiteralPath $migrationArtifact -Destination data -Recurse
    }
}
$migrationNotifications = Join-Path $migrationSource 'notifications.json'
if (Test-Path -LiteralPath $migrationNotifications) {
    Copy-Item -LiteralPath $migrationNotifications -Destination data/notifications.json
}
```

確認 `.env` 的帳號與備份來源相同，再完成 auth、模型／掃描／Discord 驗證與排程啟動。新機不需要沿用舊機路徑，任務產物使用 data 內的相對路徑；若使用自訂 DATA_DIR／DATABASE_PATH，上述搬移路徑必須對應調整。

已記錄的提交 intent 應由原任務恢復核對；不要新建資料庫或清空任務來逃過核對。未備份通知紀錄可能重發舊結果；不要用 `notify --resend` 當一般開機步驟。新機失敗要退回舊機時，先停止新機；若新機已產生新提交或任務狀態，須先把最新資料遷回再啟動舊機，不能直接使用過時備份。

## 11. Codex 最終交接格式

回報以下內容即可，不附 .env／auth／課程清單／原始日誌：

```text
部署目錄與 git commit：
Python / Node / npm / Codex CLI 版本：
依賴 / Chromium / pytest / ruff：
gpt-6.1-sol smoke：通過／阻礙原因
NTU COOL auth / scan：通過／partial 的錯誤類別
Discord：已停用／測試 receipt 與可見 embed 已確認／待處理
常駐 task / heartbeat / session state：
下一輪 600 秒巡檢：已觀察／尚未等到
影片與作業真實交付：已驗證／尚無結果可驗證
舊機停用與資料／通知還原：不適用／已確認／尚未確認
剩餘待處理事項與本機診斷路徑：
```

安裝成功代表上述項目有各自的證據；目前尚無隔離程式 runner、任意附件辨識或所有題型完成保證。24×7 的驗收還需要長時間觀察與電腦持續可用；單次啟動成功不是 24 小時 soak 證據。
