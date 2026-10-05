from __future__ import annotations

import re
import asyncio
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .artifacts import artifact_path, atomic_write, digest
from .browser import BrowserSession, is_login
from .errors import WorkflowError
from .models import Answer, Assignment, Attachment, Course, Submission, Video
from .progress import coverage


def timestamp(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        raise WorkflowError("date_unparseable") from None


class ProblemHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.images = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "li", "pre", "tr", "h1", "h2", "h3"):
            self.text.append("\n")
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "img" and attributes.get("src"):
            self.images.append(attributes["src"])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li", "pre", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def submission_from_api(value) -> Submission:
    if not isinstance(value, dict):
        return Submission()
    state = value.get("workflow_state")
    submitted = value.get("submitted_at")
    if submitted or state in ("submitted", "pending_review", "graded"):
        # Graded with no submitted_at can be an instructor-assigned grade; still skip writing.
        return Submission("present", text=value.get("body"),
                          files=tuple(x["filename"] for x in value.get("attachments", [])),
                          attempt_id=str(value.get("attempt")) if value.get("attempt") else None,
                          submitted_at_ms=timestamp(submitted))
    if state == "unsubmitted":
        return Submission("absent")
    return Submission()


class CoolClient:
    def __init__(self, session: BrowserSession, role="scanner"):
        self.session = session
        self.config = session.config
        self.role = role
        self.video_cache = {}
        self.video_tasks = set()
        self.video_page = None
        self.current_video = None
        self.video_metadata = None

    def checked_url(self, path: str) -> str:
        result = urljoin(self.config.base_url, path)
        if urlsplit(result).netloc != urlsplit(self.config.base_url).netloc:
            raise WorkflowError("unexpected_resource_origin")
        if urlsplit(result).scheme != urlsplit(self.config.base_url).scheme:
            raise WorkflowError("unexpected_resource_origin")
        return result

    async def _goto(self, page, path: str):
        response = await page.goto(self.checked_url(path), wait_until="domcontentloaded")
        if is_login(page.url):
            raise WorkflowError("auth_expired", transient=True)
        if response and response.status in (403, 404):
            raise WorkflowError("course_forbidden")

    async def _json(self, path: str):
        response = await self.session.context.request.get(
            self.checked_url(path), timeout=30_000, max_redirects=0)
        if response.status in (401, 302, 303):
            raise WorkflowError("auth_expired", transient=True)
        if response.status in (403, 404):
            raise WorkflowError("course_forbidden")
        if not response.ok:
            raise WorkflowError("network_transient", transient=True)
        try:
            return await response.json(), response.headers.get("link", "")
        except Exception:
            raise WorkflowError("layout_changed") from None
        finally:
            await response.dispose()

    async def list_courses(self) -> list[Course]:
        async with self.session.operation(self.role) as page:
            await self._goto(page, "/courses")
            await page.locator("#my_courses_table").wait_for()
            rows = await page.evaluate("""() => [...document.querySelectorAll(
                '.course-list-table tbody tr')].map(r => ({
                href:r.querySelector('a[href*="/courses/"]')?.getAttribute('href'),
                name:r.querySelector('a[href*="/courses/"]')?.textContent.trim(),
                role:r.querySelector('.course-list-enrolled-as')?.textContent.trim()
                    || r.cells[4]?.textContent.trim()}))""")
        courses = {}
        for row in rows:
            match = re.search(r"/courses/(\d+)", row.get("href") or "")
            if not match:
                continue
            cid = match[1]
            if not self.config.all_courses and cid not in self.config.course_ids:
                continue
            student = bool(re.search(r"student|學生|学生", row.get("role") or "", re.I))
            courses[cid] = Course(cid, row["name"], self.checked_url(f"/courses/{cid}"), student)
        if not self.config.all_courses and set(self.config.course_ids) - courses.keys():
            raise WorkflowError("course_not_enrolled")
        return list(courses.values())

    async def list_assignments(self, course_id: str) -> list[dict]:
        async with self.session.operation(self.role) as page:
            await self._goto(page, f"/courses/{course_id}/assignments")
            if urlsplit(page.url).path != f"/courses/{course_id}/assignments":
                return []  # The course does not expose its assignments navigation item.
            path = f"/api/v1/courses/{course_id}/assignments?include[]=submission&per_page=100"
            seen, result = set(), []
            while path:
                if path in seen:
                    raise WorkflowError("pagination_repeated")
                seen.add(path)
                values, links = await self._json(path)
                if not isinstance(values, list):
                    raise WorkflowError("layout_changed")
                result.extend(values)
                next_link = re.search(r'<([^>]+)>;\s*rel="next"', links)
                path = next_link[1] if next_link else None
        return result

    async def read_submission(self, course_id: str, assignment_id: str) -> Submission:
        async with self.session.operation(self.role):
            value, _ = await self._json(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self")
            observed = submission_from_api(value)
            if observed.presence == 'present' and observed.files:
                from dataclasses import replace
                hashes = []
                for attachment in value.get('attachments', []):
                    # Download the exact receipt attachment through its verified Canvas file ID.
                    file_id = str(attachment.get('id', ''))
                    if not file_id.isdigit():
                        return observed
                    response = await self.session.context.request.get(self.checked_url(
                        f'/courses/{course_id}/files/{file_id}/download'), timeout=60_000)
                    try:
                        if not response.ok:
                            return observed
                        data = await response.body()
                        if len(data) > 25 * 1024 * 1024:
                            return observed
                        hashes.append(digest(data))
                    finally:
                        await response.dispose()
                return replace(observed, file_hashes=tuple(hashes))
            return observed

    async def read_assignment(self, course_id: str, assignment_id: str) -> Assignment:
        async with self.session.operation(self.role):
            value, _ = await self._json(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}?include[]=submission")
            return await self._snapshot(value)

    async def snapshot(self, value: dict) -> Assignment:
        async with self.session.operation(self.role):
            return await self._snapshot(value)

    async def _snapshot(self, value: dict) -> Assignment:
        course_id, assignment_id = str(value["course_id"]), str(value["id"])
        parser = ProblemHTML()
        parser.feed(value.get("description") or "")
        attachment_ids = {}
        for link in parser.links + parser.images:
            parsed = urlsplit(urljoin(self.config.base_url, link))
            if parsed.netloc != urlsplit(self.config.base_url).netloc:
                continue
            match = re.search(r"/(?:courses/\d+/)?files/(\d+)", parsed.path)
            if match:
                attachment_ids[match[1]] = True
        attachments = []
        total = 0
        for file_id in attachment_ids:
            metadata, _ = await self._json(f"/api/v1/files/{file_id}")
            size = metadata.get("size")
            if not isinstance(size, int) or size > 25 * 1024 * 1024:
                raise WorkflowError("attachment_too_large")
            total += size
            if total > 100 * 1024 * 1024:
                raise WorkflowError("attachment_too_large")
            response = await self.session.context.request.get(
                self.checked_url(f"/courses/{course_id}/files/{file_id}/download"), timeout=60_000)
            try:
                if not response.ok:
                    raise WorkflowError("attachment_download_failed", transient=True)
                data = await response.body()
            finally:
                await response.dispose()
            if len(data) > 25 * 1024 * 1024 or len(data) != size:
                raise WorkflowError("attachment_size_mismatch")
            suffix = Path(metadata.get("filename") or "file").suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".bin"
            relative = f"attachments/{file_id}/{digest(data)}{suffix}"
            atomic_write(artifact_path(self.config.data_dir, relative), data)
            attachments.append(Attachment(file_id, metadata["filename"], relative, len(data), digest(data)))
        # Public NTU PDFs use a separate HTTP client, never the authenticated cookie jar.
        external = set()
        for link in parser.links + re.findall(r'https?://[^\s<>]+', ''.join(parser.text)):
            parsed = urlsplit(link)
            if (parsed.hostname and parsed.hostname.endswith('.ntu.edu.tw')
                    and parsed.netloc != urlsplit(self.config.base_url).netloc
                    and parsed.path.lower().endswith('.pdf') and not parsed.query):
                external.add(link)
        if external:
            from .materials import fetch_pdf
            for link in sorted(external):
                data = await asyncio.to_thread(fetch_pdf, link)
                total += len(data)
                if total > 100 * 1024 * 1024:
                    raise WorkflowError('attachment_too_large')
                file_id = 'public-' + digest(link.encode())[:16]
                relative = f'attachments/{file_id}/{digest(data)}.pdf'
                atomic_write(artifact_path(self.config.data_dir, relative), data)
                attachments.append(Attachment(file_id, Path(urlsplit(link).path).name,
                                              relative, len(data), digest(data)))
        unsupported = None
        if value.get("group_category_id"):
            unsupported = "group_assignment_unsupported"
        elif value.get("is_quiz_assignment"):
            unsupported = "quiz_unsupported"
        elif value.get("locked_for_user"):
            unsupported = "assignment_closed"
        elif value.get("published") is False:
            unsupported = "assignment_unpublished"
        rubric = "\n".join(
            f"{criterion.get('description', '')}: {criterion.get('long_description', '')}"
            for criterion in value.get("rubric", []))
        return Assignment(
            course_id, assignment_id, self.checked_url(f"/courses/{course_id}/assignments/{assignment_id}"),
            value["name"], ("".join(parser.text).strip() +
                            ('\n\nReferenced URLs:\n' + '\n'.join(parser.links) if parser.links else '')),
            tuple(value.get("submission_types") or ()),
            tuple(x.lower().lstrip(".") for x in value.get("allowed_extensions") or ()),
            timestamp(value.get("due_at")), timestamp(value.get("unlock_at")),
            timestamp(value.get("lock_at")), rubric, tuple(attachments),
            submission_from_api(value.get("submission")), unsupported,
        )

    async def stage_answer(self, assignment: Assignment, answer: Answer):
        if self.role != "assignment":
            raise WorkflowError("role_cannot_submit")
        async with self.session.operation(self.role) as page:
            await self._goto(page, assignment.url)
            opener = page.locator("#assignment_show").get_by_role(
                "button", name=re.compile(r"^繳交作業$|^提交作業$|^Submit Assignment$", re.I))
            if await opener.count() == 1 and await opener.is_visible():
                await opener.click()
            if answer.kind == "files":
                control = page.locator("#submission_file_drop_0")
                await control.wait_for(state="attached")
                paths = [str(artifact_path(self.config.data_dir, p)) for p in answer.files]
                await control.set_input_files(paths[0])
                for index, path in enumerate(paths[1:], 1):
                    add = page.get_by_text(re.compile(r'^Add Another File$|^新增其他檔案$|^新增另一個檔案$', re.I))
                    if await add.count() != 1:
                        raise WorkflowError('multiple_file_upload_unverified')
                    await add.click()
                    await page.locator(f'#submission_file_drop_{index}').set_input_files(path)
            else:
                textarea = page.locator("#submission_body")
                await textarea.wait_for(state="attached")
                frames = page.frame_locator("iframe[id$='_ifr']")
                if await page.locator("iframe[id$='_ifr']").count() == 1:
                    await frames.locator("body").fill(answer.text)
                else:
                    await textarea.fill(answer.text)
            await page.locator("#submit_file_button").wait_for(state="visible")
            if not await page.locator("#submit_file_button").is_enabled():
                raise WorkflowError("upload_not_ready")

    async def click_submit(self):
        if self.role != "assignment":
            raise WorkflowError("role_cannot_submit")
        async with self.session.operation(self.role) as page:
            await page.locator("#submit_file_button").click(no_wait_after=True)

    async def list_videos(self, course_id: str) -> list[Video]:
        async with self.session.operation(self.role):
            path = f'/api/v1/courses/{course_id}/modules?include[]=items&per_page=100'
            modules, seen = [], set()
            while path:
                if path in seen:
                    raise WorkflowError('pagination_repeated')
                seen.add(path)
                values, links = await self._json(path)
                modules.extend(values)
                next_link = re.search(r'<([^>]+)>;\s*rel="next"', links)
                path = next_link[1] if next_link else None
            videos = {}
            for module in sorted(modules, key=lambda m: m['position']):
                if module.get('state') == 'locked':
                    continue
                items = module.get('items') or []
                if len(items) < module.get('items_count', 0):
                    path = f"/api/v1/courses/{course_id}/modules/{module['id']}/items?per_page=100"
                    items, seen = [], set()
                    while path:
                        if path in seen:
                            raise WorkflowError('pagination_repeated')
                        seen.add(path)
                        values, links = await self._json(path)
                        items.extend(values)
                        next_link = re.search(r'<([^>]+)>;\s*rel="next"', links)
                        path = next_link[1] if next_link else None
                for item in sorted(items, key=lambda i: i['position']):
                    external = urlsplit(item.get('external_url') or '')
                    match = re.fullmatch(r'/ltiv1p1/launch/videos/(\d+)', external.path)
                    if (item.get('type') != 'ExternalTool' or not match
                            or external.hostname != 'cool-video.dlc.ntu.edu.tw'):
                        continue
                    source = match[1]
                    url = self.checked_url(f"/courses/{course_id}/modules/items/{item['id']}")
                    videos.setdefault(source, Video(course_id, source, url,
                        digest(f'{course_id}:{source}:{item["id"]}'.encode()),
                        player_kind='ntu-cool-lti', order=len(videos), title=item.get('title') or ''))
            return list(videos.values())

    async def _capture_video_response(self, response, source_page):
        if source_page is not self.video_page:
            return
        selected_video = self.current_video
        parsed = urlsplit(response.url)
        if (parsed.hostname != 'cool-video.dlc.ntu.edu.tw' or response.status != 200
                or not re.fullmatch(r'/api/users/current|/api/courses/\d+/videos/\d+/view|'
                    r'/api/courses/\d+/course-videos/\d+/viewing-records/summaries', parsed.path)):
            return
        try:
            value = await response.json()
            if source_page is self.video_page and self.current_video == selected_video:
                self.video_cache[parsed.path] = value
        except Exception:
            pass

    def _video_response(self, response, source_page):
        task = asyncio.create_task(self._capture_video_response(response, source_page))
        self.video_tasks.add(task)
        task.add_done_callback(self.video_tasks.discard)

    async def _video_element(self, page):
        for frame in page.frames:
            if urlsplit(frame.url).hostname not in {
                    'cool-video.dlc.ntu.edu.tw', 'www.youtube.com', 'www.youtube-nocookie.com'}:
                continue
            locator = frame.locator('video')
            if await locator.count() == 1:
                return locator
        return None

    async def inspect_video(self, video: Video):
        async with self.session.operation(self.role) as page:
            if self.video_page is not page:
                page.on('response', lambda response: self._video_response(response, page))
                self.video_page = page
            self.video_cache.clear()
            self.current_video = video
            self.video_metadata = None
            await self._goto(page, video.url)
            for _ in range(60):
                for path, value in list(self.video_cache.items()):
                    if re.fullmatch(f'/api/courses/{video.course_id}/videos/\\d+/view', path):
                        if str(value.get('videoId')) != video.video_id:
                            raise WorkflowError('video_source_changed')
                        self.video_metadata = value
                locator = await self._video_element(page)
                if self.video_metadata and locator is not None:
                    await locator.evaluate('v=>v.pause()')
                    if await self.video_progress() != 'unknown':
                        return
                # Some players create their iframe only after the normal Play button.
                for frame in page.frames:
                    if urlsplit(frame.url).hostname == 'cool-video.dlc.ntu.edu.tw':
                        play = frame.get_by_role('button', name='Play Video', exact=True)
                        if await play.count() == 1 and await play.is_visible():
                            await play.click()
                await asyncio.sleep(.5)
            raise WorkflowError('video_player_unavailable', transient=True)

    async def open_video(self, video: Video, position_s: float):
        if self.current_video != video or not self.video_metadata:
            await self.inspect_video(video)
        async with self.session.operation(self.role) as page:
            locator = await self._video_element(page)
            if locator is None:
                raise WorkflowError('video_player_unavailable', transient=True)
            if await locator.evaluate('v=>v.readyState === 0 && !v.currentSrc'):
                # Video.js can cover the YouTube iframe with its own Play overlay.
                # Start through that control so the player loads a media source.
                started = False
                for frame in page.frames:
                    if urlsplit(frame.url).hostname == 'cool-video.dlc.ntu.edu.tw':
                        play = frame.get_by_role('button', name='Play Video', exact=True)
                        if await play.count() == 1 and await play.is_visible():
                            await play.click()
                            started = True
                            break
                if not started:
                    for frame in page.frames:
                        if urlsplit(frame.url).hostname not in {'www.youtube.com', 'www.youtube-nocookie.com'}:
                            continue
                        if await frame.locator('video').count() != 1:
                            continue
                        play = frame.get_by_role('button', name=re.compile(
                            r'^(Play video|播放影片|播放视频)$', re.I))
                        if await play.count() == 1 and await play.is_visible():
                            await play.click()
                        break
            await locator.evaluate("""async (v, position) => {
                v.muted=true;
                v.playbackRate=1;
                if (v.readyState < 1) await new Promise((resolve,reject)=>{
                    v.addEventListener('loadedmetadata', resolve, {once:true});
                    setTimeout(()=>reject(new Error('metadata timeout')), 30000);
                });
                v.currentTime=Math.max(0, Math.min(position,v.duration||0));
                v.playbackRate=1;
                v.muted=true;
                await Promise.race([v.play(), new Promise((_,reject)=>
                    setTimeout(()=>reject(new Error('play timeout')),30000))]);
            }""", position_s)

    async def playback(self):
        async with self.session.operation(self.role) as page:
            locator = await self._video_element(page)
            if locator is None:
                raise WorkflowError('video_player_unavailable', transient=True)
            return await locator.evaluate("""v=>({
                position_s:v.currentTime,duration_s:Number.isFinite(v.duration)?v.duration:null,
                paused:v.paused,ended:v.ended,buffering:v.readyState<3})""")

    async def resume_playback(self):
        async with self.session.operation(self.role) as page:
            locator = await self._video_element(page)
            if locator is None:
                raise WorkflowError('video_player_unavailable', transient=True)
            await locator.evaluate("v=>v.play()")

    def missing_video_ranges(self):
        if not self.video_metadata or not self.current_video:
            return None
        cid, vid = self.current_video.course_id, self.video_metadata['id']
        summaries = self.video_cache.get(f'/api/courses/{cid}/course-videos/{vid}/viewing-records/summaries')
        user = self.video_cache.get('/api/users/current', {})
        user_id = user.get('id')
        if not isinstance(summaries, list) or user_id is None:
            return None
        own = [x for x in summaries if str(x.get('userId')) == str(user_id)
               and str(x.get('courseId')) == cid and str(x.get('courseVideoId')) == str(vid)]
        if len(own) > 1:
            return None
        try:
            _, gaps = coverage(own[0].get('records', []) if own else [], self.video_metadata['length'])
            return gaps
        except (ValueError, KeyError, TypeError):
            return None

    async def video_progress(self, *, refresh=False):
        if refresh and self.current_video and self.video_metadata:
            # Use the site's normal viewing-records tab; retain the player so its
            # own heartbeat/ended handler can finish saving records.
            async with self.session.operation(self.role) as page:
                path = (f'/api/courses/{self.current_video.course_id}/course-videos/'
                        f'{self.video_metadata["id"]}/viewing-records/summaries')
                for frame in page.frames:
                    if urlsplit(frame.url).hostname != 'cool-video.dlc.ntu.edu.tw':
                        continue
                    tab = frame.get_by_role('tab', name=re.compile(r'觀看紀錄|Viewing Records', re.I))
                    if await tab.count() != 1:
                        continue
                    other = frame.get_by_role('tab', name=re.compile(r'^留言$|^Comments$', re.I))
                    if await other.count() == 1:
                        await other.click()
                    self.video_cache.pop(path, None)
                    await tab.click()
                    for _ in range(20):
                        if path in self.video_cache:
                            break
                        await asyncio.sleep(.25)
                    break
        gaps = self.missing_video_ranges()
        return 'unknown' if gaps is None else ('incomplete' if gaps else 'complete')

    def video_evidence(self):
        return {'course_id': self.current_video.course_id if self.current_video else None,
                'source_id': self.current_video.video_id if self.current_video else None,
                'platform_video_id': self.video_metadata.get('id') if self.video_metadata else None,
                'platform_duration_s': self.video_metadata.get('length') if self.video_metadata else None,
                'missing_ranges': self.missing_video_ranges()}
