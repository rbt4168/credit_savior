from __future__ import annotations

import asyncio
import time

from .artifacts import write_json
from .cool import submission_from_api, timestamp
from .errors import WorkflowError
from .models import now_ms


def next_tick(origin: float, current: float, interval: float) -> float:
    return origin + (int(max(0, current - origin) // interval) + 1) * interval


class Scanner:
    def __init__(self, client, store, *, budget_s=480):
        self.client = client
        self.store = store
        self.data_dir = client.config.data_dir
        self.budget_s = budget_s
        self.course_cursor = 0

    async def course(self, course, scan_id):
        if not course.student:
            self.store.record_course_scan(scan_id, course.course_id, "succeeded")
            return {"examined": 0, "eligible": 0}
        assignments = await self.client.list_assignments(course.course_id)
        eligible = 0
        for value in assignments:
            aid = str(value["id"])
            self.store.schedule_reconciliation(course.course_id, aid)
            observed = submission_from_api(value.get("submission"))
            due, lock = timestamp(value.get("due_at")), timestamp(value.get("lock_at"))
            if observed.presence == "present" or (due is not None and due <= now_ms()) or (
                    lock is not None and lock <= now_ms()):
                self.store.cancel_subject(course.course_id, aid, 'ineligible')
                continue
            if not set(value.get('submission_types') or ()).intersection(
                    {'online_text_entry', 'online_upload'}):
                self.store.cancel_subject(course.course_id, aid, 'unsupported_submission_type')
                continue
            assignment = await self.client.snapshot(value)
            relative = (f"snapshots/{course.course_id}/{aid}/{assignment.content_hash}.json")
            write_json(self.data_dir / relative, assignment.to_dict())
            self.store.upsert_assignment(assignment, relative)
            reason = assignment.eligibility(now_ms())
            pending = self.store.pending(course.course_id, aid)
            if pending and pending["state"] in ("dispatching", "uncertain"):
                continue
            state = "queued" if reason == "eligible" else (
                "retry_wait" if reason == "not_open" else "needs_input")
            self.store.enqueue(
                "assignment", course.course_id, aid, assignment.content_hash, relative,
                state=state, error_code=None if reason == "eligible" else reason,
                due_at_ms=assignment.due_at_ms, retry_after_ms=assignment.unlock_at_ms,
            )
            if reason == "eligible":
                eligible += 1
        videos = await self.client.list_videos(course.course_id)
        for video in videos:
            self.store.upsert_video(video)
            relative = f"videos/{course.course_id}/{video.video_id}/{video.revision}.json"
            write_json(self.data_dir / relative, video.to_dict())
            if video.completion != "complete":
                self.store.enqueue(
                    "video", course.course_id, video.video_id, video.revision, relative,
                    checkpoint={"playlist_order": video.order, "last_position_s": 0},
                )
        self.store.record_course_scan(scan_id, course.course_id, "succeeded",
                                     len(assignments), len(videos))
        return {"examined": len(assignments), "eligible": eligible}

    async def once(self, scheduled_at_ms=None):
        scan_id = self.store.start_scan(scheduled_at_ms or now_ms())
        started = time.monotonic()
        counters = {"course_count": 0, "examined": 0, "eligible": 0, "errors": []}
        try:
            courses = await self.client.list_courses()
            counters["course_count"] = len(courses)
            for course in courses:
                self.store.upsert_course(course)
            if courses:
                offset = self.course_cursor % len(courses)
                ordered = courses[offset:] + courses[:offset]
            else:
                ordered = []
            for index, course in enumerate(ordered):
                remaining = self.budget_s - (time.monotonic() - started)
                if remaining <= 0:
                    for pending in ordered[index:]:
                        self.store.record_course_scan(
                            scan_id, pending.course_id, "budget_exceeded",
                            error_code="scan_capacity_exceeded")
                    counters["errors"].append("scan_capacity_exceeded")
                    self.course_cursor = (offset + index) % len(courses)
                    break
                try:
                    result = await asyncio.wait_for(self.course(course, scan_id), remaining)
                    counters["examined"] += result["examined"]
                    counters["eligible"] += result["eligible"]
                except asyncio.TimeoutError:
                    self.store.record_course_scan(scan_id, course.course_id, "budget_exceeded",
                                                  error_code="scan_capacity_exceeded")
                    counters["errors"].append("scan_capacity_exceeded")
                    self.course_cursor = (offset + index) % len(courses)
                    break
                except WorkflowError as error:
                    self.store.record_course_scan(scan_id, course.course_id, "failed",
                                                  error_code=error.code)
                    counters["errors"].append(error.code)
                    if error.code == "auth_expired":
                        await self.client.session.recover()
                except Exception:
                    self.store.record_course_scan(scan_id, course.course_id, "failed",
                                                  error_code="scan_unexpected_error")
                    counters["errors"].append("scan_unexpected_error")
            state = "partial" if counters["errors"] else "succeeded"
            self.store.finish_scan(scan_id, state,
                                   counters["errors"][0] if counters["errors"] else None)
            return {"scan_id": scan_id, "state": state, **counters}
        except BaseException:
            self.store.finish_scan(scan_id, "interrupted", "scan_interrupted")
            raise

    async def run(self, stop: asyncio.Event):
        origin = time.monotonic()
        while not stop.is_set():
            await self.once()
            delay = max(0, next_tick(origin, time.monotonic(), 600) - time.monotonic())
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
