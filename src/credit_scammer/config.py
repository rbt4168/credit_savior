from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

from .errors import ConfigError
import re


def contained(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("path_outside_root")
    return resolved


@dataclass(frozen=True)
class Config:
    root: Path
    base_url: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    course_ids: tuple[str, ...]
    all_courses: bool
    interval_s: int
    headless: bool
    auth_path: Path
    data_dir: Path
    database_path: Path
    model: str
    timezone: str
    discord_webhook: str = field(default='', repr=False)

    @classmethod
    def load(cls, root: Path, *, require_auth: bool = False) -> Config:
        root = root.resolve()
        values = {**dotenv_values(root / ".env"), **os.environ}
        invalid: list[str] = []
        base = (values.get("COOL_BASE_URL") or "https://cool.ntu.edu.tw").rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme, parsed.netloc, parsed.path) != ("https", "cool.ntu.edu.tw", ""):
            invalid.append("COOL_BASE_URL")
        courses = (values.get("COOL_COURSE_IDS") or "").strip()
        all_courses = courses.lower() == "all"
        ids = tuple(dict.fromkeys(item.strip() for item in courses.split(",") if item.strip()))
        if not all_courses and (not ids or any(not x.isdigit() or int(x) < 1 for x in ids)):
            invalid.append("COOL_COURSE_IDS")
        if all_courses:
            ids = ()
        interval = values.get("CHECK_INTERVAL_SECONDS") or "600"
        if interval != "600":
            invalid.append("CHECK_INTERVAL_SECONDS")
        headless = (values.get("BROWSER_HEADLESS") or "true").lower()
        if headless not in ("true", "false"):
            invalid.append("BROWSER_HEADLESS")
        username = values.get("COOL_USERNAME") or ""
        password = values.get("COOL_PASSWORD") or ""
        if require_auth and not username:
            invalid.append("COOL_USERNAME")
        timezone = values.get("TZ") or "Asia/Taipei"
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            invalid.append("TZ")
        model = values.get("LLM_MODEL") or "gpt-6.1-sol"
        if model != "gpt-6.1-sol":
            invalid.append("LLM_MODEL")
        webhook = values.get('DISCORD_WEBHOOK_URL') or ''
        if webhook and not re.fullmatch(r'https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_-]+', webhook):
            invalid.append('DISCORD_WEBHOOK_URL')

        def path(key: str, default: str, parent: Path) -> Path:
            try:
                result = root / (values.get(key) or default)
                return contained(parent, result)
            except (ValueError, OSError):
                invalid.append(key)
                return parent / "invalid"

        data = path("DATA_DIR", "data", root / "data")
        database = path("DATABASE_PATH", "data/state.sqlite3", data)
        auth = path("AUTH_STATE_PATH", "playwright/.auth/state.json", root / "playwright/.auth")
        if database == data:
            invalid.append("DATABASE_PATH")
        if auth == (root / 'playwright/.auth').resolve():
            invalid.append('AUTH_STATE_PATH')
        if invalid:
            raise ConfigError(invalid)
        return cls(root, base, username, password, ids, all_courses, 600, headless == "true",
                   auth, data, database, model, timezone, webhook)
