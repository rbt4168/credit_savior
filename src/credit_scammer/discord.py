from __future__ import annotations

import asyncio
import json
import re
import time
from threading import Lock
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
from pypdf import PdfReader

from .artifacts import artifact_path, canonical_hash, digest, read_json, write_json
from .models import Answer
from .workers import follow_up_notes
from .errors import WorkflowError
from .models import now_ms


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def multipart(payload, files):
    boundary = 'ntu-cool-' + uuid4().hex
    blocks = [(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
               'Content-Type: application/json\r\n\r\n').encode(),
              json.dumps(payload, ensure_ascii=False).encode('utf-8'), b'\r\n']
    for index, (filename, content) in enumerate(files):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', filename):
            raise WorkflowError('notification_filename_invalid')
        blocks.extend([(f'--{boundary}\r\nContent-Disposition: form-data; '
                        f'name="files[{index}]"; filename="{filename}"\r\n'
                        'Content-Type: application/octet-stream\r\n\r\n').encode(),
                       content, b'\r\n'])
    blocks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(blocks), f'multipart/form-data; boundary={boundary}'


class DiscordTransport:
    def __init__(self, webhook):
        if not re.fullmatch(r'https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_-]+', webhook):
            raise WorkflowError('notification_config_invalid')
        self._webhook = webhook
        self._rate_lock = Lock()
        self._not_before = 0.0

    def post(self, content, files=()):
        with self._rate_lock:
            for attempt in range(3):
                while self._not_before > time.monotonic():
                    time.sleep(min(10, self._not_before-time.monotonic()))
                try:
                    return self._post(content, files)
                except RateLimited as error:
                    self._not_before = time.monotonic() + error.retry_after
                    if attempt == 2:
                        raise

    def _post(self, content, files=()):
        if len(content) > 2000 or len(files) > 10:
            raise WorkflowError('notification_payload_invalid')
        title, _, description = content.partition('\n')
        payload = {'embeds': [{'title': title[:256], 'description': description[:4096],
                              'color': 0x2ECC71 if '已提交並確認' in content else 0xF39C12}],
                   'username': 'NTU COOL',
                   'allowed_mentions': {'parse': []}}
        if files:
            payload['attachments'] = [{'id': index, 'filename': item[0]}
                                      for index, item in enumerate(files)]
            data, content_type = multipart(payload, files)
        else:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json'
        request = Request(self._webhook + '?wait=true', data=data, method='POST', headers={
            'Content-Type': content_type, 'User-Agent': 'NTU-COOL-Worker/0.1'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                value = json.loads(response.read(1024 * 1024))
                headers = getattr(response, 'headers', {})
                if headers.get('X-RateLimit-Remaining') == '0':
                    try:
                        delay = max(0, min(60, float(headers.get('X-RateLimit-Reset-After', 2))))
                    except (ValueError, TypeError):
                        delay = 2
                    self._not_before = time.monotonic() + delay
            if not isinstance(value, dict) or not value.get('id'):
                raise WorkflowError('notification_unconfirmed')
            if files and {x.get('filename') for x in value.get('attachments', [])} != {
                    x[0] for x in files}:
                raise WorkflowError('notification_attachment_unconfirmed')
            # Keep only confirmation data; the response may contain channel/user metadata.
            return {'message_id': str(value['id']), 'attachment_count': len(value.get('attachments', []))}
        except HTTPError as error:
            if error.code == 429:
                try:
                    delay = float(json.loads(error.read(4096)).get('retry_after', 5))
                except (ValueError, TypeError):
                    delay = 5
                error.close()
                raise RateLimited(max(1, min(60, delay))) from None
            code = 'notification_rate_limited' if error.code == 429 else 'notification_http_failed'
            error.close()
            raise WorkflowError(code, transient=True) from None
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError('notification_delivery_unconfirmed', transient=True) from None


class RateLimited(WorkflowError):
    def __init__(self, retry_after):
        super().__init__('notification_rate_limited', transient=True)
        self.retry_after = retry_after


def item_message(course_name, course_id, category, title, message):
    return f'{course_name} ({course_id}) - {category}\n**{title}**\n{message}'


def problem_message(kind, error_code):
    descriptions = {
        'missing_problem_material': '缺少必要題目或報告設定，暫時略過；已註記之後要詢問。',
        'runner_unavailable': '程式題的隔離執行環境尚未配置，暫時略過；已註記之後要處理。',
        'progress_unavailable': '影片已播放，但平台觀看紀錄仍有缺口或無法核對；已註記之後要處理。',
        'submission_uncertain': '平台提交結果尚未確認；保留紀錄並只讀核對，不自動重送。',
        'interactive_required': '登入需要本人完成額外驗證，常駐程序暫停。',
        'auth_expired': '登入狀態已失效，需要重新驗證。',
        'scan_capacity_exceeded': '巡檢超過單輪時間上限，部分範圍留待後續處理。',
    }
    description = descriptions.get(error_code, '此流程遇到問題，已保存本機診斷並暫時略過。')
    label = {'assignment': '作業', 'video': '影片', 'scan': '巡檢', 'process': '常駐程序'}.get(kind, '系統')
    # Only controlled labels/error codes leave this host, never titles, IDs or model text.
    safe_code = error_code if re.fullmatch(r'[a-z_]{1,80}', error_code or '') else 'unspecified'
    return item_message('NTU COOL', '系統', '其他', label+'需要處理',
                        description+f'\n問題類型：{safe_code}')


class Notifier:
    def __init__(self, config, transport=None):
        self.path = config.data_dir / 'notifications.json'
        self.config = config
        self.transport = transport or (DiscordTransport(config.discord_webhook)
                                       if config.discord_webhook else None)
        try:
            self.state = read_json(self.path)
        except (OSError, ValueError):
            self.state = {'schema_version': 1, 'delivered': {}}
        self.lock = asyncio.Lock()
        self.not_before = 0

    def redact(self, text):
        values = [self.config.username, self.config.password, self.config.discord_webhook,
                  str(self.config.root), self.config.root.as_posix()]
        if self.config.discord_webhook:
            values.append(self.config.discord_webhook.rsplit('/', 1)[-1])
        for value in sorted(set(values), key=len, reverse=True):
            if value:
                text = re.sub(re.escape(value), '[已隱藏]', text, flags=re.I)
        # Local profile paths and credential-bearing URLs never leave the host.
        text = re.sub(r'[A-Za-z]:[\\/]Users[\\/][^\s]+', '[本機路徑已隱藏]', text)
        text = re.sub(r'https://discord\.com/api/webhooks/[^\s]+', '[Webhook 已隱藏]', text)
        return text

    def answer_files(self, job):
        directory = self.config.data_dir / 'tasks' / job['job_id'] / 'answer'
        manifest = directory / 'manifest.json'
        if not manifest.exists():
            response_path = directory / 'model-response.json'
            if not response_path.exists():
                return []
            response = read_json(response_path)
            if not response.get('answer_text') and not response.get('artifacts'):
                return []
            # Render partial model output only for delivery. The worker must never
            # reuse this manifest as a validated solution on a later retry.
            from .solver import CodexSolver
            directory = self.config.data_dir / 'delivery-drafts' / job['job_id']
            directory.mkdir(parents=True, exist_ok=True)
            if response.get('artifacts'):
                response = {**response, 'answer_kind': 'files'}
            CodexSolver(self.config.data_dir).render(response, directory)
            manifest = directory / 'manifest.json'
        answer = Answer.from_dict(read_json(manifest))
        if answer.kind == 'text':
            return [('answer.txt', self.redact(answer.text).encode('utf-8'))]
        files = []
        if not answer.files or len(answer.files) != len(answer.hashes) or len(answer.files) > 10:
            raise WorkflowError('notification_artifact_invalid')
        for relative, expected in zip(answer.files, answer.hashes):
            path = artifact_path(self.config.data_dir, relative)
            data = path.read_bytes()
            if digest(data) != expected or len(data) > 10 * 1024 * 1024:
                raise WorkflowError('notification_artifact_invalid')
            if path.suffix == '.pdf':
                text = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
            else:
                text = data.decode('utf-8')
            if self.redact(text) != text or self.redact(path.name) != path.name:
                raise WorkflowError('notification_artifact_contains_private_data')
            files.append((path.name, data))
        return files

    async def send(self, keys, message, files=()):
        if not self.transport:
            return False
        async with self.lock:
            if time.monotonic() < self.not_before:
                return False
            pending = [canonical_hash(key) for key in keys
                       if canonical_hash(key) not in self.state['delivered']]
            if not pending:
                return True
            try:
                receipt = await asyncio.to_thread(self.transport.post, self.redact(message)[:2000], files)
            except WorkflowError:
                self.not_before = time.monotonic() + 60
                self.state['last_error_code'] = 'notification_delivery_unconfirmed'
                write_json(self.path, self.state)
                return False
            self.state['last_error_code'] = None
            for original in keys:
                key = canonical_hash(original)
                if key not in pending:
                    continue
                record = {'at_ms': now_ms(), **receipt}
                self.state['delivered'][key] = record
                if 'manual_resend' in original:
                    normal = {k: v for k, v in original.items() if k != 'manual_resend'}
                    self.state['delivered'][canonical_hash(normal)] = record
            write_json(self.path, self.state)
            return True

    async def poll(self, store, *, resend=False):
        resend_token = uuid4().hex if resend else None
        video_problems = {}
        for job in store.list_jobs():
            recovered = (resend and job['kind'] == 'video' and job['state'] == 'queued'
                         and job['checkpoint'].get('error_class') == 'TargetClosedError')
            if job['state'] not in ('needs_input', 'failed', 'succeeded') and not recovered:
                continue
            if job['state'] == 'succeeded' and job['kind'] != 'assignment':
                continue
            kind, code = job['kind'], job['error_code'] or 'unspecified'
            if recovered:
                code = 'browser_recovery_queued'
            if kind == 'video':
                video_problems.setdefault((job['course_id'], code), []).append(job)
                continue
            key = {'job_id': job['job_id'], 'state': job['state'], 'error_code': code,
                   'model_attempts': job['checkpoint'].get('model_attempts', 0)}
            if resend_token:
                key['manual_resend'] = resend_token
            if kind == 'assignment':
                key['delivery_format'] = 'course-category-subtitle-v1'
                directory = self.config.data_dir / 'tasks' / job['job_id'] / 'answer'
                source = directory / 'manifest.json'
                if not source.exists():
                    source = directory / 'model-response.json'
                if source.exists():
                    key['artifact_version'] = digest(source.read_bytes())
            if canonical_hash(key) in self.state['delivered']:
                continue
            if kind == 'assignment':
                info = store.subject_info(job)
                success = job['state'] == 'succeeded'
                notes = job['checkpoint'].get('follow_up') or follow_up_notes(
                    self.config.data_dir, job, code)
                main = (f"{'已提交並確認回執。' if success else '暫時略過，待補資料/處理。'}\n"
                        f"位置：{info['url']}\n")
                message = item_message(info['course_name'], job['course_id'], '作業', info['title'], main)
                if not success:
                    message += '尚未確認提交。之後要詢問/處理：\n' + '\n'.join('- '+x for x in notes)
                try:
                    files = await asyncio.to_thread(self.answer_files, job)
                except Exception:
                    files = []
                    message += '\n附件暫不傳送：未通過隱私或檔案完整性檢查；檔案保留本機。'
                if not files:
                    message += '\n目前沒有可附上的答案檔案。'
                elif not success:
                    message += '\n附件為未提交的草稿。'
                await self.send([key], message, files)
        alerts = self.state.setdefault('video_alerts', {})
        active_alerts = set()
        for (course_id, code), jobs in video_problems.items():
            alert_key = course_id+':'+code
            active_alerts.add(alert_key)
            previous = alerts.get(alert_key)
            if not resend and previous and (now_ms() - previous['at_ms'] < 3600_000
                             or previous['count'] == len(jobs)):
                continue
            entries = []
            for job in jobs:
                try:
                    video = read_json(artifact_path(self.config.data_dir, job['payload_path']))
                except (OSError, ValueError):
                    continue
                if not video.get('title'):
                    continue  # Wait for a scan to capture the actual title instead of an anonymous alert.
                entries.append(f"課程：{store.course_name(job['course_id'])}\n"
                               f"影片：{video['title']}\n位置：{video['url']}")
            if not entries:
                continue
            full_list = '\n\n'.join(entries)
            files = []
            preview = full_list
            if len(preview) > 1200:
                preview = entries[0] + f'\n\n其餘影片見附件完整清單（共 {len(entries)} 支）。'
                files = [('video-problems.txt', self.redact(full_list).encode('utf-8'))]
            # The requested course/title structure remains readable inside one grouped embed.
            first_job = jobs[0]
            first_video = read_json(artifact_path(self.config.data_dir, first_job['payload_path']))
            message = (item_message(store.course_name(first_job['course_id']), first_job['course_id'],
                        '影片', first_video.get('title') or '影片問題清單',
                        ('先前瀏覽器中斷；已排回待重試。' if code == 'browser_recovery_queued'
                         else '影片處理暫時略過。')+'問題類型：'+code) + '\n\n' + preview +
                       '\n\n同類問題合併通知；至少冷卻 60 分鐘，不逐支洗版。')
            key = {'video_summary': code, 'course_id': course_id, 'hour': now_ms() // 3600_000}
            if resend_token:
                key['manual_resend'] = resend_token
            if await self.send([key], message, files):
                alerts[alert_key] = {'at_ms': now_ms(), 'count': len(jobs)}
                write_json(self.path, self.state)
        for code in set(alerts) - active_alerts:
            del alerts[code]
            write_json(self.path, self.state)
        last = store.summary()['last_scan']
        if last and last['state'] == 'partial':
            code = last['error_code'] or 'scan_partial'
            await self.send([{'scan_id': last['scan_id'], 'error_code': code}],
                            problem_message('scan', code))

    async def run(self, store, stop):
        while not stop.is_set():
            try:
                await self.poll(store)
            except Exception:
                # Delivery failures do not prevent course checks or destroy the queue.
                self.state['last_error_code'] = 'notification_local_error'
                write_json(self.path, self.state)
            try:
                await asyncio.wait_for(stop.wait(), 15)
            except asyncio.TimeoutError:
                pass
