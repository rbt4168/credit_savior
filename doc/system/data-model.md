# 資料模型與交易設計

[實作計畫](../plan.md) · [系統架構](architecture.md) · [作業流程](assignments.md) · [影片流程](videos.md)

## 時間、識別與序列化

時間一律為 UTC epoch milliseconds，欄位使用 `_at_ms`／`_until_ms`；只有排程等待採 monotonic clock。平台 ID 保存為字串，避免依賴整數寬度。內部 job ID、attempt ID、scan ID 與 owner ID 使用 UUID。

JSON 使用 UTF-8，帶 `schema_version: 1`。讀取時驗證必要欄位、型別及 enum；不直接把任意 JSON 轉成瀏覽器操作。任務產物路徑保存為相對 DATA_DIR 的路徑。

## 核心 DTO

| 型別 | 欄位 |
| --- | --- |
| AssignmentRef | course_id、assignment_id、url。 |
| AssignmentSnapshot | ref、title、prompt_text、rubric_text、submission_types、allowed_extensions、max_upload_bytes、due_at_ms、unlock_at_ms、lock_at_ms、attachments、submission_observation、observed_at_ms。 |
| Attachment | platform_file_id、display_name、local_path、byte_size、sha256、source_version（可為 null）。 |
| ProblemBundle | job_id、snapshot_path、content_hash、materials、output_requirements。 |
| AnswerArtifact | kind（text／files）、text_path、files、content_hash、solver_model、generated_at_ms。 |
| ValidationResult | passed、checks、errors、validated_at_ms；每個 check 帶名稱、結果與本機 evidence_path。 |
| VideoSnapshot | course_id、video_id、url、player_kind、duration_s、source_revision、observed_at_ms。 |
| SubmissionObservation | presence、ownership、platform_attempt_id、submitted_at_ms、files、text_digest、evidence_path。 |
| ProgressObservation | completion（complete／incomplete／unknown）、coverage_ratio、covered_ranges、observed_at_ms、evidence_path。 |

答卷 content_hash 是答案產物雜湊；題目 content_hash 是題目與附件雜湊。兩者使用不同欄位名稱或 DTO，不混用為提交去重鍵。

## 題目版本與去重鍵

`content_hash = SHA256(canonical_problem_json)`。canonical JSON 對 key 排序，題目使用 Unicode NFC、換行正規化；保留程式碼的空白與大小寫。內容包括題目、rubric、提交格式、期限與依 platform_file_id 排序的附件 SHA-256。

不納入動態頁面時間、cookie、URL 的驗證 query 或繳交狀態。若附件有可靠 source_version，版本不變時可重用已下載雜湊；版本不可靠時重新下載計算，不能只比較檔名。下載失敗的作業不產生「完整題目」雜湊，先記錄掃描錯誤。

作業 job 去重鍵為 `(kind, course_id, subject_id, revision)`，revision 使用完整 content_hash。影片 revision 使用已驗證的來源 ID／版本；同一 ID 的來源替換可建立新 job，僅標題改名不重播。

## SQLite schema v1

