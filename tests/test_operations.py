import json
from types import SimpleNamespace

from credit_savior.main import main, publish_health
from credit_savior.models import Video


def test_status_reads_follow_up_snapshot_and_stop_command(config, store, assignment, capsys):
    job_id = store.enqueue('assignment', '1', '2', assignment.content_hash, 'snapshot.json')
    store.claim('assignment', 'owner')
    store.update(job_id, 'owner', state='needs_input', error_code='missing_problem_material',
                 checkpoint={'follow_up': ['Ask later about the missing report topic.']})
    publish_health(config, store, SimpleNamespace(state='ready'))
    assert main(['--root', str(config.root), 'status']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['follow_ups'][0]['ask_later'] == ['Ask later about the missing report topic.']
    assert not result['stale']
    assert main(['--root', str(config.root), 'stop']) == 0
    assert (config.data_dir / 'stop.request').exists()


def test_video_claim_follows_playlist_order(store):
    for order, vid in ((2, '7'), (1, '8'), (0, '9')):
        store.upsert_video(Video('1', vid, 'url', 'rev'))
        store.enqueue('video', '1', vid, 'rev', 'video.json', checkpoint={'playlist_order': order})
    assert store.claim('video', 'owner')['subject_id'] == '9'
