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
