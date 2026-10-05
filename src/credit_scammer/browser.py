from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from .artifacts import digest, read_json, write_json
from .config import Config
from .errors import WorkflowError


def is_login(url: str) -> bool:
    parsed = urlsplit(url)
    return "/login" in parsed.path or parsed.hostname == "adfs.ntu.edu.tw"


def normalize_username(value: str) -> str:
    return value.lower().split("\\")[-1].split("@")[0]


class SessionGate:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writing = False
        self.waiting_writers = 0

    @asynccontextmanager
    async def read(self):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.writing and not self.waiting_writers)
            self.readers += 1
        try:
            yield
        finally:
            async with self.condition:
                self.readers -= 1
                self.condition.notify_all()

    @asynccontextmanager
    async def write(self):
        async with self.condition:
            self.waiting_writers += 1
            try:
                await self.condition.wait_for(lambda: not self.writing and not self.readers)
                self.writing = True
            finally:
                self.waiting_writers -= 1
                self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.writing = False
                self.condition.notify_all()


class BrowserSession:
    def __init__(self, config: Config, *, interactive=False):
        self.config = config
        self.interactive = interactive
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = {}
        self.generation = 0
        self.state = "cold"
        self.gate = SessionGate()
        self.auth_lock = asyncio.Lock()
        self.submission_lock = asyncio.Lock()
        self.account_id = None
        self._operation = ContextVar('browser_operation', default=None)

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False if self.interactive else self.config.headless)
        await self.ensure_ready()

    async def _create_context(self, state=None):
        if self.context:
            await self.context.close()
        self.context = await self.browser.new_context(storage_state=state, accept_downloads=True)
        self.context.set_default_timeout(15_000)
        self.context.set_default_navigation_timeout(30_000)
        self.pages.clear()
        self.generation += 1

    async def _identity(self, page):
        if is_login(page.url) or urlsplit(page.url).hostname != "cool.ntu.edu.tw":
            return None
        return await page.evaluate(
            "() => window.ENV?.current_user_id || window.ENV?.current_user?.id || null")

    async def _save_state(self, user_id):
        self.account_id = str(user_id)
        state = await self.context.storage_state(indexed_db=True)
        write_json(self.config.auth_path, state)
        write_json(self.config.auth_path.with_suffix(".meta.json"), {
            "username": normalize_username(self.config.username), "account_id": str(user_id),
            "state_hash": digest(self.config.auth_path.read_bytes()),
        })

    async def _load_and_check(self) -> bool:
        state = None
        metadata = None
        try:
            state = read_json(self.config.auth_path)
            metadata = read_json(self.config.auth_path.with_suffix(".meta.json"))
            if (metadata["username"] != normalize_username(self.config.username)
                    or metadata["state_hash"] != digest(self.config.auth_path.read_bytes())):
                state = None
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            state = None
        await self._create_context(state)
        page = await self.context.new_page()
        self.pages["auth"] = page
        if state is None:
            return False
        await page.goto(self.config.base_url + "/courses", wait_until="domcontentloaded")
        user_id = await self._identity(page)
        valid = user_id is not None and str(user_id) == metadata["account_id"]
        if valid:
            self.account_id = str(user_id)
        return valid

    async def _login(self):
        if not self.config.password:
            raise WorkflowError("password_required")
        await self._create_context()
        page = await self.context.new_page()
        self.pages["auth"] = page
        await page.goto(self.config.base_url + "/login/portal", wait_until="domcontentloaded")
        button = page.get_by_role("button", name=re.compile(
            r"NTU\s*Account\s*Sign in|以計中帳號登入|臺大帳號登入|台大帳號登入", re.I))
        await button.click()
        await page.locator("#ContentPlaceHolder1_UsernameTextBox").wait_for()
        if urlsplit(page.url).hostname != "adfs.ntu.edu.tw":
            raise WorkflowError("unexpected_auth_origin")
        await page.locator("#ContentPlaceHolder1_UsernameTextBox").fill(self.config.username)
        await page.locator("#ContentPlaceHolder1_PasswordTextBox").fill(self.config.password)
        await page.locator("#ContentPlaceHolder1_SubmitButton").click()
        try:
            await page.wait_for_url(
                lambda url: urlsplit(str(url)).hostname == "cool.ntu.edu.tw"
                and not is_login(str(url)), timeout=60_000)
        except Exception:
            # The interactive command allows the owner to complete any extra verification.
            if not self.interactive:
                raise WorkflowError("interactive_required") from None
            await page.wait_for_url(
                lambda url: urlsplit(str(url)).hostname == "cool.ntu.edu.tw"
                and not is_login(str(url)), timeout=180_000)
        await page.goto(self.config.base_url + "/courses", wait_until="domcontentloaded")
        user_id = await self._identity(page)
        if user_id is None:
            raise WorkflowError("identity_unverified")
        await self._save_state(user_id)

    async def ensure_ready(self) -> int:
        if self.state == "ready" and self.context:
            return self.generation
        async with self.auth_lock:
            if self.state == "ready" and self.context:
                return self.generation
            async with self.gate.write():
                self.state = "checking"
                try:
                    if not await self._load_and_check():
                        self.state = "authenticating"
                        await self._login()
                    self.state = "ready"
                except WorkflowError as error:
                    self.state = "interactive_required" if error.code == "interactive_required" else "failed"
                    raise
                except Exception:
                    self.state = "failed"
                    raise WorkflowError("auth_unavailable", transient=True) from None
        return self.generation

    async def recover(self):
        self.state = "expired"
        await self.ensure_ready()

    @asynccontextmanager
    async def operation(self, role: str):
        active = self._operation.get()
        task = asyncio.current_task()
        if active == (task, role):
            yield self.pages[role]
            return
        await self.ensure_ready()
        async with self.gate.read():
            if role not in self.pages or self.pages[role].is_closed():
                self.pages[role] = await self.context.new_page()
            token = self._operation.set((task, role))
            try:
                yield self.pages[role]
            finally:
                self._operation.reset(token)

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.state = "closed"
