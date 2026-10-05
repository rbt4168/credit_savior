from contextlib import asynccontextmanager
from types import SimpleNamespace
import sqlite3

import pytest

from credit_savior.cool import CoolClient, course_code_from_information
from credit_savior.errors import WorkflowError
from credit_savior.models import Course
from credit_savior.scanner import Scanner
from credit_savior.store import Store


@pytest.mark.parametrize(('html', 'expected'), [
    ('<table><tr><td><span>課號</span></td><td>CSIE&nbsp; 1001</td>'
     '<td>課程識別碼</td><td>902 10010</td></tr></table>', 'CSIE 1001'),
    ('<table><tr><th>Course Code:</th><td>IM2010</td></tr></table>', 'IM2010'),
    ('<p>課號 CSIE1001 is mentioned in the reading list.</p>', None),
    ('<table><tr><td>課程識別碼</td><td>902 10010</td></tr></table>', None),
    ('<table><tr><td>課號</td><td>CSIE1001</td></tr>'
     '<tr><td>課號</td><td>IM2010</td></tr></table>', None),
    ('<table><tr><td>課號</td><td>--</td></tr></table>', None),
])
def test_course_code_requires_unambiguous_information_field(html, expected):
    assert course_code_from_information(html) == expected


async def test_client_reads_syllabus_information_not_canvas_course_code(config):
    @asynccontextmanager
    async def operation(role):
        yield None

    client = CoolClient(SimpleNamespace(config=config, operation=operation))
    paths = []

    async def response(path):
        paths.append(path)
        return {'id': 1, 'course_code': '115-1 Example course display name',
                'syllabus_body': '<table><tr><td>課號</td><td>CSIE1001</td></tr></table>'}, ''

    client._json = response
    assert await client.read_course_code('1') == 'CSIE1001'
    assert paths == ['/api/v1/courses/1?include[]=syllabus_body']
    with pytest.raises(WorkflowError, match='course_information_unverified'):
        await client.read_course_code('2')


def test_additive_course_information_preserves_jobs_and_legacy_course_writes(config, store, assignment):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    before = store.get(job_id)
    store.db.execute('DROP TABLE course_information')  # A v1 database from the previous release.
    store.close()
    migrated = Store(config.database_path)
    try:
        assert migrated.get(job_id) == before
        assert migrated.course_code('1') is None
        migrated.upsert_course(Course('1', 'Test', 'url', course_code='CSIE1001'))
        migrated.upsert_course(Course('1', 'Test', 'url'))  # List discovery doesn't erase evidence.
        assert migrated.course_code('1') == 'CSIE1001'
        # The original table shape remains compatible with old INSERT ... VALUES callers.
        with sqlite3.connect(config.database_path) as legacy:
            legacy.execute("INSERT INTO courses VALUES ('9','Legacy','url',1,'accessible',0)")
            assert len(legacy.execute("SELECT * FROM courses WHERE course_id='1'").fetchone()) == 6
        assert migrated.get(job_id) == before
        assert migrated.db.execute('PRAGMA user_version').fetchone()[0] == 1
    finally:
        migrated.close()


async def test_scan_enriches_course_code_before_assignment_processing(config, store):
    calls = []

    class Client:
        def __init__(self):
            self.config = config

        async def read_course_code(self, course_id):
            calls.append('information')
            return 'IM2010'

        async def list_assignments(self, course_id):
            assert store.course_code(course_id) == 'IM2010'
            calls.append('assignments')
            return []

        async def list_videos(self, course_id):
            return []

    scan_id = store.start_scan(store.clock())
    await Scanner(Client(), store).course(Course('1', 'Test', 'url'), scan_id)
    assert calls == ['information', 'assignments']
