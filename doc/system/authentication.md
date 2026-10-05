# 登入與憑證

本文件保留完整設計目標。目前程式、已驗證介面與未完成項目請以 [實作狀態](implementation.md) 為準。

[實作計畫](../plan.md) · [系統架構](architecture.md) · [環境設定](configuration.md)

## 輸入與成功條件

輸入為 COOL_BASE_URL、COOL_USERNAME、COOL_PASSWORD、AUTH_STATE_PATH 與指定課程。帳密只用於登入表單；模型與一般錯誤日誌不可讀取。NTU 官方說明登入帳密同臺大 Email；目前的登入重新導向與驗證機制仍須實測。[官方說明](https://www.dlc.ntu.edu.tw/ceibaandcool/)

成功條件是頁面顯示已登入帳戶、帳戶與設定一致，且至少一個指定課程能讀取。單純 URL 改變或登入按鈕消失不足以證明成功。登入成功但課程不在權限範圍時回報 course_forbidden，不反覆重新登入。

## Session 狀態

| 狀態 | 進入原因 | 下一步 |
| --- | --- | --- |
| cold | 程序啟動，context 尚未建立。 | 載入狀態並建立 context。 |
| checking | 已載入狀態或剛完成登入。 | 查帳戶身分與指定課程。 |
| ready | 身分及課程檢查成功。 | 開放網站操作。 |
| expired | 重導至登入頁或明確驗證失效。 | 關閉 session gate，嘗試登入。 |
| authenticating | 正在帳密登入。 | 進 checking 或 interactive_required。 |
| interactive_required | MFA、CAPTCHA 或其他互動驗證。 | 保存等待原因，提示執行 auth 指令。 |
| failed | 帳密被拒或超過恢復上限。 | 停止登入重試，保留本機診斷。 |

## 首次登入與互動登入

規劃 CLI 為 python -m credit_savior auth；此指令尚未實作。它使用可見瀏覽器，完成登入後核對身分、存入狀態，再關閉；與 run 指令共用單實例鎖，避免兩個 context 同時覆寫登入狀態。

常駐程序遇到互動驗證時轉 interactive_required，暫停需要登入的工作，不讓無人值守的程序永久等待可見視窗。使用者完成 auth 後重新啟動程序；既有佇列與播放檢查點保留。

## 登入狀態保存

1. 有狀態檔時先建立 context，開啟課程頁檢查有效性。
2. JSON 損毀或狀態失效，保留一份本機診斷副本，再嘗試新登入。
3. 驗證成功才以暫存檔寫入，原子替換 AUTH_STATE_PATH；失敗不得覆寫仍可用的檔案。
4. 檔案包含 cookie、local storage 等狀態，只留本機。若實際依賴 IndexedDB，按測試結果啟用相應保存選項；session storage 要另行驗證。

Playwright 提供 storage_state 以重用登入狀態，且指出 session storage 不是自動持久化的一部分。[官方文件](https://playwright.dev/python/docs/auth)

## 恢復與並行

登入恢復最多嘗試兩次，每次 60 秒；第一次網路失敗後等待 10 秒再試。明確帳密錯誤直接 failed；互動驗證直接 interactive_required。不同於一般讀取操作，登入不使用持續指數重試，避免不停送出錯誤密碼。

恢復期間 worker 保存 phase 與 lease，不執行新網站操作。若需換成新 context，通知所有角色頁面失效；未決提交先進回執核對，影片從最後確認的檢查點重新開啟。

## 輸出與診斷

對外輸出 session state、generation、last_verified_at_ms、error_code。日誌禁止輸出密碼、cookie、token、完整驗證 URL 或表單內容。登入頁不保存 trace；必要錯誤截圖只存本機並遮蔽帳密欄位。

驗收包含首次登入、重用狀態、過期狀態、損毀狀態、帳密拒絕、互動驗證、帳戶不符及指定課程無權限。[測試矩陣](testing.md)
