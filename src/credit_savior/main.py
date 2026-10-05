from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from .artifacts import atomic_write, read_json, write_json
from .assignments import AssignmentWorker
from .browser import BrowserSession
from .config import Config
from .cool import CoolClient
from .errors import ConfigError, WorkflowError
from .locking import InstanceLock
from .models import now_ms
from .scanner import Scanner
from .solver import CodexSolver
from .store import Store
from .videos import VideoWorker
from .workers import follow_up_notes, worker_loop
from .discord import Notifier, problem_message


def configure_logs(data_dir):
    log_path = data_dir / "logs" / "worker.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("credit_savior")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024,
                                     backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def emit(logger, event, **fields):
    logger.info(json.dumps({"timestamp": now_ms(), "event": event, **fields}))


def publish_health(config, store, session, state="running", error_code=None):
    write_json(config.data_dir / 'follow-ups.json', {'schema_version': 1, 'items': [
        {'job_id': job['job_id'], 'kind': job['kind'], 'course_id': job['course_id'],
         'subject_id': job['subject_id'], 'error_code': job['error_code'],
         'ask_later': job['checkpoint'].get('follow_up') or
                      follow_up_notes(config.data_dir, job, job['error_code'])}
        for job in store.list_jobs() if job['state'] in ('needs_input', 'failed')]})
    write_json(config.data_dir / "health.json", {
        "schema_version": 1, "process_state": state, "heartbeat_at_ms": now_ms(),
        "session_state": session.state, **store.summary(), "last_error_code": error_code,
    })


async def heartbeat(config, store, session, stop):
    while not stop.is_set():
        publish_health(config, store, session)
        try:
            await asyncio.wait_for(stop.wait(), 30)
        except asyncio.TimeoutError:
            pass


async def watch_stop(config, stop):
    while not stop.is_set():
        if (config.data_dir / 'stop.request').exists():
            stop.set()
            return
        try:
            await asyncio.wait_for(stop.wait(), 1)
        except asyncio.TimeoutError:
            pass


async def online(config, command, *, once=False):
    logger = configure_logs(config.data_dir)
    with InstanceLock(config.data_dir / "worker.lock"):
        store = Store(config.database_path)
        session = BrowserSession(config, interactive=command == "auth")
        notifier = Notifier(config)
        tasks = []
        try:
            store.recover()
            await session.start()
            emit(logger, "session_ready")
            if command == "auth":
                publish_health(config, store, session, "authenticated")
                return {"session_state": session.state}
            scanner = Scanner(CoolClient(session, "scanner"), store)
            if command == "scan":
                result = await scanner.once()
                publish_health(config, store, session, "scanned")
                emit(logger, "scan_completed", **result)
                return result
            owner = str(uuid4())
            stop = asyncio.Event()
            (config.data_dir / 'stop.request').unlink(missing_ok=True)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except NotImplementedError:
                    signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
            assignment_worker = AssignmentWorker(
                CoolClient(session, "assignment"), store, CodexSolver(config.data_dir, config.model))
            video_worker = VideoWorker(CoolClient(session, "video"), store)
            if once:
                tasks = [asyncio.create_task(heartbeat(config, store, session, stop))]
                result = await scanner.once()
                await worker_loop(assignment_worker, owner + ":assignment", stop, once=True)
                publish_health(config, store, session, "completed_once")
                await notifier.poll(store)
                return {"scan": result, **store.summary()}
            tasks = [
                asyncio.create_task(scanner.run(stop)),
                asyncio.create_task(worker_loop(assignment_worker, owner + ":assignment", stop)),
                asyncio.create_task(worker_loop(video_worker, owner + ":video", stop)),
                asyncio.create_task(heartbeat(config, store, session, stop)),
                asyncio.create_task(watch_stop(config, stop)),
                asyncio.create_task(notifier.run(store, stop)),
            ]
            stopped = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            stopped.cancel()
            for task in done:
                if task is not stopped and not task.cancelled() and task.exception():
                    raise WorkflowError("supervisor_task_failed", transient=True)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), 30)
            except asyncio.TimeoutError:
                pass
            publish_health(config, store, session, "stopped")
            return {"process_state": "stopped"}
        except WorkflowError as error:
            state = "waiting_auth" if error.code in {
                "interactive_required", "password_required", "identity_unverified"} else "failed"
            publish_health(config, store, session, state, error.code)
            emit(logger, "process_failed", error_code=error.code)
            await notifier.send([{'process_error': error.code}],
                                problem_message('process', error.code))
            raise
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await session.close()
            store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Credit Savior: NTU COOL course automation")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("auth", "scan", "status", 'stop'):
        commands.add_parser(name)
    notify = commands.add_parser('notify')
    notify.add_argument('--resend', action='store_true', help='explicitly resend current results once')
    run = commands.add_parser("run")
    run.add_argument("--once", action="store_true",
                     help="scan once and process queued assignments; videos run in continuous mode")
    retry = commands.add_parser("retry")
    retry.add_argument("job_id")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.root, require_auth=args.command in ("auth", "scan", "run"))
        if args.command == 'stop':
            atomic_write(config.data_dir / 'stop.request', b'')
            result = {'stop_requested': True}
        elif args.command == "status":
            result = read_json(config.data_dir / "health.json")
            result["stale"] = now_ms() - result["heartbeat_at_ms"] > 90_000
            result['follow_ups'] = read_json(config.data_dir / 'follow-ups.json')['items']
            try:
                result['discord_error'] = read_json(config.data_dir / 'notifications.json').get('last_error_code')
            except (OSError, ValueError):
                result['discord_error'] = None
        elif args.command in ("retry", "backup", 'notify'):
            with InstanceLock(config.data_dir / "worker.lock"):
                store = Store(config.database_path)
                try:
                    if args.command == 'notify':
                        notifier = Notifier(config)
                        asyncio.run(notifier.poll(store, resend=args.resend))
                        result = {'discord_enabled': bool(config.discord_webhook),
                                  'confirmed_notifications': len(notifier.state['delivered']),
                                  'error_code': notifier.state.get('last_error_code')}
                        if result['error_code']:
                            print(json.dumps(result))
                            return 30
                    elif args.command == "retry":
                        store.retry(args.job_id)
                        result = {"queued_job_id": args.job_id}
                    else:
                        destination = args.destination.resolve()
                        if destination.exists() or destination.is_relative_to(config.data_dir):
                            raise WorkflowError("backup_destination_invalid")
                        destination.mkdir(parents=True)
                        store.backup(destination / "state.sqlite3")
                        for name in ("tasks", "snapshots", "attachments", "videos"):
                            source = config.data_dir / name
                            if source.exists():
                                shutil.copytree(source, destination / name)
                        result = {"backup_completed": True}
                finally:
                    store.close()
        else:
            result = asyncio.run(online(config, args.command, once=getattr(args, "once", False)))
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except ConfigError as error:
        print(json.dumps({"error_code": error.code, "fields": error.fields}))
        return 10
    except WorkflowError as error:
        print(json.dumps({"error_code": error.code}))
        if error.code in {"interactive_required", "password_required", "identity_unverified"}:
            return 20
        return 30 if error.transient else 40
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error_code": "local_data_unavailable"}))
        return 40
    except Exception:
        # Browser exceptions may include form values or authenticated URLs.
        print(json.dumps({'error_code': 'unexpected_local_error'}))
        return 40
