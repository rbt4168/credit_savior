"""Read course information and report changes without creating submission jobs."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import re
from io import BytesIO
from pypdf import PdfReader
from urllib.error import HTTPError
from urllib.parse import urlsplit, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .artifacts import canonical_hash, read_json, write_json
from .cool import ProblemHTML
from .errors import WorkflowError
from .models import now_ms


def text(html):
    parser = ProblemHTML()
    parser.feed(html or '')
    return '\n'.join(line.strip() for line in ''.join(parser.text).splitlines() if line.strip())


def external_urls(data_dir, course_id):
    path = data_dir / 'monitoring' / 'sources.json'
    if not path.exists():
        return []
    try:
        urls = read_json(path).get('courses', {}).get(course_id, [])
        if not isinstance(urls, list) or len(urls) > 12 or any(not isinstance(u, str) for u in urls):
            raise ValueError()
        return list(dict.fromkeys(urls))
    except (OSError, ValueError, AttributeError):
        raise WorkflowError('monitoring_config_invalid') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_external(url):
    for _ in range(6):
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            raise WorkflowError('external_source_url_invalid')
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
        if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise WorkflowError('external_source_url_invalid')
        try:
            with build_opener(NoRedirect()).open(Request(url, headers={
                    'User-Agent': 'Credit-Savior-Course-Monitor/0.1'}), timeout=15) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise WorkflowError('external_source_too_large')
                kind = response.headers.get_content_type()
                if kind == 'application/pdf' or data.startswith(b'%PDF-'):
                    pdf = PdfReader(BytesIO(data))
                    if len(pdf.pages) > 100:
                        raise WorkflowError('external_source_too_large')
                    return {'title': parsed.path.rsplit('/', 1)[-1], 'url': url,
                            'text': '\n'.join(page.extract_text() or '' for page in pdf.pages)}
                if kind not in ('text/html', 'text/plain', 'text/csv', 'application/xhtml+xml'):
                    raise WorkflowError('external_source_format_unsupported')
                meta = re.search(br'charset\s*=\s*["\x27]?([A-Za-z0-9_-]+)', data[:4096], re.I)
                encoding = response.headers.get_content_charset() or (meta[1].decode('ascii') if meta else 'utf-8')
                body = data.decode(encoding, errors='replace')
                return {'title': parsed.hostname + parsed.path, 'url': url,
                        'text': body if kind in ('text/plain', 'text/csv') else text(body)}
        except HTTPError as error:
            location = error.headers.get('Location')
            status = error.code
            error.close()
            if status not in (301, 302, 303, 307, 308) or not location:
                raise WorkflowError('external_source_http_failed') from None
            url = urljoin(url, location)
    raise WorkflowError('external_source_redirect_limit')


def resource_record(source, value, course_id):
    identifier = str(value.get('id', value.get('page_id', 'information')))
    url = value.get('html_url') or f'https://cool.ntu.edu.tw/courses/{course_id}'
    record = {'title': value.get('title') or value.get('name') or 'Course information',
              'url': url, 'text': text(value.get('message') or value.get('body')
                                      or value.get('description') or value.get('syllabus_body'))}
    record['dates'] = {key: value[key] for key in
        ('start_at', 'end_at', 'due_at', 'posted_at', 'delayed_post_at', 'updated_at',
         'last_reply_at') if value.get(key)}
    if source == 'discussions':
        record['reply_count'] = value.get('discussion_subentry_count', 0)
    if source == 'assignments':
        record['submission_types'] = value.get('submission_types') or []
    if source == 'modules':
        record['items'] = [{key: item.get(key) for key in
            ('id', 'title', 'position', 'type', 'external_url', 'html_url')}
            for item in value.get('items') or []]
    return f'{source}:{identifier}', record


async def collect_resources(client, course_id):
    sources = {
        'information': f'/api/v1/courses/{course_id}?include[]=syllabus_body',
        'announcements': f'/api/v1/announcements?context_codes[]=course_{course_id}&per_page=100',
        'discussions': f'/api/v1/courses/{course_id}/discussion_topics?per_page=100',
        'assignments': f'/api/v1/courses/{course_id}/assignments?per_page=100',
        'pages': f'/api/v1/courses/{course_id}/pages?per_page=100',
        'modules': f'/api/v1/courses/{course_id}/modules?include[]=items&per_page=100',
        'calendar': f'/api/v1/calendar_events?context_codes[]=course_{course_id}&all_events=true&type=event&per_page=100',
    }
    records, errors, checked = {}, [], []
    for source, path in sources.items():
        try:
            async with client.session.operation(client.role):
                if source == 'information':
                    value, _ = await client._json(path)
                    values = [value]
                else:
                    values = await client.paginated(path)
                if source == 'pages':
                    if len(values) > 50:
                        raise WorkflowError('course_pages_capacity_exceeded')
                    values = [(await client._json(f'/api/v1/courses/{course_id}/pages/{v["page_id"]}'))[0]
                              for v in values]
                if source == 'modules':
                    for value in values:
                        if len(value.get('items') or []) < value.get('items_count', 0):
                            value['items'] = await client.paginated(
                                f'/api/v1/courses/{course_id}/modules/{value["id"]}/items?per_page=100')
                for value in values:
                    key, record = resource_record(source, value, course_id)
                    records[key] = record
            checked.append(source)
        except Exception as error:
            if isinstance(error, WorkflowError) and error.code == 'auth_expired':
                raise
            if source == 'pages' and isinstance(error, WorkflowError) and error.code == 'course_not_found':
                checked.append('pages:not_exposed')
                continue
            errors.append({'source': source, 'error_code': getattr(error, 'code', 'course_source_error'),
                           'error_class': type(error).__name__})
    for url in external_urls(client.config.data_dir, course_id):
        try:
            records['external:' + url] = await asyncio.to_thread(read_external, url)
            checked.append('external:' + url)
        except Exception as error:
            errors.append({'source': 'external:' + url,
                           'error_code': getattr(error, 'code', 'external_source_unavailable'),
                           'error_class': type(error).__name__})
    return records, checked, errors


async def monitor_course(client, course_id):
    path = client.config.data_dir / 'monitoring' / f'{course_id}.json'
    try:
        previous = read_json(path)
    except FileNotFoundError:
        previous = None
    records, checked, errors = await collect_resources(client, course_id)
    entries = dict(previous.get('entries', {})) if previous else {}
    for key, old in list(entries.items()):
        source = key.split(':', 1)[0] if not key.startswith('external:') else key
        if key not in records and source in checked and not old.get('removed'):
            record = {**old['record'], 'text': '[Removed from course source]\n' + old['record']['text']}
            entries[key] = {'hash': canonical_hash(record), 'record': record,
                            'changed_at_ms': now_ms(), 'notify': True, 'removed': True}
    for key, record in records.items():
        digest = canonical_hash(record)
        old = entries.get(key)
        if not old or old['hash'] != digest:
            # Baseline each source only after its first successful read.
            source = key.split(':', 1)[0] if not key.startswith('external:') else key
            initialized = bool(previous and source in previous.get('initialized_sources', []))
            entries[key] = {'hash': digest, 'record': record, 'changed_at_ms': now_ms(),
                            'notify': initialized}
    initialized = set(previous.get('initialized_sources', [])) if previous else set()
    initialized.update(checked)
    result = {'course_id': course_id, 'checked_at_ms': now_ms(), 'checked_sources': checked,
              'initialized_sources': sorted(initialized), 'errors': errors, 'entries': entries}
    write_json(path, result)
    return result


def monitoring_states(data_dir, course_ids):
    for course_id in sorted(course_ids):
        try:
            yield read_json(data_dir / 'monitoring' / f'{course_id}.json')
        except FileNotFoundError:
            continue


def monitoring_summary(data_dir, course_ids):
    states = list(monitoring_states(data_dir, course_ids))
    return {'courses_checked': len(states), 'configured_courses': len(course_ids),
            'resource_count': sum(len(state['entries']) for state in states),
            'unavailable_sources': [{'course_id': state['course_id'], 'source': source}
                for state in states for source in state['checked_sources'] if source.endswith(':not_exposed')],
            'errors': [{'course_id': state['course_id'], **error}
                       for state in states for error in state['errors']]}
