from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from credit_savior.artifacts import read_json, write_json
from credit_savior.cool import CoolClient
from credit_savior.discord import Notifier
from credit_savior.errors import WorkflowError
from credit_savior.models import Course, Video, now_ms
from credit_savior.monitoring import collect_resources, monitor_course
from credit_savior.videos import VideoWorker


async def test_all_courses_excludes_past_terms_nonstudents_and_explicit_exclusions(config):
    config = replace(config, excluded_course_ids=('7',))
    rows = [{'href': f'/courses/{cid}', 'name': cid, 'role': role}
            for cid, role in [('1', 'Student'), ('9', 'Student'), ('7', 'Student'), ('8', 'TA')]]
    page = SimpleNamespace(evaluate=AsyncMock(return_value=rows),
                           locator=lambda _: SimpleNamespace(wait_for=AsyncMock()))

    @asynccontextmanager
    async def operation(role):
        yield page

    client = CoolClient(SimpleNamespace(config=config, operation=operation))
    client._goto = AsyncMock()
    client._json = AsyncMock(return_value=([
        {'id': int(cid), 'term': {'start_at': None, 'end_at': '2001-01-01T00:00:00Z'
                                 if cid == '9' else None}} for cid in ('1', '9', '7')], ''))
    assert [c.course_id for c in await client.list_courses()] == ['1']
    client.config = replace(config, all_courses=False, course_ids=('9', '7'))
    assert [c.course_id for c in await client.list_courses()] == ['9']


def test_excluded_course_jobs_stop_without_erasing_receipts(store, assignment):
    store.upsert_course(Course('9', 'Past course', 'url'))
    old = replace(assignment, course_id='9')
    store.upsert_assignment(old, 'old.json')
    old_id = store.enqueue('assignment', '9', '2', old.content_hash, 'old.json')
    store.claim('assignment', 'owner')
    intent = store.prepare_attempt(old_id, 'owner', 'answer')
    store.dispatch(intent['attempt_id'], old_id, 'owner')
    store.recover()
    store.restrict_courses({'1'})
    assert store.pending('9', '2')['state'] == 'dispatching'
    assert store.claim('assignment', 'next') is None
    assert store.get(old_id)['phase'] == 'reconciling'
    store.upsert_video(Video('9', '70', 'url', 'rev'))
    video_id = store.enqueue('video', '9', '70', 'rev', 'video.json')
    store.restrict_courses({'1'})
    assert store.get(video_id)['state'] == 'cancelled'
    assert store.get(video_id)['error_code'] == 'course_excluded'
    assert store.claim('video', 'next') is None
    store.upsert_course(Course('9', 'Re-enabled', 'url'))
    assert store.enqueue('video', '9', '70', 'rev', 'video.json') == video_id
    assert store.claim('video', 'next')['job_id'] == video_id


def test_video_retries_cannot_starve_unattempted_courses(store):
    store.clock = lambda: 1000
    store.upsert_video(Video('1', '70', 'url', 'rev'))
    first = store.enqueue('video', '1', '70', 'rev', 'video.json', checkpoint={'playlist_order': 0})
    store.claim('video', 'owner')
    store.update(first, 'owner', state='retry_wait', retry_after_ms=1000)
    store.db.execute('UPDATE jobs SET run_attempt=161 WHERE job_id=?', (first,))
    store.upsert_course(Course('9', 'Another current course', 'url'))
    store.upsert_video(Video('9', '80', 'url', 'rev'))
    second = store.enqueue('video', '9', '80', 'rev', 'video.json')
    assert store.claim('video', 'next')['job_id'] == second


async def test_missing_progress_parks_without_replaying_or_marking_complete(config, store):
    store.clock = lambda: 1000
    video = Video('1', '70', 'url', 'rev')
    write_json(config.data_dir / 'video.json', video.to_dict())
    store.upsert_video(video)
    job_id = store.enqueue('video', '1', '70', 'rev', 'video.json')
    job = store.claim('video', 'owner')
    client = SimpleNamespace(config=config, inspect_video=AsyncMock(),
        video_progress=AsyncMock(return_value='unknown'), missing_video_ranges=lambda: None,
        open_video=AsyncMock(), session=SimpleNamespace(generation=1, reconnect=AsyncMock()))
    await VideoWorker(client, store).process(job, 'owner')
    saved = store.get(job_id)
    assert saved['state'] == 'retry_wait' and saved['retry_after_ms'] == 601000
    assert saved['error_code'] == 'progress_unavailable'
    client.open_video.assert_not_called()
    client.session.reconnect.assert_not_called()
    assert store.db.execute('SELECT progress_state FROM videos').fetchone()[0] == 'unknown'


def test_confirmed_video_survives_metadata_only_scan_but_not_new_revision(store):
    video = Video('1', '70', 'url', 'rev')
    store.upsert_video(video)
    job_id = store.enqueue('video', '1', '70', 'rev', 'video.json')
    store.claim('video', 'owner')
    store.complete_video(job_id, 'owner')
    store.upsert_video(video)
    assert store.db.execute('SELECT progress_state FROM videos').fetchone()[0] == 'complete'
    store.upsert_video(replace(video, revision='new'))
    assert store.db.execute('SELECT progress_state FROM videos').fetchone()[0] == 'unknown'


