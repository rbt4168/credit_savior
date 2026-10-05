from dataclasses import replace

import pytest

from credit_savior.config import Config
from credit_savior.models import Assignment, Course, Submission
from credit_savior.store import Store


@pytest.fixture
def config(tmp_path, monkeypatch):
    for key in ('COOL_BASE_URL', 'COOL_COURSE_IDS', 'LLM_MODEL', 'DATA_DIR', 'DATABASE_PATH',
                'AUTH_STATE_PATH', 'CHECK_INTERVAL_SECONDS', 'COOL_USERNAME', 'COOL_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('COOL_COURSE_IDS=all\nCOOL_USERNAME=test-owner\n'
                                 'COOL_PASSWORD=local-test-secret\n', encoding='utf-8')
    return Config.load(tmp_path, require_auth=True)


@pytest.fixture
def assignment():
    return Assignment('1', '2', 'https://cool.ntu.edu.tw/courses/1/assignments/2',
                      'Arithmetic', 'Explain 2+2.', ('online_text_entry',),
                      submission=Submission('absent'))


@pytest.fixture
def store(config, assignment):
    value = Store(config.database_path)
    value.upsert_course(Course('1', 'Test', 'https://cool.ntu.edu.tw/courses/1', course_code='CS1001'))
    value.upsert_assignment(assignment, 'snapshots/test.json')
    yield value
    value.close()


@pytest.fixture
def changed_assignment(assignment):
    return replace(assignment, prompt='Explain 3+3.')
