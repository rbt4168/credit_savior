import json
from dataclasses import replace

import pytest

from credit_scammer.artifacts import write_json
from credit_scammer.discord import DiscordTransport, Notifier, multipart
from credit_scammer.errors import WorkflowError
from credit_scammer.models import Answer


class Transport:
    def __init__(self):
        self.calls = []

    def post(self, content, files=()):
        self.calls.append((content, files))
        return {'message_id': 'confirmed-test', 'attachment_count': len(files)}


async def test_missing_assignment_notified_with_course_reason_and_no_credentials(config, store, assignment):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    store.claim('assignment', 'owner')
    store.update(job_id, 'owner', state='needs_input', error_code='missing_problem_material',
                 checkpoint={'follow_up': ['Missing topic. test-owner local-test-secret']})
    transport = Transport()
    notifier = Notifier(config, transport)
    await notifier.poll(store)
    assert len(transport.calls) == 1
    text = transport.calls[0][0]
    assert 'Arithmetic' in text and 'Test' in text and assignment.url in text
    assert 'Missing topic' in text
    assert config.username not in text and config.password not in text
    await Notifier(config, transport).poll(store)
    assert len(transport.calls) == 1


async def test_manual_resend_does_not_trigger_another_automatic_copy(config, store, assignment):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    store.claim('assignment', 'owner')
    store.update(job_id, 'owner', state='needs_input', error_code='missing_problem_material')
    transport = Transport()
    notifier = Notifier(config, transport)
    await notifier.poll(store, resend=True)
    await notifier.poll(store)
    assert len(transport.calls) == 1


async def test_confirmed_submission_sends_answer_attachment(config, store, assignment):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    store.claim('assignment', 'owner')
    answer = Answer('text', text='2 + 2 = 4.')
    write_json(config.data_dir / 'tasks' / job_id / 'answer/manifest.json', answer.to_dict())
    attempt = store.prepare_attempt(job_id, 'owner', answer.answer_hash)
    store.dispatch(attempt['attempt_id'], job_id, 'owner')
    store.settle(attempt['attempt_id'], job_id, 'owner', confirmed=True, evidence_path='receipt')
    transport = Transport()
    await Notifier(config, transport).poll(store)
    assert '已提交並確認回執' in transport.calls[0][0]
    assert transport.calls[0][1] == [('answer.txt', b'2 + 2 = 4.')]


async def test_failed_delivery_is_retained_for_retry(config, store, assignment):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    store.claim('assignment', 'owner')
    store.update(job_id, 'owner', state='needs_input', error_code='missing_problem_material')

    class Failed:
        def post(self, *args):
            raise WorkflowError('notification_delivery_unconfirmed')
    notifier = Notifier(config, Failed())
    await notifier.poll(store)
    assert not notifier.state['delivered']
    assert notifier.state['last_error_code'] == 'notification_delivery_unconfirmed'


def test_private_file_is_not_transmitted(config):
    job = {'job_id': 'test'}
    directory = config.data_dir / 'tasks/test/answer'
    directory.mkdir(parents=True)
    path = directory / 'report.txt'
    path.write_text(config.password, encoding='utf-8')
    from credit_scammer.artifacts import digest
    write_json(directory / 'manifest.json', Answer('files', files=('tasks/test/answer/report.txt',),
               hashes=(digest(path.read_bytes()),)).to_dict())
    with pytest.raises(WorkflowError, match='notification_artifact_contains_private_data'):
        Notifier(config).answer_files(job)


def test_partial_model_output_sent_as_draft_without_worker_manifest(config):
    directory = config.data_dir / 'tasks/partial/answer'
    write_json(directory / 'model-response.json', {
        'answer_kind': 'text', 'answer_text': 'Partial result.', 'missing_information': ['Topic missing'],
        'artifacts': [{'logical_name': 'draft', 'format': 'txt', 'content': 'Unsubmitted draft'}]})
    files = Notifier(config).answer_files({'job_id': 'partial'})
    assert files == [('01_draft.txt', b'Unsubmitted draft')]
    assert not (directory / 'manifest.json').exists()
    assert (config.data_dir / 'delivery-drafts/partial/manifest.json').exists()


