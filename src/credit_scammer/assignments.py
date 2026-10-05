from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from .artifacts import artifact_path, read_json, write_json
from .errors import WorkflowError
from .models import Answer, Assignment, now_ms
from .validation import validate
from .cool import ProblemHTML


def receipt_text(text):
    parser = ProblemHTML()
    parser.feed(text)
    return ' '.join(''.join(parser.text).split())


def matches_receipt(observation, answer, attempt):
    if (observation.presence != 'present' or observation.submitted_at_ms is None
            or observation.submitted_at_ms < attempt['started_at_ms'] - 2000):
        return False
    if answer.kind == 'text':
        return observation.text is not None and receipt_text(observation.text) == ' '.join(answer.text.split())
    return (len(observation.files) == len(answer.files) == len(observation.file_hashes)
            and sorted(zip(observation.files, observation.file_hashes))
            == sorted(zip((Path(p).name for p in answer.files), answer.hashes)))


class AssignmentWorker:
    kind = "assignment"

    def __init__(self, client, store, solver, *, receipt_delays=(2, 5, 10, 20, 30)):
        self.client = client
        self.store = store
        self.solver = solver
        self.data_dir = client.config.data_dir
        self.receipt_delays = receipt_delays

    async def reconcile(self, job, owner, assignment, answer, attempt):
        observations = []
        for delay in (0, *self.receipt_delays):
            if delay:
                await asyncio.sleep(delay)
            self.store.assert_owned(job["job_id"], owner)
            observation = await self.client.read_submission(
                assignment.course_id, assignment.assignment_id)
            observations.append(asdict(observation))
            matches = matches_receipt(observation, answer, attempt)
            if matches:
                relative = f"tasks/{job['job_id']}/receipts/{attempt['attempt_id']}.json"
                write_json(self.data_dir / relative, {"observations": observations})
                self.store.settle(attempt["attempt_id"], job["job_id"], owner, confirmed=True,
                                  evidence_path=relative, platform_attempt_id=observation.attempt_id)
                return
        relative = f"tasks/{job['job_id']}/receipts/{attempt['attempt_id']}.json"
        write_json(self.data_dir / relative, {"observations": observations})
        self.store.settle(attempt["attempt_id"], job["job_id"], owner, confirmed=False,
                          evidence_path=relative)

    async def process(self, job, owner):
        job_id = job["job_id"]
        assignment = Assignment.from_dict(read_json(artifact_path(self.data_dir, job["payload_path"])))
        if assignment.content_hash != job["revision"]:
            raise WorkflowError("artifact_invalid")
        output_dir = self.data_dir / "tasks" / job_id / "answer"
        manifest = output_dir / "manifest.json"
        pending = self.store.pending(assignment.course_id, assignment.assignment_id)
        if pending and pending["state"] in ("dispatching", "uncertain"):
            if not manifest.is_file():
                raise WorkflowError("artifact_missing")
            answer = Answer.from_dict(read_json(manifest))
            self.store.update(job_id, owner, phase="reconciling")
            await self.reconcile(job, owner, assignment, answer, pending)
            return
        # Read current eligibility before spending model requests.
        current = await self.client.read_assignment(assignment.course_id, assignment.assignment_id)
        if current.content_hash != assignment.content_hash:
            self.store.update(job_id, owner, state="cancelled", error_code="superseded")
            return
        reason = current.eligibility(now_ms())
        if reason != "eligible":
            self.store.update(job_id, owner, state="cancelled" if reason in {
                "already_submitted", "assignment_expired", "assignment_closed"} else "needs_input",
                error_code=reason)
            return
        checkpoint = job["checkpoint"]
        answer = Answer.from_dict(read_json(manifest)) if manifest.is_file() else None
        feedback = []
        while True:
            if answer is None:
                attempts = checkpoint.get("model_attempts", 0)
                if attempts >= 3:
                    raise WorkflowError("model_attempts_exhausted")
                checkpoint["model_attempts"] = attempts + 1
                self.store.update(job_id, owner, phase="solving", checkpoint=checkpoint)
                answer = await self.solver.solve(assignment, output_dir, feedback)
            self.store.update(job_id, owner, phase="validating")
            feedback = await asyncio.to_thread(validate, assignment, answer, self.data_dir)
            write_json(output_dir.parent / "validation.json", {
                "passed": not feedback, "errors": feedback, "validated_at_ms": now_ms(),
                "scope": "artifact format, integrity and size; not a guarantee of grading correctness",
            })
            if not feedback:
                break
            if "runner_unavailable" in feedback:
                raise WorkflowError("runner_unavailable")
            answer = None
        self.store.update(job_id, owner, phase="ready")
        # Gate precedes the submission lock and remains held through staging and receipt checks.
        async with self.client.session.operation('assignment'), self.client.session.submission_lock:
            current = await self.client.read_assignment(assignment.course_id, assignment.assignment_id)
            if current.content_hash != assignment.content_hash:
                self.store.update(job_id, owner, state="cancelled", error_code="superseded")
                return
            reason = current.eligibility(now_ms())
            if reason != "eligible":
                raise WorkflowError(reason)
            if await asyncio.to_thread(validate, current, answer, self.data_dir):
                raise WorkflowError("artifact_invalid")
            pending = self.store.prepare_attempt(job_id, owner, answer.answer_hash)
            if pending["state"] != "prepared":
                await self.reconcile(job, owner, assignment, answer, pending)
                return
            await self.client.stage_answer(current, answer)
            observed = await self.client.read_submission(assignment.course_id, assignment.assignment_id)
            if observed.presence != "absent":
                raise WorkflowError("submission_state_unknown")
            self.store.assert_owned(job_id, owner)
            latest = await self.client.read_assignment(assignment.course_id, assignment.assignment_id)
            if latest.content_hash != assignment.content_hash:
                raise WorkflowError('superseded')
            if latest.eligibility(now_ms()) != 'eligible':
                raise WorkflowError(latest.eligibility(now_ms()))
            self.store.dispatch(pending["attempt_id"], job_id, owner)
            try:
                await self.client.click_submit()
            except Exception:
                # A failed click can still have dispatched the form.
                pass
            self.store.update(job_id, owner, phase="reconciling")
            await self.reconcile(job, owner, assignment, answer, pending)
