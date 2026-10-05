from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from playwright.async_api import Error as BrowserError

from .artifacts import artifact_path, read_json, write_json
from .errors import LeaseLost, WorkflowError
from .models import Video


class VideoWorker:
    kind = "video"

    def __init__(self, client, store, *, sample_s=10, stall_s=60,
                 progress_delays=(5, 15, 30, 60, 120), reconnect_delays=(5, 15, 45),
                 retry_s=600):
        self.client = client
        self.store = store
        self.sample_s = sample_s
        self.stall_s = stall_s
        self.progress_delays = progress_delays
        self.reconnect_delays = reconnect_delays
        self.retry_s = retry_s

    async def process(self, job, owner):
        value = read_json(artifact_path(self.client.config.data_dir, job["payload_path"]))
        video = Video(**{k: v for k, v in value.items() if k != "schema_version"})
        checkpoint = job["checkpoint"]
        checkpoint['reconnect_exhausted'] = False
        checkpoint.pop('follow_up', None)
        for attempt in range(len(self.reconnect_delays) + 1):
            self.store.assert_owned(job['job_id'], owner)
            checkpoint['reconnect_attempts'] = attempt
            self.store.update(job['job_id'], owner, phase='opening', checkpoint=checkpoint)
            generation = getattr(self.client.session, 'generation', 0) if hasattr(self.client, 'session') else 0
            try:
                await self._play(job, owner, video, checkpoint)
                return
            except LeaseLost:
                raise
            except (BrowserError, WorkflowError) as error:
                if isinstance(error, WorkflowError) and not (
                        error.transient or error.code in {'playback_stalled', 'duration_unknown'}):
                    raise
                checkpoint['last_reconnect_error'] = error.code if isinstance(error, WorkflowError) else type(error).__name__
                if attempt == len(self.reconnect_delays):
                    break
                checkpoint['reconnect_total'] = checkpoint.get('reconnect_total', 0) + 1
                self.store.update(job['job_id'], owner, phase='opening', checkpoint=checkpoint)
                await asyncio.sleep(self.reconnect_delays[attempt])
                self.store.assert_owned(job['job_id'], owner)
                try:
                    if isinstance(error, WorkflowError) and error.code == 'auth_expired':
                        await self.client.session.recover()
                    else:
                        await self.client.session.reconnect('video', expected_generation=generation)
                except WorkflowError as restore_error:
                    if not restore_error.transient:
                        raise
                    checkpoint['last_reconnect_error'] = restore_error.code
                except BrowserError as restore_error:
                    checkpoint['last_reconnect_error'] = type(restore_error).__name__
        checkpoint['reconnect_exhausted'] = True
        checkpoint['follow_up'] = ['影片連續重接失敗，10 分鐘後自動再試；若持續失敗再檢查播放器或網路。']
        self.store.update(job['job_id'], owner, phase='opening', state='retry_wait',
                          checkpoint=checkpoint, error_code='video_reconnect_delayed',
                          retry_after_ms=self.store.clock() + int(self.retry_s * 1000))

    async def _play(self, job, owner, video, checkpoint):
        try:
            await self._play_once(job, owner, video, checkpoint)
        finally:
            # Save the latest observed position on reconnect, cancellation and shutdown.
            with suppress(LeaseLost):
                self.store.update(job['job_id'], owner, checkpoint=checkpoint)

    async def _play_once(self, job, owner, video, checkpoint):
        self.store.update(job["job_id"], owner, phase="opening")
        await self.client.inspect_video(video)
        self.save_evidence(job)
        if await self.client.video_progress() == 'complete':
            self.store.update(job['job_id'], owner, phase='completed', state='succeeded')
            return
        gaps = self.client.missing_video_ranges()
        if gaps is None:
            raise WorkflowError('progress_unavailable')
        # Resume from platform evidence, including gaps earlier than a saved player position.
        position = max(0, gaps[0][0] - 5)
        await self.client.open_video(video, position)
        last_position, advanced_at, saved_at = position, time.monotonic(), time.monotonic()
        self.store.update(job["job_id"], owner, phase="playing")
        while True:
            observed = await self.client.playback()
            checkpoint['last_position_s'] = observed['position_s']
            if observed["ended"]:
                break
            if observed["duration_s"] is None:
                raise WorkflowError("duration_unknown")
            if observed["position_s"] > last_position + .1:
                last_position, advanced_at = observed["position_s"], time.monotonic()
            if observed["paused"]:
                await self.client.resume_playback()
            if time.monotonic() - saved_at >= 30:
                checkpoint["last_position_s"] = last_position
                self.store.update(job["job_id"], owner, checkpoint=checkpoint)
                saved_at = time.monotonic()
            if time.monotonic() - advanced_at >= self.stall_s:
                raise WorkflowError('playback_stalled', transient=True)
            await asyncio.sleep(self.sample_s)
        checkpoint["last_position_s"] = observed["position_s"]
        self.store.update(job["job_id"], owner, phase="verifying", checkpoint=checkpoint)
        for delay in (0, *self.progress_delays):
            if delay:
                await asyncio.sleep(delay)
            progress = await self.client.video_progress(refresh=bool(delay))
            self.save_evidence(job)
            if progress == "complete":
                self.store.update(job["job_id"], owner, phase="completed", state="succeeded")
                return
        checkpoint['follow_up'] = ['之後核對影片平台觀看紀錄、末秒缺口與播放器長度差異。']
        self.store.update(job["job_id"], owner, phase="played_unverified", state="needs_input",
                          checkpoint=checkpoint,
                          error_code="progress_unavailable")

    def save_evidence(self, job):
        if hasattr(self.client, 'video_evidence'):
            write_json(self.client.config.data_dir / 'tasks' / job['job_id'] / 'video-evidence.json',
                       self.client.video_evidence())
