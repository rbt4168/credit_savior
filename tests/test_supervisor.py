import json
import sys

from scripts.supervise_worker import supervise


def run_fixture(config, text, **kwargs):
    script = config.root / 'worker_fixture.py'
    script.write_text(text, encoding='utf-8')
    return supervise(config.root, command=[sys.executable, str(script), str(config.data_dir)],
                     poll_s=.01, retry_delays=(0,), **kwargs)


def events(config):
    return [json.loads(s) for s in (config.data_dir / 'logs/supervisor.jsonl').read_text().splitlines()]


def test_stderr_warning_does_not_abort_and_exit_one_is_restarted(config):
    assert run_fixture(config, '''
from pathlib import Path
import sys
p=Path(sys.argv[1]); marker=p/'started-once'
sys.stderr.write('Synthetic nonfatal warning\\n')
if not marker.exists():
    marker.write_text('first'); raise SystemExit(1)
(p/'stop.request').write_text('')
''') == 0
    assert sum(e['event'] == 'worker_started' for e in events(config)) == 2
    assert b'Synthetic nonfatal warning' in (config.data_dir / 'logs/worker-console.log').read_bytes()


def test_missing_heartbeat_kills_only_child_and_restarts(config):
    assert run_fixture(config, '''
from pathlib import Path
import sys,time
p=Path(sys.argv[1]); marker=p/'started-once'
if not marker.exists():
    marker.write_text('first'); time.sleep(30)
else:
    (p/'stop.request').write_text('')
''', startup_s=.15) == 0
    assert any(e['event'] == 'heartbeat_stale' for e in events(config))
    assert sum(e['event'] == 'worker_started' for e in events(config)) == 2


def test_required_interactive_login_stops_without_restart_storm(config):
    assert run_fixture(config, 'raise SystemExit(20)') == 0
    assert sum(e['event'] == 'worker_started' for e in events(config)) == 1
    assert events(config)[-1]['event'] == 'waiting_for_configuration_or_login'