async def test_video_errors_grouped_with_titles_courses_and_cooldown(config, store):
    from credit_scammer.models import Video
    for index in range(3):
        video = Video('1', str(index+10), f'https://cool.ntu.edu.tw/courses/1/modules/items/{index}',
                      'rev', title=f'Lecture {index}')
        store.upsert_video(video)
        payload = f'videos/{index}.json'
        write_json(config.data_dir / payload, video.to_dict())
        job_id = store.enqueue('video', '1', video.video_id, 'rev', payload)
        store.claim('video', 'owner')
        store.update(job_id, 'owner', state='failed', error_code='worker_unexpected_error')
    transport = Transport()
    notifier = Notifier(config, transport)
    await notifier.poll(store)
    assert len(transport.calls) == 1
    text = transport.calls[0][0]
    assert 'Test' in text and 'Lecture 0' in text and 'Lecture 2' in text
    await notifier.poll(store)
    assert len(transport.calls) == 1


async def test_deferred_video_reconnect_notification_explains_automatic_retry(config, store):
    from credit_scammer.models import Video
    video = Video('1', '80', 'https://cool.ntu.edu.tw/courses/1/modules/items/80',
                  'rev', title='Lecture 80')
    store.upsert_video(video)
    write_json(config.data_dir / 'videos/80.json', video.to_dict())
    job_id = store.enqueue('video', '1', '80', 'rev', 'videos/80.json')
    store.claim('video', 'owner')
    store.update(job_id, 'owner', state='retry_wait', error_code='video_reconnect_delayed',
                 retry_after_ms=store.clock()+600000, checkpoint={'reconnect_exhausted': True})
    transport = Transport()
    notifier = Notifier(config, transport)
    await notifier.poll(store)
    assert len(transport.calls) == 1
    assert 'Test (1) - 影片' in transport.calls[0][0]
    assert 'Lecture 80' in transport.calls[0][0]
    assert '10 分鐘後自動再試' in transport.calls[0][0]
    await notifier.poll(store)
    assert len(transport.calls) == 1


@pytest.mark.parametrize('confirmed', [True, False])
async def test_finished_video_report_has_course_title_status_and_persistent_dedup(config, store, confirmed):
    from credit_scammer.models import Video
    video = Video('1', '81', 'https://cool.ntu.edu.tw/courses/1/modules/items/81',
                  'rev', title='Lecture 81')
    store.upsert_video(video)
    write_json(config.data_dir / 'videos/81.json', video.to_dict())
    job_id = store.enqueue('video', '1', '81', 'rev', 'videos/81.json')
    store.claim('video', 'owner')
    store.update(job_id, 'owner', state='succeeded' if confirmed else 'needs_input',
                 phase='completed' if confirmed else 'played_unverified',
                 error_code=None if confirmed else 'progress_unavailable',
                 checkpoint={'last_position_s': 30, 'follow_up': ['Check final-second gap.']})
    transport = Transport()
    await Notifier(config, transport).poll(store)
    assert len(transport.calls) == 1
    message = transport.calls[0][0]
    assert 'Test (1) - 影片' in message and 'Lecture 81' in message and video.url in message
    assert '已播放到結束' in message
    if confirmed:
        assert '平台已確認完成' in message
    else:
        assert '平台進度尚未確認' in message and 'Check final-second gap' in message
        assert '平台已確認完成' not in message
    await Notifier(config, transport).poll(store)
    assert len(transport.calls) == 1


def test_transport_uses_embed_instead_of_plain_content(monkeypatch):
    from credit_scammer import discord
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return b'{"id":"confirmed","attachments":[]}'

    class Opener:
        def open(self, request, **kwargs):
            captured.append(json.loads(request.data))
            return Response()
    monkeypatch.setattr(discord, 'build_opener', lambda *args: Opener())
    DiscordTransport('https://discord.com/api/webhooks/123/test-only').post('Title\nDescription')
    assert 'content' not in captured[0]
    assert captured[0]['embeds'][0]['title'] == 'Title'
    assert captured[0]['embeds'][0]['description'] == 'Description'
    assert captured[0]['allowed_mentions']['parse'] == []


def test_multipart_attachments_and_mentions_suppressed():
    data, content_type = multipart({'content': 'Report', 'allowed_mentions': {'parse': []}},
                                   [('answer.txt', b'2+2=4')])
    assert 'multipart/form-data; boundary=' in content_type
    assert b'name="files[0]"; filename="answer.txt"' in data
    assert b'2+2=4' in data
    assert json.dumps({'parse': []}).encode() in data
    with pytest.raises(WorkflowError):
        DiscordTransport('https://other.example/api/webhooks/123/secret')


def test_webhook_not_in_config_repr(config):
    value = replace(config, discord_webhook='https://discord.com/api/webhooks/123/local-only-token')
    assert 'local-only-token' not in repr(value)
