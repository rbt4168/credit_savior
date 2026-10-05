from __future__ import annotations

import asyncio
import time

from .artifacts import artifact_path, read_json, write_json
from .errors import WorkflowError
from .models import Video


class VideoWorker:
    kind = "video"

    def __init__(self, client, store, *, sample_s=10, stall_s=60, progress_delays=(5, 15, 30, 60, 120)):
        self.client = client
        self.store = store
        self.sample_s = sample_s
        self.stall_s = stall_s
        self.progress_delays = progress_delays

    async def process(self, job, owner):
        value = read_json(artifact_path(self.client.config.data_dir, job["payload_path"]))
        video = Video(**{k: v for k, v in value.items() if k != "schema_version"})
        checkpoint = job["checkpoint"]
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
                count = checkpoint.get("reopen_count", 0)
                if count >= 3:
                    raise WorkflowError("playback_stalled")
                checkpoint["reopen_count"] = count + 1
                checkpoint["last_position_s"] = last_position
                self.store.update(job["job_id"], owner, checkpoint=checkpoint)
                await asyncio.sleep((5, 15, 45)[count])
                await self.client.inspect_video(video)
                await self.client.open_video(video, max(0, last_position - 5))
                advanced_at = time.monotonic()
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
