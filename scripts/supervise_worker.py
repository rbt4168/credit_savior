"""Keep one local worker alive; child stderr never passes through PowerShell."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from credit_savior.config import Config
from credit_savior.locking import InstanceLock
from credit_savior.models import now_ms


def terminate_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        process.kill()
    process.wait(timeout=15)


def supervise(root, *, command=None, poll_s=2, startup_s=180, stale_s=120,
              retry_delays=(15, 60, 300)):
    config = Config.load(Path(root))
    directory = config.data_dir / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    stop_path = config.data_dir / 'stop.request'
    health_path = config.data_dir / 'health.json'
    command = command or [sys.executable, '-m', 'credit_savior', '--root', str(config.root), 'run']

    def event(name, **fields):
        with (directory / 'supervisor.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'at_ms': now_ms(), 'event': name, **fields}) + '\n')

    with InstanceLock(config.data_dir / 'supervisor.lock'):
        stop_path.unlink(missing_ok=True)
        failures = 0
        while True:
            started_ms, started = now_ms(), time.monotonic()
            with (directory / 'worker-console.log').open('ab') as output:
                env = {**os.environ, 'CREDIT_SAVIOR_SUPERVISED': '1'}
                process = subprocess.Popen(command, cwd=config.root, stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT, env=env,
                                           **({'creationflags': subprocess.CREATE_NO_WINDOW}
                                              if os.name == 'nt' else {}))
                event('worker_started', pid=process.pid)
                stop_at = None
                try:
                    while process.poll() is None:
                        current = time.monotonic()
                        if stop_path.exists():
                            stop_at = stop_at or current
                            if current - stop_at > 40:
                                event('stop_grace_exceeded')
                                terminate_tree(process)
                            time.sleep(poll_s)
                            continue
                        try:
                            health = json.loads(health_path.read_text(encoding='utf-8'))
                            heartbeat = health.get('heartbeat_at_ms', 0)
                        except (OSError, ValueError, AttributeError):
                            heartbeat = 0
                        fresh = heartbeat >= started_ms - 2000
                        if ((not fresh and current - started > startup_s)
                                or (fresh and (now_ms() - heartbeat) / 1000 > stale_s)):
                            event('heartbeat_stale')
                            terminate_tree(process)
                            break
                        time.sleep(poll_s)
                finally:
                    if process.poll() is None:
                        terminate_tree(process)
                code = process.wait()
            event('worker_exited', exit_code=code)
            if stop_path.exists():
                return 0
            if code in (10, 20):
                event('waiting_for_configuration_or_login', exit_code=code)
                return 0
            # Normal stop requests are handled above. Any other exit needs recovery.
            failures = 0 if time.monotonic() - started > 600 else failures
            delay = retry_delays[min(failures, len(retry_delays) - 1)]
            failures += 1
            event('restart_scheduled', delay_s=delay)
            until = time.monotonic() + delay
            while time.monotonic() < until:
                if stop_path.exists():
                    return 0
                time.sleep(min(poll_s, max(0, until - time.monotonic())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    try:
        raise SystemExit(supervise(args.root))
    except Exception as error:
        # Keep credentials and exception text out of PowerShell/Task Scheduler output.
        print(json.dumps({'error_code': getattr(error, 'code', 'supervisor_unexpected_error')}))
        raise SystemExit(1) from None
