"""Write credentials received on stdin, without exposing them in process arguments."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key


class SetupError(Exception):
    pass


def configure(root: Path, fields: dict[str, str]) -> None:
    root = root.resolve()
    destination = root / '.env'
    if destination.is_symlink():
        raise SetupError('env_symlink_not_supported')
    allowed = {'COOL_USERNAME', 'COOL_PASSWORD', 'DISCORD_WEBHOOK_URL'}
    if (not isinstance(fields, dict) or set(fields) - allowed or
            any(not isinstance(value, str) or len(value) > 2048 or
                any(character in value for character in '\r\n\x00') for value in fields.values())):
        raise SetupError('invalid_credential_input')
    updates = {key: value for key, value in fields.items() if value}
    for key in ('COOL_USERNAME', 'DISCORD_WEBHOOK_URL'):
        if key in updates:
            updates[key] = updates[key].strip()
    source = destination if destination.exists() else root / '.env.example'
    existing = dotenv_values(source, encoding='utf-8', interpolate=False)
    values = {**existing, **updates}
    if not values.get('COOL_USERNAME'):
        raise SetupError('COOL_USERNAME_required')
    if not values.get('COOL_PASSWORD'):
        raise SetupError('COOL_PASSWORD_required')
    if (updates.get('COOL_USERNAME', existing.get('COOL_USERNAME')) != existing.get('COOL_USERNAME')
            and existing.get('COOL_USERNAME') and not updates.get('COOL_PASSWORD')):
        raise SetupError('password_required_for_account_change')
    webhook = values.get('DISCORD_WEBHOOK_URL') or ''
    if webhook and not re.fullmatch(r'https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_-]+', webhook):
        raise SetupError('invalid_webhook')
    text = source.read_text(encoding='utf-8')
    descriptor, temporary_name = tempfile.mkstemp(prefix='.env.', suffix='.tmp', dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as output:
            output.write(text)
        for key, value in updates.items():
            set_key(temporary, key, value, quote_mode='always', encoding='utf-8')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    fields = {}
    try:
        payload = sys.stdin.read(8193)
        if len(payload) > 8192:
            raise SetupError('invalid_credential_input')
        fields = json.loads(payload)
        configure(args.root, fields)
        print(json.dumps({'saved': True}))
        return 0
    except SetupError as error:
        print(json.dumps({'saved': False, 'error_code': str(error)}))
        return 1
    except Exception:
        print(json.dumps({'saved': False, 'error_code': 'configuration_write_failed'}))
        return 1
    finally:
        if isinstance(fields, dict):
            fields.clear()


if __name__ == '__main__':
    sys.exit(main())
