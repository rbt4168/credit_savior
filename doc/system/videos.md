# 影片播放與進度確認詳細設計

本文件保留完整設計目標。目前程式、已驗證介面與未完成項目請以 [實作狀態](implementation.md) 為準。

目前的斷線恢復已實作：page/iframe 控制失效或 60 秒播放停滯時，5/15/45 秒後重接，重開影片頁或重建 browser/context/driver。登入 gate 保護其他角色頁面；同 generation 下重開影片頁不影響正在編輯的作業。重新讀取平台紀錄後由最早缺口前 5 秒續播；失敗三次後存 retry_wait，600 秒後自動再試。取消及斷線都保存最新觀察位置；來源變更與平台未確認完成仍維持原有判定。

[實作計畫](../plan.md) · [資料模型](data-model.md) · [平台整合](platform-integration.md)

## 目標與完成證據

系統建立指定課程播放清單，以正常播放累積觀看紀錄。NTU COOL 提供影片、速度調整與學習足跡；自有影片與 YouTube 播放器須各自整合。[官方功能說明](https://www.dlc.ntu.edu.tw/ntu-cool/)

本機 ended 事件與播放位置只是檢查點。完成需平台明確完成標記，或平台可讀取的全片涵蓋紀錄；平台未提供學生可讀的紀錄時標記 played_unverified，不宣稱滿進度。

## 狀態流程

| phase／state | 進入條件 | 下一步 |
| --- | --- | --- |
| discovered／queued | 影片未確認完成且 source revision 固定。 | opening。 |
| opening／running | claim 後取得 video page。 | 載入 metadata、平台進度與播放控制。 |
| playing／running | 播放成功。 | 每 10 秒採樣、每 30 秒 checkpoint。 |
| verifying／running | ended，或平台已顯示完整涵蓋。 | 有限次查平台完成紀錄。 |
| completed／succeeded | 平台證據明確。 | 保存 progress evidence，跳下一部。 |
| played_unverified／needs_input | 已播放但無可靠完成證據。 | 後續巡檢只查進度，不重新完整播放。 |
| 原 phase／retry_wait | 短暫網路或播放器錯誤且仍有額度。 | 重開該影片頁並續播。 |

## 播放器介面

```python
class PlayerAdapter:
    async def load(self, video: VideoSnapshot) -> None: ...
    async def observe(self) -> PlaybackObservation: ...
    async def play(self) -> None: ...
    async def pause(self) -> None: ...
    async def seek(self, position_s: float) -> None: ...
    async def set_rate(self, rate: float) -> None: ...
```

HTML5 與 YouTube adapter 各自解析已驗證的頁面控制，包含 iframe。不得直接讀取跨 origin iframe 的 DOM；透過 Playwright frame 與播放器提供的正常控制整合。遇到其他外部播放器標記 unsupported_player，而非猜測 API。

第一版 rate 固定 1.0；後續若要求調速，再驗證播放器允許範圍與平台紀錄。播放前等待有限 duration，超過 metadata 等待上限 30 秒仍為未知長度則 needs_input/duration_unknown。直播不支援。

## 播放佇列與續播

一次只播放一部。課程順序依設定，影片依課程單元／播放清單順序；scanner page 與 assignment page 的導航不動 video page。

opening 先查平台進度：complete 直接成功；incomplete 且有 covered_ranges 時選最早缺漏片段；unknown 時從本機最後 checkpoint 前 5 秒開始，最小為 0。checkpoint 只記錄正常前進的播放位置與 source_revision，不以播放器被 seek 後的較大位置當作觀看證據。

若來源 revision 變更，舊 checkpoint 不套用到新影片。若平台要求按單元順序解鎖，保存 blocked_by_module 與 retry_wait，下一輪重新檢查，不略過必要條件。

## 採樣與停滯處理

每 10 秒讀取 position_s、paused、buffering、ended 並續租。播放位置 60 秒未前進且非 ended 時判定 stall；buffering 不會永久豁免 stall 判定。

1. 若 paused，使用正常 play 控制恢復。
2. 60 秒未前進，重開該影片頁，重讀平台紀錄後從最早缺口前 5 秒續播。
3. 每輪重接最多三次，按 5、15、45 秒延後；保存當輪次數與累計次數，累計值不因重啟重置。
4. 當輪額度用盡存 retry_wait/video_reconnect_delayed，600 秒後自動再試，期間繼續下一部影片。
5. context 重建不是觀看完成；取得新 generation 後仍須重讀平台與播放位置。

未初始化的 YouTube/video.js 先使用播放器正常 Play 按鈕載入媒體，等待 loadedmetadata，再設定續播位置並以 1 倍速播放；避免外層覆蓋與新 load request 使 play 請求失效。

每支影片完成或播放到 ended 但平台尚未確認時，Discord 發送課程、課號、影片標題及平台連結，明確區分兩種結果。通知保存確認回執，常駐重啟不重複發送；播放到 ended 不宣稱平台已完成。

固定 30 秒保存最後正常觀察位置；正常關閉、暫停與錯誤恢復前再保存一次。可接受的中斷重播範圍為最後 checkpoint 起最多 30 秒，加上續播重疊 5 秒。

## 平台完成核對

播完後依 5、15、30、60、120 秒間隔查 ProgressObservation。優先採明確 completion=complete；只有實測證實 coverage_ratio 與全片區間語意相同時，才以 1.0 判定完成，不使用「接近 100%」的任意門檻。

若有可靠缺漏區間，最多做兩輪補播，再核對；補播與重開計數分開保存。平台回傳 incomplete 但不提供缺漏位置時不無限整片重播，改 played_unverified 並保存診斷。

觀察 unknown 時不寫 coverage_ratio=0；unknown 與 incomplete 分開。後續巡檢查到 complete，可將 played_unverified 更新成 succeeded/completed，保存新的平台證據。

## 互動題與例外

影片若含已整合的互動題，暫停播放器並交由對應題型流程；第一版尚未整合則 needs_input/interactive_question，保存位置後處理下一部。學生無觀看紀錄權限、登入失效、課程不再開放與播放器不支援各自保存獨立錯誤碼。

完成判定不修改本機時鐘、播放器時間或伺服器進度值；影片中的 seek 僅用於續播與平台明示的缺漏片段。

## 驗收

在測試站控制播放器與平台紀錄兩個獨立狀態，覆蓋：正常播放、paused、長緩衝、永久停滯、中斷續播、來源替換、平台更新延遲、ended 但紀錄未知、缺漏片段補播、跨 origin iframe 與影片播放期間巡檢。[測試設計](testing.md)
