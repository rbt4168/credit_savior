import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from credit_scammer.artifacts import write_json
from credit_scammer.assignments import AssignmentWorker
from credit_scammer.browser import BrowserSession
from credit_scammer.cool import CoolClient
from credit_scammer.models import Answer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, content_type='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/api/v1/courses/1/assignments/2/submissions/self'):
            self.reply(json.dumps(self.server.submission).encode())
        elif self.path.startswith('/api/v1/courses/1/assignments/2'):
            value = {**self.server.assignment, 'submission': self.server.submission}
            self.reply(json.dumps(value).encode())
        else:
            self.reply(b'''<div id="assignment_show"><button type="button">Submit Assignment</button></div>
            <textarea id="submission_body"></textarea><button id="submit_file_button">Submit</button>
            <script>document.querySelector('#submit_file_button').onclick=async()=>{
              await fetch('/submit',{method:'POST',body:JSON.stringify({text:document.querySelector('#submission_body').value})});
            };</script>''', 'text/html; charset=utf-8')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.clicks += 1
        from datetime import datetime, timezone
        self.server.submission = {'workflow_state': 'submitted', 'attempt': 1,
            'submitted_at': datetime.now(timezone.utc).isoformat(),
            'body': '<p>' + escape(body['text']) + '</p>', 'attachments': []}
        self.reply(b'{}')


@asynccontextmanager
async def server():
    instance = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    instance.assignment = {'id': 2, 'course_id': 1, 'name': 'Arithmetic',
        'description': '<p>Explain 2+2.</p>', 'submission_types': ['online_text_entry']}
    instance.submission = {'workflow_state': 'unsubmitted'}
    instance.clicks = 0
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, f'http://127.0.0.1:{instance.server_port}'
    finally:
        await asyncio.to_thread(instance.shutdown)
        instance.server_close()
        thread.join()


async def test_real_browser_submission_form_and_receipt(config, store):
    async with server() as (http, base):
        session = BrowserSession(replace(config, base_url=base))
        from playwright.async_api import async_playwright
        session.playwright = await async_playwright().start()
        session.browser = await session.playwright.chromium.launch(headless=True)
        await session._create_context()
        session.state = 'ready'
        client = CoolClient(session, 'assignment')
        try:
            assignment = await client.read_assignment('1', '2')
            payload = 'snapshots/browser.json'
            write_json(config.data_dir / payload, assignment.to_dict())
            store.upsert_assignment(assignment, payload)
            job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, payload)

            class Solver:
                async def solve(self, assignment, output_dir, feedback):
                    answer = Answer('text', text='2 + 2 = 4. <verified>')
                    write_json(output_dir / 'manifest.json', answer.to_dict())
                    return answer
            worker = AssignmentWorker(client, store, Solver(), receipt_delays=(.05, .1, .2))
            await worker.process(store.claim('assignment', 'owner'), 'owner')
            assert store.get(job_id)['state'] == 'succeeded'
            assert http.clicks == 1
            assert store.pending('1', '2') is None
        finally:
            await session.close()


async def test_nested_operation_with_waiting_auth_writer(config):
    session = BrowserSession(config)
    session.context, session.state = object(), 'ready'

    class Page:
        def is_closed(self):
            return False
    session.pages['assignment'] = Page()
    entered = asyncio.Event()

    async def writer():
        async with session.gate.write():
            entered.set()

    async with session.operation('assignment'):
        task = asyncio.create_task(writer())
        await asyncio.sleep(0)
        assert session.gate.waiting_writers == 1
        deadline = asyncio.get_running_loop().call_later(1, asyncio.current_task().cancel)
        try:
            async with session.operation('assignment'):
                assert not entered.is_set()
        finally:
            deadline.cancel()
    await task
    assert entered.is_set()
