from dataclasses import replace
import json

import pytest

from credit_savior.artifacts import write_json
from credit_savior.models import now_ms
from credit_savior.notebooks import enrich_assignment, course_source, refresh_source


def source(config):
    folder = config.data_dir / 'course-sources/ExampleCourse'
    folder.mkdir(parents=True)
    write_json(folder.parent / 'sources.json', {'courses': {'1': {
        'repository': 'https://github.com/example/ExampleCourse', 'model': 'gpt-6-astra'}}})
    cells = [{'cell_type': 'markdown', 'source': text} for text in [
        '## Lecture', 'Not a homework question.', '## 週五作業',
        'Prove the Friday statement.', '### 週一作業', 'Prove the Monday statement.']]
    (folder / '1005.ipynb').write_text(json.dumps({'cells': cells}), encoding='utf-8')
    return folder


def test_date_mapping_excludes_same_day_lecture_and_other_homework(config, assignment):
    folder = source(config)
    (folder / '1012.ipynb').write_text(json.dumps({'cells': []}), encoding='utf-8')
    from datetime import datetime
    from zoneinfo import ZoneInfo
    due = int(datetime(2026, 10, 12, 6, tzinfo=ZoneInfo('Asia/Taipei')).timestamp() * 1000)
    result = enrich_assignment(config.data_dir, replace(assignment, title='1012', prompt='', due_at_ms=due))
    assert 'Monday statement' in result.prompt
    assert 'Friday statement' not in result.prompt and 'Lecture' not in result.prompt
    assert course_source(config.data_dir, '1')['model'] == 'gpt-6-astra'
    friday = enrich_assignment(config.data_dir, replace(assignment, title='1009', prompt='', due_at_ms=due))
    assert 'Friday statement' in friday.prompt and 'Monday statement' not in friday.prompt


def test_missing_current_week_does_not_reuse_old_homework(config, assignment):
    source(config)
    from datetime import datetime
    due = int(datetime(2026, 10, 23).timestamp() * 1000)
    value = replace(assignment, title='1023', prompt='', due_at_ms=due)
    assert enrich_assignment(config.data_dir, value) == value


def test_repository_updates_are_read_only_and_do_not_receive_credentials(config, monkeypatch):
    source(config)
    from credit_savior import notebooks
    calls = []
    monkeypatch.setenv('COOL_PASSWORD', 'private-test-value')

    def git(args, **kwargs):
        from types import SimpleNamespace
        calls.append((args, kwargs))
        value = b'https://github.com/example/ExampleCourse.git\n' if args[-3:] == ['remote', 'get-url', 'origin'] else b''
        return SimpleNamespace(returncode=0, stdout=value)

    monkeypatch.setattr(notebooks.subprocess, 'run', git)
    refresh_source(config.data_dir, '1')
    assert any('fetch' in args for args, _ in calls)
    assert any('--ff-only' in args for args, _ in calls)
    assert all('COOL_PASSWORD' not in kwargs['env'] for _, kwargs in calls)
    assert all('credential.helper=' in args for args, _ in calls)


def test_late_assignment_requires_explicit_platform_open_state(assignment, config, store):
    old = replace(assignment, due_at_ms=now_ms() - 1000)
    assert old.eligibility(now_ms()) == 'assignment_expired'
    reopened = replace(old, locked_for_user=False)
    assert reopened.content_hash == old.content_hash
    assert reopened.eligibility(now_ms()) == 'eligible'
    assert replace(reopened, locked_for_user=True).eligibility(now_ms()) == 'assignment_closed'
    assert replace(reopened, lock_at_ms=now_ms()-1).eligibility(now_ms()) == 'assignment_closed'
    job_id = store.enqueue('assignment', '1', '2', old.content_hash, 'snapshot.json',
                           state='cancelled', error_code='ineligible')
    assert store.enqueue('assignment', '1', '2', old.content_hash, 'snapshot.json') == job_id
    assert store.get(job_id)['state'] == 'queued'


def test_source_rejects_unapproved_repository_and_model(config):
    source(config)
    file = config.data_dir / 'course-sources/sources.json'
    write_json(file, {'courses': {'1': {'repository': 'https://other.example/token', 'model': 'gpt-6-astra'}}})
    from credit_savior.errors import WorkflowError
    with pytest.raises(WorkflowError, match='course_source_config_invalid'):
        course_source(config.data_dir, '1')