以下 DDL 為待實作 schema，並未建立真實資料庫。啟動時使用 foreign_keys=ON、journal_mode=WAL、busy_timeout=1000；Store 的所有交易都明確結束。SQLite 介面與交易控制見[Python 官方文件](https://docs.python.org/3/library/sqlite3.html)。

```sql
CREATE TABLE courses (
    course_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    access_state TEXT NOT NULL,
    last_seen_at_ms INTEGER NOT NULL
);

CREATE TABLE assignments (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    assignment_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    current_content_hash TEXT,
    snapshot_path TEXT,
    due_at_ms INTEGER,
    unlock_at_ms INTEGER,
    lock_at_ms INTEGER,
    submission_presence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (submission_presence IN ('present', 'absent', 'unknown')),
    submitted_at_ms INTEGER,
    last_seen_at_ms INTEGER NOT NULL,
    PRIMARY KEY (course_id, assignment_id)
);

CREATE TABLE videos (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    video_id TEXT NOT NULL,
    url TEXT NOT NULL,
    player_kind TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    duration_s REAL CHECK (duration_s IS NULL OR duration_s >= 0),
    last_position_s REAL NOT NULL DEFAULT 0 CHECK (last_position_s >= 0),
    progress_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (progress_state IN ('complete', 'incomplete', 'unknown')),
    coverage_ratio REAL CHECK (coverage_ratio IS NULL
        OR coverage_ratio BETWEEN 0 AND 1),
    progress_evidence_path TEXT,
    last_seen_at_ms INTEGER NOT NULL,
    PRIMARY KEY (course_id, video_id)
);

CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('assignment', 'video')),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    subject_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'queued', 'running', 'retry_wait', 'needs_input',
        'failed', 'succeeded', 'cancelled')),
    phase TEXT NOT NULL,
    priority_due_at_ms INTEGER,
    payload_path TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    run_attempt INTEGER NOT NULL DEFAULT 0 CHECK (run_attempt >= 0),
    retry_after_ms INTEGER,
    lease_owner TEXT,
    lease_until_ms INTEGER,
    error_code TEXT,
    error_detail TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (kind, course_id, subject_id, revision)
);

CREATE TABLE submission_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    course_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    answer_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'prepared', 'dispatching', 'confirmed', 'rejected', 'uncertain', 'abandoned')),
    platform_attempt_id TEXT,
    started_at_ms INTEGER NOT NULL,
    confirmed_at_ms INTEGER,
    evidence_path TEXT,
    error_code TEXT,
    FOREIGN KEY (course_id, assignment_id)
        REFERENCES assignments(course_id, assignment_id)
);

CREATE UNIQUE INDEX one_pending_submission_per_assignment
ON submission_attempts(course_id, assignment_id)
WHERE state IN ('prepared', 'dispatching', 'uncertain');

CREATE TABLE scan_runs (
    scan_id TEXT PRIMARY KEY,
    scheduled_at_ms INTEGER NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    state TEXT NOT NULL CHECK (state IN (
        'running', 'succeeded', 'partial', 'failed', 'interrupted')),
    error_code TEXT
);

CREATE TABLE scan_course_results (
    scan_id TEXT NOT NULL REFERENCES scan_runs(scan_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    state TEXT NOT NULL CHECK (state IN (
        'succeeded', 'failed', 'budget_exceeded')),
    assignment_count INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    PRIMARY KEY (scan_id, course_id)
);

CREATE INDEX eligible_jobs
ON jobs(kind, state, retry_after_ms, priority_due_at_ms, created_at_ms);
CREATE INDEX submissions_by_job ON submission_attempts(job_id);
```

jobs.subject_id 對應的資料表由 kind 決定，因此 schema 沒有多型 foreign key；Store.enqueue 必須在同一交易確認 subject 存在。phase、payload 與 checkpoint 的 kind 對應也由 Store 驗證。

## 工作 claim 與 lease

claim 使用短 `BEGIN IMMEDIATE` 交易：選出可執行的 queued／到期 retry_wait job → 條件更新成 running，設定 owner、120 秒 lease 並增加 run_attempt → COMMIT。沒有符合任務回傳 null；不得先 SELECT、結束交易後再無條件 UPDATE。

作業按 due_at_ms 有值者優先、期限由近到遠、created_at_ms、job_id 排序；影片按 course 設定順序與課程播放清單順序，由 checkpoint 保存 playlist_order。worker 每 30 秒續租，即使模型或影片仍在等待。

checkpoint／續租／結束更新都帶 `WHERE job_id=? AND lease_owner=? AND state='running'`。零筆更新表示持有權已失效，立即停止任何新的寫入操作。

同一程序內 lease 到期時先取消並等待舊 worker 退出，不能與舊 worker 同時回收；程序重啟後由單實例鎖確保舊程序已退出，再回收。平台最終提交已送出者回收後進 reconciling，而非重新提交。

## 泛用狀態與專屬 phase

| state | 含義 |
| --- | --- |
| queued | 可 claim；未開始或等待持續流程。 |
| running | worker 持有 lease；phase 指出具體步驟。 |
| retry_wait | 已保存階段，直到 retry_after_ms 才可 claim。 |
| needs_input | 缺資料、型別未支援或結果不明；不自動重新提交。 |
| failed | 已達有限重試上限或不可恢復錯誤。 |
| succeeded | 有足夠平台證據證明完成。 |
| cancelled | 題目已被新版本取代、課程停用或已有外部提交。 |

assignment phase：discovered、preparing、solving、validating、ready、submitting、reconciling、submitted。video phase：discovered、opening、playing、verifying、played_unverified、completed。state 與 phase 的有效組合由各 worker 的狀態轉移表控制。

## Checkpoint JSON 契約

共同欄位為 schema_version、session_generation、stage_retry_counts。assignment checkpoint 另保存 problem_hash、model_attempts、answer_manifest_path、validation_path、attempt_id；video checkpoint 保存 source_revision、playlist_order、last_position_s、last_position_observed_at_ms、reopen_count、gap_replay_count。

model_attempts 與 retry counter 必須在發出請求前持久化；position 只在正常觀察前進時更新。DB 的 state／phase 是權威狀態，不以 checkpoint 中的文字覆寫。checkpoint.json 為本機診斷副本，資料庫 checkpoint_json 才是恢復輸入。

相同 subject 的舊版本歷史保存在 immutable job payload，不依賴 assignments.current_content_hash 取回舊題目。所有 artifact manifest 帶檔名、大小及 SHA-256，重啟時重新核對。

## 檔案與交易一致性

```text
data/
  state.sqlite3
  health.json
  logs/worker.jsonl
  tasks/<job_id>/
    problem.json
    attachments/<file_id>/<generated_name>
    answer/manifest.json
    answer/<generated_name>
    validation.json
    checkpoint.json
    receipts/<attempt_id>.json
    diagnostics/<event_id>.png
```

任務快照以暫存檔寫入、驗證雜湊後原子改名，再提交指向該檔案的 DB 交易。DB 交易失敗留下的孤立檔案可在啟動清理；DB 有引用但檔案遺失時不得提交，改 needs_input/artifact_missing。

提交 intent 必須先 COMMIT 為 dispatching，才點擊最終提交；回執先保存檔案，再更新 attempt 與 job。崩潰可能發生在任一邊界，因此恢復以平台觀察為準，不以本機有無答案檔判定成功。

## Schema 升級與備份

首次建立 schema 後設定 PRAGMA user_version=1。已有較新版本時停止啟動；版本不符不能自動刪除資料庫重建。未來 migration 在單實例鎖內先備份，再以交易升級 schema；本次不新增虛構 migration。

規劃 backup CLI 使用 SQLite backup API；不可直接複製仍在寫入中的主資料庫檔而忽略 WAL。登入狀態不包進一般備份，答案與回執則與資料庫一起保存以便核對。
