from dataclasses import replace

import pytest

from credit_scammer.artifacts import artifact_path
from credit_scammer.config import Config
from credit_scammer.errors import ConfigError, LeaseLost, WorkflowError
from credit_scammer.models import Attachment, Submission
from credit_scammer.progress import coverage
from credit_scammer.scanner import next_tick
from credit_scammer.store import Store


def enqueue(store, assignment):
    return store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshots/test.json')


def test_claim_is_exclusive_across_connections(store, config, assignment):
    job_id = enqueue(store, assignment)
    other = Store(config.database_path)
    try:
        assert store.claim('assignment', 'first')['job_id'] == job_id
        assert other.claim('assignment', 'second') is None
        with pytest.raises(LeaseLost):
            other.update(job_id, 'second', phase='solving')
    finally:
        other.close()


def test_expired_owner_cannot_dispatch_or_renew(store, assignment):
    at = [100]
    store.clock = lambda: at[0]
    job_id = enqueue(store, assignment)
    store.claim('assignment', 'old')
    attempt = store.prepare_attempt(job_id, 'old', 'answer')
    at[0] += 120001
    assert not store.renew(job_id, 'old')
    with pytest.raises(LeaseLost):
        store.dispatch(attempt['attempt_id'], job_id, 'old')
    assert store.pending('1', '2')['state'] == 'prepared'


def test_dispatch_survives_revision_change_and_restart(store, assignment, changed_assignment):
    job_id = enqueue(store, assignment)
    store.claim('assignment', 'first')
    attempt = store.prepare_attempt(job_id, 'first', 'answer')
    store.dispatch(attempt['attempt_id'], job_id, 'first')
    store.upsert_assignment(changed_assignment, 'snapshots/new.json')
    enqueue(store, changed_assignment)
    store.recover()
    resumed = store.claim('assignment', 'second')
    assert resumed['job_id'] == job_id
    assert resumed['phase'] == 'reconciling'
    with pytest.raises(WorkflowError, match='submission_uncertain'):
        store.dispatch(attempt['attempt_id'], job_id, 'second')
    store.settle(attempt['attempt_id'], job_id, 'second', confirmed=False, evidence_path='receipt')
    store.retry(job_id)
    assert store.get(job_id)['phase'] == 'reconciling'
    assert store.pending('1', '2')['state'] == 'uncertain'


def test_superseded_prepared_attempt_abandoned(store, assignment, changed_assignment):
    job_id = enqueue(store, assignment)
    store.claim('assignment', 'owner')
    store.prepare_attempt(job_id, 'owner', 'answer')
    store.upsert_assignment(changed_assignment, 'new.json')
    assert store.get(job_id)['state'] == 'cancelled'
    assert store.pending('1', '2') is None


def test_terminal_receipt_cannot_be_changed(store, assignment):
    job_id = enqueue(store, assignment)
    store.claim('assignment', 'owner')
    attempt = store.prepare_attempt(job_id, 'owner', 'answer')
    with pytest.raises(WorkflowError, match='invalid_receipt_transition'):
        store.settle(attempt['attempt_id'], job_id, 'owner', confirmed=True, evidence_path='receipt')
    assert store.pending('1', '2')['state'] == 'prepared'


def test_hash_ignores_receipt_and_attachment_cache_location(assignment):
    a = Attachment('7', 'first.pdf', 'attachments/first.pdf', 5, 'sha')
    b = Attachment('8', 'second.pdf', 'attachments/second.pdf', 7, 'sha2')
    original = replace(assignment, attachments=(a, b))
    other = replace(original, attachments=(b, replace(a, path='elsewhere.pdf', name='alias.pdf')),
                    submission=Submission('present'))
    assert original.content_hash == other.content_hash
    assert replace(original, prompt='changed').content_hash != original.content_hash


def test_due_lock_and_unlock_boundary(assignment):
    assert replace(assignment, due_at_ms=100).eligibility(100) == 'assignment_expired'
    assert replace(assignment, lock_at_ms=100).eligibility(100) == 'assignment_closed'
    assert replace(assignment, unlock_at_ms=101, unsupported='assignment_closed').eligibility(100) == 'not_open'
    assert replace(assignment, submission=Submission()).eligibility(100) == 'submission_state_unknown'


def test_configuration_secrets_and_path_escape(config, monkeypatch):
    assert 'local-test-secret' not in repr(config)
    assert config.all_courses and config.model == 'gpt-6.1-sol'
    monkeypatch.setenv('DATABASE_PATH', '../outside.sqlite3')
    with pytest.raises(ConfigError) as caught:
        Config.load(config.root)
    assert caught.value.fields == ['DATABASE_PATH']
    with pytest.raises(ValueError):
        artifact_path(config.data_dir, '../secret')


def test_progress_overlaps_gaps_and_final_second():
    merged, gaps = coverage([{'start': 0, 'end': 10}, {'start': 5, 'end': 15},
                             {'start': 20, 'end': 29}], 30)
    assert merged == [(0, 15), (20, 29)]
    assert gaps == [(15, 20), (29, 30)]
    assert coverage([{'start': 0, 'end': 1425}], 1426)[1] == [(1425, 1426)]
    assert coverage([{'start': -3, 'end': 40}], 30)[1] == []
    with pytest.raises(ValueError):
        coverage([{'start': 0, 'end': float('nan')}], 30)


def test_scheduler_keeps_grid_without_overlap():
    assert next_tick(0, 601, 600) == 1200
    assert next_tick(0, 600, 600) == 1200
    assert next_tick(0, 1801, 600) == 2400
