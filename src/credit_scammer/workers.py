from __future__ import annotations

import asyncio
from contextlib import suppress
from playwright.async_api import Error as BrowserError

from .errors import LeaseLost, WorkflowError
from .models import now_ms
from .artifacts import read_json


def follow_up_notes(data_dir, job, error_code):
    if error_code == 'missing_problem_material':
        try:
            response = read_json(data_dir / 'tasks' / job['job_id']
                                 / 'answer' / 'model-response.json')
            notes = response.get('missing_information')
            if isinstance(notes, list) and all(isinstance(x, str) for x in notes) and notes:
                return notes[:20]
        except (OSError, ValueError):
            pass
    return [{
        'runner_unavailable': '之後確認程式題的 runtime、隔離 runner 與測試要求。',
        'missing_problem_material': '之後詢問題目缺少的附件、報告主題或補充說明。',
        'image_attachment_unsupported': '之後取得圖片/PDF 的可讀題目文字或支援視覺處理。',
        'submission_uncertain': '之後核對平台提交回執；禁止直接重送。',
        'progress_unavailable': '之後核對平台觀看紀錄與影片長度差異。',
    }.get(error_code, f'之後確認此任務無法完成的原因：{error_code}。')]


async def keep_lease(store, job_id, owner, task):
    while True:
        await asyncio.sleep(30)
        if not store.renew(job_id, owner):
            task.cancel()
            return


async def execute_claimed(worker, job, owner):
    current = asyncio.current_task()
    lease = asyncio.create_task(keep_lease(worker.store, job["job_id"], owner, current))
    try:
        await worker.process(job, owner)
    except LeaseLost:
        return
    except WorkflowError as error:
        latest = worker.store.get(job["job_id"])
        checkpoint = latest["checkpoint"]
        failures = checkpoint.get("failures", 0) + 1
        checkpoint["failures"] = failures
        checkpoint['follow_up'] = follow_up_notes(worker.client.config.data_dir, job, error.code)
        try:
            pending = worker.store.pending(job["course_id"], job["subject_id"])
            if pending and pending["state"] in ("dispatching", "uncertain"):
                worker.store.update(job["job_id"], owner, phase="reconciling", state="needs_input",
                                    checkpoint=checkpoint, error_code="submission_uncertain")
            elif error.transient and failures <= 3:
                worker.store.update(job["job_id"], owner, state="retry_wait", checkpoint=checkpoint,
                                    error_code=error.code,
                                    retry_after_ms=now_ms() + (5, 15, 45)[failures - 1] * 1000)
            else:
                worker.store.update(job["job_id"], owner,
                                    state="failed" if error.transient else "needs_input",
                                    checkpoint=checkpoint, error_code=error.code)
        except LeaseLost:
            pass
        if error.code == "auth_expired":
            await worker.client.session.recover()
    except asyncio.CancelledError:
        # Keep the durable phase/intent; startup recovery decides whether to reconcile.
        raise
    except BrowserError as error:
        if type(error).__name__ == 'TargetClosedError':
            with suppress(LeaseLost):
                pending = worker.store.pending(job['course_id'], job['subject_id'])
                uncertain = pending and pending['state'] in ('dispatching', 'uncertain')
                worker.store.update(job['job_id'], owner,
                    phase='reconciling' if uncertain else None,
                    state='needs_input' if uncertain else 'retry_wait',
                    error_code='submission_uncertain' if uncertain else 'browser_closed',
                    retry_after_ms=now_ms()+5000,
                    checkpoint={**worker.store.get(job['job_id'])['checkpoint'],
                                'error_class': 'TargetClosedError'})
            # End this process instead of consuming every remaining job against a dead browser.
            raise WorkflowError('browser_closed', transient=True) from None
        with suppress(LeaseLost):
            worker.store.update(job['job_id'], owner, state='needs_input',
                                error_code='browser_action_unavailable',
                                checkpoint={**worker.store.get(job['job_id'])['checkpoint'],
                                            'error_class': type(error).__name__,
                                            'follow_up': ['之後確認播放器或頁面控制項是否可用。']})
    except Exception as error:
        with suppress(LeaseLost):
            pending = worker.store.pending(job['course_id'], job['subject_id'])
            uncertain = pending and pending['state'] in ('dispatching', 'uncertain')
            worker.store.update(job["job_id"], owner,
                                phase='reconciling' if uncertain else None,
                                state='needs_input' if uncertain else 'failed',
                                error_code='submission_uncertain' if uncertain else 'worker_unexpected_error',
                                checkpoint={**worker.store.get(job['job_id'])['checkpoint'],
                                            'error_class': type(error).__name__,
                                            'follow_up': ['之後檢查此任務的錯誤及平台回執。']})
    finally:
        lease.cancel()
        with suppress(asyncio.CancelledError):
            await lease


async def worker_loop(worker, owner, stop: asyncio.Event, *, once=False):
    while not stop.is_set():
        job = worker.store.claim(worker.kind, owner)
        if job:
            await execute_claimed(worker, job, owner)
        elif once:
            return
        else:
            try:
                await asyncio.wait_for(stop.wait(), 2)
            except asyncio.TimeoutError:
                pass
