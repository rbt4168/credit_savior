from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import LeaseLost, WorkflowError
from .models import Assignment, Course, Video, now_ms

STATES = {"queued", "running", "retry_wait", "needs_input", "failed", "succeeded", "cancelled"}
PHASES = {
    "assignment": {"discovered", "preparing", "solving", "validating", "ready", "submitting",
                   "reconciling", "submitted"},
    "video": {"discovered", "opening", "playing", "verifying", "played_unverified", "completed"},
}
PENDING = ("prepared", "dispatching", "uncertain")


class Store:
    def __init__(self, path: Path, clock=now_ms):
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise WorkflowError("schema_unknown")
            schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            self.db.executescript("BEGIN IMMEDIATE;\n" + schema + "\nPRAGMA user_version=1;\nCOMMIT;")
        elif version != 1:
            raise WorkflowError("schema_version_unsupported")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def upsert_course(self, course: Course):
        self.db.execute(
            """INSERT INTO courses VALUES (?,?,?,1,'accessible',?)
            ON CONFLICT(course_id) DO UPDATE SET name=excluded.name, url=excluded.url,
            enabled=1, access_state='accessible', last_seen_at_ms=excluded.last_seen_at_ms""",
            (course.course_id, course.name, course.url, self.clock()),
        )

    def upsert_assignment(self, assignment: Assignment, payload_path: str):
        a = assignment
        with self.transaction() as db:
            db.execute(
                """INSERT INTO assignments(course_id,assignment_id,title,url,current_content_hash,
                snapshot_path,due_at_ms,unlock_at_ms,lock_at_ms,submission_presence,
                submitted_at_ms,last_seen_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(course_id,assignment_id) DO UPDATE SET
                title=excluded.title,url=excluded.url,current_content_hash=excluded.current_content_hash,
                snapshot_path=excluded.snapshot_path,due_at_ms=excluded.due_at_ms,
                unlock_at_ms=excluded.unlock_at_ms,lock_at_ms=excluded.lock_at_ms,
                submission_presence=excluded.submission_presence,
                submitted_at_ms=excluded.submitted_at_ms,last_seen_at_ms=excluded.last_seen_at_ms""",
                (a.course_id, a.assignment_id, a.title, a.url, a.content_hash, payload_path,
                 a.due_at_ms, a.unlock_at_ms, a.lock_at_ms, a.submission.presence,
                 a.submission.submitted_at_ms, self.clock()),
            )
            db.execute(
                """UPDATE jobs SET state='cancelled',error_code='superseded',updated_at_ms=?
                WHERE kind='assignment' AND course_id=? AND subject_id=? AND revision<>?
                AND state IN ('queued','retry_wait','running','needs_input')
                AND job_id NOT IN (SELECT job_id FROM submission_attempts
                    WHERE state IN ('dispatching','uncertain'))""",
                (self.clock(), a.course_id, a.assignment_id, a.content_hash),
            )
            db.execute(
                """UPDATE submission_attempts SET state='abandoned'
                WHERE state='prepared' AND job_id IN
                (SELECT job_id FROM jobs WHERE state='cancelled')"""
            )

    def upsert_video(self, video: Video):
        self.db.execute(
            """INSERT INTO videos(course_id,video_id,url,player_kind,source_revision,duration_s,
            progress_state,last_seen_at_ms) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(course_id,video_id) DO UPDATE SET url=excluded.url,
            player_kind=excluded.player_kind,source_revision=excluded.source_revision,
            duration_s=excluded.duration_s,progress_state=excluded.progress_state,
            last_position_s=CASE WHEN videos.source_revision=excluded.source_revision
                THEN videos.last_position_s ELSE 0 END,last_seen_at_ms=excluded.last_seen_at_ms""",
            (video.course_id, video.video_id, video.url, video.player_kind, video.revision,
             video.duration_s, video.completion, self.clock()),
        )

    def enqueue(self, kind: str, course_id: str, subject_id: str, revision: str,
                payload_path: str, *, state="queued", error_code=None, due_at_ms=None,
                checkpoint=None, retry_after_ms=None) -> str:
        if kind not in PHASES or state not in STATES:
            raise ValueError("invalid_job")
        with self.transaction() as db:
            table, id_column = ("assignments", "assignment_id") if kind == "assignment" else (
                "videos", "video_id")
            if not db.execute(f"SELECT 1 FROM {table} WHERE course_id=? AND {id_column}=?",
                              (course_id, subject_id)).fetchone():
                raise ValueError("subject_missing")
            existing = db.execute(
                "SELECT job_id FROM jobs WHERE kind=? AND course_id=? AND subject_id=? AND revision=?",
                (kind, course_id, subject_id, revision)).fetchone()
            if existing:
                return existing["job_id"]
            job_id = str(uuid4())
            db.execute(
                """INSERT INTO jobs(job_id,kind,course_id,subject_id,revision,state,phase,
                priority_due_at_ms,payload_path,checkpoint_json,retry_after_ms,error_code,
                created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,'discovered',?,?,?,?,?,?,?)""",
                (job_id, kind, course_id, subject_id, revision, state, due_at_ms, payload_path,
                 json.dumps(checkpoint or {}), retry_after_ms, error_code, self.clock(), self.clock()),
            )
            return job_id

    @staticmethod
    def decode(row):
        if row is None:
            return None
        job = dict(row)
        job["checkpoint"] = json.loads(job.pop("checkpoint_json"))
        return job

    def get(self, job_id: str) -> dict:
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise WorkflowError("job_not_found")
        return self.decode(row)

    def claim(self, kind: str, owner: str) -> dict | None:
        at = self.clock()
        with self.transaction() as db:
            row = db.execute(
                """SELECT * FROM jobs WHERE kind=? AND (state='queued' OR
                (state='retry_wait' AND retry_after_ms<=?))
                ORDER BY CASE WHEN phase='reconciling' THEN 0 ELSE 1 END,
                priority_due_at_ms IS NULL,priority_due_at_ms,
                CASE WHEN kind='video' THEN course_id ELSE '' END,
                CASE WHEN kind='video' THEN json_extract(checkpoint_json,'$.playlist_order') ELSE 0 END,
                created_at_ms,job_id LIMIT 1""",
                (kind, at)).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE jobs SET state='running',lease_owner=?,lease_until_ms=?,
                run_attempt=run_attempt+1,updated_at_ms=? WHERE job_id=?""",
                (owner, at + 120_000, at, row["job_id"]),
            )
        return self.get(row["job_id"])

    def assert_owned(self, job_id: str, owner: str):
        row = self.db.execute(
            """SELECT 1 FROM jobs WHERE job_id=? AND state='running'
            AND lease_owner=? AND lease_until_ms>?""", (job_id, owner, self.clock())).fetchone()
        if row is None:
            raise LeaseLost()

    def update(self, job_id: str, owner: str, *, phase=None, state="running",
               checkpoint=None, error_code=None, retry_after_ms=None):
        job = self.get(job_id)
        phase = phase or job["phase"]
        if state not in STATES or phase not in PHASES[job["kind"]]:
            raise ValueError("invalid_transition")
        if state == "succeeded" and phase != (
                "submitted" if job["kind"] == "assignment" else "completed"):
            raise ValueError("completion_phase_required")
        values = {
            "phase": phase, "state": state, "updated_at_ms": self.clock(),
            "error_code": error_code, "retry_after_ms": retry_after_ms,
        }
        if checkpoint is not None:
            values["checkpoint_json"] = json.dumps(checkpoint)
        if state != "running":
            values.update(lease_owner=None, lease_until_ms=None)
        result = self.db.execute(
            "UPDATE jobs SET " + ",".join(key + "=?" for key in values)
            + " WHERE job_id=? AND lease_owner=? AND state='running' AND lease_until_ms>?",
            (*values.values(), job_id, owner, self.clock()),
        )
        if result.rowcount != 1:
            raise LeaseLost()
        return self.get(job_id)

    def renew(self, job_id: str, owner: str) -> bool:
        at = self.clock()
        return self.db.execute(
            """UPDATE jobs SET lease_until_ms=? WHERE job_id=? AND state='running'
            AND lease_owner=? AND lease_until_ms>?""",
            (at + 120_000, job_id, owner, at)).rowcount == 1

    def pending(self, course_id: str, assignment_id: str):
        row = self.db.execute(
            """SELECT * FROM submission_attempts WHERE course_id=? AND assignment_id=?
            AND state IN ('prepared','dispatching','uncertain')""",
            (course_id, assignment_id)).fetchone()
        return dict(row) if row else None

    def cancel_subject(self, course_id, subject_id, reason):
        with self.transaction() as db:
            db.execute("""UPDATE jobs SET state='cancelled',error_code=?,lease_owner=NULL,
                lease_until_ms=NULL,updated_at_ms=? WHERE kind='assignment' AND course_id=?
                AND subject_id=? AND state IN ('queued','retry_wait','needs_input','failed')
                AND job_id NOT IN (SELECT job_id FROM submission_attempts
                    WHERE state IN ('dispatching','uncertain'))""",
                (reason, self.clock(), course_id, subject_id))
            db.execute("""UPDATE submission_attempts SET state='abandoned' WHERE state='prepared'
                AND job_id IN (SELECT job_id FROM jobs WHERE state='cancelled')""")

    def prepare_attempt(self, job_id: str, owner: str, answer_hash: str) -> dict:
        job = self.get(job_id)
        with self.transaction() as db:
            self.assert_owned(job_id, owner)
            existing = self.pending(job["course_id"], job["subject_id"])
            if existing:
                if existing["job_id"] != job_id or existing["answer_hash"] != answer_hash:
                    raise WorkflowError("submission_conflict")
                return existing
            attempt_id = str(uuid4())
            db.execute(
                """INSERT INTO submission_attempts(attempt_id,job_id,course_id,assignment_id,
                answer_hash,state,started_at_ms) VALUES (?,?,?,?,?,'prepared',?)""",
                (attempt_id, job_id, job["course_id"], job["subject_id"], answer_hash, self.clock()),
            )
        return self.pending(job["course_id"], job["subject_id"])

    def dispatch(self, attempt_id: str, job_id: str, owner: str):
        with self.transaction() as db:
            self.assert_owned(job_id, owner)
            changed = db.execute(
                """UPDATE submission_attempts SET state='dispatching'
                WHERE attempt_id=? AND job_id=? AND state='prepared'""",
                (attempt_id, job_id)).rowcount
            if changed != 1:
                raise WorkflowError("submission_uncertain")
            self.update(job_id, owner, phase="submitting")

    def settle(self, attempt_id: str, job_id: str, owner: str, *, confirmed: bool,
               evidence_path: str, platform_attempt_id=None):
        with self.transaction() as db:
            self.assert_owned(job_id, owner)
            changed = db.execute(
                """UPDATE submission_attempts SET state=?,evidence_path=?,platform_attempt_id=?,
                confirmed_at_ms=?,error_code=? WHERE attempt_id=? AND job_id=?
                AND state IN ('dispatching','uncertain')""",
                ("confirmed" if confirmed else "uncertain", evidence_path, platform_attempt_id,
                 self.clock() if confirmed else None,
                 None if confirmed else "submission_uncertain", attempt_id, job_id),
            ).rowcount
            if changed != 1:
                raise WorkflowError('invalid_receipt_transition')
            self.update(job_id, owner,
                        phase="submitted" if confirmed else "reconciling",
                        state="succeeded" if confirmed else "needs_input",
                        error_code=None if confirmed else "submission_uncertain")

    def schedule_reconciliation(self, course_id: str, assignment_id: str):
        self.db.execute(
            """UPDATE jobs SET state='queued',phase='reconciling',error_code=NULL,
            lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?
            WHERE state IN ('needs_input','failed','retry_wait') AND job_id IN
            (SELECT job_id FROM submission_attempts WHERE course_id=? AND assignment_id=?
                AND state IN ('dispatching','uncertain'))""",
            (self.clock(), course_id, assignment_id),
        )

    def recover(self):
        with self.transaction() as db:
            db.execute(
                """UPDATE jobs SET state='queued',phase='reconciling',
                lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=?
                WHERE state='running' AND job_id IN
                (SELECT job_id FROM submission_attempts WHERE state IN ('dispatching','uncertain'))""",
                (self.clock(),))
            db.execute(
                """UPDATE jobs SET state='queued',lease_owner=NULL,lease_until_ms=NULL,
                updated_at_ms=? WHERE state='running'""", (self.clock(),))
            db.execute("UPDATE scan_runs SET state='interrupted',completed_at_ms=? WHERE state='running'",
                       (self.clock(),))
            db.execute("""UPDATE jobs SET state='queued',error_code=NULL,updated_at_ms=?
                WHERE kind='video' AND state='failed' AND error_code='worker_unexpected_error'
                AND json_extract(checkpoint_json,'$.error_class')='TargetClosedError'""",
                (self.clock(),))
            db.execute("""UPDATE jobs SET state='queued',error_code=NULL,retry_after_ms=NULL,
                updated_at_ms=? WHERE kind='video' AND state IN ('failed','needs_input')
                AND (error_code IN ('browser_action_unavailable','playback_stalled',
                    'video_player_unavailable','browser_closed','duration_unknown')
                    OR (error_code='worker_unexpected_error' AND
                        (json_extract(checkpoint_json,'$.error_class') IN
                            ('Error','TimeoutError','TargetClosedError')
                        OR json_extract(checkpoint_json,'$.error_class') IS NULL)))""",
                (self.clock(),))

    def retry(self, job_id: str):
        job = self.get(job_id)
        if job["state"] not in ("failed", "needs_input", "retry_wait"):
            raise WorkflowError("job_not_retryable")
        pending = self.pending(job["course_id"], job["subject_id"])
        phase = "reconciling" if pending and pending["state"] != "prepared" else job["phase"]
        self.db.execute(
            """UPDATE jobs SET state='queued',phase=?,retry_after_ms=NULL,
            error_code=NULL,updated_at_ms=? WHERE job_id=?""", (phase, self.clock(), job_id))

    def start_scan(self, scheduled_at_ms: int) -> str:
        scan_id = str(uuid4())
        self.db.execute("INSERT INTO scan_runs VALUES (?,?,?,NULL,'running',NULL)",
                        (scan_id, scheduled_at_ms, self.clock()))
        return scan_id

    def record_course_scan(self, scan_id: str, course_id: str, state: str, assignments=0,
                           videos=0, error_code=None):
        self.db.execute(
            "INSERT OR REPLACE INTO scan_course_results VALUES (?,?,?,?,?,?)",
            (scan_id, course_id, state, assignments, videos, error_code))

    def finish_scan(self, scan_id: str, state: str, error_code=None):
        self.db.execute("UPDATE scan_runs SET completed_at_ms=?,state=?,error_code=? WHERE scan_id=?",
                        (self.clock(), state, error_code, scan_id))

    def summary(self) -> dict:
        counts = {row["state"]: row["count"] for row in self.db.execute(
            "SELECT state,count(*) AS count FROM jobs GROUP BY state")}
        last = self.db.execute("SELECT * FROM scan_runs ORDER BY started_at_ms DESC LIMIT 1").fetchone()
        last_ok = self.db.execute(
            "SELECT started_at_ms FROM scan_runs WHERE state='succeeded' ORDER BY started_at_ms DESC LIMIT 1"
        ).fetchone()
        return {"job_counts": counts, "last_scan": dict(last) if last else None,
                'job_counts_by_kind': [dict(r) for r in self.db.execute(
                    'SELECT kind,state,count(*) AS count FROM jobs GROUP BY kind,state')],
                'blocked_jobs': [dict(r) for r in self.db.execute(
                    """SELECT job_id,kind,course_id,subject_id,phase,error_code FROM jobs
                    WHERE state IN ('needs_input','failed') ORDER BY updated_at_ms DESC LIMIT 30""")],
                "last_scan_succeeded_at_ms": last_ok[0] if last_ok else None,
                "pending_submission_count": self.db.execute(
                    "SELECT count(*) FROM submission_attempts WHERE state IN ('dispatching','uncertain')"
                ).fetchone()[0]}

    def list_jobs(self):
        return [self.decode(row) for row in self.db.execute(
            "SELECT * FROM jobs ORDER BY created_at_ms,job_id")]

    def subject_info(self, job):
        row = self.db.execute("""SELECT c.name AS course_name,a.title,a.url
            FROM assignments a JOIN courses c ON c.course_id=a.course_id
            WHERE a.course_id=? AND a.assignment_id=?""",
            (job['course_id'], job['subject_id'])).fetchone()
        if row is None:
            raise WorkflowError('subject_missing')
        return dict(row)

    def course_name(self, course_id):
        row = self.db.execute('SELECT name FROM courses WHERE course_id=?', (course_id,)).fetchone()
        return row['name'] if row else '課程名稱待取得'

    def backup(self, destination: Path):
        if destination.exists():
            raise WorkflowError("backup_destination_exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.db.backup(target)
