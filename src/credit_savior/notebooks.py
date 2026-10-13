"""Read user-configured public notebook sources without executing their code."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

from .artifacts import digest, read_json, write_json
from .errors import WorkflowError


def course_source(data_dir: Path, course_id: str):
    path = data_dir / 'course-sources/sources.json'
    if not path.is_file():
        return None
    try:
        profile = read_json(path)['courses'].get(course_id)
        if profile is None:
            return None
        if not isinstance(profile, dict) or profile.get('model') not in {'gpt-6.1-sol', 'gpt-6-astra'}:
            raise ValueError()
        repository = profile.get('repository', '')
        match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_-]+)', repository)
        if not match:
            raise ValueError()
        return {**profile, 'directory': data_dir / 'course-sources' / match[2]}
    except (KeyError, TypeError, ValueError, OSError):
        raise WorkflowError('course_source_config_invalid') from None


def refresh_source(data_dir: Path, course_id: str):
    profile = course_source(data_dir, course_id)
    if not profile:
        return
    folder = profile['directory']
    folder.parent.mkdir(parents=True, exist_ok=True)
    hooks = folder.parent / 'empty-hooks'
    hooks.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'HOME', 'TEMP', 'TMP',
        'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'}}
    env['GIT_TERMINAL_PROMPT'] = '0'
    base = ['git', '-c', f'core.hooksPath={hooks.resolve()}', '-c', 'credential.helper=']

    def git(args):
        try:
            result = subprocess.run(base + args, env=env, capture_output=True, timeout=45,
                                    **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        except (OSError, subprocess.TimeoutExpired):
            raise WorkflowError('course_source_unavailable', transient=True) from None
        if result.returncode:
            raise WorkflowError('course_source_unavailable', transient=True)
        return result.stdout.decode('utf-8').strip()

    if folder.exists():
        if git(['-C', str(folder), 'remote', 'get-url', 'origin']).removesuffix('.git') != profile['repository']:
            raise WorkflowError('course_source_repository_mismatch')
        if git(['-C', str(folder), 'status', '--porcelain']):
            raise WorkflowError('course_source_local_changes')
        git(['-C', str(folder), 'fetch', 'origin'])
        git(['-C', str(folder), 'merge', '--ff-only', 'origin/HEAD'])
    else:
        git(['clone', profile['repository'] + '.git', str(folder)])


def enrich_assignment(data_dir: Path, assignment):
    profile = course_source(data_dir, assignment.course_id)
    match = re.match(r'^(\d{4})(?:\b|\s)', assignment.title)
    if not profile or not match:
        return assignment
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    year = datetime.fromtimestamp(assignment.due_at_ms / 1000, ZoneInfo('Asia/Taipei')).year if assignment.due_at_ms else now.year
    try:
        target = datetime.strptime(str(year) + match[1], '%Y%m%d').date()
    except ValueError:
        return assignment
    if target.weekday() not in (0, 4):
        return assignment
    candidates = []
    for file in profile['directory'].glob('*.ipynb'):
        if not re.fullmatch(r'\d{4}', file.stem):
            continue
        for candidate_year in (year - 1, year):
            try:
                day = datetime.strptime(str(candidate_year) + file.stem, '%Y%m%d').date()
            except ValueError:
                continue
            if timedelta(0) < target - day <= timedelta(days=7):
                candidates.append((day, file))
    if not candidates:
        return assignment
    _, file = max(candidates, key=lambda item: item[0])
    if file.stat().st_size > 5 * 1024 * 1024:
        raise WorkflowError('course_source_too_large')
    try:
        cells = json.loads(file.read_text(encoding='utf-8'))['cells']
        header = '週一作業' if target.weekday() == 0 else '週五作業'
        selected, content = False, []
        for cell in cells:
            source = cell.get('source', [])
            text = ''.join(source) if isinstance(source, list) else source
            if re.search(r'^#{1,6}\s*週[一五]作業', text, re.M):
                if selected:
                    break
                selected = header in text
            if selected:
                if re.search(r'!\[.*?\]\(', text):
                    raise WorkflowError('notebook_image_unsupported')
                content.append(text)
        material = '\n\n'.join(content).strip()
        if not material or len(content) < 2:
            return assignment
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        raise WorkflowError('course_source_invalid') from None
    directory = data_dir / 'course-sources/provenance'
    write_json(directory / f'{assignment.course_id}-{assignment.assignment_id}.json', {
        'repository': profile['repository'], 'notebook': file.name,
        'source_sha256': digest(file.read_bytes()), 'section': header, 'model': profile['model']})
    return replace(assignment, prompt=(assignment.prompt + '\n\n' + material).strip())
