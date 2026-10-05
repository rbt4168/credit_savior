import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from html import escape
from types import SimpleNamespace

import pytest

from credit_savior.artifacts import write_json
from credit_savior.assignments import AssignmentWorker, matches_receipt
from credit_savior.models import Answer, Submission, now_ms


class PowerLoss(BaseException):
    pass


class FakeSession:
    submission_lock = None

    def __init__(self):
        self.submission_lock = asyncio.Lock()

    @asynccontextmanager
    async def operation(self, role):
        yield None


class FakeSolver:
    calls = 0

    async def solve(self, assignment, output_dir, feedback):
        self.calls += 1
        answer = Answer('text', text='2 + 2 = 4. <reason>')
        write_json(output_dir / 'manifest.json', answer.to_dict())
        return answer


class FakeClient:
    def __init__(self, config, assignment, store):
        self.config, self.assignment, self.store = config, assignment, store
        self.session = FakeSession()
        self.observation = Submission('absent')
        self.clicks, self.crash = 0, None

    async def read_assignment(self, *args):
        return replace(self.assignment, submission=self.observation)

    async def read_submission(self, *args):
        return self.observation

    async def stage_answer(self, assignment, answer):
        self.answer = answer
        if self.crash == 'before':
            self.crash = None
            raise PowerLoss()

    async def click_submit(self):
        assert self.store.pending('1', '2')['state'] == 'dispatching'
        self.clicks += 1
        if self.crash != 'uncertain':
            self.observation = Submission('present', text='<p>' + escape(self.answer.text) + '</p>',
                                          attempt_id='1', submitted_at_ms=now_ms())
        if self.crash == 'after':
            self.crash = None
            raise PowerLoss()


def setup(config, assignment, store):
    payload = 'snapshots/test.json'
    write_json(config.data_dir / payload, assignment.to_dict())
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, payload)
    client, solver = FakeClient(config, assignment, store), FakeSolver()
    worker = AssignmentWorker(client, store, solver, receipt_delays=())
    return job_id, client, solver, worker


@pytest.mark.parametrize('crash', ['before', 'after'])
async def test_power_loss_resumes_without_duplicate_submission(config, assignment, store, crash):
    job_id, client, solver, worker = setup(config, assignment, store)
    client.crash = crash
    with pytest.raises(PowerLoss):
        await worker.process(store.claim('assignment', 'first'), 'first')
    store.recover()
    await worker.process(store.claim('assignment', 'second'), 'second')
    assert client.clicks == 1
    assert solver.calls == 1
    assert store.get(job_id)['state'] == 'succeeded'


async def test_uncertain_receipt_retry_only_reads(config, assignment, store):
    job_id, client, solver, worker = setup(config, assignment, store)
    client.crash = 'uncertain'
    await worker.process(store.claim('assignment', 'first'), 'first')
    assert store.get(job_id)['state'] == 'needs_input'
    store.retry(job_id)
    await worker.process(store.claim('assignment', 'second'), 'second')
    assert client.clicks == solver.calls == 1
    assert store.pending('1', '2')['state'] == 'uncertain'


async def test_new_deadline_during_staging_prevents_click(config, assignment, store):
    job_id, client, solver, worker = setup(config, assignment, store)
    original = client.stage_answer

    async def stage(*args):
        await original(*args)
        client.assignment = replace(assignment, due_at_ms=now_ms()-1)
    client.stage_answer = stage
    from credit_savior.errors import WorkflowError
    with pytest.raises(WorkflowError):
        await worker.process(store.claim('assignment', 'owner'), 'owner')
    assert client.clicks == 0
    assert store.pending('1', '2')['state'] == 'prepared'


def test_receipt_requires_bytes_and_recent_submission():
    answer = Answer('files', files=('path/answer.pdf',), hashes=('correct',))
    attempt = {'started_at_ms': 10000}
    observed = Submission('present', files=('answer.pdf',), submitted_at_ms=11000,
                          file_hashes=('wrong',))
    assert not matches_receipt(observed, answer, attempt)
    assert matches_receipt(replace(observed, file_hashes=('correct',)), answer, attempt)
    assert not matches_receipt(replace(observed, file_hashes=('correct',), submitted_at_ms=1), answer, attempt)


async def test_ended_video_without_platform_confirmation_is_not_success(config, store):
    from credit_savior.models import Video
    from credit_savior.videos import VideoWorker
    video = Video('1', '7', 'https://cool.ntu.edu.tw/courses/1/modules/items/9', 'revision')
    store.upsert_video(video)
    write_json(config.data_dir / 'videos/test.json', video.to_dict())
    job_id = store.enqueue('video', '1', '7', 'revision', 'videos/test.json')

    class Client:
        async def inspect_video(self, v):
            pass

        async def video_progress(self, **kwargs):
            return 'incomplete'

        def missing_video_ranges(self):
            return [(0, 10)]

        async def open_video(self, *args):
            pass

        async def playback(self):
            return {'ended': True, 'position_s': 10}
    client = Client()
    client.config = SimpleNamespace(data_dir=config.data_dir)
    worker = VideoWorker(client, store, progress_delays=())
    await worker.process(store.claim('video', 'owner'), 'owner')
    assert store.get(job_id)['state'] == 'needs_input'
    assert store.get(job_id)['phase'] == 'played_unverified'
