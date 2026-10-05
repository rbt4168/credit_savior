import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as BrowserError

from credit_scammer.artifacts import write_json
from credit_scammer.cool import CoolClient
from credit_scammer.models import Video
from credit_scammer.videos import VideoWorker


def setup_video(config, store):
    video = Video('1', '70', 'https://cool.ntu.edu.tw/courses/1/modules/items/90', 'rev')
    payload = 'videos/reconnect.json'
    write_json(config.data_dir / payload, video.to_dict())
    store.upsert_video(video)
    job_id = store.enqueue('video', '1', '70', 'rev', payload)
    return store.claim('video', 'owner'), job_id


class Client:
    def __init__(self, config, store, job_id, observations):
        self.config, self.store, self.job_id = config, store, job_id
        self.observations = iter(observations)
        self.inspect_count = 0
        self.positions = []
        self.reconnections = []
        self.ended = False
        self.session = SimpleNamespace(generation=7, reconnect=self.reconnect)

    async def reconnect(self, role, *, expected_generation):
        self.reconnections.append((role, expected_generation,
                                   self.store.get(self.job_id)['checkpoint'].get('last_position_s')))
        self.session.generation += 1

    async def inspect_video(self, video):
        self.inspect_count += 1

    async def video_progress(self, **kwargs):
        return 'complete' if self.ended else 'incomplete'

    def missing_video_ranges(self):
        return [(0 if self.inspect_count == 1 else 6, 30)]

    async def open_video(self, video, position):
        self.positions.append(position)

    async def playback(self):
        value = next(self.observations)
        if isinstance(value, BaseException):
            raise value
        self.ended = value['ended']
        return {'duration_s': 30, 'paused': False, **value}


async def test_player_disconnect_saves_position_and_resumes_earliest_platform_gap(config, store):
    job, job_id = setup_video(config, store)
    closed_error = type('TargetClosedError', (BrowserError,), {})
    client = Client(config, store, job_id, [
        {'position_s': 12, 'ended': False}, closed_error('closed'),
        {'position_s': 30, 'ended': True}])
    worker = VideoWorker(client, store, sample_s=0, reconnect_delays=(0,), progress_delays=())
    await worker.process(job, 'owner')
    assert client.reconnections == [('video', 7, 12)]
    assert client.positions == [0, 1]  # Gap begins at 6, even though saved player position is 12.
    assert store.get(job_id)['state'] == 'succeeded'


async def test_stalled_player_reconnects_instead_of_becoming_terminal(config, store):
    job, job_id = setup_video(config, store)
    client = Client(config, store, job_id, [
        {'position_s': 0, 'ended': False}, {'position_s': 30, 'ended': True}])
    worker = VideoWorker(client, store, stall_s=0, reconnect_delays=(0,), progress_delays=())
    await worker.process(job, 'owner')
    assert len(client.reconnections) == 1
    assert store.get(job_id)['state'] == 'succeeded'


async def test_repeated_disconnect_parks_then_is_claimable_without_manual_retry(config, store):
    store.clock = lambda: 1000
    job, job_id = setup_video(config, store)
    client = Client(config, store, job_id, [BrowserError('disconnected') for _ in range(4)])
    worker = VideoWorker(client, store, reconnect_delays=(0, 0, 0), progress_delays=())
    await worker.process(job, 'owner')
    saved = store.get(job_id)
    assert client.inspect_count == 4 and len(client.reconnections) == 3
    assert saved['state'] == 'retry_wait'
    assert saved['retry_after_ms'] == 601000
    assert saved['checkpoint']['reconnect_exhausted']
    assert store.claim('video', 'later') is None
    store.clock = lambda: 601000
    assert store.claim('video', 'later')['job_id'] == job_id


async def test_cancellation_saves_position_without_reopening_player(config, store):
    job, job_id = setup_video(config, store)
    client = Client(config, store, job_id, [
        {'position_s': 12, 'ended': False}, asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await VideoWorker(client, store, sample_s=0).process(job, 'owner')
    assert not client.reconnections
    assert store.get(job_id)['checkpoint']['last_position_s'] == 12


def test_startup_requeues_old_player_errors_but_preserves_unverified_completion(config, store):
    job, job_id = setup_video(config, store)
    store.update(job_id, 'owner', state='needs_input', error_code='browser_action_unavailable')
    store.recover()
    assert store.get(job_id)['state'] == 'queued'
    store.claim('video', 'owner')
    store.update(job_id, 'owner', phase='played_unverified', state='needs_input',
                 error_code='progress_unavailable')
    store.recover()
    assert store.get(job_id)['state'] == 'needs_input'


async def test_closed_page_response_cannot_replace_reconnected_video_evidence(config):
    client = CoolClient(SimpleNamespace(config=config), role='video')
    old_page = object()
    client.video_page = old_page
    client.current_video = Video('1', '70', 'url', 'rev')
    reading, resume = asyncio.Event(), asyncio.Event()

    class Response:
        url = 'https://cool-video.dlc.ntu.edu.tw/api/users/current'
        status = 200

        async def json(self):
            reading.set()
            await resume.wait()
            return {'id': 999}

    task = asyncio.create_task(client._capture_video_response(Response(), old_page))
    await reading.wait()
    client.video_page = object()
    client.video_cache['/api/users/current'] = {'id': 410}
    resume.set()
    await task
    assert client.video_cache['/api/users/current'] == {'id': 410}