async def test_resource_collection_covers_announcements_pages_and_offline_assignments(config):
    @asynccontextmanager
    async def operation(role):
        yield None

    async def paginated(path):
        if '/pages?' in path:
            return [{'page_id': 10}, {'page_id': 11}]
        if '/announcements?' in path:
            return [{'id': 2, 'title': 'Midterm', 'message': '<p>Exam on Friday</p>'}]
        if '/assignments?' in path:
            return [{'id': 3, 'name': 'Paper homework', 'submission_types': ['on_paper'],
                     'description': 'Bring a hard copy', 'due_at': '2026-10-30T09:00:00Z'}]
        return []

    async def response(path):
        if '/pages/' in path:
            return {'page_id': int(path.rsplit('/', 1)[1]), 'title': 'Instructions',
                    'body': '<p>Group signup deadline</p>'}, ''
        return {'id': 1, 'syllabus_body': '<p>Course schedule</p>'}, ''

    client = SimpleNamespace(config=config, role='scanner',
        session=SimpleNamespace(operation=operation), paginated=paginated, _json=response)
    records, checked, errors = await collect_resources(client, '1')
    assert not errors
    assert {'announcements', 'discussions', 'pages', 'calendar', 'modules', 'information',
            'assignments'} == set(checked)
    assert {'pages:10', 'pages:11'} <= records.keys()
    assert records['announcements:2']['text'] == 'Exam on Friday'
    assert records['assignments:3']['submission_types'] == ['on_paper']


async def test_baseline_changes_and_delivery_are_deduplicated(config, store, monkeypatch):
    record = {'title': 'Exam schedule', 'url': 'https://example.edu/course', 'text': 'Friday'}
    responses = [({'announcements:2': record}, ['announcements'], []),
                 ({'announcements:2': {**record, 'text': 'Monday'}}, ['announcements'], []),
                 ({'announcements:2': {**record, 'text': 'Monday'}}, ['announcements'], [])]
    monkeypatch.setattr('credit_savior.monitoring.collect_resources', AsyncMock(side_effect=responses))
    client = SimpleNamespace(config=config)
    first = await monitor_course(client, '1')
    assert not first['entries']['announcements:2']['notify']
    second = await monitor_course(client, '1')
    assert second['entries']['announcements:2']['notify']
    third = await monitor_course(client, '1')
    assert third['entries'] == second['entries']
    messages = []

    class Transport:
        def post(self, message, files=()):
            messages.append(message)
            return {'message_id': 'test-receipt'}

    notifier = Notifier(config, transport=Transport())
    await notifier.poll(store)
    await notifier.poll(store)
    assert len(messages) == 1 and 'Monday' in messages[0]
    assert 'Friday' not in messages[0]
    assert read_json(config.data_dir / 'monitoring' / '1.json')['checked_at_ms'] <= now_ms()


@pytest.mark.parametrize('code', ['course_not_found', 'course_forbidden'])
async def test_missing_pages_feature_is_distinct_from_forbidden_content(config, code):
    @asynccontextmanager
    async def operation(role):
        yield None

    async def paginated(path):
        if '/pages?' in path:
            raise WorkflowError(code)
        return []

    client = SimpleNamespace(config=config, role='scanner',
        session=SimpleNamespace(operation=operation), paginated=paginated,
        _json=AsyncMock(return_value=({'id': 1}, '')))
    _, checked, errors = await collect_resources(client, '1')
    assert ('pages:not_exposed' in checked) == (code == 'course_not_found')
    assert bool(errors) == (code == 'course_forbidden')


async def test_deleted_items_alert_once_and_failed_sources_preserve_evidence(config, monkeypatch):
    record = {'title': 'Exam', 'url': 'https://example.edu/course', 'text': 'Friday'}
    responses = [({'announcements:2': record}, ['announcements'], []),
                 ({}, [], [{'source': 'announcements', 'error_code': 'network_transient'}]),
                 ({}, ['announcements'], []), ({}, ['announcements'], [])]
    monkeypatch.setattr('credit_savior.monitoring.collect_resources', AsyncMock(side_effect=responses))
    client = SimpleNamespace(config=config)
    await monitor_course(client, '1')
    failed = await monitor_course(client, '1')
    assert failed['entries']['announcements:2']['record'] == record
    removed = await monitor_course(client, '1')
    assert removed['entries']['announcements:2']['removed']
    assert removed['entries']['announcements:2']['notify']
    repeat = await monitor_course(client, '1')
    assert repeat['entries'] == removed['entries']


def test_external_big5_pages_are_decoded_without_authenticated_headers(monkeypatch):
    from io import BytesIO
    from email.message import Message
    from credit_savior.monitoring import read_external

    body = '<meta charset="big5"><p>期末考時間</p>'.encode('big5')
    response = BytesIO(body)
    response.headers = Message()
    response.headers['Content-Type'] = 'text/html'
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        return response

    monkeypatch.setattr('credit_savior.monitoring.socket.getaddrinfo',
                        lambda *args: [(2, 1, 6, '', ('93.184.216.34', 80))])
    monkeypatch.setattr('credit_savior.monitoring.build_opener',
                        lambda *args: SimpleNamespace(open=open_request))
    result = read_external('http://example.edu/course')
    assert result['text'] == '期末考時間'
    assert not {'cookie', 'authorization'} & {k.lower() for k in requests[0].headers}
