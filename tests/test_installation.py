import json
import base64
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from scripts.configure_env import SetupError, configure


def test_dialog_config_preserves_existing_settings_and_quotes_password(config):
    password = "different # password ' with spaces"
    configure(config.root, {'COOL_USERNAME': '', 'COOL_PASSWORD': password, 'DISCORD_WEBHOOK_URL': ''})
    values = dotenv_values(config.root / '.env')
    assert values['COOL_USERNAME'] == config.username
    assert values['COOL_PASSWORD'] == password
    assert values['COOL_COURSE_IDS'] == 'all'
    assert not list(config.root.glob('.env.*.tmp'))


def test_dialog_first_install_uses_example_and_requires_both_credentials(tmp_path):
    example = Path(__file__).parents[1] / '.env.example'
    (tmp_path / '.env.example').write_bytes(example.read_bytes())
    with pytest.raises(SetupError, match='COOL_PASSWORD_required'):
        configure(tmp_path, {'COOL_USERNAME': 'fixture-owner'})
    assert not (tmp_path / '.env').exists()
    configure(tmp_path, {'COOL_USERNAME': 'fixture-owner', 'COOL_PASSWORD': 'fixture-pass'})
    values = dotenv_values(tmp_path / '.env')
    assert values['LLM_MODEL'] == 'gpt-6.1-sol'
    assert values['CHECK_INTERVAL_SECONDS'] == '600'


def test_dialog_invalid_webhook_and_account_change_leave_original_file(config):
    original = (config.root / '.env').read_bytes()
    with pytest.raises(SetupError, match='invalid_webhook'):
        configure(config.root, {'DISCORD_WEBHOOK_URL': 'https://example.invalid/token'})
    with pytest.raises(SetupError, match='password_required_for_account_change'):
        configure(config.root, {'COOL_USERNAME': 'different-owner'})
    assert (config.root / '.env').read_bytes() == original


def test_dialog_helper_receives_secret_on_stdin_and_never_returns_it(config):
    helper = Path(__file__).parents[1] / 'scripts/configure_env.py'
    secret = 'fixture-dialog-secret'
    result = subprocess.run([sys.executable, str(helper), '--root', str(config.root)],
                            input=json.dumps({'COOL_PASSWORD': secret}), text=True,
                            capture_output=True, check=True)
    assert json.loads(result.stdout) == {'saved': True}
    assert secret not in result.stdout + result.stderr
    assert dotenv_values(config.root / '.env')['COOL_PASSWORD'] == secret


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows dialog uses PowerShell')
def test_dialog_powershell_bridge_preserves_unicode_and_hides_credentials(config):
    scripts = Path(__file__).parents[1] / 'scripts'
    secret = 'fixture-' + chr(0x5bc6) + chr(0x78bc) + " # ' " + chr(0x5c)

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    command = f'''
    $pythonPath = {literal(sys.executable)}
    $helperPath = {literal(scripts / 'configure_env.py')}
    $repoRoot = {literal(config.root)}
    $parseTokens = $null; $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        {literal(scripts / 'ConfigureEnv.ps1')}, [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count) {{ throw 'PowerShell parse failed' }}
    $definition = $ast.Find({{ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Save-LocalSettings'
    }}, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
    $fields = @{{ COOL_PASSWORD = {literal(secret)} }}
    $reply = Save-LocalSettings $fields
    if (-not $reply.saved) {{ throw ('Save failed: ' + $reply.error_code) }}
    if ($fields.Count) {{ throw 'Input fields retained' }}
    $reply | ConvertTo-Json -Compress
    '''
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                             '-EncodedCommand', encoded], capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    output = result.stdout.decode('utf-8-sig').strip()
    assert json.loads(output) == {'saved': True}
    assert secret.encode() not in result.stdout + result.stderr
    assert dotenv_values(config.root / '.env')['COOL_PASSWORD'] == secret
