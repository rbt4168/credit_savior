from types import SimpleNamespace

import pytest
from playwright.async_api import Error as BrowserError

from credit_savior.errors import WorkflowError
from credit_savior.models import Video
from credit_savior.workers import execute_claimed


async def test_closed_browser_stops_worker_without_consuming_rest_of_queue(config, store):
    for vid in ('20', '21', '22'):
        store.upsert_video(Video('1', vid, 'url', 'revision'))
        store.enqueue('video', '1', vid, 'revision', 'video.json')
    closed_error = type('TargetClosedError', (BrowserError,), {})

    class Worker:
        async def process(self, job, owner):
            raise closed_error('Target page, context or browser has been closed')
    worker = Worker()
    worker.store, worker.client = store, SimpleNamespace(config=config)
    job = store.claim('video', 'owner')
    with pytest.raises(WorkflowError, match='browser_closed'):
        await execute_claimed(worker, job, 'owner')
    assert store.get(job['job_id'])['state'] == 'retry_wait'
    assert len([j for j in store.list_jobs() if j['state'] == 'queued']) == 2
